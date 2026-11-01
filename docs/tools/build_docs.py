"""MD와 독립 실행 HTML을 같은 템플릿으로 생성·동기화 검사한다. 외부 의존성 없음."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
from pathlib import Path
import re

DOCS = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).with_name("guide-template.html")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render(source: Path) -> str:
    # 개행을 정규화하여 Windows와 다른 환경에서 같은 결과를 만든다.
    markdown = source.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    template = TEMPLATE.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    heading = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = heading.group(1) if heading else source.stem
    replacements = {
        "@@TITLE@@": html.escape(title),
        "@@SOURCE_HASH@@": digest(markdown.encode("utf-8")),
        "@@TEMPLATE_HASH@@": digest(template.encode("utf-8")),
        "@@SOURCE_TEXT@@": html.escape(markdown),
        "@@SOURCE_FILE@@": html.escape(source.name),
        "@@SOURCE_BASE64@@": base64.b64encode(markdown.encode("utf-8")).decode("ascii"),
    }
    # 한 번에 치환하므로 MD 안에 같은 표식이 있어도 다시 치환하지 않는다.
    return re.sub(r"@@[A-Z0-9_]+@@", lambda m: replacements.get(m.group(), m.group()), template)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="変更せずMD/HTMLの同期を検査")
    args = parser.parse_args()
    problems = []
    sources = sorted(DOCS.glob("*.md"))
    for source in sources:
        target = source.with_suffix(".html")
        expected = render(source)
        if args.check:
            actual = target.read_text(encoding="utf-8-sig") if target.exists() else ""
            if actual != expected:
                problems.append(target.name)
        else:
            target.write_text(expected, encoding="utf-8", newline="\n")
            print(f"{source.name} -> {target.name}")
    if problems:
        print("HTML 재생성이 필요합니다: " + ", ".join(problems))
        return 1
    print(f"{len(sources)} MD/HTML pairs {'checked' if args.check else 'generated'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
