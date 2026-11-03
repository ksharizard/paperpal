"""Append periodic progress snapshots for the resumable paper scrape."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "papers" / "scraped-manifest.json"
LOG = ROOT / "papers" / "scrape-progress.log"


def snapshot() -> tuple[str, bool]:
    if not MANIFEST.exists():
        return "manifest not created yet", False
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        records = payload.get("papers", [])
        counts = Counter(record.get("status") for record in records)
        total = payload.get("paperCount", 0)
        pdfs = sum(1 for path in (ROOT / "papers").rglob("*.pdf"))
        line = (
            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"records={len(records)}/{total} pdfs={pdfs} "
            f"downloaded={counts['downloaded']} existing={counts['existing']} "
            f"duplicate={counts['duplicate']} failed={counts['failed']}"
        )
        return line, len(records) >= total > 0
    except (OSError, ValueError) as error:
        return f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} error={error}", False


LOG.parent.mkdir(parents=True, exist_ok=True)
with LOG.open("a", encoding="utf-8") as handle:
    handle.write("\n--- scrape monitor started ---\n")
    while True:
        line, complete = snapshot()
        handle.write(line + "\n")
        handle.flush()
        if complete:
            break
        time.sleep(30)
