from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import StrategySpec

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class StrategyRecord:
    strategy_id: str
    version: int
    created_at: str
    updated_at: str
    spec: StrategySpec

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "strategy_id": self.strategy_id,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "strategy": self.spec.to_dict(),
        }


class StrategyStore:
    """Small atomic JSON store; point it at a persistent volume in deployments."""

    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or os.environ.get("MRQ_STRATEGIES_DIR")
        if configured is None:
            state_root = Path(os.environ.get("MRQ_STATE_DIR", "~/.macro-regime-quant")).expanduser()
            configured = state_root / "strategies"
        self.root = Path(configured).expanduser()

    def save(
        self,
        spec: StrategySpec,
        strategy_id: str | None = None,
        *,
        replace: bool = False,
    ) -> StrategyRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        normalized_id = _safe_id(strategy_id or _slug(spec.name))
        destination = self.root / f"{normalized_id}.json"
        now = datetime.now(UTC).isoformat()
        previous: dict[str, object] | None = None
        if destination.exists():
            if not replace:
                raise FileExistsError(f"Strategy {normalized_id!r} already exists")
            previous = json.loads(destination.read_text(encoding="utf-8"))
        record = StrategyRecord(
            strategy_id=normalized_id,
            version=int(previous.get("version", 0)) + 1 if previous else 1,
            created_at=str(previous.get("created_at", now)) if previous else now,
            updated_at=now,
            spec=spec,
        )
        # Preserve a legacy latest-only record before moving its pointer. The
        # immutable archive then keeps every strategy spec used by past runs.
        if previous is not None:
            prior_record = _record_from_dict(normalized_id, previous)
            prior_path = self._version_path(normalized_id, prior_record.version)
            if not prior_path.exists():
                self._write_atomic(prior_path, prior_record.to_dict())
        version_path = self._version_path(normalized_id, record.version)
        if version_path.exists():
            raise FileExistsError(f"Strategy version {normalized_id!r} v{record.version} already exists")
        if not replace and destination.exists():
            raise FileExistsError(f"Strategy {normalized_id!r} already exists")
        self._write_atomic(destination, record.to_dict())
        # The latest record is itself sufficient as a fallback version source;
        # archival write follows so an interrupted save cannot leave an orphan
        # future version that blocks the next update.
        self._write_atomic(version_path, record.to_dict())
        return record

    def get(self, strategy_id: str) -> StrategyRecord:
        normalized_id = _safe_id(strategy_id)
        path = self.root / f"{normalized_id}.json"
        if not path.is_file():
            raise KeyError(f"Strategy {normalized_id!r} was not found")
        return _record_from_dict(normalized_id, json.loads(path.read_text(encoding="utf-8")))

    def get_version(self, strategy_id: str, version: int) -> StrategyRecord:
        normalized_id = _safe_id(strategy_id)
        if not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        path = self._version_path(normalized_id, version)
        if path.is_file():
            return _record_from_dict(normalized_id, json.loads(path.read_text(encoding="utf-8")))
        # Read pre-archive stores when the requested version is their current
        # record. Older versions cannot be reconstructed from latest-only data.
        latest = self.root / f"{normalized_id}.json"
        if latest.is_file():
            record = self.get(normalized_id)
            if record.version == version:
                return record
        raise KeyError(f"Strategy {normalized_id!r} version {version} was not found")

    def list_versions(self, strategy_id: str) -> list[StrategyRecord]:
        normalized_id = _safe_id(strategy_id)
        directory = self.root / normalized_id
        latest_path = self.root / f"{normalized_id}.json"
        if not directory.is_dir() and not latest_path.is_file():
            raise KeyError(f"Strategy {normalized_id!r} was not found")
        records = [
            _record_from_dict(normalized_id, json.loads(path.read_text(encoding="utf-8")))
            for path in directory.glob("v*.json")
            if path.is_file()
        ] if directory.exists() else []
        if latest_path.is_file():
            latest = self.get(normalized_id)
            if all(row.version != latest.version for row in records):
                records.append(latest)
        return sorted(records, key=lambda row: row.version)

    def list(self) -> list[StrategyRecord]:
        if not self.root.exists():
            return []
        return [self.get(path.stem) for path in sorted(self.root.glob("*.json"))]

    def _version_path(self, strategy_id: str, version: int) -> Path:
        return self.root / strategy_id / f"v{version}.json"

    @staticmethod
    def _write_atomic(path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        handle, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as temp_file:
                temp_file.write(encoded)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def _safe_id(value: str) -> str:
    normalized = value.strip().lower()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError("strategy_id may only contain lowercase letters, numbers, '-' and '_'")
    return normalized


def _record_from_dict(strategy_id: str, raw: dict[str, object]) -> StrategyRecord:
    if raw.get("schema_version", 1) != 1:
        raise ValueError(f"Unsupported strategy schema version for {strategy_id}")
    return StrategyRecord(
        strategy_id=strategy_id,
        version=int(raw["version"]),
        created_at=str(raw["created_at"]),
        updated_at=str(raw["updated_at"]),
        spec=StrategySpec.from_dict(dict(raw["strategy"])),
    )


def _slug(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:48]
    return slug or f"strategy-{uuid.uuid4().hex[:10]}"
