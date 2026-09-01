#!/usr/bin/env python3
"""Measure rendered line count of a resume markdown file.

The customizer runs this during its own turn to self-measure against the
line budget (target 70, ceiling 75 — see _config/resume-customization-protocol.md
and docs/adr/0003-line-budget-target-70-ceiling-75.md). LLMs cannot reliably
count rendered lines, so this script does it deterministically.

Bullet detection: lines starting with `* ` (the format used by the base resume
and all few-shot examples). Trailing two-space markdown hard breaks are stripped
before counting, so a bullet's character count is its visible text length.

Rendered lines per bullet = ceil(char_count / wrap_chars), minimum 1.
Widow flags are reported as diagnostics only — no agent or script acts on them
(see docs/adr/0004-drop-widow-elimination.md).

Usage:
    python3 -m pipeline.helpers.count_lines <path> [--json] [--wrap-chars N]
"""
import argparse
import json
import math
import re
import sys

BULLET_RE = re.compile(r"^\*\s+(.*)$")
WIDOW_TAIL_MAX = 13  # last line with <= 13 chars is flagged as a widow


def parse_bullets(text: str) -> list[dict]:
    """Extract bullets from markdown text.

    Each bullet is a line starting with `* `. Trailing two-space markdown
    hard breaks are stripped before counting characters.
    """
    bullets = []
    for raw_line in text.splitlines():
        m = BULLET_RE.match(raw_line)
        if not m:
            continue
        body = m.group(1)
        # Strip trailing two-space markdown hard break.
        if body.endswith("  "):
            body = body[:-2]
        char_count = len(body)
        bullets.append({"text": body, "char_count": char_count})
    return bullets


def measure(bullets: list[dict], wrap_chars: int) -> list[dict]:
    """Annotate each bullet with rendered line count and widow flag."""
    measured = []
    for i, b in enumerate(bullets, start=1):
        char_count = b["char_count"]
        rendered = max(1, math.ceil(char_count / wrap_chars))
        # Widow: a multi-line bullet whose last line is short (<= WIDOW_TAIL_MAX).
        # 2-line widow: char_count in (wrap, wrap + WIDOW_TAIL_MAX]
        # 3-line widow: char_count in (2*wrap, 2*wrap + WIDOW_TAIL_MAX]
        widow = False
        if rendered >= 2:
            last_line_len = char_count - (rendered - 1) * wrap_chars
            if 0 < last_line_len <= WIDOW_TAIL_MAX:
                widow = True
        measured.append({
            "index": i,
            "char_count": char_count,
            "rendered_lines": rendered,
            "widow": widow,
            "preview": (b["text"][:60] + "…") if len(b["text"]) > 60 else b["text"],
        })
    return measured


def human_report(measured: list[dict], wrap_chars: int) -> str:
    total_lines = sum(b["rendered_lines"] for b in measured)
    total_bullets = len(measured)
    widows = [b for b in measured if b["widow"]]

    lines = []
    lines.append(f"wrap_chars: {wrap_chars}")
    lines.append(f"total rendered lines: {total_lines}")
    lines.append(f"total bullets: {total_bullets}")
    lines.append(f"widows flagged: {len(widows)} (diagnostic only — no auto-fix)")
    lines.append("")
    header = f"{'#':>3}  {'chars':>5}  {'lines':>5}  {'widow':>5}  preview"
    lines.append(header)
    lines.append("-" * len(header))
    for b in measured:
        lines.append(
            f"{b['index']:>3}  {b['char_count']:>5}  {b['rendered_lines']:>5}  "
            f"{'yes' if b['widow'] else '':>5}  {b['preview']}"
        )
    lines.append("")
    lines.append(f"TARGET: 70 rendered lines  |  CEILING: 75  |  current: {total_lines}")
    if total_lines > 75:
        lines.append(f"OVER CEILING by {total_lines - 75} — trim least JD-relevant bullets.")
    elif total_lines > 70:
        lines.append(f"Over target by {total_lines - 70}, under ceiling — OK.")
    else:
        lines.append("Within budget.")
    return "\n".join(lines)


def json_report(measured: list[dict], wrap_chars: int) -> str:
    total_lines = sum(b["rendered_lines"] for b in measured)
    return json.dumps({
        "wrap_chars": wrap_chars,
        "total_rendered_lines": total_lines,
        "total_bullets": len(measured),
        "widows": [b["index"] for b in measured if b["widow"]],
        "over_ceiling": total_lines > 75,
        "over_target": total_lines > 70,
        "bullets": measured,
    }, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="Path to the resume markdown file.")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output (used by the customizer agent).")
    parser.add_argument("--wrap-chars", type=int, default=102,
                        help="Characters per rendered line (default: 102, Calibri 11pt 0.5\" margins).")
    args = parser.parse_args()

    try:
        with open(args.path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"error: cannot read {args.path}: {e}", file=sys.stderr)
        sys.exit(1)

    bullets = parse_bullets(text)
    measured = measure(bullets, args.wrap_chars)

    if args.json:
        print(json_report(measured, args.wrap_chars))
    else:
        print(human_report(measured, args.wrap_chars))


if __name__ == "__main__":
    main()
