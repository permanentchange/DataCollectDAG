#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in tools.lib.calibration."""
import sys
from pathlib import Path
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "src"))
from tools.lib.calibration import *
if __name__ == "__main__":
    raise SystemExit(main() or 0)
