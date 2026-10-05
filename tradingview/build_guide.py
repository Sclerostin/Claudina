"""Build AMD_Kit.html: guide_template.html with the Pine scripts embedded.

The page's Copy buttons copy the text of each code block, so the published page always carries
exactly what is in pine/.   Run: python3 build_guide.py
"""
import html
import re
from pathlib import Path

ROOT = Path(__file__).parent
SCRIPTS = {"AMD_DAY_TRADER": "AMD_Day_Trader.pine", "AMD_RADAR": "AMD_Radar.pine", "DT_VOLUME": "DT_Volume.pine"}

TOKEN = re.compile(
    r'(?P<c>//.*$)'
    r'|(?P<s>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')'
    r'|(?P<n>#[0-9a-fA-F]{6,8}\b|\b\d+(?:\.\d+)?\b)'
    r'|(?P<k>\b(?:if|else|for|in|var|switch|and|or|not|true|false|na|float|int|bool|string|color|array|table)\b)'
)


def highlight(line):
    out, pos = [], 0
    for m in TOKEN.finditer(line):
        out.append(html.escape(line[pos:m.start()], quote=False))
        out.append(f'<span class="tk-{m.lastgroup}">{html.escape(m.group(), quote=False)}</span>')
        pos = m.end()
    out.append(html.escape(line[pos:], quote=False))
    return "".join(out)


def main():
    page = (ROOT / "guide_template.html").read_text()
    for key, name in SCRIPTS.items():
        code = (ROOT / "pine" / name).read_text().rstrip("\n")
        assert code.isascii(), f"{name} has non-ASCII characters"
        lines = code.split("\n")
        page = page.replace(f"%%{key}_LINES%%", str(len(lines)))
        page = page.replace(f"%%{key}%%", "\n".join(highlight(x) for x in lines))
    assert "%%" not in page, "unfilled placeholder"
    (ROOT / "AMD_Kit.html").write_text(page)
    print("wrote AMD_Kit.html")


if __name__ == "__main__":
    main()
