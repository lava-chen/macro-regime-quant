from __future__ import annotations

import csv
import html
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import yaml

NBS_LIST_URL = "https://www.stats.gov.cn/sj/zxfb/"
PBOC_LIST_URL = "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html"
NBS_NAME = "National Bureau of Statistics of China"
PBOC_NAME = "People's Bank of China"

NBS_SERIES = {
    "industrial_production": {
        "series_key": "cn_industrial_production",
        "filename": "industrial_production_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "monthly_yoy_percent; Jan-Feb is a joint-period release",
        "phrase": r"规模以上工业增加值",
    },
    "retail_sales": {
        "series_key": "cn_retail_sales",
        "filename": "retail_sales_monthly_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "monthly_yoy_percent",
        "phrase": r"社会消费品零售总额",
    },
    "retail_sales_ytd": {
        "series_key": "cn_retail_sales_ytd",
        "filename": "retail_sales_ytd_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "ytd_yoy_percent; Jan-Feb is a joint-period release",
        "phrase": r"社会消费品零售总额",
    },
    "fixed_asset_investment": {
        "series_key": "cn_fixed_asset_investment",
        "filename": "fixed_asset_investment_ytd_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "ytd_yoy_percent",
        "phrase": r"全国固定资产投资|固定资产投资（不含农户）|固定资产投资\(不含农户\)",
    },
    "cpi": {
        "series_key": "cn_cpi",
        "filename": "cpi_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "yoy_percent",
        "phrase": r"居民消费价格",
    },
    "core_cpi": {
        "series_key": "cn_core_cpi",
        "filename": "core_cpi_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "yoy_percent",
        "phrase": r"核心CPI",
    },
    "ppi": {
        "series_key": "cn_ppi",
        "filename": "ppi_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "yoy_percent",
        "phrase": r"工业生产者出厂价格",
    },
    "pmi_new_orders": {
        "series_key": "cn_pmi_new_orders",
        "filename": "pmi_new_orders.csv",
        "unit": "index_points",
        "reported_as": "diffusion_index",
        "phrase": "",
    },
}
PBOC_SERIES = {
    "m1": {
        "series_key": "cn_m1",
        "filename": "m1_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "yoy_percent",
    },
    "m2": {
        "series_key": "cn_m2",
        "filename": "m2_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "yoy_percent",
    },
    "tsf_stock": {
        "series_key": "cn_tsf_stock_yoy",
        "filename": "tsf_stock_yoy.csv",
        "unit": "percent_yoy",
        "reported_as": "yoy_percent",
    },
}
SNAPSHOT_COLUMNS = [
    "observation_date",
    "observation_period",
    "available_date",
    "availability_basis",
    "availability_evidence_url",
    "source_value_url",
    "value",
    "source_title",
]


@dataclass(frozen=True)
class Link:
    title: str
    url: str


@dataclass(frozen=True)
class Article:
    title: str
    publication_date: str | None
    body_text: str
    all_text: str
    links: tuple[Link, ...]
    page_count: int | None = None


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.links: list[Link] = []
        self.title_parts: list[str] = []
        self.date_parts: list[str] = []
        self.body_parts: list[str] = []
        self.all_parts: list[str] = []
        self._anchor_href: str | None = None
        self._anchor_title: str | None = None
        self._anchor_parts: list[str] = []
        self._h1 = False
        self._body_depth = 0
        self._date_depth = 0
        self.page_count: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = values.get("name", "").lower()
            if key in {"articletitle", "pubdate"}:
                self.metadata[key] = values.get("content", "").strip()
        if (
            tag.lower() == "input"
            and values.get("name", "").lower() == "article_paging_list_hidden"
        ):
            raw_count = values.get("totalpage") or values.get("totalpages")
            try:
                self.page_count = int(raw_count)
            except (TypeError, ValueError):
                self.page_count = None
        if tag.lower() == "a":
            href = values.get("href") or values.get("tagname")
            if href:
                self._anchor_href = href
                self._anchor_title = values.get("title") or None
                self._anchor_parts = []
        if tag.lower() == "h1":
            self._h1 = True
        if tag.lower() == "div":
            classes = values.get("class", "").lower().split()
            if (
                values.get("id", "").lower() == "zoom"
                or any(name in {"trs_editor_view", "trs_editor"} for name in classes)
                or self._body_depth
            ):
                self._body_depth += 1
            if "detail-title-des" in classes or self._date_depth:
                self._date_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._anchor_href is not None:
            title = " ".join("".join(self._anchor_parts).split()) or self._anchor_title or ""
            self.links.append(Link(title=title, url=self._anchor_href))
            self._anchor_href = None
            self._anchor_title = None
            self._anchor_parts = []
        elif tag == "h1":
            self._h1 = False
        elif tag == "div":
            if self._body_depth:
                self._body_depth -= 1
            if self._date_depth:
                self._date_depth -= 1

    def handle_data(self, data: str) -> None:
        self.all_parts.append(data)
        if self._anchor_href is not None:
            self._anchor_parts.append(data)
        if self._h1:
            self.title_parts.append(data)
        if self._body_depth:
            self.body_parts.append(data)
        if self._date_depth:
            self.date_parts.append(data)


