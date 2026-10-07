from __future__ import annotations

import numpy as np
import pandas as pd


def equity_curve(returns: pd.Series) -> pd.Series:
    return (1.0 + returns.fillna(0.0)).cumprod()


def cagr(returns: pd.Series, periods_per_year: int = 252) -> float:
    r = returns.dropna()
    if r.empty:
        return float("nan")
    years = len(r) / periods_per_year
    return float((1.0 + r).prod() ** (1.0 / years) - 1.0)


def annualized_vol(returns: pd.Series, periods_per_year: int = 252) -> float:
    return float(returns.dropna().std(ddof=1) * np.sqrt(periods_per_year))


def sharpe(returns: pd.Series, periods_per_year: int = 252, rf: float = 0.0) -> float:
    r = returns.dropna() - rf / periods_per_year
    vol = r.std(ddof=1)
    if vol == 0 or np.isnan(vol):
        return float("nan")
    return float(r.mean() / vol * np.sqrt(periods_per_year))


def max_drawdown(returns: pd.Series) -> float:
    eq = equity_curve(returns)
    dd = eq / eq.cummax() - 1.0
    return float(dd.min())


def summary(returns: pd.Series, periods_per_year: int = 252) -> dict[str, float]:
    g = cagr(returns, periods_per_year)
    mdd = max_drawdown(returns)
    return {
        "cagr": g,
        "vol": annualized_vol(returns, periods_per_year),
        "sharpe": sharpe(returns, periods_per_year),
        "max_drawdown": mdd,
        "calmar": float(g / abs(mdd)) if mdd < 0 else float("nan"),
    }
