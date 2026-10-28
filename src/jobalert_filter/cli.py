"""Command line: `jobalert --help`."""

from __future__ import annotations

import logging
import re
import shutil
import sys
import webbrowser
from datetime import date, timedelta
from importlib import resources
from pathlib import Path
from typing import Annotated

import typer

from jobalert_filter import schedule as scheduler
from jobalert_filter.config import Config, ConfigError, Profile, load_config, load_profile
from jobalert_filter.enrich import DescriptionFetcher
from jobalert_filter.filter.llm import OllamaScorer
from jobalert_filter.mail.auth import AuthError, Credentials
from jobalert_filter.mail.imap_source import ImapSource
from jobalert_filter.mail.smtp_sender import send_email
from jobalert_filter.pipeline import Pipeline
from jobalert_filter.store import STATUSES, Store, normalize_status

app = typer.Typer(help="Filter job-alert emails against your profile.", no_args_is_help=True)
schedule_app = typer.Typer(help="Run automatically every day.", no_args_is_help=True)
app.add_typer(schedule_app, name="schedule")

log = logging.getLogger("jobalert_filter")

ConfigOpt = Annotated[Path, typer.Option("--config", "-c", help="Path to config.yaml")]
ProfileOpt = Annotated[
    Path | None,
    typer.Option("--profile", "-p", help="Path to profile.yaml (default: next to config)"),
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Show debug output")]
SinceOpt = Annotated[
    str | None, typer.Option("--since", help="How far back to read: '14d' or a date '2026-10-01'")
]


def _setup_logging(config: Config | None, verbose: bool) -> None:
    root = logging.getLogger("jobalert_filter")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    if sys.stderr is not None:  # pythonw has no console
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)
    if config is not None:
        path = config.resolve(config.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(file_handler)


def _load(config_path: Path, profile_path: Path | None, verbose: bool) -> tuple[Config, Profile]:
    try:
        config = load_config(config_path)
        profile = load_profile(profile_path or config.base_dir / "profile.yaml")
    except ConfigError as exc:
        _setup_logging(None, verbose)
        log.error("%s", exc)
        raise typer.Exit(2) from exc
    _setup_logging(config, verbose)
    return config, profile


def _parse_since(value: str | None) -> date | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d+)d", value.strip())
    if match:
        return date.today() - timedelta(days=int(match.group(1)))
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("use e.g. 14d or 2026-10-01") from exc


def _pipeline(config: Config, profile: Profile, store: Store) -> Pipeline:
    scorer = OllamaScorer(config.llm, profile, config.language) if config.llm.enabled else None
    fetcher = DescriptionFetcher(config.enrich) if config.enrich.enabled else None
    return Pipeline(config, profile, store, scorer=scorer, fetcher=fetcher)


def _fetch(config: Config, pipeline: Pipeline, store: Store, since: date | None,
           save_samples: Path | None = None) -> None:
    source = ImapSource(config, Credentials(config))
    start = pipeline.since_date(since)
    log.info("Reading %s since %s (read-only)", config.mail.address, start)
    stats = pipeline.ingest(source.fetch(start, store.known_message_ids()), save_samples)
    log.info("%d new email(s), %d job(s) found, %d new", stats.emails, stats.jobs_found,
             stats.jobs_new)


def _score(pipeline: Pipeline, rescore: bool = False) -> None:
    stats = pipeline.score(rescore=rescore)
    log.info("Rules rejected %d; LLM scored %d%s%s", stats.rule_rejected, stats.llm_scored,
             " (LLM unavailable)" if stats.llm_skipped else "",
             f"; {stats.enriched} description(s) fetched" if stats.enriched else "")


def _outputs(pipeline: Pipeline, open_report: bool) -> None:
    report_path, tracker_path = pipeline.write_outputs()
    log.info("Report: %s", report_path)
    log.info("Tracker: %s", tracker_path)
    if open_report:
        webbrowser.open(report_path.resolve().as_uri())


def _sender(config: Config):
    credentials = Credentials(config)

    def send(to: str, subject: str, text: str, html: str) -> None:
        send_email(config, credentials, to, subject, text, html)

    return send


