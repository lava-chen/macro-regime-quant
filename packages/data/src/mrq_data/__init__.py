"""Data access: catalog, snapshots, providers, availability.

Owns everything about *getting and landing* an observation. It does not decide
how often a model should look at it — frequency alignment is a modelling
choice, not a property of the data.
"""

from .catalog import CatalogError, check_catalog_consistency, load_catalog
from .registry import default_provider_registry
from .snapshots import SnapshotValidation, metadata_path, validate_snapshot

__all__ = [
    "CatalogError",
    "SnapshotValidation",
    "check_catalog_consistency",
    "default_provider_registry",
    "load_catalog",
    "metadata_path",
    "validate_snapshot",
]
