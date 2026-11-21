"""Normalize project weekly/daily report workbooks into MiniApp rows.

The script is intentionally deterministic and offline: it reads Excel/CSV files
from the agent workspace, applies a per-customer profile, and writes normalized
CSV plus a manifest. The agent then imports those CSV files through
``app_import_rows``; this module never calls the Octop API or the database.

Usage::

    python skills/pm-report-ingest/scripts/ingest.py \
        --input reports/ \
        --profile reports/import-profile.json \
        --out reports/normalized/
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_PROFILE_PATH = (
    Path(__file__).resolve().parent.parent / "references" / "profile.default.json"
)
SUPPORTED_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xls", ".csv", ".tsv"})
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_ROWS_PER_FILE = 100_000

TASK_FIELDS = frozenset(
    {
        "project",
        "period",
        "task_id",
        "task_name",
        "owner",
        "status",
        "progress",
        "priority",
        "start_date",
        "due_date",
        "notes",
    }
)
TASK_OUTPUT_COLUMNS = (
    "project",
    "period",
    "task_id",
    "task_name",
    "owner",
    "status",
    "progress",
    "priority",
    "start_date",
    "due_date",
    "health",
    "days_late",
    "notes",
    "batch_id",
)
REPORT_OUTPUT_COLUMNS = (
    "batch_id",
    "project",
    "period",
    "period_type",
    "source_file",
    "source_hash",
    "row_count",
    "imported_at",
    "status",
    "error_summary",
)
STATUS_VALUES = frozenset({"not_started", "doing", "blocked", "done"})
PRIORITY_VALUES = frozenset({"low", "medium", "high"})
HEALTH_VALUES = frozenset({"on_track", "at_risk", "delayed"})

_PERIOD_WEEK_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>\d{1,2})$", re.IGNORECASE)
_PERIOD_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")
_TOTAL_ROW_RE = re.compile(r"^(?:合计|小计|总计|总合计|subtotal|total)\b", re.IGNORECASE)


def _normalize_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value).strip()


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass(frozen=True)
class Profile:
    """One customer's Excel layout and value mappings."""

    version: int = 1
    file_pattern: re.Pattern[str] | None = None
    sheet: str | None = None
    header_row: int | None = None
    columns: Mapping[str, str] = field(default_factory=dict)
    status_map: Mapping[str, str] = field(default_factory=dict)
    priority_map: Mapping[str, str] = field(default_factory=dict)
    task_id_fallback: tuple[str, ...] = ("project", "task_name")
    date_formats: tuple[str, ...] = ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日")
    skip_row_patterns: tuple[re.Pattern[str], ...] = ()
    pii_columns: frozenset[str] = frozenset()
    at_risk_days: int = 3
    delayed_statuses: frozenset[str] = frozenset({"blocked"})
    default_period_type: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Profile:
        if not isinstance(raw, Mapping):
            raise ValueError("profile must be a JSON object")
        columns_raw = raw.get("columns")
        if not isinstance(columns_raw, Mapping):
            raise ValueError("profile.columns must be an object")
        columns: dict[str, str] = {}
        for source, canonical in columns_raw.items():
            field_name = str(canonical or "").strip()
            if field_name not in TASK_FIELDS:
                raise ValueError(f"unknown target column: {canonical!r}")
            columns[str(source)] = field_name
        pattern_raw = str(raw.get("file_pattern") or "").strip()
        try:
            pattern = re.compile(pattern_raw) if pattern_raw else None
        except re.error as exc:
            raise ValueError(f"invalid file_pattern: {exc}") from exc
        skip_patterns: list[re.Pattern[str]] = []
        for item in _as_str_list(raw.get("skip_row_patterns")):
            try:
                skip_patterns.append(re.compile(item, re.IGNORECASE))
            except re.error as exc:
                raise ValueError(f"invalid skip_row_patterns entry {item!r}: {exc}") from exc
        health = raw.get("health_rules")
        health = health if isinstance(health, Mapping) else {}
        status_map = raw.get("status_map")
        priority_map = raw.get("priority_map")
        return cls(
            version=int(raw.get("version") or 1),
            file_pattern=pattern,
            sheet=str(raw["sheet"]).strip() if raw.get("sheet") else None,
            header_row=int(raw["header_row"]) if isinstance(raw.get("header_row"), int) else None,
            columns=columns,
            status_map={
                str(key).strip(): str(value).strip()
                for key, value in (status_map or {}).items()
                if str(key).strip() and str(value).strip()
            }
            if isinstance(status_map, Mapping)
            else {},
            priority_map={
                str(key).strip(): str(value).strip()
                for key, value in (priority_map or {}).items()
                if str(key).strip() and str(value).strip()
            }
            if isinstance(priority_map, Mapping)
            else {},
            task_id_fallback=tuple(
                field_name
                for field_name in _as_str_list(raw.get("task_id_fallback"))
                if field_name in TASK_FIELDS
            )
            or ("project", "task_name"),
            date_formats=tuple(_as_str_list(raw.get("date_formats")))
            or ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"),
            skip_row_patterns=tuple(skip_patterns),
            pii_columns=frozenset(_as_str_list(raw.get("pii_columns"))),
            at_risk_days=max(0, int(health.get("at_risk_days", 3))),
            delayed_statuses=frozenset(
                str(item).strip()
                for item in health.get("delayed_statuses", ["blocked"])
                if str(item).strip()
            ),
            default_period_type=(
                str(raw["default_period_type"]).strip() if raw.get("default_period_type") else None
            ),
        )


