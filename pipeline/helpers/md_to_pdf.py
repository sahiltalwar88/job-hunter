#!/usr/bin/env python3
"""Convert a resume markdown file to a clean, professional PDF.

Usage:
    python3 -m pipeline.helpers.md_to_pdf <input.md> <output.pdf>

Styling: compact, single-column, sans-serif, fits 2 pages. Matches the
formatting conventions of the base resume (no periods on bullets, tight
spacing, two-column skills table).
"""
import sys
from pathlib import Path
from markdown_it import MarkdownIt
from weasyprint import HTML, CSS

CSS_TEXT = """
@page {
    margin: 0.5in 0.6in 0.5in 0.6in;
    size: Letter;
}
body {
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: 9.5pt;
    line-height: 1.35;
    color: #1a1a1a;
}
h1 {
    font-size: 17pt;
    margin: 0 0 2pt 0;
    text-align: center;
}
h1 + p {
    text-align: center;
    font-size: 8.5pt;
    margin: 0 0 8pt 0;
    color: #333;
}
h2 {
    font-size: 10.5pt;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    border-bottom: 1pt solid #1a1a1a;
    margin: 10pt 0 4pt 0;
    padding-bottom: 1pt;
}
h3 {
    font-size: 10pt;
    margin: 6pt 0 0 0;
}
h4 {
    font-size: 9.5pt;
    margin: 4pt 0 0 0;
    color: #333;
}
p {
    margin: 0;
}
em {
    color: #444;
    font-style: italic;
}
ul {
    margin: 2pt 0 4pt 0;
    padding-left: 14pt;
}
li {
    margin: 1pt 0;
}
strong {
    font-weight: 600;
}
/* Skills table */
table {
    border-collapse: collapse;
    width: 100%;
    margin: 2pt 0;
}
td {
    padding: 1pt 4pt;
    vertical-align: top;
    font-size: 9pt;
}
tr td:first-child {
    text-align: right;
    font-weight: 500;
    width: 50%;
}
/* Education bullets are short */
h2 + h3 + p + ul li {
    margin: 0;
}
"""

def fix_tables(md_text: str) -> str:
    """Insert GFM separator rows for pipe-table blocks that lack one.

    The base resume uses `| col | col |` rows without a `|---|---|` separator,
    which markdown-it won't parse as a table. Detect consecutive pipe-row
    blocks and insert a separator after the first row.
    """
    lines = md_text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Detect start of a pipe-row block (line starts with | and has >1 pipe)
        if line.strip().startswith("|") and line.count("|") >= 2:
            block = [line]
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("|") and lines[j].count("|") >= 2:
                block.append(lines[j])
                j += 1
            # Check if the second line is already a separator (---)
            if len(block) < 2 or not set(block[1].strip().strip("|").strip()) <= set("-: "):
                # Insert separator based on column count of first row
                cols = line.count("|") - 1
                sep = "|" + "|".join(["---"] * cols) + "|"
                result.append(block[0])
                result.append(sep)
                result.extend(block[1:])
            else:
                result.extend(block)
            i = j
        else:
            result.append(line)
            i += 1
    return "\n".join(result)

def md_to_html(md_text: str) -> str:
    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    return md.render(fix_tables(md_text))

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 -m pipeline.helpers.md_to_pdf <input.md> <output.pdf>", file=sys.stderr)
        sys.exit(1)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    md_text = src.read_text(encoding="utf-8")
    html_body = md_to_html(md_text)
    html = f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"
    HTML(string=html).write_pdf(str(dst), stylesheets=[CSS(string=CSS_TEXT)])
    print(f"Wrote {dst} ({dst.stat().st_size} bytes)")

if __name__ == "__main__":
    main()
