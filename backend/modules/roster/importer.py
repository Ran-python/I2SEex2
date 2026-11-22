"""Roster file parsing (.xlsx / .xls / .csv / Google Forms CSV export).

Works on a header-less read of the file so it can:
* find the header row even if the sheet starts with a title row,
* auto-detect Name / First+Last name / ID number / Email columns in Hebrew or English,
* fall back to content sniffing (emails contain '@', IDs are 5-9 digits...) when headers are unknown.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

from modules.roster.service import clean_text, normalize_email, normalize_id_number

# ---------------------------------------------------------------------------
# Header vocabulary (already normalised: casefold, punctuation stripped)
# ---------------------------------------------------------------------------
FULL_NAME_HEADERS = {
    "full name", "fullname", "name", "student name", "student", "your name", "name and surname",
    "student full name", "full name of student",
    "שם מלא", "שם", "שם הסטודנט", "שם הסטודנטית", "שם התלמיד", "שם התלמידה", "שם ומשפחה",
    "שם פרטי ומשפחה", "שם פרטי ושם משפחה", "שם ושם משפחה", "שם הסטודנטית הסטודנט", "שם מלא של הסטודנט",
}
FIRST_NAME_HEADERS = {"first name", "firstname", "given name", "first", "שם פרטי", "פרטי"}
LAST_NAME_HEADERS = {"last name", "lastname", "surname", "family name", "last", "שם משפחה", "משפחה"}
ID_HEADERS = {
    "id", "id number", "idnumber", "id no", "id num", "id #", "teudat zehut", "tz", "student id",
    "national id", "identity number", "personal id",
    "תז", "ת ז", "תעודת זהות", "מספר תעודת זהות", "מס תז", "מס ת ז", "מספר זהות", "זהות", "מס זהות",
    "מספר תז", "תעודת זהות מספר",
}
EMAIL_HEADERS = {
    "email", "e mail", "email address", "e mail address", "mail", "student email",
    "כתובת אימייל", "אימייל", "מייל", "דואל", "דואר אלקטרוני", "כתובת דואל", "כתובת מייל",
    "כתובת דואר אלקטרוני", "כתובת הדואל",
}
IGNORED_HEADERS = {"timestamp", "חותמת זמן", "date", "תאריך", "score", "ציון", "time", "שעה"}

# Substrings that strongly indicate a column in long Google-Forms style questions.
EMAIL_HINTS = ("email", "e mail", "אימייל", "מייל", "דואל", "דואר אלקטרוני")
ID_HINTS = ("id number", "teudat", "תעודת זהות", "מספר זהות", "תז")
FIRST_HINTS = ("first name", "given name", "שם פרטי")
LAST_HINTS = ("last name", "surname", "family name", "שם משפחה")
NAME_HINTS = ("full name", "your name", "student name", "שם מלא", "שמך", "שם הסטודנט", "שם התלמיד")

_HEADER_PUNCT = re.compile(r"[\"'`׳״.,:;?!*()\[\]{}\-_/\\|]+")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ID_RE = re.compile(r"^\d{5,9}(\.0+)?$")


class RosterImportError(ValueError):
    pass


@dataclass
class ParsedRoster:
    rows: list[dict] = field(default_factory=list)
    detected_columns: dict[str, str | None] = field(default_factory=dict)
    skipped_rows: int = 0


def _norm_header(value) -> str:
    text = clean_text(value) or ""
    text = _HEADER_PUNCT.sub(" ", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def _read_raw(filename: str, content: bytes) -> pd.DataFrame:
    import pandas as pd

    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        try:
            return pd.read_excel(io.BytesIO(content), header=None, dtype=str, engine=None)
        except Exception as exc:  # noqa: BLE001
            raise RosterImportError(f"Could not read Excel file: {exc}") from exc

    if name.endswith((".csv", ".txt", ".tsv")) or not name:
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "cp1255", "utf-16", "latin-1"):
            try:
                text = content.decode(encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
                continue
            for kwargs in ({"sep": None, "engine": "python"}, {"sep": ","}):
                try:
                    return pd.read_csv(io.StringIO(text), header=None, dtype=str,
                                       skip_blank_lines=True, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
            # Fallback for ragged CSVs (e.g., a 1-column title row above a 3-column table).
            try:
                delim = "\t" if name.endswith(".tsv") else (";" if text.count(";") > text.count(",") else ",")
                rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
                if rows:
                    return pd.DataFrame(rows, dtype=str)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise RosterImportError(f"Could not read CSV file: {last_error}")

    raise RosterImportError("Unsupported file type. Please upload .xlsx, .xls or .csv")


def _classify_header(header: str) -> str | None:
    """Map a normalised header to a logical field, or None."""
    if not header or header in IGNORED_HEADERS:
        return None
    tokens = set(header.split())
    if header in EMAIL_HEADERS:
        return "email"
    if header in ID_HEADERS:
        return "id_number"
    if header in FIRST_NAME_HEADERS:
        return "first_name"
    if header in LAST_NAME_HEADERS:
        return "last_name"
    if header in FULL_NAME_HEADERS:
        return "full_name"
    # Fuzzy matching for longer headers ("What is your email address?").
    if any(h in header for h in EMAIL_HINTS):
        return "email"
    if any(h in header for h in ID_HINTS) or tokens & {"id", "tz", "ת״ז"}:
        return "id_number"
    if any(h in header for h in FIRST_HINTS):
        return "first_name"
    if any(h in header for h in LAST_HINTS):
        return "last_name"
    if any(h in header for h in NAME_HINTS) or tokens & {"name", "שם"}:
        return "full_name"
    return None


def _detect_header_row(raw: pd.DataFrame) -> tuple[int | None, dict[str, int]]:
    best_row, best_map, best_score = None, {}, 0
    for row_idx in range(min(10, len(raw))):
        mapping: dict[str, int] = {}
        for col_idx, value in enumerate(raw.iloc[row_idx].tolist()):
            field_name = _classify_header(_norm_header(value))
            if field_name and field_name not in mapping:
                mapping[field_name] = col_idx
        score = len(mapping) + (2 if {"full_name", "first_name"} & mapping.keys() else 0)
        if score > best_score:
            best_row, best_map, best_score = row_idx, mapping, score
    return best_row, best_map


def _ratio(series: pd.Series, predicate) -> float:
    values = [v for v in (clean_text(x) for x in series.tolist()) if v]
    if not values:
        return 0.0
    return sum(1 for v in values if predicate(v)) / len(values)


def _sniff_columns(data: pd.DataFrame, mapping: dict[str, int]) -> dict[str, int]:
    """Fill in missing fields by looking at cell contents."""
    used = set(mapping.values())
    columns = [c for c in range(data.shape[1]) if c not in used]

    if "email" not in mapping:
        for c in columns:
            if _ratio(data.iloc[:, c], lambda v: bool(_EMAIL_RE.match(v))) >= 0.6:
                mapping["email"] = c
                columns.remove(c)
                break
    if "id_number" not in mapping:
        for c in columns:
            if _ratio(data.iloc[:, c], lambda v: bool(_ID_RE.match(v.replace("-", "")))) >= 0.6:
                mapping["id_number"] = c
                columns.remove(c)
                break
    if not {"full_name", "first_name"} & mapping.keys():
        def looks_like_name(v: str) -> bool:
            return "@" not in v and not any(ch.isdigit() for ch in v) and any(ch.isalpha() for ch in v)
        for c in columns:
            if _ratio(data.iloc[:, c], looks_like_name) >= 0.6:
                mapping["full_name"] = c
                break
    return mapping


def parse_roster_file(filename: str, content: bytes) -> ParsedRoster:
    if not content:
        raise RosterImportError("The uploaded file is empty.")
    raw = _read_raw(filename, content).dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    raw.columns = range(raw.shape[1])
    if raw.empty:
        raise RosterImportError("No rows found in the uploaded file.")

    header_row, mapping = _detect_header_row(raw)
    if header_row is not None:
        headers = [clean_text(v) or f"Column {i + 1}" for i, v in enumerate(raw.iloc[header_row].tolist())]
        data = raw.iloc[header_row + 1:].reset_index(drop=True)
    else:
        headers = [f"Column {i + 1}" for i in range(raw.shape[1])]
        data = raw

    mapping = _sniff_columns(data, mapping)
    if not {"full_name", "first_name", "last_name"} & mapping.keys():
        raise RosterImportError(
            "Could not find a name column. Expected a header such as 'Full Name' / 'שם מלא' "
            "or 'First Name' + 'Last Name' / 'שם פרטי' + 'שם משפחה'."
        )

    def cell(row: list, key: str):
        idx = mapping.get(key)
        return row[idx] if idx is not None and idx < len(row) else None

    parsed = ParsedRoster(detected_columns={
        key: headers[idx] for key, idx in mapping.items()
    })
    for row in data.itertuples(index=False, name=None):
        row = list(row)
        full_name = clean_text(cell(row, "full_name"))
        if not full_name:
            parts = [clean_text(cell(row, "first_name")), clean_text(cell(row, "last_name"))]
            full_name = " ".join(p for p in parts if p) or None
        if not full_name:
            parsed.skipped_rows += 1
            continue
        parsed.rows.append({
            "full_name": full_name,
            "id_number": normalize_id_number(cell(row, "id_number")),
            "email": normalize_email(cell(row, "email")),
        })
    return parsed
