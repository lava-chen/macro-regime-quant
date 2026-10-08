"""Data source adapters. Every provider returns a normalized observation frame."""

from .alfred import AlfredProvider, latest_vintage_date
from .base import DataProvider
from .csv import CsvProvider
from .fred import FredProvider
from .sec import SecProvider, SecRateLimited
from .yahoo import YahooProvider

__all__ = [
    "AlfredProvider",
    "CsvProvider",
    "DataProvider",
    "FredProvider",
    "SecProvider",
    "SecRateLimited",
    "YahooProvider",
    "latest_vintage_date",
]
