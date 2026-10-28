"""SQLite storage: processed emails, unique jobs, scores and your application status."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jobalert_filter.models import Job, LlmResult, RuleResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    message_id   TEXT PRIMARY KEY,
    subject      TEXT,
    sender       TEXT,
    received_at  TEXT,
    job_count    INTEGER,
    processed_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    company     TEXT,
    location    TEXT,
    workplace   TEXT,
    url         TEXT,
    source      TEXT,
    extra       TEXT,
    description TEXT,
    first_seen  TEXT,
    last_seen   TEXT,
    seen_count  INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS scores (
    job_id        TEXT PRIMARY KEY REFERENCES jobs(job_id),
    rule_score    INTEGER,
    rejected      INTEGER,
    rule_reasons  TEXT,
    llm_score     INTEGER,
    llm_verdict   TEXT,
    llm_reasons   TEXT,
    llm_missing   TEXT,
    llm_model     TEXT,
    llm_profile   TEXT,
    llm_has_desc  INTEGER DEFAULT 0,
    final_score   INTEGER,
    scored_at     TEXT
);
CREATE TABLE IF NOT EXISTS status (
    job_id     TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    note       TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS notified (
    job_id TEXT PRIMARY KEY,
    at     TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

STATUSES = ("new", "applied", "interview", "offer", "rejected", "ignored")
STATUS_ALIASES = {
    "": "new", "yeni": "new",
    "basvuruldu": "applied", "başvuruldu": "applied", "basvurdum": "applied",
    "başvurdum": "applied",
    "mulakat": "interview", "mülakat": "interview",
    "teklif": "offer",
    "ret": "rejected", "red": "rejected", "reddedildi": "rejected",
    "ilgilenmiyorum": "ignored", "gizle": "ignored", "not interested": "ignored",
}


def normalize_status(value: str) -> str | None:
    value = value.strip().lower()
    if value in STATUSES:
        return value
    return STATUS_ALIASES.get(value)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


@dataclass
class JobRow:
    job_id: str
    title: str
    company: str
    location: str
    workplace: str
    url: str
    source: str
    extra: list[str]
    description: str
    first_seen: str
    last_seen: str
    seen_count: int
    rule_score: int | None
    rejected: bool
    rule_reasons: list[str]
    llm_score: int | None
    llm_verdict: str | None
    llm_reasons: list[str]
    llm_missing: list[str]
    final_score: int | None
    status: str
    note: str

    def as_job(self) -> Job:
        return Job(
            job_id=self.job_id, title=self.title, url=self.url, company=self.company,
            location=self.location, workplace=self.workplace, source=self.source,
            description=self.description, extra=self.extra,
        )


class Store:
    def __init__(self, path: Path):
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- meta -------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # --- emails & jobs ----------------------------------------------------
    def known_message_ids(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT message_id FROM emails")}

    def add_email(self, message_id: str, subject: str, sender: str,
                  received_at: datetime | None, jobs: list[Job]) -> int:
        """Store an email and its jobs; returns how many jobs were new."""
        new = 0
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO emails VALUES(?, ?, ?, ?, ?, ?)",
                (message_id, subject, sender, _iso(received_at), len(jobs), _now()),
            )
            for job in jobs:
                seen = _iso(job.received_at) or _now()
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO jobs(job_id, title, company, location, workplace, url,"
                    " source, extra, description, first_seen, last_seen)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job.job_id, job.title, job.company, job.location, job.workplace, job.url,
                     job.source, json.dumps(job.extra, ensure_ascii=False), job.description,
                     seen, seen),
                )
                if cur.rowcount:
                    new += 1
                else:
                    self.db.execute(
                        "UPDATE jobs SET seen_count = seen_count + 1,"
                        " last_seen = MAX(last_seen, ?),"
                        " company = CASE WHEN company = '' THEN ? ELSE company END,"
                        " location = CASE WHEN location = '' THEN ? ELSE location END,"
                        " workplace = CASE WHEN workplace = '' THEN ? ELSE workplace END"
                        " WHERE job_id = ?",
                        (seen, job.company, job.location, job.workplace, job.job_id),
                    )
        return new

    def set_description(self, job_id: str, description: str) -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET description = ? WHERE job_id = ?",
                            (description, job_id))

    # --- scores -----------------------------------------------------------
    def save_rule_result(self, job_id: str, result: RuleResult) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO scores(job_id, rule_score, rejected, rule_reasons, scored_at)"
                " VALUES(?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET"
                " rule_score = excluded.rule_score, rejected = excluded.rejected,"
                " rule_reasons = excluded.rule_reasons, scored_at = excluded.scored_at",
                (job_id, result.score, int(result.rejected),
                 json.dumps(result.reasons, ensure_ascii=False), _now()),
            )

    def needs_llm(self, job_id: str, model: str, profile_fp: str, has_description: bool) -> bool:
        row = self.db.execute(
            "SELECT llm_model, llm_profile, llm_has_desc FROM scores WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not row or row["llm_model"] is None:
            return True
        return (row["llm_model"] != model or row["llm_profile"] != profile_fp
                or (has_description and not row["llm_has_desc"]))

    def save_llm_result(self, job_id: str, result: LlmResult, profile_fp: str,
                        has_description: bool) -> None:
        with self.db:
            self.db.execute(
                "UPDATE scores SET llm_score = ?, llm_verdict = ?, llm_reasons = ?,"
                " llm_missing = ?, llm_model = ?, llm_profile = ?, llm_has_desc = ?"
                " WHERE job_id = ?",
                (result.score, result.verdict, json.dumps(result.reasons, ensure_ascii=False),
                 json.dumps(result.missing, ensure_ascii=False), result.model, profile_fp,
                 int(has_description), job_id),
            )

    def clear_llm(self, job_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE scores SET llm_score = NULL, llm_verdict = NULL, llm_reasons = NULL,"
                " llm_missing = NULL, llm_model = NULL, llm_profile = NULL WHERE job_id = ?",
                (job_id,),
            )

    def update_final_scores(self, rule_weight: float) -> None:
        with self.db:
            self.db.execute(
                "UPDATE scores SET final_score = CASE"
                " WHEN rejected = 1 THEN 0"
                " WHEN llm_score IS NULL THEN rule_score"
                " ELSE CAST(ROUND(? * rule_score + (1 - ?) * llm_score) AS INTEGER) END",
                (rule_weight, rule_weight),
            )

    # --- status -----------------------------------------------------------
    def set_status(self, job_id: str, status: str, note: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO status(job_id, status, note, updated_at) VALUES(?, ?, ?, ?)"
                " ON CONFLICT(job_id) DO UPDATE SET status = excluded.status,"
                " note = COALESCE(excluded.note, status.note), updated_at = excluded.updated_at",
                (job_id, status, note, _now()),
            )

    def mark_notified(self, job_ids: list[str]) -> None:
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO notified VALUES(?, ?)",
                                [(j, _now()) for j in job_ids])

    def notified_ids(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT job_id FROM notified")}

    # --- reading ----------------------------------------------------------
    def rows(self) -> list[JobRow]:
        cur = self.db.execute(
            "SELECT j.*, s.rule_score, s.rejected, s.rule_reasons, s.llm_score, s.llm_verdict,"
            " s.llm_reasons, s.llm_missing, s.final_score,"
            " COALESCE(st.status, 'new') AS status, COALESCE(st.note, '') AS note"
            " FROM jobs j LEFT JOIN scores s USING(job_id) LEFT JOIN status st USING(job_id)"
            " ORDER BY COALESCE(s.final_score, -1) DESC, j.last_seen DESC"
        )
        return [
            JobRow(
                job_id=r["job_id"], title=r["title"], company=r["company"] or "",
                location=r["location"] or "", workplace=r["workplace"] or "",
                url=r["url"] or "", source=r["source"] or "",
                extra=json.loads(r["extra"] or "[]"), description=r["description"] or "",
                first_seen=r["first_seen"] or "", last_seen=r["last_seen"] or "",
                seen_count=r["seen_count"] or 1, rule_score=r["rule_score"],
                rejected=bool(r["rejected"]), rule_reasons=json.loads(r["rule_reasons"] or "[]"),
                llm_score=r["llm_score"], llm_verdict=r["llm_verdict"],
                llm_reasons=json.loads(r["llm_reasons"] or "[]"),
                llm_missing=json.loads(r["llm_missing"] or "[]"),
                final_score=r["final_score"], status=r["status"], note=r["note"],
            )
            for r in cur
        ]
