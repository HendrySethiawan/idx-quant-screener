#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
from src.__main__ import main
if __name__ == "__main__":
    main()
