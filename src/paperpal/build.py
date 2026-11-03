"""Build and validate the PaperPal question bank.

The hand-curated question files in ``data/questions`` are merged with the
paper and subject metadata into a ``data/bank.json`` manifest plus size-limited
question parts under ``data/bank-parts``.  The static frontend loads the smaller
``data/bank-index.json`` and per-subject chunks instead of downloading the full
bank.
"""

from __future__ import annotations

import json
import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from .auto_questions import build_auto_questions

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
QUESTION_DIR = DATA / "questions"
BUILD_DIR = ROOT / "build"
WEB_DIR = ROOT / "web"
BANK_FILE = DATA / "bank.json"
BANK_PART_DIR = DATA / "bank-parts"
BANK_PART_MAX_BYTES = 20 * 1024 * 1024
BANK_INDEX_FILE = DATA / "bank-index.json"
BANK_SUBJECT_DIR = DATA / "bank-subjects"
ARCHIVE_FILE = DATA / "archive.json"
PAPERS_DIR = ROOT / "papers"
MANIFEST_FILE = PAPERS_DIR / "scraped-manifest.json"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}
IMAGE_PLACEMENTS = {"question", "solution"}


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_sources():
    subjects = load_json(DATA / "subjects.json")
    papers = load_json(DATA / "papers.json")
    questions = []
    for path in sorted(QUESTION_DIR.glob("*.json")):
        chunk = load_json(path)
        if not isinstance(chunk, list):
            raise ValueError(f"{path}: expected a JSON array")
        for question in chunk:
            question["_file"] = path.name
        questions.extend(chunk)
    return subjects, papers, questions


def _archive_subject_name(folder: str, manifest_records: list[dict]) -> str:
    """Choose a readable label for a scraped subject folder.

    The source catalogue has inconsistent capitalisation and occasional typos,
    so the most common catalogue label is a better display name than the raw
    slug.  The folder itself remains the stable subject identifier.
    """
    labels = Counter(
        record.get("subject", "").strip()
        for record in manifest_records
        if record.get("subject", "").strip()
    )
    if labels:
        label = labels.most_common(1)[0][0]
        if re.search(r"[A-Za-z]", label):
            return _clean_subject_name(label)
    return _clean_subject_name(re.sub(r"[-_]+", " ", folder).strip().title()) or "Other"


