"""Read question files uploaded by admins (.xlsx) and build the template."""
import hashlib
import io
import re
from dataclasses import dataclass, field

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

import config


class ExcelFormatError(Exception):
    """The file as a whole is unusable (not Excel, no header row, ...)."""


@dataclass
class Row:
    rownum: int
    subject: str
    question: str
    options: list
    correct: int
    explanation: str
    content_hash: str


@dataclass
class ParseResult:
    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)      # [(rownum, message)]
    duplicates: list = field(default_factory=list)  # [rownum] repeated inside the file
    samples: int = 0                                # template sample rows skipped


# Header names accepted for each column (compared after lower-casing and
# stripping everything except letters and digits).
HEADERS = {
    "subject": {"subject"},
    "question": {"question", "questiontext"},
    "a": {"a", "optiona", "choicea"},
    "b": {"b", "optionb", "choiceb"},
    "c": {"c", "optionc", "choicec"},
    "d": {"d", "optiond", "choiced"},
    "correct": {"correct", "correctanswer", "answer", "key", "correctoption"},
    "explanation": {"explanation", "explain", "reason"},
}
REQUIRED = ("subject", "question", "a", "b", "correct")


def _norm_header(v):
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def _text(v, single_line=True):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).replace("\r", "")
    return " ".join(s.split()) if single_line else s.strip()


def _hash(subject, question):
    norm = " ".join(question.casefold().split())
    return hashlib.sha256(f"{subject}|{norm}".encode()).hexdigest()


def _find_header(rows):
    """Return (header_rownum, {field: column_index}) or None."""
    for rownum, cells in enumerate(rows[:10], start=1):
        names = [_norm_header(c) for c in cells]
        mapping = {}
        for fld, aliases in HEADERS.items():
            for idx, name in enumerate(names):
                if name in aliases:
                    mapping[fld] = idx
                    break
        if "subject" in mapping and "question" in mapping:
            return rownum, mapping
    return None


def _validate_row(rownum, cells, col):
    def cell(fld):
        i = col.get(fld)
        return cells[i] if i is not None and i < len(cells) else None

    subject = config.normalize_subject(_text(cell("subject")))
    if not subject:
        raise ValueError(
            f"unknown subject '{_text(cell('subject'))}' (use: {', '.join(config.SUBJECTS)})"
        )

    question = _text(cell("question"), single_line=False)
    if not question:
        raise ValueError("question is empty")
    if len(question) > config.LIMIT_QUESTION:
        raise ValueError(f"question is {len(question)} characters (max {config.LIMIT_QUESTION})")

    raw_opts = [_text(cell(f)) for f in ("a", "b", "c", "d")]
    last = max((i for i, o in enumerate(raw_opts) if o), default=-1)
    options = raw_opts[: last + 1]
    for i, o in enumerate(options):
        if not o:
            raise ValueError(f"option {'ABCD'[i]} is empty but a later option is filled")
        if len(o) > config.LIMIT_OPTION:
            raise ValueError(
                f"option {'ABCD'[i]} is {len(o)} characters (max {config.LIMIT_OPTION})"
            )
    if len(options) < 2:
        raise ValueError("needs at least 2 options (A and B)")
    if len({o.casefold() for o in options}) != len(options):
        raise ValueError("two options are identical")

    c = _text(cell("correct")).upper()
    if c in ("A", "B", "C", "D"):
        correct = "ABCD".index(c)
    elif c in ("1", "2", "3", "4"):
        correct = int(c) - 1
    else:
        raise ValueError(f"correct must be A, B, C or D (got '{c}')")
    if correct >= len(options):
        raise ValueError(f"correct is '{'ABCD'[correct]}' but that option is empty")

    explanation = _text(cell("explanation"))
    if len(explanation) > config.LIMIT_EXPLANATION:
        raise ValueError(
            f"explanation is {len(explanation)} characters (max {config.LIMIT_EXPLANATION})"
        )

    return Row(rownum, subject, question, options, correct, explanation, _hash(subject, question))


