"""실행 파일과 개발 소스의 위치를 명확히 분리한다."""

import os
import sys
from pathlib import Path


def source_launcher() -> str:
    checkout = Path(__file__).resolve().parents[2] / "run.py"
    if checkout.is_file():
        return str(checkout)
    return str(Path(__file__).resolve().parent / "__main__.py")


def executable_dir() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    return str(Path(source_launcher()).parent)


def data_dir() -> str:
    # 서비스와 화면은 같은 절대 경로를 사용해야 한다.
    if getattr(sys, "frozen", False):
        return executable_dir()
    if Path(source_launcher()).name == "run.py":
        return str(Path(executable_dir()) / "data")
    return str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Captain-Tunnel")


def icon_path() -> str:
    return str(Path(__file__).resolve().parent / "assets" / "captain_tunnel.ico")