SUBJECT_TYPO_REPLACEMENTS = {
    "aaalysis": "analysis",
    "acaustics": "acoustics",
    "accuisition": "acquisition",
    "adavances": "advances",
    "advabced": "advanced",
    "advacne": "advance",
    "advacned": "advanced",
    "advance": "advanced",
    "aerodyamics": "aerodynamics",
    "agumentaed": "augmented",
    "aircrat": "aircraft",
    "algebera": "algebra",
    "algoritham": "algorithm",
    "andengineering": "and engineering",
    "analysys": "analysis",
    "anylatical": "analytical",
    "apatial": "spatial",
    "apllications": "applications",
    "approch": "approach",
    "architcture": "architecture",
    "architecural": "architectural",
    "architure": "architecture",
    "areodynamics": "aerodynamics",
    "areolasticity": "aeroelasticity",
    "aritficial": "artificial",
    "artificail": "artificial",
    "assesment": "assessment",
    "astronuatical": "astronautical",
    "astronuatics": "astronautics",
    "austronautics": "astronautics",
    "automative": "automotive",
    "avoinics": "avionics",
    "ayalysis": "analysis",
    "beginers": "beginners",
    "bilogicais": "biologicals",
    "bilogy": "biology",
    "biochemidtry": "biochemistry",
    "biochenistry": "biochemistry",
    "biologiocal": "biological",
    "biomaterails": "biomaterials",
    "biomaterilas": "biomaterials",
    "biomedial": "biomedical",
    "bioprocesiing": "bioprocessing",
    "bioremidiation": "bioremediation",
    "biostatics": "biostatistics",
    "buiding": "building",
    "buidling": "building",
    "buidlings": "buildings",
    "buling": "building",
    "caatalysis": "catalysis",
    "ceotechnical": "geotechnical",
    "chasis": "chassis",
    "chemistrt": "chemistry",
    "circcuits": "circuits",
    "combinatiional": "combinational",
    "communicationand": "communication and",
    "comples": "complex",
    "complier": "compiler",
    "compter": "computer",
    "compuetr": "computer",
    "concrteate": "concrete",
    "conrollers": "controllers",
    "conservetion": "conservation",
    "consstruction": "construction",
    "constrcution": "construction",
    "constriuction": "construction",
    "controliers": "controllers",
    "controll": "control",
    "controlle": "controller",
    "controllerss": "controllers",
    "controlstechnology": "controls technology",
    "crashworthness": "crashworthiness",
    "cryphtography": "cryptography",
    "cybetr": "cyber",
    "dalgorithms": "algorithms",
    "datascience": "data science",
    "datastructures": "data structures",
    "dconstruction": "construction",
    "dcontrol": "control",
    "ddigital": "digital",
    "defind": "defined",
    "deigns": "designs",
    "deisgn": "design",
    "delopment": "development",
    "desgin": "design",
    "designand": "design and",
    "desing": "design",
    "destrucive": "destructive",
    "detaection": "detection",
    "detaling": "detailing",
    "devlopmement": "development",
    "dgital": "digital",
    "didgiatl": "digital",
    "digiral": "digital",
    "diognastics": "diagnostics",
    "downsteam": "downstream",
    "dprogramming": "programming",
    "dprotection": "protection",
    "elcetive": "elective",
    "elctronic": "electronic",
    "elctronics": "electronics",
    "electrial": "electrical",
    "electrinic": "electronic",
    "electromeganetic": "electromagnetic",
    "electronoics": "electronic",
    "elelement": "element",
    "eletrical": "electrical",
    "eletronic": "electronic",
    "eletronics": "electronics",
    "eelective": "elective",
    "elments": "elements",
    "embded": "embedded",
    "embeded": "embedded",
    "enggineering": "engineering",
    "engineeering": "engineering",
    "engineeringthermodynamics": "engineering thermodynamics",
    "enterpreneurship": "entrepreneurship",
    "entreprenueusrhisp": "entrepreneurship",
    "enviorment": "environment",
    "enviornmental": "environmental",
    "enviornment": "environment",
    "enviornments": "environments",
    "envoirnment": "environment",
    "envoirnmental": "environmental",
    "envoirnments": "environments",
    "examinition": "examination",
    "feild": "field",
    "figuresfigures": "figures",
    "finacial": "financial",
    "finiter": "finite",
    "fluidd": "fluid",
    "formala": "formal",
    "fuild": "fluid",
    "funcatiional": "functional",
    "fuzy": "fuzzy",
    "geiotechnical": "geotechnical",
    "genetical": "genetic",
    "harshsness": "harshness",
    "helath": "health",
    "helicaptor": "helicopter",
    "helocopter": "helicopter",
    "hiistory": "history",
    "hydraculas": "hydraulics",
    "hydralic": "hydraulic",
    "hydrulic": "hydraulic",
    "illlumination": "illumination",
    "imapct": "impact",
    "implimentation": "implementation",
    "inderminate": "indeterminate",
    "indertimate": "indeterminate",
    "industrail": "industrial",
    "ineering": "engineering",
    "instrumenation": "instrumentation",
    "instrumenetation": "instrumentation",
    "intelegence": "intelligence",
    "intelence": "intelligence",
    "intellegence": "intelligence",
    "intellegent": "intelligent",
    "intellegnece": "intelligence",
    "intercultral": "intercultural",
    "interiros": "interior",
    "kinemetics": "kinematics",
    "lanaguage": "language",
    "langauges": "languages",
    "laxury": "luxury",
    "lectrical": "electrical",
    "ligic": "logic",
    "ligthing": "lighting",
    "liner": "linear",
    "machanical": "mechanical",
    "machanics": "mechanics",
    "machinary": "machinery",
    "machnies": "machines",
    "maintainace": "maintenance",
    "maintanance": "maintenance",
    "maintantance": "maintenance",
    "maintence": "maintenance",
    "makemup": "makeup",
    "managememet": "management",
    "managment": "management",
    "mangement": "management",
    "manaufacturing": "manufacturing",
    "manufcaturing": "manufacturing",
    "masss": "mass",
    "matallurgy": "metallurgy",
    "mateial": "material",
    "matematics": "mathematics",
    "mater": "matter",
    "materails": "materials",
    "mathamatics": "mathematics",
    "mathematicl": "mathematical",
    "matrial": "material",
    "mechine": "machine",
    "mechines": "machines",
    "mechnics": "mechanics",
    "mesurements": "measurements",
    "meterialss": "materials",
    "methododlogy": "methodology",
    "metodological": "methodological",
    "metrogy": "metrology",
    "mettalurgy": "metallurgy",
    "micontrollsers": "microcontrollers",
    "microcontrollores": "microcontrollers",
    "microprocontrollers": "microcontrollers",
    "miniing": "mining",
    "misslies": "missiles",
    "mnaufacturing": "manufacturing",
    "modren": "modern",
    "nanaotechnology": "nanotechnology",
    "nanootechnology": "nanotechnology",
    "neuarla": "neural",
    "neurall": "neural",
    "nevier": "navier",
    "nucler": "nuclear",
    "nueal": "neural",
    "numerecal": "numerical",
    "numeriacl": "numerical",
    "nural": "neural",
    "organizationa": "organization",
    "ocupational": "occupational",
    "oeration": "operation",
    "ofdatastructures": "of data structures",
    "oject": "object",
    "ontroduction": "introduction",
    "openn": "open",
    "opewrations": "operations",
    "packaigung": "packaging",
    "packging": "packaging",
    "pahysics": "physics",
    "paradigims": "paradigms",
    "parallell": "parallel",
    "particel": "particle",
    "pavemenet": "pavement",
    "penamatic": "pneumatic",
    "performence": "performance",
    "petrlcum": "petroleum",
    "phython": "python",
    "physchology": "psychology",
    "physcology": "psychology",
    "physocology": "psychology",
    "pnematic": "pneumatic",
    "pnematics": "pneumatics",
    "pnumatic": "pneumatic",
    "pollusion": "pollution",
    "porlusion": "pollution",
    "powerelectronic": "power electronic",
    "prinring": "printing",
    "principals": "principles",
    "principels": "principles",
    "principes": "principles",
    "printinng": "printing",
    "probablity": "probability",
    "probality": "probability",
    "processesing": "processing",
    "prog": "programming",
    "programe": "program",
    "programing": "programming",
    "programm": "program",
    "propolution": "propulsion",
    "propulusion": "propulsion",
    "protemics": "proteomics",
    "prpgramabale": "programmable",
    "psycology": "psychology",
    "qualiaty": "quality",
    "quilty": "quality",
    "radiaological": "radiological",
    "reactorthermal": "reactor thermal",
    "rchitecture": "architecture",
    "refrigeretion": "refrigeration",
    "rehabilation": "rehabilitation",
    "rehabilization": "rehabilitation",
    "rehebiliation": "rehabilitation",
    "reinfforced": "reinforced",
    "renewabke": "renewable",
    "repeter": "repeater",
    "retiraval": "retrieval",
    "saftey": "safety",
    "saatellite": "satellite",
    "sceinceand": "science and",
    "scienceand": "science and",
    "scince": "science",
    "scinces": "sciences",
    "scineces": "sciences",
    "screenprinting": "screen printing",
    "sectroscopy": "spectroscopy",
    "sefety": "safety",
    "sensign": "sensing",
    "serveceiability": "serviceability",
    "serveying": "surveying",
    "servicebility": "serviceability",
    "settelements": "settlements",
    "soild": "solid",
    "spacial": "spatial",
    "spoace": "space",
    "ssytems": "systems",
    "startegy": "strategy",
    "stastical": "statistical",
    "stare": "state",
    "stiching": "stitching",
    "stocastic": "stochastic",
    "stractures": "structures",
    "strcutues": "structures",
    "strcutures": "structures",
    "stregth": "strength",
    "strengh": "strength",
    "strictures": "structures",
    "structire": "structure",
    "strucutral": "structural",
    "struetural": "structural",
    "strutural": "structural",
    "stucture": "structure",
    "survyieng": "surveying",
    "syetms": "systems",
    "systemmme": "systems",
    "systmes": "systems",
    "sysyems": "systems",
    "sytems": "systems",
    "tchnologies": "technologies",
    "tcptp": "tcp/ip",
    "techni": "techniques",
    "techniqes": "techniques",
    "techniquies": "techniques",
    "technologie": "technologies",
    "tesfting": "testing",
    "theoritical": "theoretical",
    "thermodynamcis": "thermodynamics",
    "thermodynamicsand": "thermodynamics and",
    "toppology": "topology",
    "transducersice": "transducers",
    "transmision": "transmission",
    "trasfer": "transfer",
    "treatement": "treatment",
    "tretement": "treatment",
    "tribilogy": "tribology",
    "trnasportation": "transportation",
    "turbins": "turbines",
    "vartual": "virtual",
    "veriology": "verilog",
    "verilogy": "verilog",
    "vertification": "verification",
    "vibrartions": "vibrations",
    "virtualizaion": "virtualization",
    "volastics": "voltaics",
    "warefare": "warfare",
    "wirelesss": "wireless",
}


