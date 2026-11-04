"""Import readable question blocks from the locally downloaded PDF archive.

These records are deliberately marked as automatic imports.  They preserve the
question text and source paper, but do not invent solutions or subject chapters.
The original PDF remains the authoritative source for context and answers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .extract import extract_pdf_text
from .mathtext import latexify

ROOT = Path(__file__).resolve().parents[2]
PAPERS_DIR = ROOT / "papers"
EXTRACTED_INDEX = ROOT / "build" / "extracted" / "index.json"
AUTO_FILE = ROOT / "data" / "auto-questions.json"
AUTO_VERSION = 9

QUESTION_MARKER = re.compile(
    r"^\s*(?:Q(?:uestion)?\s*\.?\s*)?(?P<number>\d{1,2})"
    r"(?:(?P<letter>[A-Za-z])(?:\s+|(?P<separator1>[.)\]:-])\s*)"
    r"|(?P<separator2>[.)\]:-]|\s{2,})\s*)"
    r"(?P<text>.*)$",
    re.IGNORECASE,
)
MARKS = re.compile(r"(?:\(|\[)?\s*(\d{1,2})\s*marks?\s*(?:\)|\])?", re.IGNORECASE)
ANSWER_FILE = re.compile(r"(?:answer|solution|evaluation|marking|answer-script|scheme)", re.IGNORECASE)

SYMBOL_GLYPHS = str.maketrans(
    {
        "\uf061": "α", "\uf062": "β", "\uf063": "χ", "\uf064": "δ",
        "\uf065": "ε", "\uf066": "φ", "\uf067": "γ", "\uf068": "η",
        "\uf069": "ι", "\uf06b": "κ", "\uf06c": "λ", "\uf06d": "μ",
        "\uf06e": "ν", "\uf06f": "ο", "\uf070": "π", "\uf071": "θ",
        "\uf072": "ρ", "\uf073": "σ", "\uf074": "τ", "\uf075": "υ",
        "\uf077": "ω", "\uf078": "ξ", "\uf079": "ψ", "\uf07a": "ζ",
        "\uf020": " ", "\uf025": "%", "\uf028": "(", "\uf029": ")",
        "\uf02b": "+", "\uf02c": ",", "\uf02d": "−", "\uf02e": ".",
        "\uf02f": "/", "\uf03c": "<", "\uf03d": "=", "\uf03e": ">",
        "\uf05b": "[", "\uf05c": "∴", "\uf05d": "]", "\uf0b1": "±",
        "\uf0b3": "≥", "\uf0b6": "∂", "\uf0b7": "•", "\uf0b9": "≠",
        "\uf0a2": "ℤ", "\uf0a3": "≤", "\uf0a5": "∞", "\uf0ae": "→",
        "\uf0b0": "°", "\uf0b5": "∝", "\uf0d1": "∇", "\uf0df": "→",
        "\uf041": "•", "\uf044": "Δ", "\uf057": "Ω", "\uf04a": " ",
        "\uf0e6": "(", "\uf0e7": "(", "\uf0e8": "(", "\uf0e9": "(",
        "\uf0ea": "[", "\uf0eb": "[", "\uf0f6": ")", "\uf0f7": ")",
        "\uf0f8": ")", "\uf0f9": ")", "\uf0fa": "]", "\uf0fb": "]",
        "\uf0d6": "√", "\uf0d7": "×", "\uf0e5": "∑", "\uf0f2": "∫",
        "⁄": "/",
    }
)


def _source_signature(papers: list[dict]) -> str:
    digest = hashlib.sha1()
    # Re-extracting the PDFs rewrites the extracted index; fold that stamp in so
    # a fresh ``extract`` invalidates the cached automatic questions.
    try:
        stat = EXTRACTED_INDEX.stat()
        digest.update(f"extracted:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
    except OSError:
        digest.update(b"extracted:missing")
    for paper in papers:
        path = ROOT / paper["file"]
        try:
            stat = path.stat()
            stamp = f"{paper['file']}:{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            stamp = f"{paper['file']}:missing"
        digest.update(stamp.encode("utf-8"))
    return digest.hexdigest()


def _load_extracted_index() -> dict[str, str]:
    if not EXTRACTED_INDEX.is_file():
        return {}
    try:
        entries = json.loads(EXTRACTED_INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    usable = [
        entry for entry in entries
        if entry.get("file") and entry.get("text") and "error" not in entry
    ]
    text_counts = Counter(entry["text"] for entry in usable)
    return {
        entry["file"]: entry["text"]
        for entry in usable
        if text_counts[entry["text"]] == 1
    }


def _read_text(path: Path, extracted: str | None) -> str:
    if extracted:
        extracted_path = ROOT / extracted
        if extracted_path.is_file():
            try:
                return extracted_path.read_text(encoding="utf-8")
            except OSError:
                pass
    try:
        return extract_pdf_text(path)
    except Exception:  # noqa: BLE001 - one unreadable PDF must not stop the import
        return ""


def _clean_question(value: str) -> str:
    value = value.replace("=== PAGE BREAK ===", " ")
    value = value.translate(SYMBOL_GLYPHS)
    value = re.sub(r"[\uf000-\uf8ff]", " ", value)
    value = re.sub(
        r"(?P<number>\d+(?:\.\d+)?)\s*[x×]\s*10\s*(?P<exponent>[−–-]?\s*\d+)",
        r"\g<number> × 10^\g<exponent>",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"(?<![A-Za-z])10\s*([−–-]?\s*\d+)(?=\s*(?:[A-Za-zµμΩ]|/|\b))",
        r"10^\1",
        value,
    )
    value = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", " × ", value)
    value = value.replace("−", "-").replace("–", "-")
    value = latexify(value[:6000].rstrip())
    value = re.sub(r"\s+", " ", value).strip()
    return value[:8000]


def _parse_questions(text: str) -> list[tuple[str, str, int]]:
    lines = text.replace("\r", "").splitlines()
    markers = []
    for index, line in enumerate(lines):
        match = QUESTION_MARKER.match(line)
        if not match:
            continue
        number = match.group("number")
        letter = (match.group("letter") or "").upper()
        rest = match.group("text").strip()
        if not rest and index + 1 < len(lines) and lines[index + 1].strip():
            # A few PDFs put the question number on a line by itself.
            rest = lines[index + 1].strip()
        if not rest:
            continue
        markers.append((index, f"{number}{letter}", rest))

    parsed = []
    seen = set()
    for marker_index, (start, qno, first_line) in enumerate(markers):
        end = markers[marker_index + 1][0] if marker_index + 1 < len(markers) else len(lines)
        body = _clean_question(" ".join([first_line, *lines[start + 1:end]]))
        if len(body) < 20 or not re.search(r"[A-Za-z]{3}", body):
            continue
        if re.match(r"^(?:reg\.?\s*no|time|maximum?\s*marks?|instructions?)\b", body, re.IGNORECASE):
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", body.lower()).strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        marks = MARKS.findall(body)
        mark_value = int(marks[-1]) if marks else 1
        parsed.append((qno, body, max(1, min(mark_value, 100))))
    return parsed


def _load_cache(signature: str) -> list[dict] | None:
    if not AUTO_FILE.is_file():
        return None
    try:
        payload = json.loads(AUTO_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    meta = payload.get("meta", {})
    if meta.get("version") != AUTO_VERSION or meta.get("sourceSignature") != signature:
        return None
    questions = payload.get("questions")
    return questions if isinstance(questions, list) else None


def build_auto_questions(archive: dict, progress=None) -> list[dict]:
    """Return automatic question records for archive papers with extractable text."""
    papers = [paper for paper in archive["papers"] if not paper.get("questionCount")]
    signature = _source_signature(papers)
    cached = _load_cache(signature)
    if cached is not None:
        if progress:
            progress(f"Using cached automatic extraction: {len(cached):,} questions from {len(papers):,} papers")
        return cached

    if progress:
        progress(f"Extracting questions from {len(papers):,} local papers")
    extracted_index = _load_extracted_index()
    questions = []
    for index, paper in enumerate(papers, 1):
        path = ROOT / paper["file"]
        if ANSWER_FILE.search(f"{paper['title']} {paper['file']}"):
            continue
        text = _read_text(path, extracted_index.get(paper["file"]))
        for qno, question_text, marks in _parse_questions(text):
            question_id = hashlib.sha1(
                f"{paper['id']}:{qno}:{question_text}".encode("utf-8")
            ).hexdigest()[:16]
            questions.append(
                {
                    "id": f"auto-{question_id}",
                    "subject": paper["subject"],
                    "chapter": "extracted",
                    "topic": paper["subjectName"],
                    "type": "DES",
                    "marks": marks,
                    "question": question_text,
                    "solution": "Automatic import: open the source PDF for the answer and full context.",
                    "papers": [{"id": paper["id"], "qno": qno}],
                    "note": "Automatically extracted from the downloaded paper; text may need minor cleanup.",
                    "autoImported": True,
                }
            )
        if progress and index % 1000 == 0:
            progress(f"  processed {index:,}/{len(papers):,} papers ({len(questions):,} questions)")

    AUTO_FILE.parent.mkdir(exist_ok=True)
    AUTO_FILE.write_text(
        json.dumps(
            {
                "meta": {
                    "version": AUTO_VERSION,
                    "sourceSignature": signature,
                    "sourceCount": len(papers),
                    "questionCount": len(questions),
                },
                "questions": questions,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    if progress:
        progress(f"Saved automatic extraction cache: {len(questions):,} questions")
    return questions