def _parse_date(text: str) -> str | None:
    match = re.search(
        r"(?<!\d)(20\d{2})\s*(?:[-/.年])\s*(\d{1,2})\s*(?:[-/.月])\s*(\d{1,2})",
        text,
    )
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def _parse_page(source: str) -> Article:
    parser = _PageParser()
    parser.feed(source)
    parser.close()
    title = parser.metadata.get("articletitle") or "".join(parser.title_parts)
    publication_date = _parse_date(parser.metadata.get("pubdate", ""))
    if publication_date is None:
        publication_date = _parse_date(" ".join(parser.date_parts))
    return Article(
        title=html.unescape(" ".join(title.split())).strip(),
        publication_date=publication_date,
        body_text=" ".join(" ".join(parser.body_parts).split()),
        all_text=" ".join(" ".join(parser.all_parts).split()),
        links=tuple(parser.links),
        page_count=parser.page_count,
    )


def _fetch_text(url: str) -> str:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install optional data dependencies: pip install -e '.[data]'") from exc
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.stats.gov.cn/sj/zxfb/"
        if "stats.gov.cn" in url
        else "https://www.pbc.gov.cn/",
    }
    error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=(10, 45))
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            if re.search(
                r"Please enable JavaScript and refresh the page|请开启JavaScript并刷新该页",
                response.text,
                flags=re.IGNORECASE,
            ):
                raise RuntimeError(f"Official site returned a JavaScript verification page: {url}")
            return response.text
        except (requests.RequestException, RuntimeError) as exc:
            error = exc
            if attempt < 2:
                time.sleep(0.75 * (attempt + 1))
    raise RuntimeError(f"Could not fetch official source page {url}: {error}")


def _fetch_bytes(url: str) -> bytes:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install optional data dependencies: pip install -e '.[data]'") from exc
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.pbc.gov.cn/",
    }
    error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=(10, 45))
            response.raise_for_status()
            content = response.content
            if "pdf" not in response.headers.get(
                "Content-Type", ""
            ).lower() and not content.startswith(b"%PDF-"):
                raise RuntimeError(f"Expected a PDF attachment from {url}")
            return content
        except (requests.RequestException, RuntimeError) as exc:
            error = exc
            if attempt < 2:
                time.sleep(0.75 * (attempt + 1))
    raise RuntimeError(f"Could not fetch official PDF attachment {url}: {error}")


def _extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise RuntimeError("Install PDF support with pip install 'pypdf>=5.0'") from exc
    try:
        reader = PdfReader(BytesIO(content))
        return " ".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise RuntimeError(f"Could not read official PDF attachment: {exc}") from exc


def _month_end(year: int, month: int) -> str:
    import calendar

    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def _period_from_title(title: str) -> tuple[int, int, str, str] | None:
    year_match = re.search(r"(?<!\d)(20\d{2})年", title)
    if not year_match:
        return None
    year = int(year_match.group(1))
    rest = title[year_match.end() :]
    joint = re.search(r"(\d{1,2})\s*[—–－-]\s*(\d{1,2})月", rest)
    if joint:
        first, last = map(int, joint.groups())
        if first == 1 and 1 <= last <= 12:
            return year, last, _month_end(year, last), f"{year:04d}-01/{year:04d}-{last:02d}"
    if re.search(r"前三季度|前三季", rest):
        month = 9
    elif re.search(r"一季度", rest):
        month = 3
    elif re.search(r"上半年", rest):
        month = 6
    else:
        month_match = re.search(r"(\d{1,2})月", rest)
        if month_match:
            month = int(month_match.group(1))
        elif re.search(r"金融统计数据报告", rest):
            month = 12
        else:
            return None
    if not 1 <= month <= 12:
        return None
    return year, month, _month_end(year, month), f"{year:04d}-{month:02d}"


