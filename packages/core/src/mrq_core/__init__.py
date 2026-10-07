"""Shared primitives: observation contracts, PIT as-of logic, entity identity.

Zero business dependencies on purpose. Every layer above may import this package;
this package imports nothing from them. Keeping the PIT vocabulary here is what
stops a contract change from rippling upward into engine code.
"""

from .asof import FundamentalObservation, fundamentals_asof
from .contracts import (
    AVAILABILITY_BASES,
    EVIDENCED_BASES,
    FrameContractError,
    FrameContractReport,
    assert_frame_contract,
    check_frame_contract,
    normalize_observation_frame,
)
from .entities import Company, EntityKind, Security, SecurityType
from .types import CATALOG_FIELDS, SeriesSpec

__all__ = [
    "AVAILABILITY_BASES",
    "CATALOG_FIELDS",
    "EVIDENCED_BASES",
    "Company",
    "EntityKind",
    "FrameContractError",
    "FrameContractReport",
    "FundamentalObservation",
    "Security",
    "SecurityType",
    "SeriesSpec",
    "assert_frame_contract",
    "check_frame_contract",
    "fundamentals_asof",
    "normalize_observation_frame",
]