@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="Where to create the files")] = Path("."),
) -> None:
    """Create config.yaml, profile.yaml and .env from the examples."""
    examples = resources.files("jobalert_filter") / "examples"
    pairs = [("config.example.yaml", "config.yaml"), ("profile.example.yaml", "profile.yaml"),
             ("env.example", ".env")]
    directory.mkdir(parents=True, exist_ok=True)
    for src, dst in pairs:
        target = directory / dst
        if target.exists():
            typer.echo(f"{target} exists, left unchanged")
            continue
        with resources.as_file(examples / src) as path:
            shutil.copyfile(path, target)
        typer.echo(f"Created {target}")
    typer.echo("Next: edit config.yaml and profile.yaml, then run `jobalert auth`.")


@app.command()
def auth(config_path: ConfigOpt = Path("config.yaml"), verbose: VerboseOpt = False) -> None:
    """Sign in (Outlook) and test the mailbox login."""
    config, _ = _load(config_path, None, verbose)
    try:
        Credentials(config).interactive_login(typer.echo)
        folders = ImapSource(config, Credentials(config)).check()
    except (AuthError, ConfigError, OSError) as exc:
        log.error("%s", exc)
        raise typer.Exit(1) from exc
    except Exception as exc:  # imaplib raises its own error types
        log.error("Mailbox login failed: %s", exc)
        raise typer.Exit(1) from exc
    typer.echo(f"Login OK. Folders: {', '.join(folders[:15])}")


@app.command()
def check(config_path: ConfigOpt = Path("config.yaml"), profile_path: ProfileOpt = None,
          verbose: VerboseOpt = False) -> None:
    """Check that the config, mailbox and Ollama all work."""
    config, profile = _load(config_path, profile_path, verbose)
    typer.echo(f"Config OK ({config.mail.provider}, {config.mail.address}); "
               f"profile min_score={profile.min_score}")
    try:
        folders = ImapSource(config, Credentials(config)).check()
        missing = [f for f in config.mail.folders if f not in folders]
        typer.echo("Mailbox OK" + (f" — folders not found: {missing}" if missing else ""))
    except Exception as exc:
        typer.echo(f"Mailbox FAILED: {exc}")
    if config.llm.enabled:
        ok = OllamaScorer(config.llm, profile, config.language).available()
        typer.echo(f"Ollama {'OK' if ok else 'not available'} ({config.llm.model})")


@app.command()
def fetch(config_path: ConfigOpt = Path("config.yaml"), profile_path: ProfileOpt = None,
          since: SinceOpt = None,
          save_samples: Annotated[Path | None, typer.Option(
              help="Also save the raw emails here (for writing parser tests)")] = None,
          verbose: VerboseOpt = False) -> None:
    """Read new alert emails and store the jobs in them (no scoring)."""
    config, profile = _load(config_path, profile_path, verbose)
    store = Store(config.resolve(config.database))
    _fetch(config, _pipeline(config, profile, store), store, _parse_since(since), save_samples)


@app.command()
def score(config_path: ConfigOpt = Path("config.yaml"), profile_path: ProfileOpt = None,
          rescore: Annotated[bool, typer.Option(help="Ask the LLM again for every job")] = False,
          verbose: VerboseOpt = False) -> None:
    """Score stored jobs with the rules and the LLM."""
    config, profile = _load(config_path, profile_path, verbose)
    _score(_pipeline(config, profile, Store(config.resolve(config.database))), rescore)


@app.command()
def report(config_path: ConfigOpt = Path("config.yaml"), profile_path: ProfileOpt = None,
           open_report: Annotated[bool, typer.Option("--open", help="Open in the browser")] = False,
           verbose: VerboseOpt = False) -> None:
    """Rebuild report.html and tracker.csv."""
    config, profile = _load(config_path, profile_path, verbose)
    pipeline = _pipeline(config, profile, Store(config.resolve(config.database)))
    pipeline.sync_tracker()
    _outputs(pipeline, open_report)


