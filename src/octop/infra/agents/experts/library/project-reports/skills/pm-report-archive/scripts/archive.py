"""Split an exported task ledger into archived and retained CSV files.

The agent cannot download an app export by itself (no dashboard JWT), so the
owner exports the main table from the app detail page first. This script then
splits that export by report period and writes:

- ``tasks_archived.csv``: rows older than the cutoff
- ``tasks_retained.csv``: rows at or after the cutoff, ready for a dry-run
  ``app_import_rows(mode="replace")``
- ``archive_summary.json``: counts per period and the cutoff used

It never deletes the source export or any app data.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_WEEK_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>\d{1,2})$", re.IGNORECASE)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("cannot decode CSV; expected UTF-8, GB18030 or UTF-16")


def normalize_period(value: str) -> str:
    """Zero-pad week labels and strip time from date/datetime labels."""

    text = value.strip()
    week = _WEEK_RE.match(text)
    if week:
        return f"{int(week.group('year')):04d}-W{int(week.group('week')):02d}"
    if " " in text or "T" in text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    return text


def _period_kind(value: str) -> str:
    text = value.strip()
    if _WEEK_RE.match(text):
        return "week"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "day"
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return "month"
    return "other"


def split_rows(
    rows: list[dict[str, str]], cutoff: str
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return ``(retained, archived)`` using lexical period order."""

    clean_cutoff = normalize_period(cutoff)
    if not clean_cutoff:
        raise ValueError("cutoff is empty")
    cutoff_kind = _period_kind(clean_cutoff)
    retained: list[dict[str, str]] = []
    archived: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):
        raw_period = str(row.get("period") or "").strip()
        if not raw_period:
            raise ValueError(f"row {index} has an empty period")
        period = normalize_period(raw_period)
        if cutoff_kind != "other" and _period_kind(period) != cutoff_kind:
            raise ValueError(
                f"row {index} period {raw_period!r} does not match cutoff kind {cutoff_kind!r}"
            )
        if period >= clean_cutoff:
            retained.append(row)
        else:
            archived.append(row)
    return retained, archived


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: str(row.get(name, "")) for name in fieldnames})


def run(
    input_path: Path,
    out_dir: Path,
    *,
    keep_from: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Split one export and write retained/archived CSV plus a summary."""

    text = _decode(input_path.read_bytes())
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = list(reader.fieldnames or [])
    if "period" not in fieldnames:
        raise ValueError("export has no 'period' column")
    rows = list(reader)
    if not rows:
        raise ValueError("export has no data rows")
    retained, archived = split_rows(rows, keep_from)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "tasks_retained.csv", fieldnames, retained)
    _write_csv(out_dir / "tasks_archived.csv", fieldnames, archived)
    retained_periods = Counter(normalize_period(row.get("period", "")) for row in retained)
    archived_periods = Counter(normalize_period(row.get("period", "")) for row in archived)
    summary: dict[str, Any] = {
        "source": input_path.as_posix(),
        "keep_from": normalize_period(keep_from),
        "rows": len(rows),
        "retained": len(retained),
        "archived": len(archived),
        "retained_periods": dict(sorted(retained_periods.items())),
        "archived_periods": dict(sorted(archived_periods.items())),
        "outputs": {
            "retained": str(out_dir / "tasks_retained.csv"),
            "archived": str(out_dir / "tasks_archived.csv"),
        },
        "generated_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
    }
    (out_dir / "archive_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split a project report export for archiving.")
    parser.add_argument("--input", required=True, help="Exported main-table CSV.")
    parser.add_argument(
        "--keep-from",
        required=True,
        help="Keep rows at or after this period (e.g. 2026-W01 or 2026-01-01).",
    )
    parser.add_argument(
        "--out",
        default="reports/normalized",
        help="Output directory (default: reports/normalized).",
    )
    parser.add_argument("--now", default=None, help="ISO-8601 timestamp for deterministic runs.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
    try:
        summary = run(
            Path(args.input),
            Path(args.out),
            keep_from=args.keep_from,
            now=now,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "rows": summary["rows"],
                "retained": summary["retained"],
                "archived": summary["archived"],
                "summary": str(Path(args.out) / "archive_summary.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
