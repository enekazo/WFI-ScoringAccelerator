"""Build a printable HTML of an article draft with images embedded.

Usage: python scripts/build_article_html.py docs/dissemination/techcommunity-post-v9.md out.html
"""
import base64
from html import escape
import pathlib
import re
import sys

import markdown

src_path = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
html = markdown.markdown(src_path.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])


def embed(m):
    p = src_path.parent / m.group(1)
    if not p.exists():
        raise FileNotFoundError(f"Article image not found: {p}")
    return 'src="data:image/png;base64,' + base64.b64encode(p.read_bytes()).decode() + '"'


html = re.sub(r'src="(figures/[^"]+)"', embed, html)
theme = """<script>
(() => {
  const param = new URLSearchParams(window.location.search).get("clawpilotTheme");
  const theme =
    param || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
})();
</script>"""
css = """<style>
:root {
  color-scheme: light;
  --cp-bg: #f7f4ef;
  --cp-bg-elevated: #fcfbf8;
  --cp-surface: #ffffff;
  --cp-surface-soft: #f5f5f5;
  --cp-border: #dedede;
  --cp-border-strong: #919191;
  --cp-text: #242424;
  --cp-text-muted: #5c5c5c;
  --cp-text-soft: #6f6f6f;
  --cp-accent: #b11f4b;
  --cp-accent-hover: #9a1a41;
  --cp-accent-soft: rgba(177, 31, 75, 0.08);
  --cp-accent-fg: #ffffff;
  --cp-success: #16a34a;
  --cp-danger: #dc2626;
  --cp-warning: #f59e0b;
  --cp-link: #0078d4;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.12);
  --cp-overlay: rgba(255, 255, 255, 0.8);
  --cp-panel: rgba(255, 255, 255, 0.86);
  --cp-panel-strong: rgba(255, 255, 255, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.55);
  --cp-highlight: rgba(177, 31, 75, 0.12);
}
html[data-theme="dark"] {
  color-scheme: dark;
  --cp-bg: #3d3b3a;
  --cp-bg-elevated: #343231;
  --cp-surface: #292929;
  --cp-surface-soft: #2e2e2e;
  --cp-border: #474747;
  --cp-border-strong: #5f5f5f;
  --cp-text: #dedede;
  --cp-text-muted: #919191;
  --cp-text-soft: #b0b0b0;
  --cp-accent: #fd8ea1;
  --cp-accent-hover: #fb7b91;
  --cp-accent-soft: rgba(253, 142, 161, 0.14);
  --cp-accent-fg: #1a1a1a;
  --cp-success: #4ade80;
  --cp-danger: #f87171;
  --cp-warning: #fbbf24;
  --cp-link: #4da6ff;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
  --cp-overlay: rgba(41, 41, 41, 0.88);
  --cp-panel: rgba(41, 41, 41, 0.72);
  --cp-panel-strong: rgba(41, 41, 41, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.04);
  --cp-highlight: rgba(253, 142, 161, 0.12);
}
body {
  font-family: "Segoe UI", Aptos, Calibri, -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--cp-bg);
  color: var(--cp-text);
  line-height: 1.55;
  margin: 0;
}
main { max-width: 900px; margin: 32px auto; padding: 32px; background: var(--cp-surface); }
a { color: var(--cp-link); overflow-wrap: anywhere; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.9em; }
td, th { border: 1px solid var(--cp-border-strong); padding: 8px; overflow-wrap: anywhere; }
th { background: var(--cp-surface-soft); text-align: left; }
code, pre { font-family: Consolas, "Courier New", Courier, monospace; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
blockquote { border-left: 3px solid var(--cp-border); margin-left: 0; padding-left: 16px; }
h1, h2, h3 { line-height: 1.2; }
.print-controls { max-width: 900px; margin: 16px auto; padding: 0 32px; }
button {
  font: inherit; padding: 8px 16px; border: 1px solid var(--cp-accent);
  border-radius: 0.625rem; background: var(--cp-accent); color: var(--cp-accent-fg);
  cursor: pointer;
}
button:hover { background: var(--cp-accent-hover); }
@page { size: A4; margin: 15mm; }
@media print {
  :root, html[data-theme="dark"] {
    color-scheme: light;
    --cp-bg: #ffffff;
    --cp-surface: #ffffff;
    --cp-text: #242424;
    --cp-link: #242424;
    --cp-border: #dedede;
    --cp-border-strong: #919191;
    --cp-surface-soft: #f5f5f5;
  }
  body { font-size: 10.5pt; }
  main { max-width: none; margin: 0; padding: 0; }
  .print-controls { display: none; }
  h1, h2, h3 { break-after: avoid; }
  p, li { orphans: 3; widows: 3; }
  img, tr, p:has(img) { break-inside: avoid; }
  p:has(img) { break-after: avoid; }
  img { max-height: 235mm; object-fit: contain; }
  table { font-size: 9pt; }
  thead { display: table-header-group; }
  a { text-decoration: none; }
}
</style>"""
title_match = re.search(r"^# (.+)$", src_path.read_text(encoding="utf-8"), re.MULTILINE)
title = escape(title_match.group(1) if title_match else src_path.stem)
out.write_text(
    '<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    f"<title>{title}</title>" + theme + css
    + '</head><body><nav class="print-controls" aria-label="Print controls">'
    '<button type="button" onclick="window.print()">Print / save as PDF</button>'
    "</nav><main>" + html + "</main></body></html>",
    encoding="utf-8",
)
print("wrote", out)
