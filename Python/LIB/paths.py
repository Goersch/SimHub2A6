"""Shared runtime directories outside the Python-only source tree."""

from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = PYTHON_ROOT.parent
INI_DIR = SHARED_ROOT / "INI"
LOG_DIR = SHARED_ROOT / "LOG"
SIMHUB_DATA_DIR = SHARED_ROOT / "SimHubData"