def _nbs_series(title: str) -> str | None:
    if "中国采购经理指数运行情况" in title:
        return "pmi_new_orders"
    if "规模以上工业增加值" in title and "利润" not in title:
        return "industrial_production"
    if "社会消费品零售总额" in title:
        return "retail_sales"
    if "固定资产投资" in title and "房地产" not in title:
        return "fixed_asset_investment"
    if "居民消费价格" in title:
        return "cpi"
    if "工业生产者出厂价格" in title:
        return "ppi"
    return None


def _nbs_annual_december_period(
    series: str,
    title: str,
    body: str,
) -> tuple[int, int, str, str] | None:
    year_match = re.search(r"(?<!\d)(20\d{2})年", title)
    if not year_match:
        return None
    year = int(year_match.group(1))
    if series == "fixed_asset_investment":
        return year, 12, _month_end(year, 12), f"{year:04d}-12"
    if series == "retail_sales" and re.search(r"12\s*月份", body):
        return year, 12, _month_end(year, 12), f"{year:04d}-12"
    return None


def _nbs_annual_retail_december_value(body: str) -> float | None:
    month = re.search(r"12\s*月份", body)
    if not month:
        return None
    tail = body[month.end() : month.end() + 300]
    phrase = re.search(NBS_SERIES["retail_sales"]["phrase"], tail)
    if not phrase:
        return None
    return _signed_percent(tail[phrase.end() : phrase.end() + 180])


def _signed_percent(text: str) -> float | None:
    match = re.search(
        r"(增长|上涨|上升|下降|下跌|减少|回落|持平)\s*([+-]?[\d,]+(?:\.\d+)?)\s*%",
        text,
    )
    if not match:
        return None
    movement, raw_value = match.groups()
    value = float(raw_value.replace(",", ""))
    if movement in {"下降", "下跌", "减少", "回落"}:
        return -abs(value)
    if movement == "持平":
        return 0.0
    return value


def _nbs_value(series: str, body: str, title: str) -> float | None:
    if series == "pmi_new_orders":
        match = re.search(r"新订单指数\s*(?:为|是|：|:)?\s*([\d]+(?:\.\d+)?)\s*%?", body)
        return float(match.group(1)) if match else None
    phrase = NBS_SERIES[series]["phrase"]
    phrase_match = re.search(phrase, body)
    if phrase_match:
        value = _signed_percent(body[phrase_match.end() : phrase_match.end() + 280])
        if value is not None:
            return value
    phrase_match = re.search(phrase, title)
    if phrase_match:
        return _signed_percent(title[phrase_match.end() :])
    return None


def _nbs_core_cpi_value(body: str) -> float | None:
    """Extract core CPI only when the official release states it explicitly."""

    match = re.search(r"核心\s*CPI(?:指数)?", body, flags=re.IGNORECASE)
    if not match:
        return None
    return _signed_percent(body[match.end() : match.end() + 180])


def _unique_links(links: tuple[Link, ...], base_url: str, prefix: str) -> list[Link]:
    unique: dict[str, Link] = {}
    for link in links:
        if not link.title:
            continue
        url = urljoin(base_url, link.url)
        if not urlparse(url).path.startswith(prefix):
            continue
        if url not in unique or len(link.title) > len(unique[url].title):
            unique[url] = Link(title=link.title.strip(), url=url)
    return list(unique.values())


def _nbs_year_in_range(title: str, start_year: int, end_year: int) -> bool:
    match = re.search(r"(?<!\d)(20\d{2})年", title)
    return bool(match and start_year <= int(match.group(1)) <= end_year)


