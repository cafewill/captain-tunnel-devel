"""Windows onefile 빌드. 런타임 설정과 이전 산출물을 덮어쓰지 않는다."""

from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "win32":
        raise SystemExit("Windows에서 빌드하십시오.")
    subprocess.run([sys.executable, str(ROOT / "docs/tools/build_docs.py"), "--check"], check=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = ROOT / "dist" / stamp
    # 동기화 폴더 안에서 분석·압축하지 않는다. 성공·실패 모두 임시 작업 폴더만 정리한다.
    with tempfile.TemporaryDirectory(prefix="captain_tunnel_build_") as temp:
        stage = Path(temp)
        shutil.copytree(ROOT / "src" / "captain_tunnel", stage / "captain_tunnel",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(ROOT / "tools" / "entrypoint.py", stage / "entrypoint.py")
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--clean",
            "--noconfirm", "--name", "CaptainTunnel", "--paths", str(stage),
            "--icon", str(stage / "captain_tunnel/assets/captain_tunnel.ico"),
            "--add-data", f"{stage / 'captain_tunnel/assets'};captain_tunnel/assets",
            "--hidden-import", "win32timezone", "--hidden-import", "servicemanager",
            "--hidden-import", "win32service", "--hidden-import", "win32serviceutil",
            "--hidden-import", "captain_tunnel.icons_data",
            "entrypoint.py",
        ], cwd=stage, check=True)
        executable = stage / "dist" / "CaptainTunnel.exe"
        if not executable.is_file():
            raise RuntimeError("실행 파일이 생성되지 않았습니다.")
        output.mkdir(parents=True)
        shutil.copy2(executable, output / executable.name)
        shutil.copytree(ROOT / "docs", output / "docs")
        shutil.copy2(ROOT / "README.md", output / "README.md")
        shutil.copy2(ROOT / "config/captain_tunnel.example.json", output / "captain_tunnel.example.json")
    print(f"빌드 완료: {output / 'CaptainTunnel.exe'}")
    print("설정·비밀번호·known_hosts·로그는 포함하지 않았습니다.")


if __name__ == "__main__":
    main()
