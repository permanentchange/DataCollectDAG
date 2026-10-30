#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in tools.lib.frames."""
import sys
from pathlib import Path
_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "src"))
from tools.lib.frames import *
if __name__ == "__main__":
    raise SystemExit(main() or 0)