def _save_snapshot(
    output_dir: Path,
    spec: dict[str, str],
    rows: list[dict[str, object]],
    *,
    owner: str,
    portal: str,
    downloaded_at: str,
    notes: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda row: (str(row["observation_date"]), str(row["available_date"])))
    unique: dict[str, dict[str, object]] = {}
    for row in rows:
        key = str(row["observation_date"])
        existing = unique.get(key)
        if existing is None:
            unique[key] = row
            continue
        if float(existing["value"]) != float(row["value"]):
            raise ValueError(
                f"Conflicting values for {spec['series_key']} at {key}: "
                f"{existing['value']} vs {row['value']}"
            )
        if not existing["available_date"] and row["available_date"]:
            unique[key] = row

    csv_path = output_dir / spec["filename"]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_COLUMNS)
        writer.writeheader()
        writer.writerows(unique.values())
    dates = sorted(unique)
    metadata = {
        "series_key": spec["series_key"],
        "source_name": owner,
        "source_url": portal,
        "frequency": "monthly",
        "unit": spec["unit"],
        "downloaded_at": downloaded_at,
        "reported_as": spec["reported_as"],
        "revision_policy": "frozen_value_as_reported_in_each_original_release_document",
        "notes": notes.strip()
        + (f" Observation coverage: {dates[0]} through {dates[-1]}." if dates else ""),
    }
    csv_path.with_suffix(".meta.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _snapshot_row(
    observation_date: str,
    observation_period: str,
    available_date: str | None,
    evidence_url: str,
    value: float,
    value_url: str,
    *,
    source_title: str = "",
) -> dict[str, object]:
    return {
        "observation_date": observation_date,
        "observation_period": observation_period,
        "available_date": available_date or "",
        "availability_basis": "official_release" if available_date else "unknown",
        "availability_evidence_url": evidence_url if available_date else "",
        "source_value_url": value_url,
        "value": value,
        "source_title": source_title,
    }


def _coverage(rows: list[dict[str, object]]) -> dict[str, object]:
    dates = sorted({str(row["observation_date"]) for row in rows})
    return {
        "rows": len(dates),
        "first_observation": dates[0] if dates else None,
        "last_observation": dates[-1] if dates else None,
        "confirmed_release_dates": sum(bool(row["available_date"]) for row in rows),
        "unknown_release_dates": sum(not bool(row["available_date"]) for row in rows),
    }


def _crawl_nbs(
    fetch_text: Callable[[str], str],
    start_year: int,
    end_year: int,
    max_workers: int,
) -> tuple[int, list[Link], list[dict[str, str]]]:
    first = fetch_text(NBS_LIST_URL)
    page_match = re.search(r"createPageHTML\(\s*(\d+)\s*,\s*0\s*,\s*['\"]index", first)
    if not page_match:
        raise ValueError("Could not read pagination on the official NBS releases archive")
    page_count = int(page_match.group(1))
    urls = [urljoin(NBS_LIST_URL, f"index_{i}.html") for i in range(1, page_count)]
    pages = [first]
    listing_errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_text, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                pages.append(future.result())
            except Exception as exc:  # noqa: BLE001 - report incomplete archive pages
                listing_errors.append({"url": url, "error": str(exc)})
    candidates: dict[str, Link] = {}
    for page in pages:
        parsed = _parse_page(page)
        for link in _unique_links(parsed.links, NBS_LIST_URL, "/sj/zxfb/"):
            if _nbs_year_in_range(link.title, start_year, end_year) and _nbs_series(link.title):
                candidates[link.url] = link
    return page_count, list(candidates.values()), listing_errors


def _collect_articles(
    links: list[Link],
    fetch_text: Callable[[str], str],
    max_workers: int,
) -> tuple[list[tuple[Link, Article]], list[dict[str, str]]]:
    articles: list[tuple[Link, Article]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_text, link.url): link for link in links}
        for future in as_completed(futures):
            link = futures[future]
            try:
                articles.append((link, _parse_page(future.result())))
            except RuntimeError as exc:
                errors.append({"url": link.url, "error": str(exc)})
    return articles, errors


