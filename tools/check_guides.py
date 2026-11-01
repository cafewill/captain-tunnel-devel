"""Chromium에서 생성 가이드의 DOM·설정·탭을 검사한다. Python 표준 라이브러리만 사용."""

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class Results(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reading = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "pre" and dict(attrs).get("id") == "guide-smoke-results":
            self.reading = True

    def handle_endtag(self, tag):
        if tag == "pre":
            self.reading = False

    def handle_data(self, data):
        if self.reading:
            self.parts.append(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", required=True, type=Path, help="Chrome 또는 Edge 실행 파일")
    parser.add_argument("--width", type=int, default=1440, help="모바일 검사는 390")
    args = parser.parse_args()
    script = (ROOT / "tests/guide_browser_smoke.js").read_text(encoding="utf-8")
    page = (ROOT / "docs/user-guide.html").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="captain_guide_test_") as directory:
        temp = Path(directory)
        target = temp / "guide.html"
        target.write_text(page.replace("</body>", f"<script>{script}</script></body>"), encoding="utf-8")
        try:
            result = subprocess.run([
                str(args.browser), "--headless", "--disable-gpu", "--no-first-run",
                f"--user-data-dir={temp / 'profile'}", f"--window-size={args.width},1000",
                "--dump-dom", target.as_uri(),
            ], capture_output=True, encoding="utf-8", errors="replace", timeout=35)
        except subprocess.TimeoutExpired:
            print("브라우저 실행 시간 초과 — 검증하지 못했습니다.")
            return 2
        output = Results()
        output.feed(result.stdout)
        if not output.parts:
            print("브라우저 검사 결과가 없습니다 — 검증하지 못했습니다.")
            print(result.stderr[-1200:])
            return 2
        checks = json.loads("".join(output.parts))
        for check in checks:
            print(f"{'PASS' if check['ok'] else 'FAIL'} {check['name']}")
        return int(any(not check["ok"] for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