@app.command()
def digest(config_path: ConfigOpt = Path("config.yaml"), profile_path: ProfileOpt = None,
           dry_run: Annotated[bool, typer.Option(help="Print instead of sending")] = False,
           force: Annotated[bool, typer.Option(help="Send even if one went out today")] = False,
           verbose: VerboseOpt = False) -> None:
    """Email yourself the new matches."""
    config, profile = _load(config_path, profile_path, verbose)
    pipeline = _pipeline(config, profile, Store(config.resolve(config.database)))
    pipeline.sync_tracker()  # an "ignored"/"applied" job must not show up in the digest
    if dry_run:
        built = pipeline.build_digest()
        typer.echo(f"{built.subject}\n\n{built.text}" if built else "No new matches.")
        return
    sent = pipeline.send_digest(_sender(config), force=force)
    if sent:
        typer.echo(f"Sent: {sent.subject}")


@app.command()
def run(config_path: ConfigOpt = Path("config.yaml"), profile_path: ProfileOpt = None,
        since: SinceOpt = None,
        no_fetch: Annotated[bool, typer.Option("--no-fetch", help="Skip reading mail")] = False,
        dry_run: Annotated[bool, typer.Option(help="Never send the digest email")] = False,
        open_report: Annotated[bool, typer.Option("--open", help="Open the report")] = False,
        verbose: VerboseOpt = False) -> None:
    """Everything in one go: fetch, score, report, digest. This is what the schedule runs."""
    config, profile = _load(config_path, profile_path, verbose)
    store = Store(config.resolve(config.database))
    pipeline = _pipeline(config, profile, store)
    try:
        pipeline.sync_tracker()  # your status edits count before scoring and the digest
        if not no_fetch:
            _fetch(config, pipeline, store, _parse_since(since))
        _score(pipeline)
        _outputs(pipeline, open_report)
        if config.digest.enabled and not dry_run:
            sent = pipeline.send_digest(_sender(config))
            if sent:
                log.info("Digest sent: %s", sent.subject)
    except (AuthError, ConfigError) as exc:
        log.error("%s", exc)
        raise typer.Exit(1) from exc
    except Exception as exc:
        log.exception("Run failed: %s", exc)
        raise typer.Exit(1) from exc
    finally:
        store.close()


@app.command()
def status(
    job_id: Annotated[str, typer.Argument(help="Job id (from the report or tracker.csv)")],
    new_status: Annotated[str, typer.Argument(
        metavar="STATUS", help=f"One of: {', '.join(STATUSES)} (Turkish words work too)")],
    note: Annotated[str | None, typer.Option(help="Free-text note")] = None,
    config_path: ConfigOpt = Path("config.yaml"),
    verbose: VerboseOpt = False,
) -> None:
    """Set a job's application status, then refresh the report."""
    config, profile = _load(config_path, None, verbose)
    value = normalize_status(new_status)
    if value is None:
        raise typer.BadParameter(f"unknown status {new_status!r}")
    store = Store(config.resolve(config.database))
    pipeline = _pipeline(config, profile, store)
    pipeline.sync_tracker()
    if job_id not in {r.job_id for r in store.rows()}:
        log.error("Unknown job id %s", job_id)
        raise typer.Exit(1)
    store.set_status(job_id, value, note)
    pipeline.write_outputs()
    typer.echo(f"{job_id}: {value}")


TimesOpt = Annotated[str, typer.Option(help="Comma-separated HH:MM times")]


def _times(value: str) -> list[str]:
    times = [t.strip() for t in value.split(",") if t.strip()]
    for t in times:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            raise typer.BadParameter(f"bad time {t!r}, use HH:MM")
    return times


@schedule_app.command("install")
def schedule_install(config_path: ConfigOpt = Path("config.yaml"),
                     times: TimesOpt = "09:00,18:00") -> None:
    """Run `jobalert run` every day at the given times."""
    if not config_path.exists():
        typer.echo(f"{config_path} not found")
        raise typer.Exit(2)
    for line in scheduler.install(config_path, _times(times)):
        typer.echo(line)


@schedule_app.command("remove")
def schedule_remove(times: TimesOpt = "09:00,18:00") -> None:
    """Remove the scheduled runs."""
    for line in scheduler.remove(_times(times)):
        typer.echo(line)
