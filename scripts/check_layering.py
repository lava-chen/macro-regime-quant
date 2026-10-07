#!/usr/bin/env python3
"""Fail the build if a package imports from a layer above it.

The dependency direction is the whole point of the workspace layout:

    core  <-  data  <-  engines  <-  research  <-  cli

Python does not enforce this on its own, so without this check the layering
erodes the first time someone reaches for a convenient import. The rule is
checked statically over the source text, which is enough to catch accidental
cross-layer imports and does not require importing anything.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"

#: Layer index — a package may only import from its own layer or lower ones.
LAYERS: dict[str, int] = {
    "mrq_core": 0,
    "mrq_data": 1,
    "mrq_engines": 2,
    "mrq_research": 3,
    "mrq_cli": 4,
}

VIOLATION = "import layering violation"

#: Directory under packages/ -> importable package name.
DIRECTORY_TO_PACKAGE = {
    "core": "mrq_core",
    "data": "mrq_data",
    "engines": "mrq_engines",
    "research": "mrq_research",
    "cli": "mrq_cli",
}


def package_of(path: Path) -> str | None:
    """Map packages/<name>/src/... to the importable package <name>."""

    try:
        relative = path.relative_to(PACKAGES)
    except ValueError:
        return None
    return DIRECTORY_TO_PACKAGE.get(relative.parts[0])


def imported_packages(tree: ast.AST, current: str) -> set[str]:
    """Collect every internal package this module depends on."""

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            root = node.module.split(".")[0]
            if root in LAYERS:
                found.add(root)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in LAYERS:
                    found.add(root)
    found.discard(current)
    return found


def main() -> int:
    violations: list[str] = []

    for path in sorted(PACKAGES.rglob("*.py")):
        current = package_of(path)
        if current is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - surfaced by pytest first
            print(f"syntax error in {path}: {exc}", file=sys.stderr)
            return 1

        for target in sorted(imported_packages(tree, current)):
            if LAYERS[target] > LAYERS[current]:
                rel = path.relative_to(ROOT)
                violations.append(
                    f"{rel}:{path.read_text(encoding='utf-8').count(chr(10))} "
                    f"{VIOLATION}: {current} (layer {LAYERS[current]}) imports "
                    f"{target} (layer {LAYERS[target]})"
                )

    if violations:
        print(f"{len(violations)} {VIOLATION}(s):", file=sys.stderr)
        for line in violations:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nDependencies flow one way only:\n"
            "    core <- data <- engines <- research <- cli\n"
            "Move shared code down a layer, or invert the dependency.",
            file=sys.stderr,
        )
        return 1

    print("layering OK: " + " <- ".join(sorted(LAYERS, key=LAYERS.get)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
