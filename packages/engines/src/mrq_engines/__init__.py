"""Research engines: macro, valuation, flow and the cross-engine pipeline.

Engines consume the data layer. They never call a provider directly: whatever
reaches an engine has already passed the observation-frame contract.
"""

from .pipeline import build_country_factors, load_monthly_panel

__all__ = ["build_country_factors", "load_monthly_panel"]