def parse_workbook(data):
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:
        raise ExcelFormatError("I couldn't open that file as an .xlsx Excel workbook.")

    for ws in wb.worksheets:
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
        found = _find_header(rows)
        if found:
            break
    else:
        raise ExcelFormatError(
            "I couldn't find a header row. The first row needs the columns: "
            "subject, question, A, B, C, D, correct, explanation. Send /template to get the format."
        )

    header_row, col = found
    missing = [f for f in REQUIRED if f not in col]
    if missing:
        raise ExcelFormatError("Missing column(s): " + ", ".join(missing))

    body = [(n, r) for n, r in enumerate(rows, start=1) if n > header_row and any(
        v not in (None, "") for v in r)]
    if len(body) > config.MAX_ROWS:
        raise ExcelFormatError(
            f"{len(body)} rows is too many for one file (max {config.MAX_ROWS}). Please split it."
        )

    result = ParseResult()
    seen = set()
    q_idx = col["question"]
    for rownum, cells in body:
        q_cell = _text(cells[q_idx]) if q_idx < len(cells) else ""
        if q_cell.startswith("[SAMPLE]"):
            result.samples += 1
            continue
        try:
            row = _validate_row(rownum, cells, col)
        except ValueError as e:
            result.errors.append((rownum, str(e)))
            continue
        key = (row.subject, row.content_hash)
        if key in seen:
            result.duplicates.append(rownum)
            continue
        seen.add(key)
        result.rows.append(row)
    return result


# ---- template ---------------------------------------------------------------
_SAMPLES = [
    ("logic", "All A are B, and all B are C. What follows?",
     "All A are C", "All C are A", "No A is C", "Some B are not C", "A",
     "Chaining 'all' statements: A is inside B, and B is inside C."),
    ("english", "Choose the correct sentence.",
     "She don't like tea", "She doesn't like tea", "She not like tea", "She no like tea", "B",
     "Third-person singular takes 'doesn't'."),
    ("physics", "What is the SI unit of force?",
     "Joule", "Watt", "Newton", "Pascal", "C",
     "Force = mass x acceleration, measured in newtons."),
    ("geography", "Which is the longest river in Africa?",
     "Congo", "Nile", "Niger", "Zambezi", "B",
     "The Nile is about 6,650 km long."),
    ("math", "What is 12 x 12?",
     "124", "134", "144", "154", "C", "12 x 12 = 144."),
    ("psychology", "Who is known as the father of psychoanalysis?",
     "Carl Jung", "Sigmund Freud", "B. F. Skinner", "Ivan Pavlov", "B",
     "Freud founded psychoanalysis in the late 1800s."),
    ("history", "In which year did the Battle of Adwa take place?",
     "1869", "1896", "1935", "1974", "B", "Ethiopia defeated Italy at Adwa in 1896."),
]


def build_template():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "questions"
    ws.append(["subject", "question", "A", "B", "C", "D", "correct", "explanation"])
    for s in _SAMPLES:
        ws.append([s[0], "[SAMPLE] " + s[1], *s[2:]])

    head_fill = PatternFill("solid", fgColor="1F4E78")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center")
    for letter, width in zip("ABCDEFGH", (14, 60, 24, 24, 24, 24, 9, 50)):
        ws.column_dimensions[letter].width = width
    ws.freeze_panes = "A2"

    dv_subject = DataValidation(type="list", formula1='"' + ",".join(config.SUBJECTS) + '"',
                                allow_blank=True)
    dv_correct = DataValidation(type="list", formula1='"A,B,C,D"', allow_blank=True)
    ws.add_data_validation(dv_subject)
    ws.add_data_validation(dv_correct)
    dv_subject.add("A2:A2000")
    dv_correct.add("G2:G2000")

    help_ws = wb.create_sheet("How to fill")
    for line in [
        "One question per row. Delete the [SAMPLE] rows (they are skipped anyway).",
        "subject: logic, english, physics, geography, math, psychology or history.",
        "A-D: answer options. C and D may be left empty (minimum 2 options).",
        "correct: the letter of the right option (A, B, C or D). Only one correct answer.",
        "explanation: shown after someone answers. Optional.",
        f"Limits: question {config.LIMIT_QUESTION} chars, each option {config.LIMIT_OPTION}, "
        f"explanation {config.LIMIT_EXPLANATION}.",
        "Questions are posted in the order they were uploaded (top row first).",
        "Uploading the same question text again for a subject is ignored as a duplicate.",
    ]:
        help_ws.append([line])
    help_ws.column_dimensions["A"].width = 100

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
