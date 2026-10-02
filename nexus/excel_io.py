"""All Excel reading/writing: Capital IQ export, enriched companies,
Faculty Database (including flag updates), and per-faculty output books."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)


# ======================================================================
# Capital IQ raw export
# ======================================================================

def _sheet_width(path: Path) -> Optional[int]:
    """How many columns the first sheet actually has, or None if unreadable."""
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
        try:
            return wb.worksheets[0].max_column
        finally:
            wb.close()
    except Exception:                                          # noqa: BLE001
        return None


def read_capitaliq(path: Path, cfg: dict) -> pd.DataFrame:
    """Read the raw Capital IQ screening export into a tidy DataFrame."""
    if not path.exists():
        raise FileNotFoundError(
            f"Company datasource not found at {path}. Place your Capital IQ "
            "export there (or update paths.companies_raw in config.yaml)."
        )
    # pandas returns usecols in SHEET order however they are listed, so the
    # field names must be sorted by column letter or they silently attach to
    # the wrong data. Optional columns are skipped when a config written
    # before they existed does not name one.
    wanted = [
        ("col_company", "company"), ("col_ticker", "ticker"),
        ("col_employees", "employees"),
        ("col_revenue", "revenue"), ("col_company_type", "company_type"),
        ("col_address", "address"), ("col_description", "description"),
        ("col_website", "website"), ("col_industry", "industry"),
    ]
    present = [(str(cfg[k]).upper(), name) for k, name in wanted if cfg.get(k)]
    present.sort(key=lambda kv: (len(kv[0]), kv[0]))          # A..Z, then AA..

    # An export narrower than the config expects would raise an unhelpful
    # "out-of-bounds indices" error from pandas, so configured columns past
    # the end of the sheet are dropped and their fields left blank.
    width = _sheet_width(path)
    if width:
        present = [(l, n) for l, n in present if column_index_from_string(l) <= width]

    usecols = ",".join(letter for letter, _ in present)
    names = [name for _, name in present]

    df = pd.read_excel(
        path, header=None, skiprows=cfg["data_start_row"] - 1,
        usecols=usecols, engine="openpyxl", dtype=str,
    )
    df.columns = names

    def _clean(v) -> Optional[str]:
        """None / NaN / blank -> None; everything else -> stripped string."""
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or s.lower() == "nan":
            return None
        return s

    rows = []
    for rec in df.to_dict("records"):
        company = _clean(rec.get("company"))
        if company is None:
            break  # stop at first blank company row (footer follows)
        row = {name: _clean(rec.get(name)) for name in names}
        row["company"] = company
        # keep the shape stable whether or not the optional columns exist
        for optional in ("ticker", "company_type", "industry"):
            row.setdefault(optional, None)
        rows.append(row)
    return pd.DataFrame(rows)


# ======================================================================
# Enriched companies file
# ======================================================================

from nexus.funding import FUNDING_COLUMNS  # noqa: E402

ENRICHED_COLUMNS = [
    "company", "website", "description", "address_cleaned",
    "company_type", "industry",
    "latitude", "longitude", "geocode_provider", "distance_miles",
    "employees", "revenue_usdmm",
    "distance_score", "employee_score", "revenue_score", "funding_score",
    "funding_reason",
] + FUNDING_COLUMNS + [
    "embedding_key",
]


def write_enriched(path: Path, records: List[dict], columns: List[str] = None) -> None:
    """Rebuild the enriched file from scratch. Always a full overwrite: the
    raw datasource is the single source of truth, so stale rows can never
    linger from a previous run."""
    df = pd.DataFrame(records, columns=columns or ENRICHED_COLUMNS)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Companies")
            ws = writer.sheets["Companies"]
            for cell in ws[1]:
                cell.fill = HEADER_FILL
                cell.font = HEADER_FONT
            ws.freeze_panes = "A2"
    except PermissionError as e:
        raise PermissionError(
            f"Could not write {path.name} - is it open in Excel? Close it and "
            "re-run. Nothing was lost: geocodes and embeddings are already "
            "cached, so the re-run will be fast and cost nothing."
        ) from e


def read_enriched(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Enriched company file not found at {path}. "
            "Run 'python enrich_companies.py' first."
        )
    return pd.read_excel(path, sheet_name="Companies", engine="openpyxl")


# ======================================================================
# Faculty Database
# ======================================================================

def read_faculty(path: Path, cfg: dict) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Faculty Database not found at {path}. "
            "Place it there or update paths.faculty_database in config.yaml."
        )
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    def col(key: str) -> int:
        return column_index_from_string(cfg[key])

    faculty = []
    for row in range(cfg["data_start_row"], ws.max_row + 1):
        name = ws.cell(row=row, column=col("col_name")).value
        if name is None or str(name).strip() == "":
            continue
        faculty.append({
            "row": row,
            "name": str(name).strip(),
            "school": str(ws.cell(row=row, column=col("col_school")).value or "").strip(),
            "department": str(ws.cell(row=row, column=col("col_department")).value or "").strip(),
            "research": str(ws.cell(row=row, column=col("col_research")).value or "").strip(),
            "class1": str(ws.cell(row=row, column=col("col_class1")).value or "").strip(),
            "class2": str(ws.cell(row=row, column=col("col_class2")).value or "").strip(),
            "flag": str(ws.cell(row=row, column=col("col_flag")).value or "").strip().upper(),
        })
    wb.close()
    return faculty


def set_faculty_flag(path: Path, cfg: dict, row: int, value: str = "Y") -> None:
    """Flip one faculty's flag and save. Retries advice on PermissionError
    (file open in Excel) is raised as a clear message."""
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    ws.cell(row=row, column=column_index_from_string(cfg["col_flag"])).value = value
    try:
        wb.save(path)
    except PermissionError as e:
        raise PermissionError(
            f"Could not save {path.name} - is it open in Excel? "
            "Close it and re-run; completed faculty are already flagged."
        ) from e
    finally:
        wb.close()


# ======================================================================
# Per-faculty output workbook
# ======================================================================

# (header, record key, column width). Justification sits next to the
# company name: it is the column people read first.
RESULT_COLUMNS = [
    ("Rank", "rank", 6),
    ("Company", "company", 30),
    ("Size", "size", 8),
    ("Organisation Type", "organisation_type", 14),
    ("Justification", "justification", 60),
    ("Overall Score", "overall_score", 10),
    ("Sector Match", "sector_match", 14),
    ("Website", "website", 24),
    ("Distance (mi)", "distance_miles", 10),
    ("Employees", "employees", 10),
    ("Revenue ($USDmm)", "revenue_usdmm", 12),
    ("Distance Score", "distance_score", 10),
    ("Employee Score", "employee_score", 10),
    ("Revenue Score", "revenue_score", 10),
    ("Alignment Score (1-9)", "alignment_score", 11),
    ("Alignment Reason", "alignment_reason", 45),
    ("Partnership Score (1-9)", "partnership_score", 11),
    ("Partnership Reason", "partnership_reason", 45),
    ("Funding Score (0-1)", "funding_score", 10),
    ("Funding Reason", "funding_reason", 40),
    ("Status", "company_status", 14),
    ("Parent Company", "parent_company", 24),
    ("Company Profile", "company_profile", 70),
]

ALL_COLUMNS = [
    ("Stage", "stage", 24),
    ("Rank", "rank", 6),
    ("Company", "company", 30),
    ("Size", "size", 8),
    ("Organisation Type", "organisation_type", 14),
    ("In Pre-filter", "in_prefilter", 10),
    ("Found By", "found_by", 16),
    ("Sector Match", "sector_match", 14),
    ("Website", "website", 24),
    ("Distance (mi)", "distance_miles", 10),
    ("Employees", "employees", 10),
    ("Revenue ($USDmm)", "revenue_usdmm", 12),
    ("Distance Score", "distance_score", 10),
    ("Employee Score", "employee_score", 10),
    ("Revenue Score", "revenue_score", 10),
    ("Alignment Score (1-9)", "alignment_score", 11),
    ("Alignment Reason", "alignment_reason", 45),
    ("Partnership Score (1-9)", "partnership_score", 11),
    ("Partnership Reason", "partnership_reason", 45),
    ("Funding Score (0-1)", "funding_score", 10),
    ("Funding Reason", "funding_reason", 40),
    ("Overall Score", "overall_score", 10),
    ("Status", "company_status", 14),
    ("Parent Company", "parent_company", 24),
    ("Company Profile", "company_profile", 70),
]

RESULT_HEADERS = [h for h, _, _ in RESULT_COLUMNS]
ALL_HEADERS = [h for h, _, _ in ALL_COLUMNS]

_ROUND4 = {"overall_score", "distance_score", "employee_score", "revenue_score"}


def unique_path(path: Path) -> Path:
    """Return a path that does not yet exist, adding ' (2)', ' (3)', ... if
    needed. Output filenames carry a to-the-second timestamp, so two runs for
    the same faculty in the same second would otherwise overwrite silently."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 2
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _sheet(wb, title: str, columns, rows, freeze: str, first: bool = False):
    ws = wb.active if first else wb.create_sheet(title)
    ws.title = title
    ws.append([h for h, _, _ in columns])
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        vals = []
        for _, key, _ in columns:
            v = r.get(key)
            if key in _ROUND4 and isinstance(v, (int, float)):
                v = round(v, 4)
            vals.append(v)
        ws.append(vals)
    for idx, (_, _, width) in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = freeze
    return ws


