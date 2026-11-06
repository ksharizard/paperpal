"""Apply the published CSE curriculum to subject semester metadata.

Paper metadata only tells us which semester a past paper belonged to, which is
often several semesters behind the current curriculum.  ``curriculum.json``
records the semester layout from the course-structure PDF; this module matches
those entries to bank subjects so the app can show the subjects a student is
taking now.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CURRICULUM_FILE = ROOT / "data" / "curriculum.json"

# Short tokens such as "OS" or "AI" would match too much; require a real key.
MIN_KEY_LENGTH = 5


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def load_curriculum(path: Path = CURRICULUM_FILE) -> dict | None:
    """Load the curriculum file, or ``None`` when the site has no curriculum."""
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        curriculum = json.load(handle)
    if not isinstance(curriculum, dict) or not isinstance(curriculum.get("semesters"), list):
        raise ValueError(f"{path}: expected a curriculum with a semesters array")
    return curriculum


def semester_lookup(curriculum: dict) -> dict[str, set[int]]:
    """Map every name, code and alias in the curriculum to its semesters."""
    lookup: dict[str, set[int]] = {}
    for semester in curriculum["semesters"]:
        number = int(semester["number"])
        for entry in semester.get("subjects", []):
            keys = [entry.get("code", ""), entry.get("name", ""), *(entry.get("aliases") or [])]
            for key in keys:
                normalized = _normalize(key)
                if len(normalized) >= MIN_KEY_LENGTH:
                    lookup.setdefault(normalized, set()).add(number)
    return lookup


def _subject_keys(subject: dict) -> set[str]:
    keys = {_normalize(subject.get("name", ""))}
    keys.update(_normalize(code) for code in subject.get("codes") or [])
    return {key for key in keys if len(key) >= MIN_KEY_LENGTH}


def apply_curriculum(subjects: list[dict], curriculum: dict | None = None) -> int:
    """Override semester metadata for subjects that appear in the curriculum.

    A subject can match by paper code, display name, or a curriculum alias.
    Matching is exact after normalising punctuation and case; fuzzy matching is
    deliberately avoided so an alias cannot move an unrelated subject into a
    semester.  Returns the number of subjects whose semesters changed.
    """
    curriculum = curriculum if curriculum is not None else load_curriculum()
    if not curriculum:
        return 0

    lookup = semester_lookup(curriculum)
    changed = 0
    for subject in subjects:
        semesters: set[int] = set()
        for key in _subject_keys(subject):
            semesters.update(lookup.get(key, ()))
        if not semesters:
            continue
        ordered = sorted(semesters)
        if subject.get("semesters") != ordered:
            changed += 1
        subject["semesters"] = ordered
        subject["curriculum"] = curriculum["id"]
    return changed