def _match_case(original: str, replacement: str) -> str:
    """Apply a correction without destroying the original capitalisation."""
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _clean_subject_name(value: str) -> str:
    value = re.sub(r"\b(?:semester|sem)\s*\d+\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:ele(?:ctive)?|set)\s*[-.]?\s*\d+\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\d+", " ", value)

    # Scraped titles often contain file suffixes or unclosed brackets.
    value = re.sub(r"\s*[~-]?\s*\.?\s*pdf\s*$", "", value, flags=re.IGNORECASE)
    if value.count("(") > value.count(")"):
        value += ")" * (value.count("(") - value.count(")"))
    elif value.count(")") > value.count("("):
        excess = value.count(")") - value.count("(")
        value = re.sub(r"(?<!\()\)", "", value, count=excess)

    # Split words that lost their spaces (BiomedicalSignal, DataStructures).
    value = re.sub(r"\bControlIers\b", "Controllers", value, flags=re.IGNORECASE)
    value = re.sub(r"\bBilogicaIs\b", "Biologicals", value, flags=re.IGNORECASE)
    value = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", value)
    value = re.sub(r"\b(an)\s+d\b", lambda match: _match_case(match.group(1), "and"), value, flags=re.IGNORECASE)
    value = re.sub(r"\bPart\s*([AB])\b", r"Part \1", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(I{1,3}|IV|V)PART\b", r"\1 Part", value)
    value = re.sub(r"\bDown\s+(?:Steam|Stream)\b", "Downstream", value, flags=re.IGNORECASE)
    value = re.sub(r"\bDev\s+Ops\b", "DevOps", value, flags=re.IGNORECASE)

    # Regulation/scheme markers tacked onto scraped subject labels.
    value = re.sub(r"(?<=[A-Za-z])RCS(?:ELE|CS|T|t)?\b", "", value)
    value = re.sub(r"\bRCS(?:ELE|CS|T|t)?\b(?:\s*-?\s*(?:19|20)\d{2})?\s*", " ", value)

    for typo, correction in SUBJECT_TYPO_REPLACEMENTS.items():
        value = re.sub(
            rf"\b{re.escape(typo)}\b",
            lambda match, correction=correction: _match_case(match.group(0), correction),
            value,
            flags=re.IGNORECASE,
        )

    # Repair odd capitalisation such as COntrol or DEsign.
    value = re.sub(
        r"\b(?!CSharp\b)([A-Z])([A-Z])(?=[a-z])",
        lambda match: match.group(1) + match.group(2).lower(),
        value,
    )

    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*\(\s*\)", "", value)
    value = value.strip(" -_.,:;") or "Other"
    return value[:1].upper() + value[1:]


def _infer_semester(manifest: dict, subject_name: str = "", subject_code: str = ""):
    semester = str(manifest.get("semester") or "")
    match = re.search(r"\b(?:semester|sem)\s*(\d{1,2})\b", semester, re.IGNORECASE)
    if match:
        return int(match.group(1))

    match = re.search(r"\b([1-8])\s*(?:semester|sem)\b", subject_name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"^\s*([1-8])(?:[_ -]|$)", subject_name)
    if match:
        return int(match.group(1))

    match = re.search(r"\b([1-8])\d{2,3}\b", subject_code or "")
    return int(match.group(1)) if match else None


def _archive_id(path: str) -> str:
    return "local-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:14]


def _year(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_archive(curated_papers: list[dict], subjects: list[dict]) -> dict:
    """Build a browsable index of every paper PDF physically present in papers/.

    ``data/papers.json`` intentionally stays focused on papers that have
    curated questions.  The downloaded archive is much larger, and is indexed
    separately so a scrape immediately becomes visible in the app without
    needing hand-written metadata for every PDF.  Solution-only PDFs belonging
    to curated papers remain linked from those paper records rather than being
    listed as separate papers.
    """
    manifest_records = []
    if MANIFEST_FILE.is_file():
        payload = load_json(MANIFEST_FILE)
        manifest_records = payload.get("papers", []) if isinstance(payload, dict) else []
    manifest_by_file = {record.get("file"): record for record in manifest_records if record.get("file")}
    manifest_by_folder = defaultdict(list)
    for record in manifest_records:
        file_name = record.get("file", "")
        folder = Path(file_name).parts[0] if file_name else ""
        if folder:
            manifest_by_folder[folder].append(record)

    curated_by_file = {paper["file"]: paper for paper in curated_papers}
    referenced_files = {
        path
        for paper in curated_papers
        for path in (paper.get("file"), paper.get("solutionFile"))
        if path
    }
    curated_subjects = {subject["id"]: subject for subject in subjects}
    paper_records = []
    subject_records = {}

    for path in sorted(PAPERS_DIR.rglob("*.pdf")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in referenced_files and relative not in curated_by_file:
            # A hand-curated paper already links to this PDF as its solution.
            continue

        curated = curated_by_file.get(relative)
        manifest = manifest_by_file.get(path.relative_to(PAPERS_DIR).as_posix(), {})
        if curated:
            subject_id = curated["subject"]
            subject_name = _clean_subject_name(curated_subjects[subject_id]["name"])
            semester = _infer_semester(
                {},
                curated_subjects[subject_id]["name"],
                " ".join(curated_subjects[subject_id].get("codes") or []),
            )
            record = {
                **curated,
                "subjectName": subject_name,
                "semester": semester,
                "questionCount": curated.get("questionCount", 0),
                "chapterIds": curated.get("chapterIds", []),
                "hasSolutions": bool(curated.get("solutionFile")),
            }
        else:
            folder = path.relative_to(PAPERS_DIR).parts[0]
            subject_id = f"local:{folder}"
            subject_name = _archive_subject_name(folder, manifest_by_folder[folder])
            semester = _infer_semester(
                manifest,
                manifest.get("subject", ""),
                manifest.get("subjectCode", ""),
            )
            title = (manifest.get("title") or path.stem).strip()
            record = {
                "id": _archive_id(relative),
                "title": title,
                "subject": subject_id,
                "subjectName": subject_name,
                "semester": semester,
                "code": manifest.get("subjectCode") or "",
                "institute": "Manipal Academy of Higher Education",
                "year": _year(manifest.get("year")),
                "month": "",
                "exam": manifest.get("examType") or "",
                "maxMarks": None,
                "duration": "",
                "file": relative,
                "solutionFile": None,
                "notes": None,
                "questionCount": 0,
                "chapterIds": [],
                "hasSolutions": False,
            }

        paper_records.append(record)
        if subject_id not in subject_records:
            subject_records[subject_id] = {
                "id": subject_id,
                "name": subject_name,
                "paperCount": 0,
                "semesters": [],
            }
        subject_records[subject_id]["paperCount"] += 1
        if record.get("semester") and record["semester"] not in subject_records[subject_id]["semesters"]:
            subject_records[subject_id]["semesters"].append(record["semester"])

    archive = {
        "meta": {
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "paperCount": len(paper_records),
            "subjectCount": len(subject_records),
        },
        "subjects": sorted(subject_records.values(), key=lambda item: item["name"].casefold()),
        "papers": paper_records,
    }
    DATA.mkdir(exist_ok=True)
    with ARCHIVE_FILE.open("w", encoding="utf-8") as handle:
        json.dump(archive, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return archive


def group_archive_subjects(archive: dict, protected_ids: set[str]) -> dict:
    """Merge case, punctuation and very small spelling variants in the archive."""
    candidates = sorted(
        archive["subjects"],
        key=lambda item: (
            item["id"] not in protected_ids,
            -item["paperCount"],
            item["name"].casefold(),
        ),
    )
    groups = []
    subject_to_group = {}

    for subject in candidates:
        normalized_name = re.sub(r"\band\b", " ", subject["name"].casefold())
        signature = re.sub(
            r"(?:pdf|questionpaper|question|paper|repeater)$",
            "",
            re.sub(r"[^a-z0-9]", "", normalized_name),
        )
        match = None
        for group in groups:
            if signature == group["signature"]:
                match = group
                break
            if (
                len(signature) >= 8
                and signature[:1] == group["signature"][:1]
                and abs(len(signature) - len(group["signature"])) <= 2
                and SequenceMatcher(None, signature, group["signature"]).ratio() >= 0.94
            ):
                match = group
                break
        if match is None:
            match = {
                "id": subject["id"],
                "name": subject["name"],
                "signature": signature,
                "paperCount": 0,
                "semesters": set(),
            }
            groups.append(match)
        match["paperCount"] += subject["paperCount"]
        match["semesters"].update(subject.get("semesters", []))
        subject_to_group[subject["id"]] = match["id"]

    group_names = {group["id"]: group["name"] for group in groups}
    for paper in archive["papers"]:
        if paper["subject"] in subject_to_group:
            paper["subject"] = subject_to_group[paper["subject"]]
            paper["subjectName"] = group_names[paper["subject"]]

    archive["subjects"] = sorted(
        [
            {
                "id": group["id"],
                "name": group["name"],
                "paperCount": group["paperCount"],
                "semesters": sorted(group["semesters"]),
            }
            for group in groups
        ],
        key=lambda item: item["name"].casefold(),
    )
    archive["meta"]["subjectCount"] = len(archive["subjects"])
    return archive


def validate(subjects, papers, questions):
    errors = []

    subject_map = {subject["id"]: subject for subject in subjects}
    chapter_map = {
        (subject["id"], chapter["id"]): chapter
        for subject in subjects
        for chapter in subject["chapters"]
    }
    paper_map = {paper["id"]: paper for paper in papers}
    seen_ids = set()

    for question in questions:
        qid = question.get("id", "<missing id>")
        where = f"{question.get('_file', '?')}:{qid}"
        if not qid or qid == "<missing id>":
            errors.append(f"{where}: missing id")
        if qid in seen_ids:
            errors.append(f"{where}: duplicate id")
        seen_ids.add(qid)

        subject_id = question.get("subject")
        chapter_id = question.get("chapter")
        if subject_id not in subject_map:
            errors.append(f"{where}: unknown subject {subject_id!r}")
        elif (subject_id, chapter_id) not in chapter_map:
            errors.append(f"{where}: unknown chapter {chapter_id!r} for {subject_id!r}")

        if question.get("type") not in {"MCQ", "DES"}:
            errors.append(f"{where}: type must be MCQ or DES")
        if not isinstance(question.get("marks"), (int, float)) or question.get("marks", 0) <= 0:
            errors.append(f"{where}: marks must be a positive number")
        if not question.get("question"):
            errors.append(f"{where}: empty question text")

        if question.get("type") == "MCQ":
            options = question.get("options") or []
            if len(options) < 2:
                errors.append(f"{where}: MCQ needs at least two options")
            answer = question.get("answer")
            if not isinstance(answer, int) or not 0 <= answer < len(options):
                errors.append(f"{where}: MCQ answer index out of range")
        else:
            if not (question.get("solution") or question.get("answerText")):
                errors.append(f"{where}: descriptive question needs a solution")
            if question.get("options"):
                errors.append(f"{where}: descriptive question should not have options")

        refs = question.get("papers") or []
        if not refs:
            errors.append(f"{where}: at least one paper reference is required")
        for ref in refs:
            if ref.get("id") not in paper_map:
                errors.append(f"{where}: unknown paper {ref.get('id')!r}")
            if not ref.get("qno"):
                errors.append(f"{where}: paper reference missing qno")

        images = question.get("images")
        if images is not None:
            if not isinstance(images, list):
                errors.append(f"{where}: images must be an array")
                continue
            for index, image in enumerate(images, 1):
                label = f"{where}: image {index}"
                if not isinstance(image, dict):
                    errors.append(f"{label}: must be an object")
                    continue
                src = image.get("src")
                if not isinstance(src, str) or not src.strip():
                    errors.append(f"{label}: missing src")
                    continue
                if Path(src).is_absolute() or ".." in Path(src).parts:
                    errors.append(f"{label}: src must stay inside web/ ({src!r})")
                    continue
                target = (WEB_DIR / src).resolve()
                if WEB_DIR.resolve() not in target.parents:
                    errors.append(f"{label}: src must stay inside web/ ({src!r})")
                elif target.suffix.lower() not in IMAGE_EXTENSIONS:
                    errors.append(f"{label}: unsupported image type {target.suffix!r}")
                elif not target.is_file():
                    errors.append(f"{label}: missing file {src!r}")
                placement = image.get("placement", "question")
                if placement not in IMAGE_PLACEMENTS:
                    errors.append(f"{label}: placement must be question or solution")
                for key in ("alt", "caption"):
                    if key in image and not isinstance(image[key], str):
                        errors.append(f"{label}: {key} must be text")

    return errors


def _write_bank_parts(bank: dict) -> list[str]:
    """Write the full bank as a manifest plus size-limited question parts.

    GitHub Pages rejects individual files over 100 MB, which the monolithic
    bank exceeded.  ``data/bank.json`` keeps the metadata, subjects and papers;
    the ordered question slices live in ``data/bank-parts``.
    """
    BANK_PART_DIR.mkdir(exist_ok=True)

    def encode(question: dict) -> str:
        return json.dumps(question, ensure_ascii=False, separators=(",", ":"))

    part_files: list[str] = []
    written_paths: set[Path] = set()
    part_questions: list[dict] = []
    part_size = 0
    question_offset = 0

    def flush() -> None:
        nonlocal part_questions, part_size, question_offset
        part_number = len(part_files) + 1
        name = f"questions-{part_number:04d}.json"
        path = BANK_PART_DIR / name
        path.write_text(
            json.dumps(
                {
                    "format": "paperpal-bank-questions-v1",
                    "part": part_number,
                    "questionOffset": question_offset,
                    "questionCount": len(part_questions),
                    "questions": part_questions,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        part_files.append(f"bank-parts/{name}")
        written_paths.add(path)
        question_offset += len(part_questions)
        part_questions = []
        part_size = 0

    for question in bank["questions"]:
        encoded_size = len(encode(question).encode("utf-8"))
        if part_questions and part_size + encoded_size > BANK_PART_MAX_BYTES:
            flush()
        part_questions.append(question)
        part_size += encoded_size
    if part_questions or not part_files:
        flush()

    for stale_path in BANK_PART_DIR.glob("*.json"):
        if stale_path not in written_paths:
            stale_path.unlink()

    manifest = {
        "format": "paperpal-bank-manifest-v1",
        "meta": bank["meta"],
        "subjects": bank["subjects"],
        "papers": bank["papers"],
        "questionPartCount": len(part_files),
        "questionParts": part_files,
    }
    with BANK_FILE.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    return part_files


def build_bank():
    def progress(message: str):
        print(f"[build] {message}", flush=True)

    progress("Loading source subjects, papers and curated questions")
    subjects, curated_papers, questions = load_sources()
    progress(
        f"Loaded {len(subjects):,} subjects, {len(curated_papers):,} curated papers and "
        f"{len(questions):,} curated questions"
    )
    progress("Validating curated question data")
    errors = validate(subjects, curated_papers, questions)
    if errors:
        raise SystemExit("Bank validation failed:\n  " + "\n  ".join(errors))
    progress("Curated data is valid")

    for question in questions:
        question.pop("_file", None)

    curated_subjects = {subject["id"]: subject for subject in subjects}
    curated_paper_counts = {paper["id"]: 0 for paper in curated_papers}
    curated_paper_chapters = {paper["id"]: set() for paper in curated_papers}
    for question in questions:
        for ref in question["papers"]:
            curated_paper_counts[ref["id"]] += 1
            curated_paper_chapters[ref["id"]].add(question["chapter"])

    curated_papers_enriched = [
        {
            **paper,
            "subjectName": curated_subjects[paper["subject"]]["name"],
            "questionCount": curated_paper_counts[paper["id"]],
            "chapterIds": sorted(curated_paper_chapters[paper["id"]]),
            "hasSolutions": bool(paper.get("solutionFile")),
        }
        for paper in curated_papers
    ]
    progress("Indexing downloaded PDF files")
    archive = build_archive(curated_papers_enriched, subjects)
    progress(f"Indexed {archive['meta']['paperCount']:,} PDFs across {archive['meta']['subjectCount']:,} raw subjects")
    progress("Clubbing similar subject names")
    archive = group_archive_subjects(archive, set(curated_subjects))
    progress(f"Grouped archive into {archive['meta']['subjectCount']:,} subjects")
    archive_subjects = {subject["id"]: subject for subject in archive["subjects"]}
    auto_questions = build_auto_questions(archive, progress=progress)
    progress(f"Merging {len(questions):,} curated and {len(auto_questions):,} automatic questions")
    archive_papers = {paper["id"]: paper for paper in archive["papers"]}
    for question in auto_questions:
        source = archive_papers.get(question["papers"][0]["id"])
        if source:
            question["subject"] = source["subject"]
            question["topic"] = source["subjectName"]

    auto_subjects = []
    for archive_subject in archive["subjects"]:
        if archive_subject["id"] in curated_subjects:
            continue
        auto_subjects.append(
            {
                "id": archive_subject["id"],
                "name": archive_subject["name"],
                "shortName": archive_subject["name"],
                "codes": [],
                "semesters": archive_subject.get("semesters", []),
                "paperCount": archive_subject["paperCount"],
                "description": "Questions automatically extracted from locally downloaded papers. "
                "Open the source PDF to verify the wording and find any official answer.",
                "chapters": [
                    {
                        "id": "extracted",
                        "name": "Extracted questions",
                        "description": "Raw question text imported from the source PDFs.",
                    }
                ],
            }
        )

    auto_subject_ids = {question["subject"] for question in auto_questions}
    enriched_source_subjects = []
    for subject in subjects:
        display_name = _clean_subject_name(subject["name"])
        archive_subject = archive_subjects.get(subject["id"], {})
        if not archive_subject.get("paperCount", 0):
            continue
        if subject["id"] not in auto_subject_ids:
            enriched_source_subjects.append(
                {
                    **subject,
                    "name": display_name,
                    "shortName": _clean_subject_name(subject.get("shortName") or display_name),
                    "semesters": archive_subject.get("semesters", []),
                    "paperCount": archive_subject["paperCount"],
                }
            )
            continue
        enriched_source_subjects.append(
            {
                **subject,
                "name": display_name,
                "shortName": _clean_subject_name(subject.get("shortName") or display_name),
                "semesters": archive_subject.get("semesters", []),
                "paperCount": archive_subject["paperCount"],
                "chapters": [
                    *subject["chapters"],
                    {
                        "id": "extracted",
                        "name": "Extracted questions",
                        "description": "Raw question text imported from the source PDFs.",
                    },
                ],
            }
        )
    all_subjects = enriched_source_subjects + auto_subjects
    subject_ids = {subject["id"] for subject in all_subjects}
    all_questions = [
        question for question in questions + auto_questions
        if question["subject"] in subject_ids
    ]
    subject_order = {subject["id"]: index for index, subject in enumerate(all_subjects)}
    chapter_order = {
        (subject["id"], chapter["id"]): index
        for subject in all_subjects
        for index, chapter in enumerate(subject["chapters"])
    }
    paper_counts = {paper["id"]: 0 for paper in archive["papers"]}
    paper_chapters = {paper["id"]: set() for paper in archive["papers"]}
    for question in all_questions:
        for ref in question["papers"]:
            if ref["id"] in paper_counts:
                paper_counts[ref["id"]] += 1
                paper_chapters[ref["id"]].add(question["chapter"])

    all_questions.sort(
        key=lambda question: (
            subject_order[question["subject"]],
            chapter_order[(question["subject"], question["chapter"])],
            question["id"],
        )
    )
    questions_by_chapter = defaultdict(list)
    for question in all_questions:
        questions_by_chapter[(question["subject"], question["chapter"])].append(question)

    progress("Calculating chapter, paper and subject counts")
    enriched_subjects = []
    for subject in all_subjects:
        chapters = []
        for chapter in subject["chapters"]:
            chapter_questions = questions_by_chapter[(subject["id"], chapter["id"])]
            chapters.append(
                {
                    **chapter,
                    "questionCount": len(chapter_questions),
                    "mcqCount": sum(1 for question in chapter_questions if question["type"] == "MCQ"),
                    "marks": sum(question["marks"] for question in chapter_questions),
                    "paperIds": sorted(
                        {ref["id"] for question in chapter_questions for ref in question["papers"]}
                    ),
                }
            )
        enriched_subjects.append(
            {
                **subject,
                "chapters": chapters,
                "questionCount": sum(chapter["questionCount"] for chapter in chapters),
                "paperIds": sorted(
                    {
                        ref["id"]
                        for question in all_questions
                        if question["subject"] == subject["id"]
                        for ref in question["papers"]
                    }
                ),
            }
        )

    # Subjects without any papers or questions only add noise to the sidebar.
    paper_subject_ids = {paper["subject"] for paper in archive["papers"]}
    enriched_subjects = [
        subject for subject in enriched_subjects
        if subject["questionCount"] and subject.get("paperCount")
        and subject["id"] in paper_subject_ids
    ]

    enriched_papers = []
    for paper in archive["papers"]:
        enriched_papers.append(
            {
                **paper,
                "questionCount": paper_counts[paper["id"]],
                "chapterIds": sorted(
                    paper_chapters[paper["id"]],
                    key=lambda chapter_id: chapter_order[(paper["subject"], chapter_id)],
                ),
                "hasSolutions": bool(paper.get("solutionFile")),
            }
        )
    archive["papers"] = enriched_papers

    # Drop catalogue entries that no longer have a paper behind them so the
    # paper browser and the question-bank sidebar use the same subject list.
    archived_paper_subject_ids = {paper["subject"] for paper in enriched_papers}
    archive["subjects"] = [
        subject for subject in archive["subjects"]
        if subject["id"] in archived_paper_subject_ids and subject.get("paperCount")
    ]
    archive["meta"]["subjectCount"] = len(archive["subjects"])
    archive["meta"]["questionCount"] = len(all_questions)
    archive["meta"]["autoQuestionCount"] = len(auto_questions)
    progress("Writing archive and question-bank JSON")
    with ARCHIVE_FILE.open("w", encoding="utf-8") as handle:
        json.dump(archive, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    bank = {
        "meta": {
            "name": "PaperPal",
            "institution": "Manipal Academy of Higher Education",
            "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "questionCount": len(all_questions),
            "paperCount": len(enriched_papers),
            "subjectCount": len(enriched_subjects),
            "mcqCount": sum(1 for question in all_questions if question["type"] == "MCQ"),
            "autoQuestionCount": len(auto_questions),
            "imageCount": sum(len(question.get("images") or []) for question in all_questions),
            "questionsWithImages": sum(1 for question in all_questions if question.get("images")),
        },
        "subjects": enriched_subjects,
        "papers": enriched_papers,
        "questions": all_questions,
    }

    DATA.mkdir(exist_ok=True)
    progress("Writing split question bank")
    part_files = _write_bank_parts(bank)
    progress(f"Wrote {len(part_files)} bank part files")

    # The website should not download every question just to render one
    # subject.  Keep the index small and split questions into independently
    # loadable subject files.
    progress("Writing lazy-load subject chunks")
    BANK_SUBJECT_DIR.mkdir(exist_ok=True)
    written_subject_files = set()
    index_papers = [
        {
            key: paper.get(key)
            for key in ("id", "title", "subject", "code", "exam", "year", "questionCount", "chapterIds")
        }
        for paper in enriched_papers
    ]
    papers_by_id = {paper["id"]: paper for paper in index_papers}
    index_subjects = []
    for subject in enriched_subjects:
        subject_file = f"bank-subjects/{hashlib.sha1(subject['id'].encode('utf-8')).hexdigest()[:16]}.json"
        index_subject = {
            key: value for key, value in subject.items()
            if key != "paperIds"
        }
        index_subject["chapters"] = [
            {key: value for key, value in chapter.items() if key != "paperIds"}
            for chapter in subject["chapters"]
        ]
        index_subjects.append({**index_subject, "questionFile": subject_file})
        subject_questions = [
            question for question in all_questions if question["subject"] == subject["id"]
        ]
        subject_paper_ids = {
            ref["id"] for question in subject_questions for ref in question["papers"]
        }
        subject_path = ROOT / "data" / subject_file
        subject_path.write_text(
            json.dumps(
                {
                    "questions": subject_questions,
                    "papers": [papers_by_id[paper_id] for paper_id in subject_paper_ids if paper_id in papers_by_id],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ) + "\n",
            encoding="utf-8",
        )
        written_subject_files.add(subject_path)

    # Drop chunks for subjects that no longer have questions (for example,
    # names that merged after cleaning up typos).
    for stale_path in BANK_SUBJECT_DIR.glob("*.json"):
        if stale_path not in written_subject_files:
            stale_path.unlink()

    bank_index = {
        "meta": bank["meta"],
        "subjects": index_subjects,
        "years": sorted({paper["year"] for paper in index_papers if paper.get("year") is not None}, reverse=True),
    }
    with BANK_INDEX_FILE.open("w", encoding="utf-8") as handle:
        json.dump(bank_index, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")

    progress(
        f"Finished: {bank['meta']['questionCount']:,} questions, "
        f"{bank['meta']['paperCount']:,} papers, {bank['meta']['subjectCount']:,} subjects"
    )

    return bank


def print_stats(bank):
    meta = bank["meta"]
    print(
        f"PaperPal: {meta['questionCount']} questions "
        f"({meta['mcqCount']} MCQ, {meta['imageCount']} figures) across "
        f"{meta['subjectCount']} subjects and {meta['paperCount']} papers"
    )
    for subject in bank["subjects"]:
        print(f"\n{subject['name']} ({subject['questionCount']} questions)")
        for chapter in subject["chapters"]:
            print(f"  {chapter['name']:<28} {chapter['questionCount']:>3} q")