def write_faculty_output(path: Path, faculty: dict, tabs, run_info: dict,
                         all_rows: Optional[List[dict]] = None) -> None:
    """tabs: {sheet title: ranked rows} - one tab per scoring pass (with the
    300 pre-filter, and over all companies). A plain list is one tab named
    "Top Matches"."""
    if isinstance(tabs, list):
        tabs = {"Top Matches": tabs}
    wb = openpyxl.Workbook()
    for k, (title, rows) in enumerate(tabs.items()):
        _sheet(wb, title[:31], RESULT_COLUMNS, rows, "C2", first=(k == 0))
    if all_rows:
        _sheet(wb, "All Companies", ALL_COLUMNS, all_rows, "D2")

    # ---- Run Info sheet (traceability) ----
    info = wb.create_sheet("Run Info")
    info.column_dimensions["A"].width = 28
    info.column_dimensions["B"].width = 100
    rows = [
        ("Faculty", faculty["name"]),
        ("School/College", faculty.get("school")),
        ("Department", faculty.get("department")),
        ("Research profile used", run_info.get("research_profile")),
        ("Run date", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("Scoring model", run_info.get("scoring_model")),
        ("Embedding model", run_info.get("embedding_model")),
        ("Companies in datasource", run_info.get("n_companies")),
        ("Size tiers", run_info.get("size_tiers")),
        ("Candidates after pre-filter", run_info.get("n_candidates")),
        ("Companies scored per tab", run_info.get("n_full")),
        ("Faculty source", run_info.get("faculty_source")),
        ("Secondary scoring cutoff", run_info.get("secondary_cutoff")),
        ("Weights", run_info.get("weights")),
    ]
    for k, v in rows:
        info.append([k, str(v) if v is not None else ""])
    for cell in info["A"]:
        cell.font = Font(bold=True)
    for row in info.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError as e:
        raise PermissionError(
            f"Could not write {path.name} - is it open in Excel? "
            "Close it and re-run."
        ) from e
