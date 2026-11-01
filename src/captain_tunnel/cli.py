"""GUI를 만들기 전에 처리할 명령과 플랫폼 확인."""

import os
import sys

from captain_tunnel import __version__


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print(f"캡틴 터널링 {__version__}")
        return 0
    if args in (["--help"], ["-h"]):
        print("캡틴 터널링 — Windows SSH 터널 관리")
        print("실행: captain-tunnel [--autostart] [--minimized]")
        print("      captain-tunnel --headless")
        print("      captain-tunnel --service install|uninstall|start|stop")
        print("      captain-tunnel --version")
        return 0
    valid = {"--autostart", "--minimized"}
    service = len(args) == 2 and args[0] == "--service" and args[1] in {
        "install", "uninstall", "start", "stop"
    }
    if not (service or args in (["--headless"], ["--service-run"]) or all(a in valid for a in args)):
        print("지원하지 않는 인수입니다. --help를 확인하십시오.", file=sys.stderr)
        return 2
    if os.name != "nt":
        print("캡틴 터널링 GUI·서비스는 Windows에서 실행하십시오.", file=sys.stderr)
        return 2
    from captain_tunnel.application import main as run

    return run()