def load_profile(path: Path | str | None) -> Profile:
    """Load a profile file, defaulting to the one shipped with the skill."""

    target = Path(path) if path else DEFAULT_PROFILE_PATH
    raw = json.loads(target.read_text(encoding="utf-8"))
    return Profile.from_dict(raw)


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("cannot decode text file; expected UTF-8, GB18030 or UTF-16")


def _read_csv(path: Path, suffix: str) -> list[list[str]]:
    text = _decode_bytes(path.read_bytes())
    sample = text[:4096]
    delimiter = "\t" if suffix == ".tsv" else ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel_tab if delimiter == "\t" else csv.excel
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


def _read_xlsx(path: Path, sheet_name: str | None) -> tuple[str, list[list[str]]]:
    from openpyxl import load_workbook  # noqa: PLC0415

    workbook = load_workbook(filename=path, read_only=False, data_only=True)
    try:
        if sheet_name:
            if sheet_name not in workbook.sheetnames:
                raise ValueError(f"sheet not found: {sheet_name!r}")
            sheet = workbook[sheet_name]
        else:
            sheet = workbook.active
        if sheet is None:
            raise ValueError("workbook has no active sheet")
        if int(sheet.max_row or 0) > MAX_ROWS_PER_FILE:
            raise ValueError(f"sheet exceeds {MAX_ROWS_PER_FILE} rows")
        max_col = max(int(sheet.max_column or 0), 1)
        matrix = [
            [_cell_text(value) for value in row]
            for row in sheet.iter_rows(min_row=1, max_col=max_col, values_only=True)
        ]
        for merged in sheet.merged_cells.ranges:
            value = ""
            if merged.min_row <= len(matrix):
                row = matrix[merged.min_row - 1]
                if merged.min_col <= len(row):
                    value = row[merged.min_col - 1]
            for row_index in range(merged.min_row - 1, min(merged.max_row, len(matrix))):
                for col_index in range(
                    merged.min_col - 1, min(merged.max_col, len(matrix[row_index]))
                ):
                    matrix[row_index][col_index] = value
        return sheet.title, matrix
    finally:
        workbook.close()


def _read_xls(path: Path, sheet_name: str | None) -> tuple[str, list[list[str]]]:
    import xlrd  # noqa: PLC0415

    workbook = xlrd.open_workbook(file_contents=path.read_bytes())
    if sheet_name:
        try:
            sheet = workbook.sheet_by_name(sheet_name)
        except xlrd.XLRDError as exc:
            raise ValueError(f"sheet not found: {sheet_name!r}") from exc
    else:
        sheet = workbook.sheet_by_index(0)
    if sheet.nrows > MAX_ROWS_PER_FILE:
        raise ValueError(f"sheet exceeds {MAX_ROWS_PER_FILE} rows")
    matrix: list[list[str]] = []
    for row_index in range(sheet.nrows):
        row: list[str] = []
        for col_index in range(sheet.ncols):
            cell = sheet.cell(row_index, col_index)
            if cell.ctype == xlrd.XL_CELL_DATE:
                row.append(_cell_text(xlrd.xldate_as_datetime(cell.value, workbook.datemode)))
            else:
                row.append(_cell_text(cell.value))
        matrix.append(row)
    return str(sheet.name), matrix


