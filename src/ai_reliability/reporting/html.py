"""Static HTML rendering of the generated Markdown (no frontend framework).

Supports exactly the constructs the report generator emits: headings, tables,
bullet lists, blockquotes, bold/code spans and image links. Everything is
HTML-escaped first, so model output in reports cannot inject markup.
"""

from __future__ import annotations

import re
from html import escape

_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1d1d1f}"
    "table{border-collapse:collapse;margin:1rem 0}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}"
    "blockquote{border-left:4px solid #c98a1b;margin:1rem 0;padding:.5rem 1rem;background:#fff8ec}"
    "code{background:#f2f2f2;padding:0 3px}img{max-width:100%}"
)


def _inline(text: str) -> str:
    text = escape(text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", r"<img alt='\1' src='\2'>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", text)


def markdown_to_html(markdown: str, title: str) -> str:
    out: list[str] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            header, body = (
                rows[0],
                [r for r in rows[1:] if not all(set(c) <= {"-", ":"} for c in r)],
            )
            out.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr>")
            out.extend(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body
            )
            out.append("</table>")
            continue
        if line.startswith("- "):
            out.append("<ul>")
            while i < len(lines) and lines[i].startswith("- "):
                out.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            out.append("</ul>")
            continue
        heading = re.match(r"^(#{1,4}) (.*)$", line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
        i += 1
    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{escape(title)}</title>"
        f"<style>{_STYLE}</style></head><body>{''.join(out)}</body></html>"
    )
