#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in tools.lib.ros."""
import sys
from pathlib import Path
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "src"))
from tools.lib.ros import *
if __name__ == "__main__":
    raise SystemExit(sync() or 0)
