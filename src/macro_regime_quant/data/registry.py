from __future__ import annotations

from .providers.base import DataProvider
from .providers.csv import CsvProvider
from .providers.fred import FredProvider
from .providers.yahoo import YahooProvider


def default_provider_registry() -> dict[str, DataProvider]:
    """Construct the default provider registry.

    Providers are intentionally created lazily at pipeline start; no network request
    happens merely by importing the package.
    """

    return {
        "csv": CsvProvider(),
        "fred": FredProvider(),
        "yahoo": YahooProvider(),
    }