def read_matrix(path: Path, profile: Profile) -> tuple[str, list[list[str]]]:
    """Read one workbook or text file into a string matrix."""

    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(path, profile.sheet)
    if suffix == ".xls":
        return _read_xls(path, profile.sheet)
    if suffix in {".csv", ".tsv"}:
        return path.stem, _read_csv(path, suffix)
    raise ValueError(f"unsupported file type: {path.suffix}")


def detect_header_row(
    matrix: Sequence[Sequence[str]], profile: Profile, *, max_scan: int = 15
) -> int:
    """Return the 0-based header row index."""

    if profile.header_row is not None:
        if profile.header_row >= len(matrix):
            raise ValueError(f"header_row {profile.header_row} is outside the sheet")
        return profile.header_row
    wanted = {_normalize_header(source) for source in profile.columns}
    best_index = 0
    best_score = 0
    for index, row in enumerate(matrix[:max_scan]):
        score = sum(1 for value in row if _normalize_header(value) in wanted)
        if score > best_score:
            best_index = index
            best_score = score
    if best_score < 2:
        raise ValueError(
            "cannot detect the header row; set profile.header_row or check column names"
        )
    return best_index


def _header_positions(
    header: Sequence[str],
    profile: Profile,
    *,
    include_pii: bool,
    warnings: list[str],
) -> dict[str, int]:
    normalized = {
        _normalize_header(value): index
        for index, value in enumerate(header)
        if _normalize_header(value)
    }
    pii = {_normalize_header(item) for item in profile.pii_columns}
    found: dict[str, list[tuple[str, int]]] = {}
    for source, canonical in profile.columns.items():
        key = _normalize_header(source)
        index = normalized.get(key)
        if index is None:
            continue
        if key in pii and not include_pii:
            warnings.append(f"skipped PII column: {source}")
            continue
        found.setdefault(canonical, []).append((source, index))
    positions: dict[str, int] = {}
    for canonical, matches in found.items():
        if len(matches) > 1:
            warnings.append(
                f"duplicate mapping for {canonical}: {', '.join(source for source, _ in matches)}; "
                f"using {matches[0][0]}"
            )
        positions[canonical] = matches[0][1]
    return positions


def _row_value(row: Sequence[str], positions: Mapping[str, int], key: str) -> str:
    index = positions.get(key)
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _parse_date(value: str, formats: Sequence[str]) -> str:
    text = value.strip()
    if not text:
        return ""
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid date: {text!r}") from exc


def _parse_progress(value: str) -> int:
    text = value.strip()
    if not text:
        return 0
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"invalid progress: {value!r}") from exc
    if not percent and 0 < number < 1:
        number *= 100
    return max(0, min(100, int(round(number))))


def _normalize_period(value: str) -> str:
    """Normalize week labels so lexical sorting matches calendar order."""

    text = value.strip()
    week = _PERIOD_WEEK_RE.match(text)
    if week:
        return f"{int(week.group('year')):04d}-W{int(week.group('week')):02d}"
    if " " in text or "T" in text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    return text


