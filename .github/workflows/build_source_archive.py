#!/usr/bin/env python3
"""Build release/TeamHR-Windows-Source.zip from the repository.

This is the same clean-source archive that was produced manually on the dev VM
for the final handoff. It is safe to run from a clone on any OS: it only packs
source-controlled runtime assets (no venv, no .git, no candidate data, no
secrets).

Usage:
    python .github/workflows/build_source_archive.py [--out release/TeamHR-Windows-Source.zip]

The archive is intentionally flat-named "TeamHR-Windows-Source/*" so it
extracts to a folder Windows operators can use directly.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Top-level entries to pack.
INCLUDE_TOP = [
    "app",
    "data",
    "docs",
    "scripts",
    "tests",
    ".github",
    "requirements.txt",
    "requirements-dev.txt",
    "README.md",
    "CHANGELOG.md",
    ".env.example",
    ".gitignore",
]

# Paths (relative to repo root) that must never be packed.
EXCLUDE_ANY = {
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    ".pytest_cache",
    "build",
    "dist",
    "release",
    "node_modules",
}

# Excluded relative path prefixes (runtime candidate data / tools).
EXCLUDE_PREFIX = (
    "data/database",
    "data/generated",
    "data/backups",
    "data/uploads",
    "data/portal",
    "data/logs",
    "data/validation",
    "data/ocr_benchmark/input",
    "data/ocr_benchmark/output",
)

# Explicit data files that ARE runtime assets (must be included).
DATA_KEEP = {
    "data/config.json",
    "data/masters",
    "data/templates",
    "data/ocr_benchmark/ground_truth.csv",
}

SUFFIXES = (".pyc", ".pyo", ".log", ".pid", ".deps-installed")


def _excluded(rel: str) -> bool:
    parts = set(rel.split("/"))
    if parts & EXCLUDE_ANY:
        return True
    if rel.endswith(SUFFIXES):
        return True
    if rel.startswith(EXCLUDE_PREFIX):
        return True
    return rel == ".env"


def _data_entry(rel: str) -> bool:
    """Allow-list for data/ beyond the mandatory runtime assets."""
    if rel == "data":
        return True
    if rel.startswith(tuple(DATA_KEEP)):
        return True
    return False


def build(out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for top in INCLUDE_TOP:
            src = ROOT / top
            if src.is_file():
                # Top-level file (e.g. requirements.txt)
                rel = top
                if _excluded(rel):
                    continue
                zf.write(src, f"TeamHR-Windows-Source/{rel}")
                count += 1
                continue
            for path in sorted(src.rglob("*")):
                rel = path.relative_to(ROOT).as_posix()
                if _excluded(rel):
                    continue
                if top == "data" and not _data_entry(rel):
                    continue
                if path.is_dir():
                    arc = f"TeamHR-Windows-Source/{rel}/"
                    if arc not in zf.namelist():
                        zf.writestr(arc, "")
                else:
                    zf.write(path, f"TeamHR-Windows-Source/{rel}")
                    count += 1
    print(f"Built {out} ({count} files)")
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="release/TeamHR-Windows-Source.zip")
    args = ap.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    build(out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())