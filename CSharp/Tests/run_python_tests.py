"""Run the retained regression suite independently of the checkout directory name."""

import sys
from pathlib import Path

import pytest

root = Path(__file__).resolve().parents[2]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
raise SystemExit(pytest.main([str(root / "Python" / "tests"), *sys.argv[1:]]))
