import csv
import json

import httpx
from conftest import make_raw_email

from jobalert_filter.config import LlmConfig
from jobalert_filter.enrich import DescriptionFetcher, extract_description
from jobalert_filter.filter.llm import OllamaScorer
from jobalert_filter.pipeline import Pipeline
from jobalert_filter.report import classify
from jobalert_filter.store import normalize_status


def rows_by_id(store):
    return {r.job_id: r for r in store.rows()}


def test_duplicate_jobs_across_emails_are_merged(config, profile, store):
    pipeline = Pipeline(config, profile, store)
    stats = pipeline.ingest([make_raw_email(message_id="<a>"), make_raw_email(message_id="<b>")])
    assert (stats.emails, stats.jobs_found, stats.jobs_new) == (2, 6, 3)
    assert rows_by_id(store)["4012345678"].seen_count == 2
    assert store.known_message_ids() == {"<a>", "<b>"}


def test_rules_only_scoring(config, profile, store):
    pipeline = Pipeline(config, profile, store)
    pipeline.ingest([make_raw_email()])
    stats = pipeline.score()
    rows = rows_by_id(store)
    assert stats.rule_rejected == 1
    assert rows["4087654321"].rejected and rows["4087654321"].final_score == 0
    assert classify(rows["4012345678"], profile.min_score) == "match"
    assert classify(rows["4011112222"], profile.min_score) == "match"


def fake_ollama(score, calls):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "m:latest"}]})
        calls.append(json.loads(request.content))
        reply = {"score": score, "verdict": "no", "reasons": ["Deneyim istiyor"],
                 "missing_requirements": ["2 yıl deneyim"]}
        return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})
    return handler


def test_llm_scores_are_blended_and_cached(config, profile, store):
    config.llm = LlmConfig(enabled=True, model="m", rule_weight=0.3)
    calls = []
    client = httpx.Client(base_url="http://o", transport=httpx.MockTransport(fake_ollama(20, calls)))
    pipeline = Pipeline(config, profile, store, scorer=OllamaScorer(config.llm, profile,
                                                                    client=client))
    pipeline.ingest([make_raw_email()])
    pipeline.score()
    assert len(calls) == 2  # the senior job never reaches the LLM
    acme = rows_by_id(store)["4012345678"]
    assert acme.llm_score == 20
    assert acme.final_score == round(0.3 * acme.rule_score + 0.7 * 20)
    assert classify(acme, profile.min_score) == "no"

    pipeline.score()
    assert len(calls) == 2  # cached

    profile.skills.append("Go")
    pipeline.score()
    assert len(calls) == 4  # profile changed → rescored


def test_llm_down_falls_back_to_rules(config, profile, store):
    config.llm = LlmConfig(enabled=True, model="m")

    def handler(request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(base_url="http://o", transport=httpx.MockTransport(handler))
    pipeline = Pipeline(config, profile, store, scorer=OllamaScorer(config.llm, profile,
                                                                    client=client))
    pipeline.ingest([make_raw_email()])
    stats = pipeline.score()
    assert stats.llm_skipped
    acme = rows_by_id(store)["4012345678"]
    assert acme.llm_score is None and acme.final_score == acme.rule_score


def test_outputs_and_tracker_roundtrip(config, profile, store):
    pipeline = Pipeline(config, profile, store)
    pipeline.ingest([make_raw_email()])
    pipeline.score()
    report_path, tracker_path = pipeline.write_outputs()

    html = report_path.read_text(encoding="utf-8")
    assert "Backend Developer Intern" in html
    assert "Başlıkta istenmeyen ifade" in html
    assert "trackingId" not in html

    raw = tracker_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    with tracker_path.open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh, delimiter=";"))
    assert len(records) == 3
    for record in records:
        if record["job_id"] == "4012345678":
            record["status"] = "başvuruldu"
            record["note"] = "CV gönderildi"
    with tracker_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(records[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(records)

    assert pipeline.sync_tracker() == 1
    pipeline.write_outputs()
    acme = rows_by_id(store)["4012345678"]
    assert (acme.status, acme.note) == ("applied", "CV gönderildi")
    assert "CV gönderildi" in report_path.read_text(encoding="utf-8")


def test_status_set_in_db_survives_rewriting_the_tracker(config, profile, store):
    pipeline = Pipeline(config, profile, store)
    pipeline.ingest([make_raw_email()])
    pipeline.score()
    pipeline.write_outputs()

    pipeline.sync_tracker()
    store.set_status("4011112222", "interview", "Perşembe")
    pipeline.write_outputs()
    pipeline.sync_tracker()  # next command reads the freshly written tracker
    assert rows_by_id(store)["4011112222"].status == "interview"


def test_digest_once_per_day_and_never_repeats(config, profile, store):
    pipeline = Pipeline(config, profile, store)
    pipeline.ingest([make_raw_email()])
    pipeline.score()
    sent = []

    def send(to, subject, text, html):
        sent.append((to, subject, text, html))

    digest = pipeline.send_digest(send)
    assert digest is not None and len(digest.job_ids) == 2
    to, subject, text, html = sent[0]
    assert to == "student@example.com"
    assert "2 yeni uygun ilan" in subject
    assert "Backend Developer Intern" in text and "Senior" not in text

    assert pipeline.send_digest(send) is None  # same day
    assert pipeline.send_digest(send, force=True) is None  # nothing new
    assert len(sent) == 1


def test_status_aliases():
    assert normalize_status("Mülakat") == "interview"
    assert normalize_status("ilgilenmiyorum") == "ignored"
    assert normalize_status("") == "new"
    assert normalize_status("maybe later") is None


def test_enrich_respects_limits_and_backs_off():
    from jobalert_filter.config import EnrichConfig

    page = ('<div class="show-more-less-html__markup"><p>Requirements:</p><ul>'
            "<li>Currently enrolled student</li></ul></div>"
            '<li class="description__job-criteria-item">'
            '<h3 class="description__job-criteria-subheader">Seniority level</h3>'
            '<span class="description__job-criteria-text">Internship</span></li>')
    assert "Currently enrolled student" in extract_description(page)
    assert "Seniority level: Internship" in extract_description(page)

    statuses = iter([200, 429, 200])

    def handler(request):
        return httpx.Response(next(statuses), text=page)

    sleeps = []
    fetcher = DescriptionFetcher(EnrichConfig(enabled=True, delay_seconds=3, max_per_run=5),
                                 client=httpx.Client(transport=httpx.MockTransport(handler)),
                                 sleep=sleeps.append)
    assert fetcher.fetch("1")
    assert fetcher.fetch("2") == ""  # 429 → stop for this run
    assert fetcher.fetch("3") == ""
    assert sleeps == [3]
