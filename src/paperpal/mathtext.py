"""Position-aware PDF text extraction and math-to-LaTeX conversion.

``pdftotext -layout`` flattens superscripts and subscripts into the main text
line, so ``R^4`` becomes ``R 4`` and ``10^-24`` becomes ``10-24``.  Poppler's
XML mode keeps the bounding box of every text fragment, which lets us rebuild
scripts, simple fractions and matrices before the text is parsed into questions.

This module deliberately keeps everything heuristic: the input is a scanned or
born-digital exam paper, not a typeset formula.  The output is a best-effort
LaTeX-ish string that the frontend can hand to KaTeX.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Adobe Symbol encoding for the glyphs that actually appear in the papers.
SYMBOL = {
    0x20: " ", 0x21: "!", 0x22: "\u2200", 0x23: "#", 0x24: "\u2203", 0x25: "%",
    0x26: "&", 0x27: "\u220b", 0x28: "(", 0x29: ")", 0x2A: "\u2217", 0x2B: "+",
    0x2C: ",", 0x2D: "\u2212", 0x2E: ".", 0x2F: "/", 0x3A: ":", 0x3B: ";",
    0x3C: "<", 0x3D: "=", 0x3E: ">", 0x3F: "?", 0x40: "\u2245", 0x5B: "[",
    0x5C: "\u2234", 0x5D: "]", 0x5E: "\u22a5", 0x5F: "_", 0x60: "\u203e",
    0x7B: "{", 0x7C: "|", 0x7D: "}", 0x7E: "\u223c",
    0x41: "\u0391", 0x42: "\u0392", 0x43: "\u03a7", 0x44: "\u0394", 0x45: "\u0395",
    0x46: "\u03a6", 0x47: "\u0393", 0x48: "\u0397", 0x49: "\u0399", 0x4A: "\u03d1",
    0x4B: "\u039a", 0x4C: "\u039b", 0x4D: "\u039c", 0x4E: "\u039d", 0x4F: "\u039f",
    0x50: "\u03a0", 0x51: "\u0398", 0x52: "\u03a1", 0x53: "\u03a3", 0x54: "\u03a4",
    0x55: "\u03a5", 0x56: "\u03c2", 0x57: "\u03a9", 0x58: "\u039e", 0x59: "\u03a8",
    0x5A: "\u0396",
    0x61: "\u03b1", 0x62: "\u03b2", 0x63: "\u03c7", 0x64: "\u03b4", 0x65: "\u03b5",
    0x66: "\u03c6", 0x67: "\u03b3", 0x68: "\u03b7", 0x69: "\u03b9", 0x6A: "\u03d5",
    0x6B: "\u03ba", 0x6C: "\u03bb", 0x6D: "\u03bc", 0x6E: "\u03bd", 0x6F: "\u03bf",
    0x70: "\u03c0", 0x71: "\u03b8", 0x72: "\u03c1", 0x73: "\u03c3", 0x74: "\u03c4",
    0x75: "\u03c5", 0x76: "\u03d6", 0x77: "\u03c9", 0x78: "\u03be", 0x79: "\u03c8",
    0x7A: "\u03b6",
    0xA0: "\u20ac", 0xA1: "\u03d2", 0xA2: "\u2032", 0xA3: "\u2264", 0xA4: "\u2044",
    0xA5: "\u221e", 0xA6: "\u0192", 0xAB: "\u2194", 0xAC: "\u2190", 0xAD: "\u2191",
    0xAE: "\u2192", 0xAF: "\u2193", 0xB0: "\u00b0", 0xB1: "\u00b1", 0xB2: "\u2033",
    0xB3: "\u2265", 0xB4: "\u00d7", 0xB5: "\u221d", 0xB6: "\u2202", 0xB7: "\u2022",
    0xB8: "\u00f7", 0xB9: "\u2260", 0xBA: "\u2261", 0xBB: "\u2248", 0xBC: "\u2026",
    0xBD: "\u23d0", 0xC0: "\u2135", 0xC1: "\u2111", 0xC2: "\u211c", 0xC3: "\u2118",
    0xC4: "\u2297", 0xC5: "\u2295", 0xC6: "\u2205", 0xC7: "\u2229", 0xC8: "\u222a",
    0xC9: "\u2283", 0xCA: "\u2287", 0xCB: "\u2284", 0xCC: "\u2282", 0xCD: "\u2286",
    0xCE: "\u2208", 0xCF: "\u2209", 0xD0: "\u2220", 0xD1: "\u2207", 0xD5: "\u220f",
    0xD6: "\u221a", 0xD7: "\u22c5", 0xD8: "\u00ac", 0xD9: "\u2227", 0xDA: "\u2228",
    0xDB: "\u21d4", 0xDC: "\u21d0", 0xDD: "\u21d1", 0xDE: "\u21d2", 0xDF: "\u21d3",
    0xE0: "\u25ca", 0xE1: "\u27e8", 0xE5: "\u2211", 0xE6: "\u239b", 0xE7: "\u239c",
    0xE8: "\u239d", 0xE9: "\u23a1", 0xEA: "\u23a2", 0xEB: "\u23a3", 0xEC: "\u23a7",
    0xED: "\u23a8", 0xEE: "\u23a9", 0xF1: "\u27e9", 0xF2: "\u222b", 0xF3: "\u2320",
    0xF4: "\u23ae", 0xF5: "\u2321", 0xF6: "\u239e", 0xF7: "\u239f", 0xF8: "\u23a0",
    0xF9: "\u23a4", 0xFA: "\u23a5", 0xFB: "\u23a6", 0xFC: "\u23ab", 0xFD: "\u23ac",
    0xFE: "\u23ad",
}
for _digit in range(0x30, 0x3A):
    SYMBOL[_digit] = chr(_digit)

# Glyphs that are useful enough to be worth counting as math evidence.
MATH_SYMBOL_CHARS = set(SYMBOL.values()) - set("0123456789 \t.,;:!?/()[]{}|_#%&")
BULLET_CHARS = {"\u2022", "\u00b7", "\u25cf", "\u25aa", "\u25a0", "\u2043"}


@dataclass
class Fragment:
    text: str
    left: float
    top: float
    width: float
    height: float
    size: float
    family: str
    math: bool = False


@dataclass
class Line:
    frags: list[Fragment]
    top: float
    bottom: float
    size: float
    score: int = 0


def _symbol_char(character: str) -> str:
    code = ord(character) - 0xF000
    if 0x20 <= code <= 0xFE:
        return SYMBOL.get(code, "")
    return ""


def normalize_fragment_text(text: str, family: str) -> tuple[str, int]:
    """Map Symbol-font private-use glyphs and Unicode math letters to ASCII.

    Returns the normalized text and a small score that says how much math
    evidence the fragment contributed.  Non-Symbol private-use glyphs (mostly
    Wingdings bullets) are dropped rather than guessed at.
    """
    symbol_font = "symbol" in family.lower()
    out: list[str] = []
    score = 0
    for character in text:
        code = ord(character)
        if 0xF000 <= code <= 0xF8FF:
            if not symbol_font:
                out.append(" ")
                continue
            mapped = _symbol_char(character)
            if mapped in MATH_SYMBOL_CHARS:
                score += 1
            out.append(mapped)
        elif 0x1D400 <= code <= 0x1D7FF or 0x2100 <= code <= 0x214F:
            out.append(unicodedata.normalize("NFKC", character))
            score += 1
        else:
            out.append(character)
    return "".join(out), score


# Keep this tight.  "row", "column", "vector" and "basis" match too much prose
# and used to turn state tables / K-maps into matrices.
MATRIX_CONTEXT = re.compile(
    r"\b(?:matrix|matrices|eigen(?:value|vector)?s?|determinant|diagonal(?:izable)?|"
    r"orthonormal|circulant|hessian|jacobian|bmatrix)\b",
    re.IGNORECASE,
)
TABLE_CONTEXT = re.compile(r"\btable\b", re.IGNORECASE)

_ENTRY_RE = re.compile(
    r"^[+\-\u2212]?(?:"
    r"\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?(?:[A-Za-z\u0370-\u03ff])?"
    r"(?:_\{[^}]*\}|\^\{[^}]*\})*"
    r"|[A-Za-z\u0370-\u03ff](?:_\{[^}]*\}|\^\{[^}]*\})*"
    r"|\u221a[\w.]*"
    r"|\([^()]*\)"
    r"|\u221e"
    r")$"
)
_FRACTION_PART_RE = re.compile(r"^[+\-\u2212]?[\w\u0370-\u03ff\u221a\u03c0.()/]+$")

_OPEN_BRACKETS = {"[", "\u23a1", "\u23a2", "\u23a3"}
_CLOSE_BRACKETS = {"]", "\u23a4", "\u23a5", "\u23a6"}
# Only a real bottom/right bracket closes a multi-row block.  The top and
# middle pieces of a tall bracket (⎤ ⎥) are not terminal.
_TERMINAL_CLOSE = {"]", "\u23a6"}
_SIGN_TOKENS = {"+", "-", "\u2212"}
_TOKEN_RE = re.compile(
    r"\[|\]|\u23a1|\u23a2|\u23a3|\u23a4|\u23a5|\u23a6|[^\s\[\]\u23a1-\u23a6]+"
)
_LABEL_RE = re.compile(r"^[A-Za-z\u0370-\u03ff]$|^[=+\-\u2212]$")


def _looks_like_fraction_part(text: str) -> bool:
    text = text.strip()
    if not text or len(text) > 8 or " " in text:
        return False
    if text[-1] in ".,;:":
        # List numbering such as "10." / "11." stacks like a fraction.
        return False
    if any(character in BULLET_CHARS for character in text):
        return False
    if not _FRACTION_PART_RE.fullmatch(text):
        return False
    return any(character.isalnum() or character == "\u221a" for character in text)


def detect_fractions(fragments: list[Fragment]) -> tuple[list[Fragment], int]:
    """Fold vertically stacked numerator/denominator pairs into inline \\frac."""
    candidates = [
        fragment
        for fragment in fragments
        if _looks_like_fraction_part(fragment.text) and fragment.width <= 80
    ]
    used: set[int] = set()
    created: list[Fragment] = []
    score = 0

    for numerator in candidates:
        if id(numerator) in used:
            continue
        best: tuple[float, Fragment, Fragment] | None = None
        for denominator in candidates:
            if denominator is numerator or id(denominator) in used:
                continue
            if denominator.top <= numerator.top:
                continue
            overlap = min(
                numerator.left + numerator.width, denominator.left + denominator.width
            ) - max(numerator.left, denominator.left)
            if overlap < 0.45 * max(1.0, min(numerator.width, denominator.width)):
                continue
            gap = denominator.top - (numerator.top + numerator.height)
            if not (-2 <= gap <= 8):
                continue
            # Table rows are further apart than the halves of an inline fraction.
            if abs(denominator.top - numerator.top) > 20:
                continue
            # Fraction halves are stacked, so their glyph runs are comparable.
            narrow = min(numerator.width, denominator.width)
            wide = max(numerator.width, denominator.width)
            if wide > 0 and narrow / wide < 0.3:
                continue
            if numerator.text.strip() == denominator.text.strip():
                continue

            block_left = min(numerator.left, denominator.left)
            # A base letter glued to the stack means these are a superscript and
            # a subscript (``E^2_m``), not a fraction.
            if any(
                other is not numerator
                and other is not denominator
                and block_left - (other.left + other.width) <= 4
                and block_left - (other.left + other.width) >= -2
                and other.top < denominator.top
                and other.top + other.height > numerator.top
                for other in fragments
            ):
                continue

            # A main-text fragment must sit between the two halves; that is what
            # separates a real fraction from two unrelated lines of text.
            parent = None
            for host in fragments:
                if host is numerator or host is denominator or id(host) in used:
                    continue
                if host.size <= max(numerator.size, denominator.size) * 0.9:
                    continue
                if not (
                    numerator.top + 0.18 * (denominator.top - numerator.top)
                    <= host.top
                    <= numerator.top + 0.82 * (denominator.top - numerator.top)
                ):
                    continue
                if not (
                    min(numerator.left, denominator.left) - 120
                    <= host.left
                    <= max(numerator.left + numerator.width, denominator.left + denominator.width) + 120
                ):
                    continue
                # Integral limits are scripts, not fractions.
                if not re.search(r"[A-Za-z0-9]", host.text):
                    continue
                if abs(numerator.size - denominator.size) > 0.2 * max(
                    numerator.size, denominator.size
                ):
                    continue
                # Inline fractions are set smaller than the surrounding line.
                # Full-size stacked pairs are usually table cells or pieces of a
                # piecewise definition.
                if min(numerator.size, denominator.size) >= 0.85 * host.size:
                    continue
                if re.search(r"[\u222b\u2211\u220f]", host.text) and re.fullmatch(
                    r"[+\-\u2212]?\d+", numerator.text.strip()
                ) and re.fullmatch(r"[+\-\u2212]?\d+", denominator.text.strip()):
                    continue
                upper = numerator.text.strip()
                lower = denominator.text.strip()
                if upper.isalpha() and lower.isalpha():
                    if len(upper) >= 3 and len(lower) >= 3:
                        continue
                    if upper.lower() in STOPWORDS or lower.lower() in STOPWORDS:
                        continue
                # Integral and summation limits are stacked but are not fractions.
                if (
                    re.fullmatch(r"[+\-\u2212]?\d{1,2}", upper)
                    and re.fullmatch(r"[+\-\u2212]?\d{1,2}", lower)
                    and any(
                        re.search(r"[\u222b\u2211\u220f]", neighbour.text)
                        and 0 <= block_left - (neighbour.left + neighbour.width) <= 45
                        for neighbour in fragments
                        if neighbour is not numerator and neighbour is not denominator
                    )
                ):
                    continue
                if re.fullmatch(r"\(\s*[\d+ ]+\s*\)", denominator.text.strip()):
                    continue
                parent = host
                break

            if parent is None:
                continue

            distance = abs((denominator.top - numerator.top) - 20)
            distance += abs((numerator.left + denominator.left) / 2 - parent.left) * 0.001
            if best is None or distance < best[0]:
                best = (distance, denominator, parent)

        if best is None:
            continue
        _, denominator, parent = best
        used.add(id(numerator))
        used.add(id(denominator))
        tex = (
            "\\("
            + "\\frac{"
            + numerator.text.strip()
            + "}{"
            + denominator.text.strip()
            + "}\\)"
        )
        height = (denominator.top + denominator.height) - numerator.top
        left = min(numerator.left, denominator.left)
        right = max(numerator.left + numerator.width, denominator.left + denominator.width)
        created.append(
            Fragment(
                tex,
                left,
                parent.top + 0.5 * (parent.height - height),
                right - left,
                height,
                parent.size,
                parent.family,
                math=True,
            )
        )
        score += 1

    return [fragment for fragment in fragments if id(fragment) not in used] + created, score


def group_lines(fragments: list[Fragment]) -> list[Line]:
    lines: list[Line] = []
    for fragment in sorted(fragments, key=lambda item: (item.top, item.left)):
        if not fragment.text.strip():
            continue
        baseline = fragment.top + 0.8 * fragment.height
        placed = False
        for line in lines:
            reference = max(line.size, fragment.size)
            line_baseline = line.top + 0.8 * (line.bottom - line.top)
            if abs(baseline - line_baseline) <= 0.5 * reference:
                line.frags.append(fragment)
                line.top = min(line.top, fragment.top)
                line.bottom = max(line.bottom, fragment.top + fragment.height)
                line.size = max(line.size, fragment.size)
                placed = True
                break
        if not placed:
            lines.append(
                Line([fragment], fragment.top, fragment.top + fragment.height, fragment.size)
            )
    lines.sort(key=lambda item: item.top)
    return lines


def _trim_script(text: str) -> tuple[str, str]:
    body = text.strip()
    trailing = ""
    while body and body[-1] in ".,;:":
        trailing = body[-1] + trailing
        body = body[:-1]
    return _sanitize_script_body(body), trailing


def render_line(line: Line) -> tuple[str, int]:
    if not line.frags:
        return "", 0
    fragments = sorted(line.frags, key=lambda item: item.left)
    size_weights: dict[float, int] = {}
    for fragment in fragments:
        size_weights[fragment.size] = size_weights.get(fragment.size, 0) + max(
            1, len(fragment.text.strip())
        )
    main_size = max(size_weights, key=size_weights.get)
    mains = [fragment for fragment in fragments if fragment.size >= 0.82 * main_size]
    baseline = (
        sorted(fragment.top + 0.8 * fragment.height for fragment in mains)[len(mains) // 2]
        if mains
        else line.top + 0.8 * (line.bottom - line.top)
    )

    parts: list[str] = []
    previous_right: float | None = None
    pending_space = False
    last_main: Fragment | None = None
    score = 0

    for fragment in fragments:
        text = fragment.text
        if not text:
            continue
        body_text = text.strip()
        is_script = (
            fragment.size < 0.92 * main_size
            and last_main is not None
            and len(body_text) <= 12
            and fragment.width <= 140
        )
        kind = "normal"
        if is_script:
            center = fragment.top + 0.5 * fragment.height
            main_center = last_main.top + 0.5 * last_main.height
            offset = center - main_center
            if offset < -0.05 * main_size:
                kind = "sup"
            elif offset > 0.05 * main_size:
                kind = "sub"

        if kind in {"sup", "sub"}:
            body, trailing = _trim_script(text)
            if not body:
                continue
            marker = "^" if kind == "sup" else "_"
            merged = False
            if parts:
                pattern = re.compile(re.escape(marker) + r"\{([^{}]*)\}$")
                match = pattern.search(parts[-1])
                if match:
                    parts[-1] = (
                        parts[-1][: match.start()]
                        + marker
                        + "{"
                        + match.group(1)
                        + body
                        + "}"
                        + trailing
                    )
                    merged = True
            if not merged:
                parts.append(f"{marker}{{{body}}}{trailing}")
            previous_right = fragment.left + fragment.width
            pending_space = True
            score += 1
            continue

        if previous_right is not None:
            gap = fragment.left - previous_right
            if pending_space and text[:1].isalpha():
                parts.append(" ")
            elif gap > 0.14 * main_size and parts and not parts[-1].endswith(" "):
                if text[:1] not in ",.;:)]}":
                    parts.append(" ")
        pending_space = False
        parts.append(text)
        previous_right = fragment.left + fragment.width
        last_main = fragment

    line.score += score
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip(), score


Token = tuple[str, float]


def _line_tokens(line: Line, page_width: float) -> list[Token]:
    """Split a line into tokens with an approximate x position for each."""
    raw: list[Token] = []
    for fragment in sorted(line.frags, key=lambda item: item.left):
        if fragment.left > 0.68 * page_width:
            continue
        text = fragment.text
        if not text.strip():
            continue
        length = max(1, len(text))
        for match in _TOKEN_RE.finditer(text):
            token = match.group(0)
            left = fragment.left + fragment.width * (match.start() / length)
            raw.append((token, left))
    tokens: list[Token] = []
    index = 0
    while index < len(raw):
        token, left = raw[index]
        if token in _SIGN_TOKENS and index + 1 < len(raw):
            following, _ = raw[index + 1]
            if _ENTRY_RE.match(following) and following[:1] not in _SIGN_TOKENS:
                sign = "-" if token in {"-", "\u2212"} else "+"
                tokens.append((sign + following, left))
                index += 2
                continue
        tokens.append((token, left))
        index += 1
    return tokens


def _is_label(token: str) -> bool:
    return bool(_LABEL_RE.match(token))


def _strip_brackets(
    tokens: list[Token],
) -> tuple[list[Token], list[Token], list[Token]]:
    """Return (content, prefix, suffix) for one bracketed row."""
    opens = [index for index, (token, _) in enumerate(tokens) if token in _OPEN_BRACKETS]
    closes = [index for index, (token, _) in enumerate(tokens) if token in _CLOSE_BRACKETS]
    if opens:
        first_open = opens[0]
        following_closes = [index for index in closes if index > first_open]
        prefix = tokens[:first_open]
        if following_closes:
            last_close = following_closes[-1]
            content = [
                item
                for index, item in enumerate(tokens)
                if first_open < index < last_close
                and item[0] not in _OPEN_BRACKETS
                and item[0] not in _CLOSE_BRACKETS
            ]
            suffix = tokens[last_close + 1:]
        else:
            content = [
                item
                for index, item in enumerate(tokens)
                if index > first_open
                and item[0] not in _OPEN_BRACKETS
                and item[0] not in _CLOSE_BRACKETS
            ]
            suffix = []
        return content, prefix, suffix
    if closes:
        first_close = closes[0]
        content = [
            item
            for index, item in enumerate(tokens)
            if index < first_close and item[0] not in _CLOSE_BRACKETS
        ]
        return content, [], tokens[first_close + 1:]
    return list(tokens), [], []


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _mode(values: list[int]) -> int:
    counts: dict[int, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=lambda value: counts[value])


def _dedupe_tokens(tokens: list[Token]) -> list[Token]:
    seen: set[str] = set()
    result: list[Token] = []
    for token, left in tokens:
        if token in seen:
            continue
        seen.add(token)
        result.append((token, left))
    return result


def _entry_tex(token: str) -> str:
    token = token.replace("\u2212", "-").replace("\u221e", r"\infty")
    return token.replace("%", r"\%").replace("#", r"\#").replace("&", r"\&")


def _assign_rows(
    rows: list[list[Token]], columns: int
) -> tuple[list[list[str | None]], list[Token]]:
    """Place tokens into columns using x positions, keeping labels aside."""
    exact = [row for row in rows if len(row) == columns]
    source = exact or rows
    centers = [
        _median([row[index][1] for row in source if len(row) > index])
        for index in range(columns)
    ]
    gaps = [
        centers[index + 1] - centers[index]
        for index in range(columns - 1)
        if centers[index + 1] > centers[index]
    ]
    spacing = _median(gaps) if gaps else 24.0
    tolerance = max(6.0, 0.55 * spacing)
    assigned_rows: list[list[str | None]] = []
    extra: list[Token] = []
    for row in rows:
        if len(row) == columns:
            assigned_rows.append([token for token, _ in row])
            continue
        assigned: list[str | None] = [None] * columns
        for token, left in row:
            best = min(range(columns), key=lambda index: abs(left - centers[index]))
            if abs(left - centers[best]) <= tolerance and assigned[best] is None:
                assigned[best] = token
            else:
                extra.append((token, left))
        assigned_rows.append(assigned)
    return assigned_rows, extra


def _make_matrix(
    rows: list[list[Token]],
    prefix_tokens: list[Token],
    first_line: Line,
    last_line: Line,
    strict: bool = False,
) -> list[Line] | None:
    counts = [len(row) for row in rows]
    columns = _mode(counts)
    if not 2 <= columns <= 6:
        return None
    if any(count < 2 or abs(count - columns) > 2 for count in counts):
        return None
    assigned_rows, extra = _assign_rows(rows, columns)
    numeric = 0
    total = 0
    for row in assigned_rows:
        if any(token is None for token in row):
            # A partially-empty grid is almost always a broken extraction or
            # a numbered list, not a matrix.
            return None
        for token in row:
            if not token or not _ENTRY_RE.match(token):
                return None
            if token.startswith("+"):
                # Explicit plus signs are almost always formula fragments or
                # table cells, not matrix entries.
                return None
            total += 1
            if any(character.isdigit() for character in token) or token == "\u221e":
                numeric += 1
            if strict and "%" in token:
                return None
    if strict and total and numeric / total < 2 / 3:
        return None

    output: list[Line] = []
    prefix = _dedupe_tokens(prefix_tokens + extra)
    if prefix:
        ordered = sorted(prefix, key=lambda item: item[1])
        text = " ".join(token for token, _ in ordered)
        left = min(position for _, position in ordered)
        size = first_line.size
        output.append(
            Line(
                [Fragment(text, left, first_line.top, 0, 10, size, "")],
                first_line.top,
                first_line.top + size,
                size,
            )
        )

    body = [
        " & ".join(_entry_tex(token) if token else "" for token in row)
        for row in assigned_rows
    ]
    tex = "\\[ \\begin{bmatrix} " + " \\\\ ".join(body) + " \\end{bmatrix} \\]"
    left = rows[0][0][1] if rows and rows[0] else first_line.frags[0].left
    output.append(
        Line(
            [Fragment(tex, left, first_line.top, 0, 10, first_line.size, "", math=True)],
            first_line.top,
            last_line.bottom,
            first_line.size,
        )
    )
    return output


def _has_matrix_context(lines: list[Line], start: int, end: int) -> bool:
    window = lines[max(0, start - 4): min(len(lines), end + 1)]
    return any(MATRIX_CONTEXT.search(fragment.text) for line in window for fragment in line.frags)


def _has_table_context(lines: list[Line], start: int, end: int) -> bool:
    window = lines[max(0, start - 4): min(len(lines), end + 1)]
    return any(TABLE_CONTEXT.search(fragment.text) for line in window for fragment in line.frags)


def _bracket_block(
    lines: list[Line], start: int, page_width: float
) -> tuple[list[list[Token]], list[Token], list[Token], int] | None:
    """Find a multi-row block delimited by real or tall bracket glyphs."""
    first_tokens = _line_tokens(lines[start], page_width)
    if not any(token in _OPEN_BRACKETS for token, _ in first_tokens):
        return None

    end: int | None = None
    content_rows = 0
    cursor = start
    while cursor < len(lines) and cursor - start < 8:
        if cursor > start:
            gap = lines[cursor].top - lines[cursor - 1].top
            if not 6 <= gap <= 34:
                break
        line_tokens = _line_tokens(lines[cursor], page_width)
        content, _, _ = _strip_brackets(line_tokens)
        if len(content) >= 2:
            content_rows += 1
        if any(token in _TERMINAL_CLOSE for token, _ in line_tokens):
            if content_rows >= 2 and cursor > start:
                end = cursor
            break
        cursor += 1
    if end is None:
        return None

    rows: list[list[Token]] = []
    prefix: list[Token] = []
    suffix: list[Token] = []
    for cursor in range(start, end + 1):
        line_tokens = _line_tokens(lines[cursor], page_width)
        content, before, after = _strip_brackets(line_tokens)
        prefix.extend(before)
        suffix.extend(after)
        if len(content) >= 2:
            if all(_ENTRY_RE.match(token) for token, _ in content):
                rows.append(content)
            else:
                return None
        elif content:
            if all(_is_label(token) for token, _ in content):
                prefix.extend(content)
            else:
                return None
    if not 2 <= len(rows) <= 6:
        return None
    return rows, prefix, suffix, end


def _plain_block(
    lines: list[Line], start: int, page_width: float
) -> tuple[list[list[Token]], list[Token], int] | None:
    """Find a bracket-less run of short entry rows (common in older papers)."""
    rows: list[list[Token]] = []
    prefix: list[Token] = []
    cursor = start
    previous_top: float | None = None
    while cursor < len(lines) and len(rows) < 6:
        if previous_top is not None and not 8 <= lines[cursor].top - previous_top <= 38:
            break
        line_tokens = _line_tokens(lines[cursor], page_width)
        if not line_tokens:
            break
        if len(line_tokens) <= 2 and all(_is_label(token) for token, _ in line_tokens):
            prefix.extend(line_tokens)
            previous_top = lines[cursor].top
            cursor += 1
            continue
        if not 2 <= len(line_tokens) <= 6:
            break
        if not all(_ENTRY_RE.match(token) or _is_label(token) for token, _ in line_tokens):
            break
        if sum(1 for token, _ in line_tokens if _ENTRY_RE.match(token)) < 2:
            break
        rows.append(line_tokens)
        previous_top = lines[cursor].top
        cursor += 1
    if len(rows) < 2:
        return None
    return rows, prefix, cursor - 1


def detect_matrices(lines: list[Line], page_width: float) -> tuple[list[Line], int]:
    """Collapse two or more aligned rows of short entries into a bmatrix.

    Bracketed blocks are trusted when they span at least two rows; this catches
    tall bracket glyphs (⎡ ⎢ ⎣) as well as plain ``[`` / ``]``.  Bracket-less
    blocks still need a nearby matrix word, because that shape is also used by
    state tables and K-maps.
    """
    result: list[Line] = []
    index = 0
    score = 0
    while index < len(lines):
        bracket = _bracket_block(lines, index, page_width)
        if bracket:
            rows, prefix, suffix, end = bracket
            block = _make_matrix(rows, prefix, lines[index], lines[end])
            if block:
                result.extend(block)
                if suffix:
                    ordered = sorted(suffix, key=lambda item: item[1])
                    text = " ".join(token for token, _ in ordered)
                    if text.strip():
                        size = lines[end].size
                        result.append(
                            Line(
                                [Fragment(text, ordered[0][1], lines[end].top, 0, 10, size, "")],
                                lines[end].top,
                                lines[end].top + size,
                                size,
                            )
                        )
                score += 1
                index = end + 1
                continue

        plain = _plain_block(lines, index, page_width)
        if plain:
            rows, prefix, end = plain
            if _has_matrix_context(lines, index, end) and not _has_table_context(
                lines, index, end
            ):
                block = _make_matrix(
                    rows, prefix, lines[index], lines[end], strict=True
                )
                if block:
                    result.extend(block)
                    score += 1
                    index = end + 1
                    continue
        result.append(lines[index])
        index += 1

    return result, score


def _parse_xml(payload: str) -> list[tuple[list[Fragment], float, float, int]]:
    root = ET.fromstring(payload)
    fonts: dict[str, tuple[float, str]] = {}
    for spec in root.iter("fontspec"):
        fonts[spec.get("id")] = (float(spec.get("size", "10")), spec.get("family", ""))

    pages: list[tuple[list[Fragment], float, float, int]] = []
    for page in root.findall("page"):
        fragments: list[Fragment] = []
        page_score = 0
        for node in page.findall("text"):
            raw = "".join(node.itertext())
            if not raw:
                continue
            size, family = fonts.get(node.get("font"), (10.0, ""))
            text, score = normalize_fragment_text(raw, family)
            page_score += score
            if not text.strip():
                continue
            fragments.append(
                Fragment(
                    text,
                    float(node.get("left", 0)),
                    float(node.get("top", 0)),
                    float(node.get("width", 0)),
                    float(node.get("height", 0)),
                    size,
                    family,
                )
            )
        pages.append(
            (
                fragments,
                float(page.get("width", 600)),
                float(page.get("height", 800)),
                page_score,
            )
        )
    return pages


def extract_rich_text(path: Path, timeout: int = 30) -> str | None:
    """Extract text with bounding-box awareness.

    Returns ``None`` when Poppler's XML mode is unavailable or fails.  The
    caller then falls back to the layout-based extractor.
    """
    pdftohtml = shutil.which("pdftohtml")
    if not pdftohtml:
        return None
    try:
        result = subprocess.run(
            [pdftohtml, "-xml", "-hidden", "-nodrm", "-i", "-stdout", str(path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        pages = _parse_xml(result.stdout)
    except ET.ParseError:
        return None

    rendered: list[str] = []
    evidence = 0
    for fragments, width, _height, page_score in pages:
        evidence += page_score
        fragments, fraction_score = detect_fractions(fragments)
        evidence += fraction_score
        lines = group_lines(fragments)
        lines, matrix_score = detect_matrices(lines, width)
        evidence += matrix_score * 4
        page_lines: list[str] = []
        for line in lines:
            text, script_score = render_line(line)
            evidence += script_score
            if text:
                page_lines.append(text)
        if page_lines:
            rendered.append("\n".join(page_lines))

    if not rendered:
        return None
    if evidence < 2:
        # Nothing math-ish enough to justify the noisier XML reconstruction.
        return None
    return "\n\n=== PAGE BREAK ===\n\n".join(rendered)


# --------------------------------------------------------------------- LaTeX

GREEK = {
    "\u03b1": r"\alpha", "\u03b2": r"\beta", "\u03b3": r"\gamma", "\u03b4": r"\delta",
    "\u03b5": r"\epsilon", "\u03b6": r"\zeta", "\u03b7": r"\eta", "\u03b8": r"\theta",
    "\u03b9": r"\iota", "\u03ba": r"\kappa", "\u03bb": r"\lambda", "\u03bc": r"\mu",
    "\u03bd": r"\nu", "\u03be": r"\xi", "\u03bf": "o", "\u03c0": r"\pi",
    "\u03c1": r"\rho", "\u03c3": r"\sigma", "\u03c4": r"\tau", "\u03c5": r"\upsilon",
    "\u03c6": r"\phi", "\u03c7": r"\chi", "\u03c8": r"\psi", "\u03c9": r"\omega",
    "\u0393": r"\Gamma", "\u0394": r"\Delta", "\u0398": r"\Theta", "\u039b": r"\Lambda",
    "\u039e": r"\Xi", "\u03a0": r"\Pi", "\u03a3": r"\Sigma", "\u03a5": r"\Upsilon",
    "\u03a6": r"\Phi", "\u03a8": r"\Psi", "\u03a9": r"\Omega", "\u03d1": r"\vartheta",
    "\u03d5": r"\phi", "\u03d6": r"\varpi", "\u03c2": r"\varsigma",
    "\u2112": r"\mathcal{L}",
}

SYMBOLS = {
    "\u00d7": r"\times", "\u00f7": r"\div", "\u00b1": r"\pm", "\u2213": r"\mp",
    "\u00b7": r"\cdot", "\u22c5": r"\cdot", "\u2264": r"\le", "\u2265": r"\ge",
    "\u2260": r"\ne", "\u2248": r"\approx", "\u2261": r"\equiv", "\u2245": r"\cong",
    "\u221d": r"\propto", "\u223c": r"\sim", "\u2243": r"\simeq", "\u2208": r"\in",
    "\u2209": r"\notin", "\u220b": r"\ni", "\u222a": r"\cup", "\u2229": r"\cap",
    "\u2282": r"\subset", "\u2283": r"\supset", "\u2286": r"\subseteq",
    "\u2287": r"\supseteq", "\u2205": r"\emptyset", "\u221e": r"\infty",
    "\u2202": r"\partial", "\u2207": r"\nabla", "\u2200": r"\forall",
    "\u2203": r"\exists", "\u2192": r"\to", "\u2190": r"\leftarrow",
    "\u2194": r"\leftrightarrow", "\u21d2": r"\Rightarrow",
    "\u21d4": r"\Leftrightarrow", "\u21a6": r"\mapsto", "\u2234": r"\therefore",
    "\u2235": r"\because", "\u2211": r"\sum", "\u220f": r"\prod", "\u222b": r"\int",
    "\u2220": r"\angle", "\u00b0": r"^{\circ}", "\u2032": r"^{\prime}",
    "\u2033": r"^{\prime\prime}",
    "\u2026": r"\ldots", "\u22ef": r"\cdots", "\u2016": r"\|", "\u2218": r"\circ",
    "\u2297": r"\otimes", "\u2295": r"\oplus", "\u2135": r"\aleph",
    "\u2124": r"\mathbb{Z}", "\u211d": r"\mathbb{R}", "\u2115": r"\mathbb{N}",
    "\u211a": r"\mathbb{Q}", "\u2102": r"\mathbb{C}", "\u22a5": r"\perp",
    "\u2227": r"\wedge", "\u2228": r"\vee", "\u00ac": r"\neg",
    "\u27e8": r"\langle", "\u27e9": r"\rangle", "\u2212": "-", "\u2217": "*",
}

FUNCTIONS = {
    "sin": r"\sin", "cos": r"\cos", "tan": r"\tan", "cot": r"\cot", "sec": r"\sec",
    "csc": r"\csc", "arcsin": r"\arcsin", "arccos": r"\arccos", "arctan": r"\arctan",
    "sinh": r"\sinh", "cosh": r"\cosh", "tanh": r"\tanh", "log": r"\log",
    "ln": r"\ln", "exp": r"\exp", "lim": r"\lim", "max": r"\max", "min": r"\min",
    "sup": r"\sup", "inf": r"\inf", "det": r"\det", "dim": r"\dim", "ker": r"\ker",
    "gcd": r"\gcd", "lcm": r"\operatorname{lcm}", "mod": r"\bmod", "arg": r"\arg",
    "deg": r"\deg", "rank": r"\operatorname{rank}", "Im": r"\operatorname{Im}",
    "Re": r"\operatorname{Re}", "Aut": r"\operatorname{Aut}",
    "Inn": r"\operatorname{Inn}", "Hom": r"\operatorname{Hom}",
    "span": r"\operatorname{span}", "trace": r"\operatorname{trace}",
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could",
    "did", "do", "does", "each", "every", "for", "from", "had", "has", "have", "he",
    "her", "his", "how", "if", "in", "into", "is", "it", "its", "let", "may", "must",
    "not", "of", "on", "only", "or", "our", "out", "over", "shall", "she", "should",
    "so", "some", "such", "than", "that", "the", "their", "then", "there", "these",
    "they", "this", "those", "through", "to", "under", "up", "us", "was", "we", "were",
    "what", "when", "where", "which", "while", "who", "will", "with", "would", "you",
    "all", "any", "also", "given", "find", "show", "prove", "using", "two", "one",
    "three", "four", "five", "six", "seven", "eight", "nine", "ten", "set", "sum",
    "row", "col", "map", "via", "per", "use", "get", "put", "say", "see", "new",
    "old", "way", "off", "own", "part", "same", "very", "well", "just", "like",
    "made", "make", "many", "most", "much", "need", "next", "once", "onto", "them",
    "thus", "till", "time", "until", "upon", "used", "uses", "want", "within",
    "without", "your",
}

MATH_WORDS = {
    "hc", "dx", "dy", "dz", "dt", "du", "dv", "dm", "dn", "dp", "dq", "dr", "ds",
    "AB", "BA", "HK", "KH", "HN", "NK", "mn", "xy", "yz", "zx", "bxy",
    "Tr", "Im", "Re", "Aut", "Inn", "Hom", "ker", "det", "dim", "gcd", "lcm",
    "mod", "exp", "sin", "cos", "tan", "log", "ln", "lim", "max", "min", "rank",
    "span", "trace", "adj", "cof", "rms", "avg", "std",
}

MATH_CHARS = set(SYMBOLS) | set(GREEK)
OPS = set("+-*/=<>()[]{}|,;:")
SUPERSCRIPTS = {
    "\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3", "\u2074": "4",
    "\u2075": "5", "\u2076": "6", "\u2077": "7", "\u2078": "8", "\u2079": "9",
    "\u207a": "+", "\u207b": "-", "\u207c": "=", "\u207d": "(", "\u207e": ")",
    "\u207f": "n", "\u2071": "i",
}
SUBSCRIPTS = {
    "\u2080": "0", "\u2081": "1", "\u2082": "2", "\u2083": "3", "\u2084": "4",
    "\u2085": "5", "\u2086": "6", "\u2087": "7", "\u2088": "8", "\u2089": "9",
    "\u208a": "+", "\u208b": "-", "\u208c": "=", "\u208d": "(", "\u208e": ")",
    "\u2090": "a", "\u2091": "e", "\u2092": "o", "\u2093": "x", "\u2094": "\u0259",
    "\u2095": "h", "\u2096": "k", "\u2097": "l", "\u2098": "m", "\u2099": "n",
    "\u209a": "p", "\u209b": "s", "\u209c": "t",
}

LABEL_RE = re.compile(
    r"^((?:Q(?:uestion)?\s*)?\d{1,2}[A-Za-z]?[.)]?|[a-z]\)|\([ivx]+\)|\(\d+\))\s+(?=[A-Z(])",
    re.IGNORECASE,
)

TOKEN_RE = re.compile(
    r"\\sqrt\{[^{}]*\}|\\frac\{[^{}]*\}\{[^{}]*\}|\^\{[^{}]*\}|_\{[^{}]*\}|"
    r"\^[A-Za-z0-9]|_[A-Za-z0-9]|[A-Za-z]+(?:[-\u2010][A-Za-z]+)+|"
    r"[A-Za-z]+|\d+(?:\.\d+)?|[ \t]+|.",
    re.S,
)

HYPHEN_RE = re.compile(r"[A-Za-z]+(?:[-\u2010][A-Za-z]+)+")


def _symbols_to_tex(text: str) -> str:
    out: list[str] = []
    for character in text:
        if character in GREEK:
            out.append(GREEK[character] + " ")
        elif character in SYMBOLS:
            out.append(SYMBOLS[character] + " ")
        else:
            out.append(character)
    return "".join(out)


def _normalize_super_sub(text: str) -> str:
    out: list[str] = []
    for character in text:
        if character in SUPERSCRIPTS:
            out.append("^{" + SUPERSCRIPTS[character] + "}")
        elif character in SUBSCRIPTS:
            out.append("_{" + SUBSCRIPTS[character] + "}")
        else:
            out.append(character)
    return "".join(out)


def _merge_scripts(body: str) -> str:
    """Merge adjacent superscripts/subscripts produced by split glyphs."""
    previous = None
    while previous != body:
        previous = body
        body = re.sub(
            r"\^\{([^{}]*)\}\s*\^\{([^{}]*)\}", r"^{\1\2}", body
        )
        body = re.sub(
            r"_\{([^{}]*)\}\s*_\{([^{}]*)\}", r"_{\1\2}", body
        )
        body = re.sub(
            r"\^\{([^{}]*)\}\s*\^([A-Za-z0-9])", r"^{\1\2}", body
        )
        body = re.sub(
            r"_\{([^{}]*)\}\s*_([A-Za-z0-9])", r"_{\1\2}", body
        )
        body = re.sub(
            r"\^([A-Za-z0-9])\s*\^\{([^{}]*)\}", r"^{\1\2}", body
        )
        body = re.sub(
            r"_([A-Za-z0-9])\s*_\{([^{}]*)\}", r"_{\1\2}", body
        )
    return body


def _separate_scripts(body: str) -> str:
    """Put an empty group between scripts that would otherwise double up."""
    pattern = re.compile(
        r"(\^\{[^{}]*\}|_\{[^{}]*\}|\^[A-Za-z0-9]|_[A-Za-z0-9])(\s*)(?=[\^_])"
    )
    previous = None
    while previous != body:
        previous = body
        body = pattern.sub(r"\1\2{}", body)
    return body


def _sanitize_script_body(body: str) -> str:
    body = body.replace("\\", r"\backslash ")
    body = re.sub(r"([{}%#&$])", r"\\\1", body)
    body = body.replace("_", r"\_")
    return body.replace("^", r"\wedge ")


def normalize_math_body(body: str) -> str:
    """Normalize notation inside an existing LaTeX span."""
    body = _normalize_super_sub(body)
    body = re.sub(r"\u221a\s*\(([^()]*)\)", r"\\sqrt{\1}", body)
    body = re.sub(r"\u221a\s*([0-9A-Za-z]+(?:\.[0-9]+)?)", r"\\sqrt{\1}", body)
    body = body.replace("\u221a", r"\sqrt{}")
    body = _symbols_to_tex(body)
    body = _merge_scripts(body.replace("\u2212", "-"))
    return _separate_scripts(body)


def _prepare_plain(text: str) -> str:
    text = _normalize_super_sub(text)
    # Keep hyphens inside prose words from being read as minus signs.
    text = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", "\u2010", text)
    text = re.sub(
        r"(\d+(?:\.\d+)?)\s*[x\u00d7]\s*10\s*(?:\^|\s)*\{?\s*([+-]?\s*\d+)\s*\}?",
        lambda match: f"{match.group(1)}\u00d710^{{{match.group(2).replace(' ', '')}}}",
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9])10\s*([+-\u2212]\s*\d+)(?=\s*(?:[A-Za-z\u00b5\u03bc\u03a9%]|/|$))",
        lambda match: f"10^{{{match.group(1).replace(' ', '').replace(chr(0x2212), '-')}}}",
        text,
    )
    text = re.sub(r"\u221a\s*\(([^()]*)\)", r"\\sqrt{\1}", text)
    text = re.sub(r"\u221a\s*([0-9A-Za-z]+(?:\.[0-9]+)?)", r"\\sqrt{\1}", text)
    return text.replace("\u221a", r"\sqrt{}")


def _convert_tokens(tokens: list[str]) -> str:
    out: list[str] = []
    for token in tokens:
        if token.isspace():
            out.append(" ")
        elif token.startswith(("^{", "_{")) and token.endswith("}"):
            inner = _sanitize_script_body(token[2:-1])
            out.append(token[:2] + inner + "}")
        elif len(token) > 1 and token[0] in "^_":
            out.append(token[0] + _sanitize_script_body(token[1:]))
        elif token in {"^", "_"}:
            out.append("\\" + token)
        elif token in FUNCTIONS:
            out.append(FUNCTIONS[token] + " ")
        elif re.fullmatch(r"\d+(?:\.\d+)?", token):
            out.append(token)
        elif any(character in MATH_CHARS for character in token):
            out.append(_symbols_to_tex(token))
        elif token == "{":
            out.append(r"\{")
        elif token == "}":
            out.append(r"\}")
        elif token in {"%", "#", "&", "$"}:
            out.append("\\" + token)
        elif token == "_":
            out.append(r"\_")
        elif token == "\\":
            # Stray backslashes from broken PDF text layers are not TeX.
            out.append(r"\backslash ")
        else:
            out.append(token)
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _strong_math_context(token: str | None) -> bool:
    if token is None:
        return False
    if token.isdigit() or token in OPS or token.startswith(("^", "_")):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return True
    if HYPHEN_RE.fullmatch(token):
        return False
    return False


def _is_seed(token: str) -> bool:
    if token.startswith(("^{", "_{")) or (
        len(token) > 1 and token[0] in "^_"
    ):
        return True
    if token.startswith(("\\sqrt", "\\frac")):
        return True
    if any(character in MATH_CHARS for character in token):
        return True
    if token in FUNCTIONS or token in MATH_WORDS:
        return True
    if token.isalpha() and len(token) == 1 and token not in {"a", "A", "I", "i"}:
        return True
    return False


def _chunk_to_latex(chunk: str) -> str:
    tokens = TOKEN_RE.findall(chunk)
    count = len(tokens)
    is_math = [False] * count
    seed = [False] * count

    for index, token in enumerate(tokens):
        if token.isspace():
            continue
        if token.startswith("_") and len(token) > 1 and index:
            previous = tokens[index - 1]
            if previous[-1:].isalpha():
                # ``file_path`` / ``single_alphabet``: an identifier underscore,
                # not a subscript.
                continue
        if _is_seed(token):
            is_math[index] = True
            seed[index] = True

    changed = True
    while changed:
        changed = False
        for index, token in enumerate(tokens):
            if is_math[index] or token.isspace():
                continue
            previous = next(
                (is_math[i] for i in range(index - 1, -1, -1) if not tokens[i].isspace()),
                False,
            )
            following = next(
                (is_math[i] for i in range(index + 1, count) if not tokens[i].isspace()),
                False,
            )
            if not (previous or following):
                continue
            previous_token = next(
                (tokens[i] for i in range(index - 1, -1, -1) if not tokens[i].isspace()),
                None,
            )
            next_token = next(
                (tokens[i] for i in range(index + 1, count) if not tokens[i].isspace()),
                None,
            )
            if token in OPS:
                is_math[index] = True
                changed = True
            elif re.fullmatch(r"\d+(?:\.\d+)?", token):
                # A bare number belongs to the formula only when it is attached
                # to an operator or directly to a symbol; this keeps marks
                # columns and enumeration numbers out of the math span.
                if previous_token in OPS or next_token in OPS:
                    is_math[index] = True
                    changed = True
                else:
                    before = tokens[index - 1] if index else ""
                    after = tokens[index + 1] if index + 1 < count else ""
                    if (before and not before.isspace() and previous) or (
                        after and not after.isspace() and following
                    ):
                        is_math[index] = True
                        changed = True
            elif re.fullmatch(r"[A-Za-z]{2,3}", token) and token.lower() not in STOPWORDS:
                is_math[index] = True
                changed = True
            elif token in {"a", "A", "I", "i"}:
                if _strong_math_context(previous_token) or _strong_math_context(next_token):
                    is_math[index] = True
                    changed = True

    # Keep only runs that contain at least one real math seed.
    index = 0
    while index < count:
        if not is_math[index]:
            index += 1
            continue
        cursor = index
        last = index
        while cursor < count:
            if is_math[cursor]:
                last = cursor
                cursor += 1
            elif tokens[cursor].isspace():
                cursor += 1
            else:
                break
        if not any(seed[i] for i in range(index, last + 1)):
            for i in range(index, last + 1):
                is_math[i] = False
        index = last + 1

    for index, token in enumerate(tokens):
        if not (token.isalpha() and len(token) == 1 and is_math[index]):
            continue
        previous = next(
            (is_math[i] for i in range(index - 1, -1, -1) if not tokens[i].isspace()),
            False,
        )
        following = next(
            (is_math[i] for i in range(index + 1, count) if not tokens[i].isspace()),
            False,
        )
        if token in {"A", "I", "a", "i"}:
            if not (previous or following):
                is_math[index] = False
        elif not token.isupper() and not (previous or following):
            is_math[index] = False

    out: list[str] = []
    index = 0
    while index < count:
        if not is_math[index]:
            plain = tokens[index].replace("\u2010", "-")
            if plain == "$":
                out.append(r"\$")
            else:
                out.append(_symbols_to_tex(plain) if plain in MATH_CHARS else plain)
            index += 1
            continue

        cursor = index
        last_math = index
        while cursor < count:
            if is_math[cursor]:
                last_math = cursor
                cursor += 1
            elif tokens[cursor].isspace():
                cursor += 1
            else:
                break

        run = tokens[index:last_math + 1]
        leading: list[str] = []
        while run and (run[0].isspace() or run[0] in ",.;:"):
            leading.append(run.pop(0))
        if leading:
            out.extend(leading)
        trailing: list[str] = []
        while run and (run[-1].isspace() or run[-1] in ".,;:"):
            if run[-1] in ".,;:":
                trailing.append(run[-1])
            run.pop()
        if not run:
            out.append(tokens[index])
            index += 1
            continue
        body = _merge_scripts(_convert_tokens(run).replace("\u2010", "-"))
        body = _separate_scripts(body)
        if body:
            out.append("$" + body + "$")
        out.extend(reversed(trailing))
        index = last_math + 1

    return "".join(out)


def _text_to_latex(text: str) -> str:
    lines: list[str] = []
    for line in text.split("\n"):
        prefix = ""
        label = LABEL_RE.match(line)
        if label:
            prefix = line[: label.end()]
            line = line[label.end():]
        parts = re.split(r"([ \t]{2,})", line)
        rendered: list[str] = []
        for position, part in enumerate(parts):
            if position % 2:
                rendered.append(part)
            else:
                rendered.append(_chunk_to_latex(part))
        lines.append(prefix + "".join(rendered))
    return "\n".join(lines)


def _segment_to_latex(segment: str) -> str:
    pieces: list[tuple[str, str]] = []
    index = 0
    # Extracted PDF text never contains ``$`` delimiters: a dollar sign in a
    # paper is a currency symbol or part of a shell command.  Only the escaping
    # produced by this module (\(...\) and \[...\]) marks real maths.
    for match in re.finditer(r"\\\[.*?\\\]|\\\(.*?\\\)", segment, re.S):
        if match.start() > index:
            pieces.append(("text", segment[index:match.start()]))
        pieces.append(("tex", match.group(0)))
        index = match.end()
    if index < len(segment):
        pieces.append(("text", segment[index:]))

    out: list[str] = []
    for kind, piece in pieces:
        if kind == "tex":
            if piece.startswith("$$"):
                out.append("$$" + normalize_math_body(piece[2:-2]) + "$$")
            elif piece.startswith("\\["):
                out.append("\\[" + normalize_math_body(piece[2:-2]) + "\\]")
            elif piece.startswith("\\("):
                out.append("\\(" + normalize_math_body(piece[2:-2]) + "\\)")
            else:
                out.append("$" + normalize_math_body(piece[1:-1]) + "$")
        else:
            out.append(_text_to_latex(_prepare_plain(piece)))
    return "".join(out)


def latexify(text: str) -> str:
    """Wrap the mathematical runs in *text* with ``$...$`` and map to LaTeX."""
    if not text:
        return ""
    return _segment_to_latex(text)