def _period_end(period: str) -> date | None:
    text = period.strip()
    week = _PERIOD_WEEK_RE.match(text)
    if week:
        try:
            return date.fromisocalendar(int(week.group("year")), int(week.group("week")), 7)
        except ValueError:
            return None
    month = _PERIOD_MONTH_RE.match(text)
    if month:
        year = int(month.group("year"))
        month_value = int(month.group("month"))
        if not 1 <= month_value <= 12:
            return None
        if month_value == 12:
            return date(year, 12, 31)
        return date(year, month_value + 1, 1) - timedelta(days=1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _period_type(period: str, default: str | None) -> str:
    text = period.strip()
    if _PERIOD_WEEK_RE.match(text):
        return "weekly"
    if _PERIOD_MONTH_RE.match(text):
        return "monthly"
    try:
        date.fromisoformat(text)
    except ValueError:
        return default or "weekly"
    return "daily"


def batch_id(source_hash: str, period: str) -> str:
    """Stable provenance id for one file + period pair."""

    return hashlib.sha256(f"{source_hash}:{period}".encode()).hexdigest()[:12]


def _status(value: str, profile: Profile) -> str:
    text = value.strip()
    if not text:
        return "not_started"
    mapped = profile.status_map.get(text, text).strip().lower()
    if mapped in STATUS_VALUES:
        return mapped
    raise ValueError(f"unknown status: {value!r}; add it to profile.status_map")


def _priority(value: str, profile: Profile, warnings: list[str]) -> str:
    text = value.strip()
    if not text:
        return ""
    mapped = profile.priority_map.get(text, text).strip().lower()
    if mapped in PRIORITY_VALUES:
        return mapped
    warnings.append(f"unknown priority {value!r}; left empty")
    return ""


def _health(
    *,
    status: str,
    due_date: str,
    period_end: date | None,
    reference_date: date,
    profile: Profile,
) -> tuple[str, int]:
    if status == "done":
        return "on_track", 0
    end = period_end or reference_date
    due = date.fromisoformat(due_date) if due_date else None
    days_late = max(0, (end - due).days) if due and due < end else 0
    if status in profile.delayed_statuses or days_late > 0:
        return "delayed", days_late
    if due and (due - end).days <= profile.at_risk_days:
        return "at_risk", 0
    return "on_track", 0


def _skip_row(values: Sequence[str], profile: Profile) -> bool:
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if any(pattern.search(text) for pattern in profile.skip_row_patterns):
            return True
        if _TOTAL_ROW_RE.search(text):
            return True
    return False


def _resolve_dimension(
    row_value: str,
    match: re.Match[str] | None,
    group: str,
    override: str,
) -> str:
    text = row_value.strip()
    if text:
        return text
    if override.strip():
        return override.strip()
    if match is not None:
        captured = match.groupdict().get(group)
        if captured:
            return str(captured).strip()
    return ""


@dataclass
class FileReport:
    """One source file's normalized output and diagnostics."""

    path: str
    source_hash: str
    sheet: str
    periods: set[str] = field(default_factory=set)
    tasks: list[dict[str, str]] = field(default_factory=list)
    reports: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)


def _task_row(
    raw: Mapping[str, str],
    *,
    project: str,
    period: str,
    profile: Profile,
    period_end: date | None,
    reference_date: date,
    batch: str,
    warnings: list[str],
) -> dict[str, str]:
    task_name = str(raw.get("task_name") or "").strip()
    if not task_name:
        raise ValueError("missing task_name")
    task_id = str(raw.get("task_id") or "").strip()
    if not task_id:
        parts = [str(raw.get(name) or "").strip() for name in profile.task_id_fallback]
        if not any(parts):
            parts = [project, task_name]
        task_id = "::".join(part for part in parts if part)
    status = _status(str(raw.get("status") or ""), profile)
    priority = _priority(str(raw.get("priority") or ""), profile, warnings)
    progress = _parse_progress(str(raw.get("progress") or ""))
    start_date = _parse_date(str(raw.get("start_date") or ""), profile.date_formats)
    due_date = _parse_date(str(raw.get("due_date") or ""), profile.date_formats)
    health, days_late = _health(
        status=status,
        due_date=due_date,
        period_end=period_end,
        reference_date=reference_date,
        profile=profile,
    )
    return {
        "project": project,
        "period": period,
        "task_id": task_id,
        "task_name": task_name,
        "owner": str(raw.get("owner") or "").strip(),
        "status": status,
        "progress": str(progress),
        "priority": priority,
        "start_date": start_date,
        "due_date": due_date,
        "health": health,
        "days_late": str(days_late),
        "notes": str(raw.get("notes") or "").strip(),
        "batch_id": batch,
    }


