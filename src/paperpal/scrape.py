"""Download question papers from pyq.arzvak.com.

The public site exposes a small JSON API backed by a paper mirror. This module
fetches the full category catalogue (or selected semesters), decides a readable subject/year/exam path,
downloads each PDF once, and records everything in a manifest next to the files.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
PAPERS_DIR = ROOT / "papers"
API_BASE = "https://pyq.arzvak.com/api"
SITE_BASE = "https://pyq.arzvak.com"
MANIFEST_NAME = "scraped-manifest.json"
USER_AGENT = "PaperPal/1.0 (question-bank archiver)"

SEMESTERS = ("Semester 1", "Semester 2")

MARKERS = re.compile(
    r"\b(RCS|Regular|Make\s*-?\s*up|Makeup|Revised|New Syllabus|Old Syllabus|"
    r"Elective\s*-?\s*I+|Scheme)\b",
    re.I,
)
STANDALONE_CODE = re.compile(r"\b[A-Z]{2,4}\s*[-–]?\s*\d[\d.\-–]*\b")


def run_curl(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["curl", "-sS", "--fail", "--max-time", str(timeout), "-A", USER_AGENT, *args],
        capture_output=True,
        text=True,
    )


def fetch_json(url: str, timeout: int = 60, retries: int = 3):
    last_error = ""
    for attempt in range(retries):
        result = run_curl([url], timeout=timeout)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except ValueError as error:
                last_error = f"invalid JSON from {url}: {error}"
        else:
            last_error = result.stderr.strip() or f"curl failed for {url}"
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(
        f"Unable to reach the scrape service at {url}: {last_error}. "
        "Check your internet/DNS connection and try again."
    )


def fetch_categories(api_base: str = API_BASE) -> list[str]:
    """Every category name known to the site, from the public filters endpoint."""
    payload = fetch_json(f"{api_base}/filters")
    categories = (payload.get("data") or {}).get("categories") or []
    return [item["name"] for item in categories if item.get("name")]


def fetch_catalogue(
    semesters=None,
    limit: int = 100,
    api_base: str = API_BASE,
    categories=None,
):
    """Fetch every paper for the requested partitions, keyed by paper id.

    Passing ``categories`` walks the whole archive one category at a time,
    which keeps offset pagination stable. Otherwise each semester is fetched.
    """
    if categories:
        partitions = [("category", category) for category in categories]
    elif semesters:
        partitions = [("semester", semester) for semester in semesters]
    else:
        partitions = [("category", category) for category in fetch_categories(api_base)]

    rows = {}
    for key, value in partitions:
        offset = 0
        fetched = 0
        while True:
            query = urlencode({key: value, "limit": limit, "offset": offset})
            payload = fetch_json(f"{api_base}/papers?{query}")
            data = payload.get("data") or []
            for paper in data:
                rows[paper["id"]] = paper
            fetched += len(data)
            if len(data) < limit:
                break
            offset += limit
        print(f"  {value}: {fetched} records", flush=True)
    return list(rows.values())


def clean_subject(paper: dict) -> str:
    raw = paper.get("subject_name") or paper.get("title") or "Other"
    subject = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", raw)
    subject = MARKERS.sub(" ", subject)
    subject = STANDALONE_CODE.sub(" ", subject)
    subject = re.sub(r"\s+", " ", subject).strip(" -_–—")
    return subject or str(raw)


def slugify(value: str, fallback: str = "other") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def exam_slug(paper: dict) -> str:
    text = f"{paper.get('title', '')} {paper.get('exam_type', '')}".lower()
    if re.search(r"make\s*-?\s*up|makeup", text):
        return "makeup"
    return "regular"


def safe_filename(value: str, max_length: int = 150) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "-", value)
    name = re.sub(r"\s+", " ", name).strip(" .-_")
    if name.lower().endswith(".pdf"):
        name = name[:-4].rstrip(" .-_")
    return (name or "paper")[:max_length].rstrip(" .-_")


def remote_url(paper: dict) -> str:
    url = paper.get("storage_url") or paper.get("pdf_url") or ""
    if url.startswith("/"):
        return SITE_BASE + url
    return url


def target_for(paper: dict, out_dir: Path, taken: set[Path] | None = None) -> Path:
    subject = slugify(clean_subject(paper), fallback=slugify(paper.get("category") or "other"))
    year = str(paper.get("year") or "unknown")
    exam = exam_slug(paper)
    filename = safe_filename(paper.get("title") or paper.get("subject_name") or paper["id"])
    target = out_dir / subject / year / exam / f"{filename}.pdf"

    if taken is not None:
        if target in taken:
            target = target.with_name(f"{target.stem}-{paper['id'][:8]}.pdf")
        taken.add(target)
    return target


def download_pdf(url: str, target: Path, timeout: int = 120) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=target.name + ".", suffix=".part", delete=False
    ) as handle:
        temporary = Path(handle.name)

    try:
        result = run_curl(
            ["-L", "--retry", "3", "--retry-delay", "2", "--retry-all-errors", "-o", str(temporary), url],
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"curl exit {result.returncode}")
        with temporary.open("rb") as handle:
            if handle.read(4) != b"%PDF":
                raise RuntimeError("downloaded file is not a PDF")
        size = temporary.stat().st_size
        temporary.replace(target)
        return size
    finally:
        if temporary.exists():
            temporary.unlink()


def load_manifest(manifest_path: Path) -> dict[str, dict]:
    """Index a previous scrape's records by mirror URL.

    Used to make reruns idempotent: a paper whose mirror URL was already
    downloaded (and whose file still exists) is never requested again.
    """
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    index = {}
    for record in payload.get("papers") or []:
        url = record.get("storageUrl")
        if url:
            index[url] = record
    return index


def existing_hashes(
    out_dir: Path, manifest_path: Path, previous: dict[str, dict] | None = None
) -> dict[str, str]:
    """Map SHA-1 -> file (relative to out_dir) for PDFs already in the archive.

    Hashes recorded in the manifest are trusted so a rerun does not have to read
    every downloaded file again; only untracked PDFs are hashed on disk.
    """
    import hashlib

    index: dict[str, str] = {}
    tracked: set[Path] = set()

    for record in (previous or {}).values():
        digest = record.get("sha1")
        relative = record.get("file")
        if not digest or not relative:
            continue
        path = out_dir / relative
        if path.is_file():
            index.setdefault(digest, relative)
            tracked.add(path.resolve())

    for path in out_dir.rglob("*.pdf"):
        if path.resolve() in tracked:
            continue
        try:
            index.setdefault(hashlib.sha1(path.read_bytes()).hexdigest(), str(path.relative_to(out_dir)))
        except OSError:
            continue
    return index


def scrape_papers(
    out_dir: Path = PAPERS_DIR,
    semesters=None,
    workers: int = 4,
    limit: int = 0,
    dry_run: bool = False,
    overwrite: bool = False,
    api_base: str = API_BASE,
):
    out_dir = Path(out_dir)
    manifest_path = out_dir / MANIFEST_NAME
    papers = fetch_catalogue(semesters=semesters, api_base=api_base)
    manifest_semesters = list(semesters) if semesters else ["all categories"]

    # The catalogue contains a handful of exact mirror duplicates.
    unique = []
    seen_urls = set()
    for paper in papers:
        url = remote_url(paper)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append(paper)
    papers = unique
    if limit:
        papers = papers[:limit]

    print(f"Preparing {len(papers)} unique papers in {out_dir}")

    previous = {} if overwrite else load_manifest(manifest_path)
    reused = 0
    taken: set[Path] = set()
    planned = []
    for paper in papers:
        url = remote_url(paper)
        target = target_for(paper, out_dir, taken=taken)
        prior = previous.get(url)
        if prior is not None:
            relative = prior.get("file") or ""
            exists = bool(relative) and (out_dir / relative).is_file()
            duplicate_of = prior.get("duplicateOf") or ""
            duplicate_ok = bool(duplicate_of) and (out_dir / duplicate_of).is_file()
            if exists or (prior.get("status") == "duplicate" and duplicate_ok):
                # Already scraped by an earlier run: reuse the record, no request.
                planned.append((paper, url, target, prior))
                reused += 1
                continue
            if target.is_file():
                planned.append((paper, url, target, None))
                reused += 1
                continue
        planned.append((paper, url, target, None))

    if dry_run:
        for paper, _, target, prior in planned:
            status = "skip" if prior else "get"
            print(f"  {status:4}  {target.relative_to(out_dir)}")
        return {
            "paperCount": len(planned),
            "downloaded": 0,
            "skipped": reused,
            "failed": 0,
            "dryRun": True,
            "manifest": None,
        }

    manifest = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SITE_BASE,
        "semesters": manifest_semesters,
        "paperCount": len(planned),
        "papers": [],
        "failures": [],
    }

    known_hashes = existing_hashes(out_dir, manifest_path, previous)
    hash_lock = threading.Lock()
    counts = {"downloaded": 0, "skipped": 0, "failed": 0}
    completed = 0

    def work(item):
        paper, url, target, prior = item
        if prior is not None:
            record = dict(prior)
            record["status"] = "existing"
            path = out_dir / (record.get("file") or "")
            if record.get("size") is None and path.is_file():
                record["size"] = path.stat().st_size
            return record

        record = {
            "id": paper["id"],
            "subject": clean_subject(paper),
            "title": paper.get("title"),
            "year": paper.get("year"),
            "semester": paper.get("semester"),
            "examType": exam_slug(paper),
            "category": paper.get("category"),
            "branch": paper.get("branch"),
            "subjectCode": paper.get("subject_code") or paper.get("derived_subject_code"),
            "sourceUrl": paper.get("pdf_url"),
            "storageUrl": url,
            "file": str(target.relative_to(out_dir)),
        }
        if target.exists() and not overwrite:
            record["status"] = "existing"
            record["size"] = target.stat().st_size
            return record

        try:
            size = download_pdf(url, target)
        except Exception as error:  # noqa: BLE001 - keep scraping after a bad file
            record["status"] = "failed"
            record["error"] = str(error)
            return record

        import hashlib

        digest = hashlib.sha1(target.read_bytes()).hexdigest()
        with hash_lock:
            duplicate_of = known_hashes.get(digest)
            if duplicate_of:
                target.unlink(missing_ok=True)
                record["status"] = "duplicate"
                record["duplicateOf"] = duplicate_of
                return record
            known_hashes[digest] = str(target.relative_to(out_dir))
        record["status"] = "downloaded"
        record["size"] = size
        record["sha1"] = digest
        return record

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, item): item for item in planned}
        for future in as_completed(futures):
            record = future.result()
            manifest["papers"].append(record)
            completed += 1
            status = record["status"]
            if status == "failed":
                counts["failed"] += 1
                manifest["failures"].append(record)
            elif status in {"existing", "duplicate"}:
                counts["skipped"] += 1
            else:
                counts["downloaded"] += 1

            if completed % 25 == 0 or completed == len(planned):
                with manifest_path.open("w", encoding="utf-8") as handle:
                    json.dump(manifest, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                print(
                    f"  {completed}/{len(planned)}  "
                    f"downloaded {counts['downloaded']}  "
                    f"skipped {counts['skipped']}  failed {counts['failed']}",
                    flush=True,
                )

    manifest["papers"].sort(key=lambda item: item["file"])
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    return {
        "paperCount": len(planned),
        "downloaded": counts["downloaded"],
        "skipped": counts["skipped"],
        "failed": counts["failed"],
        "dryRun": False,
        "manifest": str(manifest_path.relative_to(ROOT)),
    }


def print_scrape(result) -> None:
    if result["dryRun"]:
        print(f"\nDry run: {result['paperCount']} papers would be downloaded.")
        return
    print(
        f"\nScrape complete: {result['downloaded']} downloaded, "
        f"{result['skipped']} skipped, {result['failed']} failed."
    )
    if result.get("manifest"):
        print(f"Manifest: {result['manifest']}")
