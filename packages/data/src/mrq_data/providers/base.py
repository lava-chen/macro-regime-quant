from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd
from mrq_core.types import SeriesSpec


class DataProvider(ABC):
    @abstractmethod
    def fetch(self, spec: SeriesSpec, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """Return columns: observation_date, value.

        Providers should not silently forward-fill or revise history. Cleaning belongs
        in a separate transformation layer.
        """
        raise NotImplementedError