def process_file(
    path: Path,
    *,
    root: Path,
    profile: Profile,
    include_pii: bool = False,
    period_override: str = "",
    now: datetime | None = None,
) -> FileReport:
    """Parse one file and return normalized rows plus diagnostics."""

    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB")
    data = path.read_bytes()
    source_hash = hashlib.sha256(data).hexdigest()
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    match = profile.file_pattern.search(path.name) if profile.file_pattern else None
    sheet, matrix = read_matrix(path, profile)
    if len(matrix) > MAX_ROWS_PER_FILE:
        raise ValueError(f"sheet exceeds {MAX_ROWS_PER_FILE} rows")
    header_index = detect_header_row(matrix, profile)
    warnings: list[str] = []
    positions = _header_positions(
        matrix[header_index],
        profile,
        include_pii=include_pii,
        warnings=warnings,
    )
    if "task_name" not in positions:
        raise ValueError("profile does not map a task_name column")
    reference = now or datetime.now(UTC)
    reference_date = reference.date()
    report = FileReport(path=relative, source_hash=source_hash, sheet=sheet, warnings=warnings)
    grouped: dict[str, list[dict[str, str]]] = {}
    grouped_errors: dict[str, list[str]] = {}
    grouped_projects: dict[str, set[str]] = {}

    for row_number, row in enumerate(matrix[header_index + 1 :], start=header_index + 2):
        if not any(str(value).strip() for value in row):
            continue
        if _skip_row(row, profile):
            warnings.append(f"skipped total row at line {row_number}")
            continue
        raw = {key: _row_value(row, positions, key) for key in positions}
        project = _resolve_dimension(
            raw.get("project", ""),
            match,
            "project",
            "",
        )
        period = _normalize_period(
            _resolve_dimension(
                raw.get("period", ""),
                match,
                "period",
                period_override,
            )
        )
        try:
            if not project:
                raise ValueError("missing project; add a project column or name the file")
            if not period:
                raise ValueError("missing period; add a period column or name the file")
            batch = batch_id(source_hash, period)
            period_end = _period_end(period)
            if period_end is None:
                warning = (
                    f"period {period!r} has no comparable end date; health uses the reference date"
                )
                if warning not in warnings:
                    warnings.append(warning)
            task = _task_row(
                raw,
                project=project,
                period=period,
                profile=profile,
                period_end=period_end,
                reference_date=reference_date,
                batch=batch,
                warnings=warnings,
            )
        except ValueError as exc:
            if period:
                grouped_errors.setdefault(period, []).append(f"line {row_number}: {exc}")
            report.failed.append({"line": row_number, "error": str(exc)})
            continue
        grouped.setdefault(period, []).append(task)
        grouped_projects.setdefault(period, set()).add(project)

    imported_at = reference.astimezone(UTC).isoformat()
    for period, tasks in grouped.items():
        errors = grouped_errors.get(period, [])
        projects = grouped_projects.get(period, set())
        report.periods.add(period)
        report.tasks.extend(tasks)
        status = "imported" if not errors else "partial"
        report.reports.append(
            {
                "batch_id": batch_id(source_hash, period),
                "project": next(iter(projects)) if len(projects) == 1 else "",
                "period": period,
                "period_type": _period_type(period, profile.default_period_type),
                "source_file": relative,
                "source_hash": source_hash,
                "row_count": str(len(tasks)),
                "imported_at": imported_at,
                "status": status,
                "error_summary": "; ".join(errors[:5]),
            }
        )
    for period, errors in grouped_errors.items():
        if period in grouped:
            continue
        report.periods.add(period)
        report.reports.append(
            {
                "batch_id": batch_id(source_hash, period),
                "project": "",
                "period": period,
                "period_type": _period_type(period, profile.default_period_type),
                "source_file": relative,
                "source_hash": source_hash,
                "row_count": "0",
                "imported_at": imported_at,
                "status": "failed",
                "error_summary": "; ".join(errors[:5]),
            }
        )
    return report


def _iter_input_files(inputs: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for item in inputs:
        if item.is_file():
            candidates = [item]
            scanning_dir = False
        elif item.is_dir():
            candidates = sorted(item.rglob("*"))
            scanning_dir = True
        else:
            continue
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            if any(part.startswith(".") for part in candidate.parts):
                continue
            if scanning_dir and any(
                part in {"normalized", "skills", "app_templates"} for part in candidate.parts
            ):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(candidate)
    return files


def _write_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: str(row.get(column, "")) for column in columns})