def collect_nbs_snapshots(
    output_dir: str | Path,
    *,
    start_year: int = 2005,
    end_year: int | None = None,
    fetch_text: Callable[[str], str] = _fetch_text,
    max_workers: int = 2,
) -> dict[str, object]:
    """Collect NBS activity, prices and PMI snapshots from original release pages."""

    end_year = end_year or datetime.now(ZoneInfo("Asia/Shanghai")).year
    if start_year > end_year:
        raise ValueError("start_year must be <= end_year")
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    page_count, links, listing_errors = _crawl_nbs(fetch_text, start_year, end_year, max_workers)
    articles, fetch_errors = _collect_articles(links, fetch_text, max_workers)
    fetch_errors = [*listing_errors, *fetch_errors]
    rows: dict[str, list[dict[str, object]]] = {name: [] for name in NBS_SERIES}
    unparsed: list[dict[str, str]] = []
    for link, article in articles:
        title = article.title or link.title
        series = _nbs_series(title) or _nbs_series(link.title)
        body = article.body_text or article.all_text
        period = _period_from_title(title) or _period_from_title(link.title)
        annual_december = False
        if series is not None and period is None:
            period = _nbs_annual_december_period(series, title, body)
            annual_december = period is not None
        if series is None or period is None:
            continue
        _, _, observation_date, observation_period = period
        if series == "retail_sales" and "/" in observation_period:
            # NBS 1—N months and the Jan-Feb joint release are cumulative periods.
            # Keep them distinct from explicit single-month YoY observations.
            series = "retail_sales_ytd"
        value = (
            _nbs_annual_retail_december_value(body)
            if annual_december and series == "retail_sales"
            else _nbs_value(series, body, title)
        )
        if value is None:
            unparsed.append({"series": series, "title": title, "url": link.url})
        else:
            rows[series].append(
                _snapshot_row(
                    observation_date,
                    observation_period,
                    article.publication_date,
                    link.url,
                    value,
                    link.url,
                    source_title=title,
                )
            )
        if series == "cpi":
            core_value = _nbs_core_cpi_value(body)
            if core_value is not None:
                rows["core_cpi"].append(
                    _snapshot_row(
                        observation_date,
                        observation_period,
                        article.publication_date,
                        link.url,
                        core_value,
                        link.url,
                        source_title=title,
                    )
                )

    output = Path(output_dir)
    downloaded_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    per_series: dict[str, dict[str, object]] = {}
    for name, spec in NBS_SERIES.items():
        notes = (
            "Values are stored as reported in the linked NBS release and are not transformed again. "
            "The indexed release archive currently begins around 2021-09; earlier releases may exist "
            "at direct URLs and have not been systematically discovered."
        )
        if name in {
            "industrial_production",
            "retail_sales",
            "retail_sales_ytd",
            "fixed_asset_investment",
        }:
            notes += " The combined January-February release is assigned to February."
        if name == "fixed_asset_investment":
            notes += (
                " This is cumulative year-to-date YoY, not monthly growth. December records use "
                "the official full-year cumulative rate."
            )
        if name == "industrial_production":
            notes += " December is captured only from a monthly December release if available."
        if name == "retail_sales":
            notes += (
                " December uses an explicit December YoY from the annual or monthly release; "
                "the annual headline rate is not substituted."
            )
        if name == "retail_sales_ytd":
            notes += " This is cumulative year-to-date YoY, not monthly growth."
        _save_snapshot(
            output,
            spec,
            rows[name],
            owner=NBS_NAME,
            portal=NBS_LIST_URL,
            downloaded_at=downloaded_at,
            notes=notes,
        )
        per_series[spec["series_key"]] = _coverage(rows[name])
    return {
        "source": "NBS",
        "listing_url": NBS_LIST_URL,
        "requested_start_year": start_year,
        "requested_end_year": end_year,
        "listing_pages_scanned": page_count,
        "candidate_articles": len(links),
        "series": per_series,
        "unparsed_candidates": unparsed,
        "fetch_errors": fetch_errors,
        "downloaded_at": downloaded_at,
    }


def _pboc_period(title: str) -> tuple[int, int, str, str] | None:
    period = _period_from_title(title)
    if period is None and re.search(r"20\d{2}年社会融资规模存量统计数据报告", title):
        year_match = re.search(r"(?<!\d)(20\d{2})年", title)
        if year_match:
            year = int(year_match.group(1))
            return year, 12, _month_end(year, 12), f"{year:04d}-12"
    if period is None:
        return None
    year, month, observation_date, observation_period = period
    return year, month, observation_date, observation_period


def _money_value(text: str, number: int) -> float | None:
    token = re.search(
        rf"M\s*[_{{（(]?\s*{number}\s*[\}}）)]?",
        text,
        flags=re.IGNORECASE,
    )
    if not token:
        return None
    tail = text[token.end() : token.end() + 420]
    value_match = re.search(
        r"(?:同比\s*)?(增长|上升|增加|下降|减少|回落)\s*([+-]?[\d,]+(?:\.\d+)?)\s*%",
        tail,
    )
    if not value_match:
        return None
    movement, raw_value = value_match.groups()
    value = float(raw_value.replace(",", ""))
    if movement in {"下降", "减少", "回落"}:
        return -abs(value)
    return value


