"""The stages behind `jobalert run`: fetch → parse → rules → (enrich) → LLM → outputs → digest."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from jobalert_filter.config import Config, Profile
from jobalert_filter.enrich import DescriptionFetcher
from jobalert_filter.filter.llm import LlmUnavailable, OllamaScorer
from jobalert_filter.filter.rules import RuleEngine
from jobalert_filter.models import RawEmail
from jobalert_filter.parse import parse_email
from jobalert_filter.report.csv_tracker import import_statuses, write_tracker
from jobalert_filter.report.digest import Digest, build_digest, pick_jobs
from jobalert_filter.report.html import render_report, write_report
from jobalert_filter.store import Store

log = logging.getLogger(__name__)

SendFn = Callable[[str, str, str, str], None]  # (to, subject, text, html)


@dataclass
class IngestStats:
    emails: int = 0
    jobs_found: int = 0
    jobs_new: int = 0


@dataclass
class ScoreStats:
    rule_rejected: int = 0
    llm_scored: int = 0
    llm_skipped: bool = False
    enriched: int = 0


class Pipeline:
    def __init__(self, config: Config, profile: Profile, store: Store,
                 scorer: OllamaScorer | None = None,
                 fetcher: DescriptionFetcher | None = None):
        self.config = config
        self.profile = profile
        self.store = store
        self.scorer = scorer
        self.fetcher = fetcher

    # --- fetch --------------------------------------------------------------
    def since_date(self, override: date | None = None) -> date:
        if override:
            return override
        last = self.store.get_meta("last_fetch")
        if last:
            # One day of overlap: IMAP SINCE has day granularity and ignores time zones.
            return date.fromisoformat(last) - timedelta(days=1)
        return date.today() - timedelta(days=self.config.fetch.initial_days)

    def ingest(self, emails: Iterable[RawEmail], save_samples: Path | None = None) -> IngestStats:
        stats = IngestStats()
        for raw in emails:
            if save_samples:
                save_samples.mkdir(parents=True, exist_ok=True)
                (save_samples / f"{stats.emails:04d}.eml").write_bytes(raw.raw)
            try:
                jobs = parse_email(raw)
            except Exception:  # one odd email must not stop the run
                log.exception("Could not parse email %r", raw.subject)
                jobs = []
            stats.emails += 1
            stats.jobs_found += len(jobs)
            stats.jobs_new += self.store.add_email(raw.message_id, raw.subject, raw.sender,
                                                   raw.received_at, jobs)
            log.debug("%s → %d job(s)", raw.subject, len(jobs))
        self.store.set_meta("last_fetch", date.today().isoformat())
        return stats

    # --- scoring ------------------------------------------------------------
    def score(self, rescore: bool = False) -> ScoreStats:
        stats = ScoreStats()
        rules = RuleEngine(self.profile, self.config.language)
        rows = self.store.rows()
        shortlisted = []
        for row in rows:
            result = rules.evaluate(row.as_job())
            self.store.save_rule_result(row.job_id, result)
            if result.rejected:
                stats.rule_rejected += 1
            else:
                shortlisted.append(row)

        if self.fetcher and self.config.enrich.enabled:
            for row in shortlisted:
                if row.description or row.status != "new":
                    continue
                description = self.fetcher.fetch(row.job_id)
                if description:
                    self.store.set_description(row.job_id, description)
                    row.description = description
                    stats.enriched += 1

        if self.scorer and self.config.llm.enabled and shortlisted:
            if not self.scorer.available():
                stats.llm_skipped = True
            else:
                fp = self.profile.fingerprint()
                model = self.config.llm.model
                todo = [r for r in shortlisted
                        if rescore or self.store.needs_llm(r.job_id, model, fp,
                                                           bool(r.description))]
                for i, row in enumerate(todo, 1):
                    log.info("LLM %d/%d: %s", i, len(todo), row.title)
                    try:
                        result = self.scorer.score(row.as_job())
                    except LlmUnavailable as exc:
                        log.warning("Ollama stopped responding (%s); remaining jobs keep "
                                    "their rule score", exc)
                        stats.llm_skipped = True
                        break
                    except ValueError as exc:
                        log.warning("%s", exc)
                        continue
                    self.store.save_llm_result(row.job_id, result, fp, bool(row.description))
                    stats.llm_scored += 1

        self.store.update_final_scores(
            self.config.llm.rule_weight if self.config.llm.enabled else 1.0
        )
        return stats

    # --- outputs ------------------------------------------------------------
    def report_paths(self) -> tuple[Path, Path]:
        out = self.config.resolve(self.config.output.directory)
        return out / self.config.output.report_file, out / self.config.output.tracker_file

    def write_outputs(self) -> tuple[Path, Path]:
        """Rewrite report and tracker. Call sync_tracker() first, or edits in the CSV are lost."""
        report_path, tracker_path = self.report_paths()
        rows = self.store.rows()
        write_report(report_path, render_report(rows, self.profile.min_score,
                                                self.config.language,
                                                self.config.output.show_rejected))
        write_tracker(tracker_path, rows, self.profile.min_score)
        return report_path, tracker_path

    def sync_tracker(self) -> int:
        """Take status/note edits from tracker.csv into the database."""
        tracker_path = self.report_paths()[1]
        changed = import_statuses(tracker_path, self.store)
        if changed:
            log.info("Took %d status change(s) from %s", changed, tracker_path.name)
        return changed

    # --- digest -------------------------------------------------------------
    def build_digest(self) -> Digest | None:
        jobs = pick_jobs(self.store.rows(), self.profile.min_score, self.store.notified_ids(),
                         self.config.digest.top_n)
        return build_digest(jobs, self.config.language)

    def send_digest(self, send: SendFn, force: bool = False) -> Digest | None:
        today = date.today().isoformat()
        if not force and self.store.get_meta("last_digest") == today:
            log.info("Digest already sent today")
            return None
        digest = self.build_digest()
        if digest is None:
            log.info("No new matches for the digest")
            return None
        send(self.config.digest.to or self.config.mail.address, digest.subject, digest.text,
             digest.html)
        self.store.mark_notified(digest.job_ids)
        self.store.set_meta("last_digest", today)
        return digest
