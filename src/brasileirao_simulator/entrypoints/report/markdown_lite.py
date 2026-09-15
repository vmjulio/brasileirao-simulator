"""The Markdown an analysis piece is written in - a deliberate subset.

Paragraphs, three heading levels, bullet lists, bold, italic and links. That is
all a piece needs, and a subset small enough to test completely beats a
dependency the host build would have to install. Text is HTML-escaped before
any markup is added, so a piece can never inject HTML; links are kept only for
http(s) and site-relative URLs.
"""

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:https?://|/)[^)\s\"']*)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")
_HEADING_TAGS = {"#": "h2", "##": "h3", "###": "h4"}


def render(text: str) -> str:
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return "\n".join(_block(b.strip()) for b in blocks)


def _block(block: str) -> str:
    marker, _, rest = block.partition(" ")
    if marker in _HEADING_TAGS and "\n" not in block:
        tag = _HEADING_TAGS[marker]
        return f"<{tag}>{_inline(rest)}</{tag}>"
    lines = block.splitlines()
    if all(line.startswith("- ") for line in lines):
        return "<ul>" + "".join(f"<li>{_inline(line[2:])}</li>" for line in lines) + "</ul>"
    return f"<p>{_inline(' '.join(line.strip() for line in lines))}</p>"


def _inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    return _ITALIC_RE.sub(r"<em>\1</em>", text)
