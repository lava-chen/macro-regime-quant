"""Data source adapters. Every provider returns a normalized observation frame."""

from .alfred import AlfredProvider, latest_vintage_date
from .base import DataProvider
from .csv import CsvProvider
from .fred import FredProvider
from .yahoo import YahooProvider

__all__ = [
    "AlfredProvider",
    "CsvProvider",
    "DataProvider",
    "FredProvider",
    "YahooProvider",
    "latest_vintage_date",
]
