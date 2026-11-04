"""Extract raw text and embedded images from ``papers/`` for review and curation."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
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


def extract_pdf_text(path: Path) -> str:
    """Extract text, keeping superscripts and subscripts where possible.

    Poppler's XML mode preserves the position of every text fragment, so
    ``R^4``, ``10^-24``, simple fractions and matrices can be rebuilt instead of
    flattened.  Papers without meaningful math fall back to ``pdftotext``
    layout mode, which handles columns and tables more cleanly.
    """
    rich = extract_rich_text(path)
    if rich:
        return rich

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
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

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text(extraction_mode="layout") or "")
        except TypeError:
            pages.append(page.extract_text() or "")
    return PAGE_BREAK.join(page.strip() for page in pages if page.strip())


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
