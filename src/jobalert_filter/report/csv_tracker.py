"""tracker.csv — a spreadsheet you edit to keep track of applications.

Written as UTF-8 with BOM and ``;`` separators so Excel (Turkish locale included)
opens it correctly. On every run the ``status`` and ``note`` columns are read back
into the database first, then the file is rewritten with fresh scores.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from jobalert_filter.report import classify
from jobalert_filter.store import JobRow, Store, normalize_status

log = logging.getLogger(__name__)

COLUMNS = ["job_id", "status", "note", "score", "bucket", "title", "company", "location",
           "workplace", "first_seen", "url", "why"]


def import_statuses(path: Path, store: Store) -> int:
    """Copy edited status/note values from the CSV into the store. Returns changes made."""
    if not path.exists():
        return 0
    current = {row.job_id: row for row in store.rows()}
    changed = 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for record in csv.DictReader(fh, delimiter=";"):
            job_id = (record.get("job_id") or "").strip()
            if job_id not in current:
                continue
            raw_status = record.get("status") or ""
            status = normalize_status(raw_status)
            if status is None:
                log.warning("tracker.csv: unknown status %r for job %s, ignored",
                            raw_status, job_id)
                continue
            note = (record.get("note") or "").strip()
            row = current[job_id]
            if status != row.status or note != row.note:
                store.set_status(job_id, status, note)
                changed += 1
    return changed


def write_tracker(path: Path, rows: list[JobRow], min_score: int) -> bool:
    """Rewrite the tracker. Returns False if the file is locked (e.g. open in Excel)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(COLUMNS)
        for row in rows:
            why = row.llm_reasons + row.rule_reasons + [f"- {m}" for m in row.llm_missing]
            writer.writerow([
                row.job_id, row.status, row.note,
                "" if row.final_score is None else row.final_score,
                classify(row, min_score), row.title, row.company, row.location, row.workplace,
                row.first_seen[:10], row.url, " | ".join(why),
            ])
    try:
        tmp.replace(path)
    except PermissionError:
        log.warning("%s is open in another program; close it and run again to refresh it", path)
        tmp.unlink(missing_ok=True)
        return False
    return True
