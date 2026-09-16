"""Fluent 瞬态 CGNS 工具。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cgns_tool import main

if __name__ == "__main__":
    raise SystemExit(main())
