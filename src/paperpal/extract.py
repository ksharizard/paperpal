"""Extract raw text and embedded images from ``papers/`` for review and curation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pypdf import PdfReader

from .mathtext import extract_rich_text

ROOT = Path(__file__).resolve().parents[2]
PAPERS_DIR = ROOT / "papers"
BUILD_DIR = ROOT / "build" / "extracted"
IMAGE_DIR = ROOT / "build" / "pdf-images"
PAGE_BREAK = "\n\n=== PAGE BREAK ===\n\n"


def safe_slug(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-")


def safe_name(path: Path) -> str:
    relative = path.relative_to(PAPERS_DIR).as_posix()
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:10]
    return f"{safe_slug(path)}-{digest}.txt"


QUESTION_LABEL_RE = re.compile(
    r"^\s*(?:Q(?:uestion)?\s*\.?\s*)?(?P<number>\d{1,2})\s*(?P<letter>[A-Za-z])?"
    r"\s*(?:[.)\]:-]|\s{2,})\s*(?P<text>.*)$",
    re.IGNORECASE,
)
QUESTION_MARKS_RE = re.compile(
    r"(?:\(|\[)?\s*\d{1,2}\s*marks?\s*(?:\)|\])?"
    r"|\(\s*\d+(?:\s*\+\s*\d+)*\s*\)"
    r"|\[\s*\d+\s*\]",
    re.IGNORECASE,
)


def _question_labels(text: str) -> list[str]:
    """Return the canonical question labels that exist in the text layer."""
    labels: list[str] = []
    for line in text.splitlines():
        match = QUESTION_LABEL_RE.match(line)
        if not match:
            continue
        number = int(match.group("number"))
        if not 1 <= number <= 20:
            continue
        letter = (match.group("letter") or "").upper()
        labels.append(f"{number}{letter}")
    return labels


def _question_label_stats(text: str) -> tuple[int, int]:
    """Return (blank labels, total labels) for question-shaped lines."""
    blank = 0
    total = 0
    for line in text.splitlines():
        match = QUESTION_LABEL_RE.match(line)
        if not match:
            continue
        number = int(match.group("number"))
        if not 1 <= number <= 20:
            continue
        rest = QUESTION_MARKS_RE.sub(" ", match.group("text")).strip(" .():;-")
        total += 1
        if len(rest) < 12:
            blank += 1
    return blank, total


OCR_LABEL_RE = re.compile(
    r"^\s*(?P<token>[^\s)\].:-]{1,3})\s*[)\].:-]\s*(?P<rest>.*)$"
)
_LABEL_GLYPHS = str.maketrans({"©": "C", "¢": "C", "℃": "C", "Ⅽ": "C"})
_TAIL_LETTERS = {
    "0": "C", "8": "B", "6": "C", "4": "A",
    "X": "C", "O": "C", "Q": "C",
}
_LEADING_DIGITS = {"S": "5", "s": "5", "l": "1", "I": "1", "i": "1"}


def _label_candidates(token: str) -> set[str]:
    """Return possible canonical labels for a mangled OCR label token."""
    cleaned = token.strip("'\"`‘’º°oO. ")
    cleaned = cleaned.translate(_LABEL_GLYPHS).upper()
    if len(cleaned) > 1 and cleaned[0] in _LEADING_DIGITS:
        cleaned = _LEADING_DIGITS[cleaned[0]] + cleaned[1:]
    candidates: set[str] = set()
    if re.fullmatch(r"\d{1,2}", cleaned):
        candidates.add(cleaned)
        if len(cleaned) == 2 and cleaned[1] in _TAIL_LETTERS:
            # ``20`` and ``58`` are usually 2C and 5B, not question 20/58.
            candidates.add(cleaned[0] + _TAIL_LETTERS[cleaned[1]])
        return candidates
    match = re.fullmatch(r"(\d{0,2})([A-Z])", cleaned)
    if not match:
        return candidates
    number, letter = match.groups()
    if letter in _TAIL_LETTERS:
        letter = _TAIL_LETTERS[letter]
    candidates.add(letter)
    if number:
        candidates.add(f"{number}{letter}")
    return candidates


def _labels_match(candidate: str, expected: str) -> bool:
    """Match a label candidate, tolerating a lost number or letter."""
    if not candidate:
        return False
    if candidate == expected:
        return True
    if len(candidate) == 1 and candidate.isalpha():
        return expected.endswith(candidate)
    if len(candidate) == 1 and candidate.isdigit():
        return expected.startswith(candidate)
    return False


def _repair_ocr_labels(ocr: str, expected: list[str]) -> str:
    """Replace mangled OCR labels with the labels seen in the text layer.

    Tesseract routinely reads ``1C)`` as ``'©)`` or ``5B)`` as ``58)``.  The
    text layer keeps the question skeleton even when the body is an image, so
    align OCR lines to that sequence and rewrite the prefix.  Look ahead a few
    labels so one missed label does not shift every later question.  Unmatched
    lines are left alone; bare sub-item numbers are wrapped so they do not
    become fake questions.
    """
    lines = ocr.splitlines()
    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            re.fullmatch(r"\s*[^\s]{1,3}\s*", line)
            and index + 1 < len(lines)
            and re.match(r"\s*[)\].:-]", lines[index + 1])
        ):
            merged.append(f"{line.strip()} {lines[index + 1].strip()}")
            index += 2
            continue
        merged.append(line)
        index += 1

    output: list[str] = []
    position = 0
    for line in merged:
        match = OCR_LABEL_RE.match(line)
        if match and position < len(expected):
            candidates = _label_candidates(match.group("token"))
            matched: int | None = None
            for offset in range(min(6, len(expected) - position)):
                if any(
                    _labels_match(candidate, expected[position + offset])
                    for candidate in candidates
                ):
                    matched = position + offset
                    break
            if matched is not None:
                output.append(f"{expected[matched]}) {match.group('rest')}")
                position = matched + 1
                continue
            token = match.group("token")
            if token.isdigit():
                output.append(f"({token}) {match.group('rest')}")
                continue
        output.append(line)
    return "\n".join(output)


def _needs_ocr(text: str) -> bool:
    """Detect papers whose questions exist only as embedded equation images.

    wkhtmltopdf-generated papers often keep the prose in the text layer but
    render every formula as an image.  The question label survives and the body
    disappears, which is what a run of blank labels looks like.
    """
    blank, total = _question_label_stats(text)
    return total >= 3 and blank >= 3 and blank / total >= 0.25


def _ocr_text_is_better(ocr: str, text: str) -> bool:
    ocr_blank, ocr_total = _question_label_stats(ocr)
    old_blank, old_total = _question_label_stats(text)
    if ocr_total < 2:
        return False
    old_ratio = old_blank / old_total if old_total else 0.0
    ocr_ratio = ocr_blank / ocr_total
    return ocr_ratio <= old_ratio - 0.15 or len(ocr) >= 1.35 * len(text)


def ocr_pdf_text(path: Path, expected_labels: list[str] | None = None) -> str | None:
    """Render pages and OCR them when the text layer is missing the maths.

    Returns ``None`` when the OCR toolchain is unavailable or fails, so the
    caller can keep the original text layer.
    """
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not pdftoppm or not tesseract:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="paperpal-ocr-") as directory:
            target = Path(directory) / "page"
            render = subprocess.run(
                [pdftoppm, "-r", "300", "-png", str(path), str(target)],
                check=False,
                capture_output=True,
                timeout=300,
            )
            if render.returncode != 0:
                return None
            pages = sorted(Path(directory).glob("page-*.png"))
            output: list[str] = []
            # The extractor already runs several papers in parallel; keep each
            # Tesseract process single-threaded instead of oversubscribing.
            environment = dict(os.environ, OMP_THREAD_LIMIT="1")
            for image in pages:
                result = subprocess.run(
                    [
                        tesseract,
                        str(image),
                        "stdout",
                        "--psm",
                        "6",
                        "-l",
                        "eng",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    env=environment,
                )
                if result.returncode == 0 and result.stdout.strip():
                    output.append(result.stdout.strip())
            if not output:
                return None
            text = PAGE_BREAK.join(output)
            if expected_labels:
                text = _repair_ocr_labels(text, expected_labels)
            return text
    except (OSError, subprocess.TimeoutExpired):
        return None


def _extract_layout_text(path: Path) -> str | None:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return None
    try:
        result = subprocess.run(
            [pdftotext, "-layout", "-enc", "UTF-8", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if result.returncode == 0 and result.stdout.strip():
            pages = result.stdout.replace("\r", "").split("\f")
            return PAGE_BREAK.join(page.strip() for page in pages if page.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _extract_pypdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text(extraction_mode="layout") or "")
        except TypeError:
            pages.append(page.extract_text() or "")
    return PAGE_BREAK.join(page.strip() for page in pages if page.strip())


def extract_pdf_text(path: Path) -> str:
    """Extract text, keeping superscripts and subscripts where possible.

    Poppler's XML mode preserves the position of every text fragment, so
    ``R^4``, ``10^-24``, simple fractions and matrices can be rebuilt instead of
    flattened.  Papers without meaningful math fall back to ``pdftotext``
    layout mode, which handles columns and tables more cleanly.

    Some papers keep only prose in the text layer and draw every equation as an
    image.  When that leaves a run of question labels with no body, OCR the
    rendered pages instead.
    """
    text = extract_rich_text(path)
    if not text:
        text = _extract_layout_text(path)
    if not text:
        text = _extract_pypdf_text(path)
    if text and _needs_ocr(text):
        ocr = ocr_pdf_text(path, _question_labels(text))
        if ocr and _ocr_text_is_better(ocr, text):
            return ocr
    return text


def _extract_record(path: Path) -> dict:
    try:
        text = extract_pdf_text(path)
    except Exception as error:  # noqa: BLE001 - report and continue
        return {"file": str(path.relative_to(ROOT)), "error": str(error)}

    target = BUILD_DIR / safe_name(path)
    target.write_text(text, encoding="utf-8")
    return {
        "file": str(path.relative_to(ROOT)),
        "text": str(target.relative_to(ROOT)),
        "pages": text.count(PAGE_BREAK) + 1 if text else 0,
        "characters": len(text),
        "preview": " ".join(text.split(PAGE_BREAK, 1)[0].split())[:160],
    }


def extract_papers():
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    paths = sorted(PAPERS_DIR.rglob("*.pdf"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        index = list(pool.map(_extract_record, paths))

    with (BUILD_DIR / "index.json").open("w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    return index


def print_extract(index):
    for entry in index:
        if "error" in entry:
            print(f"  ! {entry['file']}: {entry['error']}")
            continue
        print(f"  {entry['pages']:>2}p  {entry['characters']:>6} chars  {entry['file']}")
    print(f"\nWrote {len(index)} files to {BUILD_DIR.relative_to(ROOT)}/")


def extract_images(min_width: int = 80, min_height: int = 60):
    """Save embedded raster images that are large enough to be real diagrams.

    The output is a review folder, not the live web assets: only curated figures
    should be copied to ``web/assets/figures`` and referenced from question JSON.
    Duplicate images (logos, repeated crests) are written once.
    """
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    seen = set()

    for path in sorted(PAPERS_DIR.rglob("*.pdf")):
        try:
            reader = PdfReader(str(path))
        except Exception as error:  # noqa: BLE001 - report and continue
            index.append({"file": str(path.relative_to(ROOT)), "error": str(error)})
            continue

        slug = safe_slug(path)
        for page_number, page in enumerate(reader.pages, 1):
            try:
                images = list(page.images)
            except Exception as error:  # noqa: BLE001 - report and continue
                index.append(
                    {
                        "file": str(path.relative_to(ROOT)),
                        "page": page_number,
                        "error": str(error),
                    }
                )
                continue

            for image in images:
                try:
                    bitmap = image.image
                    width, height = bitmap.size
                except Exception as error:  # noqa: BLE001 - report and continue
                    index.append(
                        {
                            "file": str(path.relative_to(ROOT)),
                            "page": page_number,
                            "source": image.name,
                            "error": str(error),
                        }
                    )
                    continue
                if width < min_width or height < min_height:
                    continue

                data = getattr(image, "data", b"")
                digest = hashlib.sha1(data).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)

                target_dir = IMAGE_DIR / slug
                target_dir.mkdir(parents=True, exist_ok=True)
                stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(image.name).stem)
                target = target_dir / f"p{page_number}_{stem}.png"
                counter = 2
                while target.exists():
                    target = target_dir / f"p{page_number}_{stem}_{counter}.png"
                    counter += 1

                if bitmap.mode not in {"RGB", "RGBA", "L"}:
                    bitmap = bitmap.convert("RGBA")
                bitmap.save(target, optimize=True)
                index.append(
                    {
                        "file": str(path.relative_to(ROOT)),
                        "page": page_number,
                        "source": image.name,
                        "width": width,
                        "height": height,
                        "image": str(target.relative_to(ROOT)),
                    }
                )

    with (IMAGE_DIR / "index.json").open("w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    return index


def print_images(index):
    saved = [entry for entry in index if "image" in entry]
    for entry in index:
        if "error" in entry:
            print(f"  ! {entry['file']} p{entry.get('page', '?')}: {entry['error']}")
            continue
        if "image" not in entry:
            continue
        print(
            f"  p{entry['page']:>2}  {entry['width']:>4}x{entry['height']:<4}  "
            f"{entry['image']}"
        )
    print(f"\nWrote {len(saved)} images to {IMAGE_DIR.relative_to(ROOT)}/")
    print("Review them and copy the useful diagrams into web/assets/figures/.")