def _tsf_stock_value(text: str) -> float | None:
    phrase = re.search(r"社会融资规模存量", text)
    if not phrase:
        return None
    return _signed_percent(text[phrase.end() : phrase.end() + 300])


def _pboc_pdf_fallback(
    article: Article,
    release_url: str,
    fetch_bytes: Callable[[str], bytes],
) -> tuple[dict[str, float], dict[str, str], list[dict[str, str]]]:
    values: dict[str, float] = {}
    value_urls: dict[str, str] = {}
    errors: list[dict[str, str]] = []
    for link in article.links:
        if not link.url.lower().split("?", 1)[0].endswith(".pdf"):
            continue
        pdf_url = urljoin(release_url, link.url)
        try:
            text = _extract_pdf_text(fetch_bytes(pdf_url))
        except RuntimeError as exc:
            errors.append({"url": pdf_url, "error": str(exc)})
            continue
        for name, number in (("m1", 1), ("m2", 2)):
            value = _money_value(text, number)
            if name not in values and value is not None:
                values[name] = value
                value_urls[name] = pdf_url
        if len(values) == 2:
            break
    return values, value_urls, errors


def _crawl_pboc(
    fetch_text: Callable[[str], str],
    start_year: int,
    end_year: int,
    max_workers: int,
) -> tuple[int, list[Link], list[dict[str, str]]]:
    first = fetch_text(PBOC_LIST_URL)
    parsed = _parse_page(first)
    page_count = parsed.page_count
    if page_count is None:
        match = re.search(r"totalpage\s*[=:]\s*['\"]?(\d+)", first, flags=re.IGNORECASE)
        page_count = int(match.group(1)) if match else None
    if page_count is None:
        raise ValueError("Could not read pagination on the official PBOC releases archive")
    base = PBOC_LIST_URL.rsplit("/", 1)[0] + "/"
    urls = [urljoin(base, f"11871-{i}.html") for i in range(2, page_count + 1)]
    pages = [first]
    listing_errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_text, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                pages.append(future.result())
            except Exception as exc:  # noqa: BLE001 - report incomplete archive pages
                listing_errors.append({"url": url, "error": str(exc)})
    candidates: dict[str, Link] = {}
    for page in pages:
        for link in _unique_links(
            _parse_page(page).links, PBOC_LIST_URL, "/diaochatongjisi/116219/116225/"
        ):
            title = link.title
            is_money = "金融统计数据报告" in title
            is_tsf = "社会融资规模存量统计数据报告" in title
            if not (is_money or is_tsf):
                continue
            period = _pboc_period(title)
            if period and start_year <= period[0] <= end_year:
                candidates[link.url] = link
    return page_count, list(candidates.values()), listing_errors


