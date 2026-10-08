from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
from mrq_core.asof import FundamentalObservation

# SEC does not implement DataProvider on purpose. That interface is keyed on
# SeriesSpec and returns a single observation/date/value frame for a macro
# series; company fundamentals are entity-keyed, multi-metric, and already
# point-in-time. Forcing it through that interface would lose the entity
# dimension or invite passing a company through a series-shaped hole.


#: SEC's published fair-access limit. Exceeding it gets an IP throttled.
MIN_SECONDS_BETWEEN_REQUESTS = 0.11

#: Only periodic reports carry a full set of comparable statements. 8-K and
#: friends contain fragments, and mixing them in would double-count periods.
PERIODIC_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F"})

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

#: Where raw filings are cached. A companyfacts payload runs to megabytes and
#: is immutable once written, so refetching it per backtest is pure waste.
DEFAULT_CACHE = Path("data/cache/sec")


class SecRateLimited(RuntimeError):
    pass


class SecProvider:
    """US company fundamentals from SEC EDGAR XBRL.

    Unlike macro series, filings are already point-in-time and need no archive:
    every version ever submitted is still on EDGAR with the date it was filed.
    That makes ``filed`` the honest ``available_date`` — as of 2018-01-01 the
    2017-09-30 cash-flow figure is the one filed 2017-11-03, not the restatement
    that only appeared in 2018-11. A current-value feed would quietly hand a
    2018 backtest the corrected number a year early.

    The catch is that ``as_of`` is mandatory. There is no way to ask for "the
    numbers" without saying when you want to know them, which is the whole point:
    forgetting to filter is the single easiest way to fabricate a backtest.
    """

    def __init__(
        self,
        *,
        cache_dir: Path = DEFAULT_CACHE,
        user_agent: str = "macro-regime-quant research (contact: set SEC_USER_AGENT)",
        min_interval: float = MIN_SECONDS_BETWEEN_REQUESTS,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.user_agent = user_agent
        self.min_interval = min_interval
        self._last_request = 0.0

    # ------------------------------------------------------------------ #
    # transport
    # ------------------------------------------------------------------ #

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    @staticmethod
    def _require_requests():
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise RuntimeError(
                "The SEC provider needs the 'data' extra: "
                "uv sync --all-packages --extra data"
            ) from exc
        return requests

    def _get_json(self, url: str, cache_name: str, *, refresh: bool = False) -> dict:
        cache_path = self.cache_dir / cache_name
        if cache_path.exists() and not refresh:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        requests = self._require_requests()

        self._throttle()
        response = requests.get(
            url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}, timeout=60
        )
        if response.status_code == 429:
            raise SecRateLimited(
                "SEC throttled this client (HTTP 429). Wait, lower --refresh frequency, "
                "and make sure SEC_USER_AGENT names a real contact."
            )
        response.raise_for_status()

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = response.json()
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    # ------------------------------------------------------------------ #
    # identity
    # ------------------------------------------------------------------ #

    def ticker_to_cik(self, ticker: str) -> int:
        """Resolve a ticker to its zero-padded CIK.

        A ticker is not a stable entity id: it gets reused, and a class share
        can list under several. The CIK is the identifier EDGAR itself uses.
        """

        mapping = self._get_json(TICKERS_URL, "company_tickers.json")
        wanted = ticker.strip().upper()
        for row in mapping.values():
            if str(row["ticker"]).upper() == wanted:
                return int(row["cik_str"])
        raise KeyError(f"{ticker!r} not found in SEC company_tickers.json")

    def entity_id(self, ticker: str) -> str:
        """Stable namespaced id matching the Company entity contract (US:AAPL)."""

        return f"US:{self.ticker_to_cik(ticker):010d}"

    # ------------------------------------------------------------------ #
    # fundamentals
    # ------------------------------------------------------------------ #

    def company_facts(self, cik: int, *, refresh: bool = False) -> dict:
        return self._get_json(FACTS_URL.format(cik=cik), f"CIK{cik:010d}_facts.json", refresh=refresh)

    def observations(
        self,
        ticker: str,
        metrics: dict[str, str],
        *,
        as_of: str | pd.Timestamp,
        forms: frozenset[str] = PERIODIC_FORMS,
        refresh: bool = False,
    ) -> list[FundamentalObservation]:
        """Return point-in-time fundamentals for one company.

        ``metrics`` maps the us-gaap tag to the metric name recorded in the
        observation, e.g. ``{"NetCashProvidedByUsedInOperatingActivities":
        "operating_cash_flow"}``.

        As of ``as_of`` a period is represented by the most recently filed
        version that existed on that date. Later restatements are invisible by
        construction, which is the entire reason to use this feed at all.
        """

        if not as_of:
            raise ValueError(
                "as_of is required. EDGAR keeps every filed version, so 'the numbers' "
                "is not a question you can ask without saying when you want them."
            )
        cutoff = pd.Timestamp(as_of)
        cik = self.ticker_to_cik(ticker)
        facts = self.company_facts(cik, refresh=refresh)["facts"]

        out: list[FundamentalObservation] = []
        for tag, metric in metrics.items():
            node = facts.get("us-gaap", {}).get(tag)
            if node is None:
                continue
            for unit, rows in node["units"].items():
                latest: dict[str, dict] = {}
                for row in rows:
                    if row.get("form") not in forms:
                        continue
                    filed = pd.Timestamp(row["filed"])
                    period = row["end"]
                    # Never accept a filing that postdates the vantage point, and
                    # never one dated before the period it describes.
                    if filed > cutoff or filed < pd.Timestamp(period):
                        continue
                    if period not in latest or filed > pd.Timestamp(latest[period]["filed"]):
                        latest[period] = row

                for period, row in latest.items():
                    out.append(
                        FundamentalObservation(
                            company_id=f"US:{cik:010d}",
                            metric=metric,
                            period_end=pd.Timestamp(period),
                            available_date=pd.Timestamp(row["filed"]),
                            value=float(row["val"]),
                            unit=unit,
                            source=f"SEC/{row['form']}/{row['accn']}",
                        )
                    )
        return out

    def fundamentals_frame(
        self,
        ticker: str,
        metrics: dict[str, str],
        *,
        as_of: str | pd.Timestamp,
        **kwargs,
    ) -> pd.DataFrame:
        """Observations as a tidy frame, newest period first per metric."""

        rows = self.observations(ticker, metrics, as_of=as_of, **kwargs)
        if not rows:
            return pd.DataFrame(
                columns=["company_id", "metric", "period_end", "available_date", "value", "unit", "source"]
            )
        return pd.DataFrame([r.__dict__ for r in rows]).sort_values(
            ["metric", "period_end"], ascending=[True, False]
        ).reset_index(drop=True)
