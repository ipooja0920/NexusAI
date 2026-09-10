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
    "distance_score", "employee_score", "revenue_score",
] + FUNDING_COLUMNS + [
    "embedding_key",
]


def write_enriched(path: Path, records: List[dict]) -> None:
    """Rebuild the enriched file from scratch. Always a full overwrite: the
    raw datasource is the single source of truth, so stale rows can never
    linger from a previous run."""
    df = pd.DataFrame(records, columns=ENRICHED_COLUMNS)
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

RESULT_HEADERS = [
    "Rank", "Company", "Website", "Distance (mi)", "Employees", "Revenue ($USDmm)",
    "Distance Score", "Employee Score", "Revenue Score",
    "Alignment Score (1-9)", "Alignment Reason",
    "Partnership Score (1-9)", "Partnership Reason",
    "Funding Score (1-9)", "Funding Reason",
    "Overall Score", "Company Description",
]

COL_WIDTHS = {
    "A": 6, "B": 32, "C": 26, "D": 12, "E": 11, "F": 16,
    "G": 13, "H": 13, "I": 13, "J": 12, "K": 45, "L": 12, "M": 45,
    "N": 12, "O": 45, "P": 12, "Q": 60,
}


ALL_HEADERS = [
    "Stage", "Rank", "Company", "Website", "Distance (mi)", "Employees",
    "Revenue ($USDmm)", "Distance Score", "Employee Score", "Revenue Score",
    "Alignment Score (1-9)", "Alignment Reason",
    "Partnership Score (1-9)", "Partnership Reason",
    "Funding Score (1-9)", "Funding Reason",
    "Overall Score", "Company Description",
]

ALL_COL_WIDTHS = {
    "A": 22, "B": 6, "C": 32, "D": 26, "E": 12, "F": 11, "G": 16,
    "H": 13, "I": 13, "J": 13, "K": 12, "L": 45, "M": 12, "N": 45,
    "O": 12, "P": 45, "Q": 12, "R": 60,
}


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


def write_faculty_output(path: Path, faculty: dict, results: List[dict],
                         run_info: dict, all_rows: Optional[List[dict]] = None) -> None:
    wb = openpyxl.Workbook()

    # ---- Results sheet ----
    ws = wb.active
    ws.title = "Top Matches"
    ws.append(RESULT_HEADERS)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)

    for r in results:
        ws.append([
            r["rank"], r["company"], r.get("website"), r.get("distance_miles"),
            r.get("employees"), r.get("revenue_usdmm"),
            round(r["distance_score"], 4), round(r["employee_score"], 4),
            round(r["revenue_score"], 4),
            r.get("alignment_score"), r.get("alignment_reason"),
            r.get("partnership_score"), r.get("partnership_reason"),
            r.get("funding_score"), r.get("funding_reason"),
            round(r["overall_score"], 4), r.get("description"),
        ])

    for letter, width in COL_WIDTHS.items():
        ws.column_dimensions[letter].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "C2"

    # ---- All Companies sheet (full funnel visibility, v1-style) ----
    if all_rows:
        wa = wb.create_sheet("All Companies")
        wa.append(ALL_HEADERS)
        for cell in wa[1]:
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for r in all_rows:
            def _r4(v):
                return round(v, 4) if isinstance(v, (int, float)) else v
            wa.append([
                r.get("stage"), r.get("rank"), r["company"], r.get("website"),
                r.get("distance_miles"), r.get("employees"), r.get("revenue_usdmm"),
                _r4(r.get("distance_score")), _r4(r.get("employee_score")),
                _r4(r.get("revenue_score")),
                r.get("alignment_score"), r.get("alignment_reason"),
                r.get("partnership_score"), r.get("partnership_reason"),
                r.get("funding_score"), r.get("funding_reason"),
                _r4(r.get("overall_score")), r.get("description"),
            ])
        for letter, width in ALL_COL_WIDTHS.items():
            wa.column_dimensions[letter].width = width
        for row in wa.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        wa.freeze_panes = "D2"

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
        ("Candidates after pre-filter", run_info.get("n_candidates")),
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
