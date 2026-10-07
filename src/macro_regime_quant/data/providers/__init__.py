"""Data source adapters. Every provider returns a normalized observation frame."""

from .base import DataProvider
from .csv import CsvProvider
from .fred import FredProvider
from .yahoo import YahooProvider

__all__ = ["CsvProvider", "DataProvider", "FredProvider", "YahooProvider"]
