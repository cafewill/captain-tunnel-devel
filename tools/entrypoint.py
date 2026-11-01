"""PyInstaller와 서비스 재실행에 공통으로 쓰는 진입점."""

from captain_tunnel.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
