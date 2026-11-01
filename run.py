"""설치 전 개발 실행: python run.py [인수]."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from captain_tunnel.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
