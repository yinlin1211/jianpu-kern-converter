#!/usr/bin/env python3
"""CLI entry point for the MiniUnSongData/Kern adapter."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jianpu_kern.mini_unisongdata import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