def _summary(reports: Sequence[FileReport], out_dir: Path) -> dict[str, Any]:
    tasks = [task for report in reports for task in report.tasks]
    failed = [item for report in reports for item in report.failed]
    return {
        "files": len(reports),
        "tasks": len(tasks),
        "failed_rows": len(failed),
        "periods": sorted({task["period"] for task in tasks}),
        "warnings": [warning for report in reports for warning in report.warnings],
        "outputs": {
            "tasks": str(out_dir / "tasks.csv"),
            "period_reports": str(out_dir / "period_reports.csv"),
            "manifest": str(out_dir / "manifest.json"),
        },
        "file_details": [
            {
                "path": report.path,
                "sha256": report.source_hash,
                "sheet": report.sheet,
                "periods": sorted(report.periods),
                "tasks": len(report.tasks),
                "failed": len(report.failed),
                "warnings": report.warnings,
            }
            for report in reports
        ],
        "failed": failed,
    }


def run(
    inputs: Sequence[Path],
    *,
    profile: Profile,
    out_dir: Path,
    include_pii: bool = False,
    period_override: str = "",
    now: datetime | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Normalize every input file and write the three output artifacts."""

    files = _iter_input_files(inputs)
    if not files:
        raise FileNotFoundError("no supported Excel/CSV files found")
    base = root or Path.cwd()
    reports = [
        process_file(
            path,
            root=base,
            profile=profile,
            include_pii=include_pii,
            period_override=period_override,
            now=now,
        )
        for path in files
    ]
    unique_tasks: dict[tuple[str, str, str], dict[str, str]] = {}
    duplicates: list[str] = []
    for report in reports:
        for task in report.tasks:
            key = (task["project"], task["period"], task["task_id"])
            if key in unique_tasks:
                duplicates.append("::".join(key))
            unique_tasks[key] = task
    tasks = sorted(
        unique_tasks.values(),
        key=lambda item: (item["period"], item["project"], item["task_id"]),
    )
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task["batch_id"]] = counts.get(task["batch_id"], 0) + 1
    report_rows = sorted(
        (row for report in reports for row in report.reports),
        key=lambda item: (item["period"], item["project"], item["batch_id"]),
    )
    for row in report_rows:
        row["row_count"] = str(counts.get(row["batch_id"], 0))
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "tasks.csv", TASK_OUTPUT_COLUMNS, tasks)
    _write_csv(out_dir / "period_reports.csv", REPORT_OUTPUT_COLUMNS, report_rows)
    summary = _summary(reports, out_dir)
    summary["tasks"] = len(tasks)
    if duplicates:
        summary["warnings"].append(
            "duplicate task rows collapsed: " + ", ".join(sorted(set(duplicates))[:5])
        )
    if len(tasks) > 20000:
        summary["warnings"].append(
            "more than 20,000 task rows; the MiniApp table limit is 20,000 — "
            "split reports by year or archive older periods"
        )
    summary["generated_at"] = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    summary["profile_version"] = profile.version
    (out_dir / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize project reports for a MiniApp.")
    parser.add_argument(
        "--input",
        nargs="+",
        required=True,
        help="Files or directories to scan.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Profile JSON; defaults to the skill's profile.default.json.",
    )
    parser.add_argument(
        "--out",
        default="reports/normalized",
        help="Output directory (default: reports/normalized).",
    )
    parser.add_argument(
        "--period",
        default="",
        help="Override period when the file name and sheet do not carry one.",
    )
    parser.add_argument(
        "--include-pii",
        action="store_true",
        help="Keep mapped columns listed in profile.pii_columns.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-8601 timestamp for deterministic runs and tests.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
    inputs = [Path(item) for item in args.input]
    out_dir = Path(args.out)
    try:
        profile = load_profile(args.profile)
        summary = run(
            inputs,
            profile=profile,
            out_dir=out_dir,
            include_pii=args.include_pii,
            period_override=args.period,
            now=now,
            root=Path.cwd(),
        )
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 3
    print(
        json.dumps(
            {
                "files": summary["files"],
                "tasks": summary["tasks"],
                "failed_rows": summary["failed_rows"],
                "periods": summary["periods"],
                "manifest": summary["outputs"]["manifest"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["tasks"] and not summary["failed_rows"] else 2


if __name__ == "__main__":
    sys.exit(main())
