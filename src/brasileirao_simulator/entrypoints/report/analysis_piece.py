"""One analysis piece: a folder holding `piece.pt.md` and `data.json`.

The text is written by a person; the numbers are written by an analysis script
into `data.json`, and reach the text only through `{placeholders}`, so a number
on the page can always be traced to the run that produced it. Everything that
could make a piece wrong fails loudly here, before a page is written.

Only percentages are ever exposed as placeholders: how many seasons were
simulated is internal.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from brasileirao_simulator.entrypoints.report.og_card import br_date, pct

KNOWN_QUESTIONS = frozenset({"relegation_pairs"})
KNOWN_CHARTS = frozenset({"pair_matrix"})
_COUNT_KEYS = ("neither", "only_a", "only_b", "both")
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


class PieceError(ValueError):
    """A piece that must not be published as it stands."""


@dataclass(frozen=True)
class Piece:
    slug: str
    title: str
    summary: str
    date: str
    charts: tuple
    body: str
    data: dict


def load_piece(folder: Path) -> Piece:
    folder = Path(folder)
    text = (folder / "piece.pt.md").read_text(encoding="utf-8")
    meta, body = _front_matter(text, folder.name)
    data_path = folder / "data.json"
    if not data_path.is_file():
        raise PieceError(f"{folder.name}: missing data.json")
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PieceError(f"{folder.name}: data.json is not valid JSON ({e})") from e
    except OSError as e:
        raise PieceError(f"{folder.name}: data.json is unreadable ({e})") from e
    if not isinstance(data, dict):
        raise PieceError(f"{folder.name}: data.json must be a JSON object")

    piece = Piece(slug=meta.get("slug", ""), title=meta.get("title", ""), summary=meta.get("summary", ""),
                  date=meta.get("date", ""), charts=tuple(meta.get("charts", ())), body=body, data=data)
    _validate(piece, folder.name)
    return piece


def _front_matter(text: str, name: str) -> tuple:
    """The fixed key set between the leading `---` lines: strings (optionally
    double-quoted) and one bracketed list, `charts: [a, b]`."""
    if not text.startswith("---\n"):
        raise PieceError(f"{name}: piece.pt.md must open with a --- front-matter block")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise PieceError(f"{name}: front matter is not closed by ---")
    meta = {}
    for line in head.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        is_bracketed = value.startswith("[") and value.endswith("]")
        if key == "charts" and not is_bracketed:
            raise PieceError(f"{name}: charts must be a list like [pair_matrix]")
        if is_bracketed:
            meta[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key] = value[1:-1] if len(value) > 1 and value[0] == value[-1] == '"' else value
    return meta, body


def _validate(piece: Piece, folder_name: str) -> None:
    name = folder_name
    if piece.slug != folder_name:
        raise PieceError(f"{name}: slug {piece.slug!r} differs from the folder name")
    for field in ("title", "summary", "date"):
        if not getattr(piece, field):
            raise PieceError(f"{name}: front matter needs {field}")
    unknown_charts = [c for c in piece.charts if c not in KNOWN_CHARTS]
    if unknown_charts:
        raise PieceError(f"{name}: no renderer for chart(s) {unknown_charts}")
    data = piece.data
    if data.get("question") not in KNOWN_QUESTIONS:
        raise PieceError(f"{name}: unknown question {data.get('question')!r}")
    if data.get("as_of") != piece.date:
        raise PieceError(f"{name}: front-matter date {piece.date} differs from data.json as_of {data.get('as_of')}")
    if type(data.get("round")) is not int or data["round"] < 1:
        raise PieceError(f"{name}: data.json round must be a positive integer")
    counts = data.get("counts", {})
    values = [counts.get(k) for k in _COUNT_KEYS]
    if not all(type(v) is int and v >= 0 for v in values) or sum(values) <= 0:
        raise PieceError(f"{name}: counts must be four non-negative integers with a positive total")
    clubs = data.get("clubs", [])
    if not isinstance(clubs, list) or len(clubs) != 2 or not all(isinstance(c, str) for c in clubs):
        raise PieceError(f"{name}: data.json clubs must name two clubs")


def shares(counts: dict) -> dict:
    total = sum(counts[k] for k in _COUNT_KEYS)
    s = {k: 100 * counts[k] / total for k in _COUNT_KEYS}
    s["either"] = 100 - s["neither"]
    s["a_down"] = s["only_a"] + s["both"]
    s["b_down"] = s["only_b"] + s["both"]
    s["a_safe"] = 100 - s["a_down"]
    s["b_safe"] = 100 - s["b_down"]
    return s


def placeholders(piece: Piece, display: dict) -> dict:
    values = {f"{key}_pct": pct(value) for key, value in shares(piece.data["counts"]).items()}
    a, b = piece.data["clubs"]
    values.update(club_a=display.get(a, a), club_b=display.get(b, b),
                  date=br_date(piece.data["as_of"]), round=str(piece.data["round"]))
    return values


def fill(text: str, values: dict, piece_slug: str) -> str:
    def value(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values:
            raise PieceError(f"{piece_slug}: no value for placeholder {{{key}}}")
        return values[key]
    return _PLACEHOLDER_RE.sub(value, text)
