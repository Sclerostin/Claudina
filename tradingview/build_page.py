"""Embed the .pine files into page_template.html and write DT_Kit.html.

The page's Copy buttons copy the code block's text, so the published page always
carries exactly what is in pine/. Run: python3 build_page.py
"""
import html
import re
from pathlib import Path

ROOT = Path(__file__).parent
SCRIPTS = {"DT_CORE": "DT_Core.pine", "DT_MOMENTUM": "DT_Momentum.pine", "DT_VOLUME": "DT_Volume.pine"}

TOKEN = re.compile(
    r'(?P<c>//.*$)'
    r'|(?P<s>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')'
    r'|(?P<n>#[0-9a-fA-F]{6,8}\b|\b\d+(?:\.\d+)?\b)'
    r'|(?P<k>\b(?:if|else|for|in|var|switch|and|or|not|true|false|na|type|while|float|int|bool|string|color|array|map|label|table)\b)'
)
CLASS = {"c": "tk-c", "s": "tk-s", "n": "tk-n", "k": "tk-k"}


def highlight(line: str) -> str:
    out, pos = [], 0
    for m in TOKEN.finditer(line):
        out.append(html.escape(line[pos:m.start()], quote=False))
        out.append(f'<span class="{CLASS[m.lastgroup]}">{html.escape(m.group(), quote=False)}</span>')
        pos = m.end()
    out.append(html.escape(line[pos:], quote=False))
    return "".join(out)


def main() -> None:
    page = (ROOT / "page_template.html").read_text()
    for key, name in SCRIPTS.items():
        code = (ROOT / "pine" / name).read_text().rstrip("\n")
        lines = code.split("\n")
        page = page.replace(f"%%{key}_LINES%%", str(len(lines)))
        page = page.replace(f"%%{key}%%", "\n".join(highlight(l) for l in lines))
    assert "%%" not in page, "unfilled placeholder"
    (ROOT / "DT_Kit.html").write_text(page)
    print("wrote DT_Kit.html")


if __name__ == "__main__":
    main()