def collect_pboc_snapshots(
    output_dir: str | Path,
    *,
    start_year: int = 2005,
    end_year: int | None = None,
    fetch_text: Callable[[str], str] = _fetch_text,
    fetch_bytes: Callable[[str], bytes] = _fetch_bytes,
    max_workers: int = 2,
) -> dict[str, object]:
    """Collect PBOC M1/M2 and TSF stock YoY from monthly/periodic releases."""

    end_year = end_year or datetime.now(ZoneInfo("Asia/Shanghai")).year
    if start_year > end_year:
        raise ValueError("start_year must be <= end_year")
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    page_count, links, listing_errors = _crawl_pboc(fetch_text, start_year, end_year, max_workers)
    articles, fetch_errors = _collect_articles(links, fetch_text, max_workers)
    fetch_errors = [*listing_errors, *fetch_errors]
    rows: dict[str, list[dict[str, object]]] = {name: [] for name in PBOC_SERIES}
    unparsed: list[dict[str, str]] = []
    pdf_attachments: list[dict[str, str]] = []
    for link, article in articles:
        title = article.title or link.title
        period = _pboc_period(title) or _pboc_period(link.title)
        if period is None:
            continue
        _, _, observation_date, observation_period = period
        body = article.body_text or article.all_text
        is_tsf = "社会融资规模存量统计数据报告" in title
        is_money = "金融统计数据报告" in title and not is_tsf
        release_url = link.url
        values: dict[str, float | None] = {}
        source_urls: dict[str, str] = {}
        if is_money:
            values["m1"] = _money_value(body, 1)
            values["m2"] = _money_value(body, 2)
            source_urls.update({"m1": release_url, "m2": release_url})
            if values["m1"] is None or values["m2"] is None:
                pdf_values, pdf_urls, pdf_errors = _pboc_pdf_fallback(
                    article, release_url, fetch_bytes
                )
                fetch_errors.extend(pdf_errors)
                for name, value in pdf_values.items():
                    if values.get(name) is None:
                        values[name] = value
                        source_urls[name] = pdf_urls[name]
                if pdf_urls:
                    pdf_attachments.extend(
                        {"title": title, "url": url} for url in sorted(set(pdf_urls.values()))
                    )
            for name in ("m1", "m2"):
                if values.get(name) is None:
                    unparsed.append({"series": name, "title": title, "url": release_url})
        if is_tsf:
            values["tsf_stock"] = _tsf_stock_value(body)
            source_urls["tsf_stock"] = release_url
            if values["tsf_stock"] is None:
                unparsed.append({"series": "tsf_stock", "title": title, "url": release_url})
        for name, value in values.items():
            if value is None:
                continue
            rows[name].append(
                _snapshot_row(
                    observation_date,
                    observation_period,
                    article.publication_date,
                    release_url,
                    value,
                    source_urls.get(name, release_url),
                    source_title=title,
                )
            )

    output = Path(output_dir)
    downloaded_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    per_series: dict[str, dict[str, object]] = {}
    for name, spec in PBOC_SERIES.items():
        note = (
            "Values and publication dates are from the linked original PBOC report. "
            "The indexed archive begins around 2009; earlier reports are not filled."
        )
        if name == "m1":
            note += " M1 definition changes are retained as originally reported; historical breaks are not backfilled."
        if name == "tsf_stock":
            note += (
                " TSF scope/methodology changes are retained as originally reported. "
                "Year-end observations from annual stock reports are included when their YoY "
                "rate is explicit; no monthly value is interpolated."
            )
        _save_snapshot(
            output,
            spec,
            rows[name],
            owner=PBOC_NAME,
            portal=PBOC_LIST_URL,
            downloaded_at=downloaded_at,
            notes=note,
        )
        per_series[spec["series_key"]] = _coverage(rows[name])
    return {
        "source": "PBOC",
        "listing_url": PBOC_LIST_URL,
        "requested_start_year": start_year,
        "requested_end_year": end_year,
        "listing_pages_scanned": page_count,
        "candidate_articles": len(links),
        "series": per_series,
        "unparsed_candidates": unparsed,
        "pdf_attachments_used": pdf_attachments,
        "fetch_errors": fetch_errors,
        "downloaded_at": downloaded_at,
    }


def collect_china_snapshots(
    output_dir: str | Path,
    *,
    start_year: int = 2005,
    end_year: int | None = None,
    fetch_text: Callable[[str], str] = _fetch_text,
    fetch_bytes: Callable[[str], bytes] = _fetch_bytes,
    max_workers: int = 2,
) -> dict[str, object]:
    """Collect official China NBS and PBOC snapshots with article-level provenance."""

    end_year = end_year or datetime.now(ZoneInfo("Asia/Shanghai")).year
    nbs = collect_nbs_snapshots(
        output_dir,
        start_year=start_year,
        end_year=end_year,
        fetch_text=fetch_text,
        max_workers=max_workers,
    )
    pboc = collect_pboc_snapshots(
        output_dir,
        start_year=start_year,
        end_year=end_year,
        fetch_text=fetch_text,
        fetch_bytes=fetch_bytes,
        max_workers=max_workers,
    )
    audit = {
        "requested_start_year": start_year,
        "requested_end_year": end_year,
        "harvested_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "release_date_policy": (
            "Only dates on the original official release page (metadata or visible publication date) "
            "are accepted. URL timestamps and schedules are never used as actual publication dates."
        ),
        "sources": {"nbs": nbs, "pboc": pboc},
        "not_collected": [
            "NBS core CPI commentary series.",
            "China government-bond yields from China Money / ChinaBond.",
            "Customs trade and SAFE external-balance series.",
            "NBS releases before the current indexed archive floor require direct-link discovery.",
        ],
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "china_harvest_audit.yaml").write_text(
        yaml.safe_dump(audit, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return audit
