"""Turns stored, scored jobs into the HTML report, the CSV tracker and the digest email."""

from __future__ import annotations

from jobalert_filter.store import JobRow

MAYBE_MARGIN = 15
TRACKED = ("applied", "interview", "offer")

LABELS = {
    "tr": {
        "title": "İlan Süzgeci",
        "generated": "Oluşturulma",
        "match": "Uygun", "maybe": "Belki", "no": "Elenen", "tracked": "Takipte", "all": "Tümü",
        "search": "Başlık, şirket veya konum ara…",
        "score": "Puan", "rule": "kural", "llm": "LLM",
        "missing": "Eksik / riskli",
        "seen": "{n} mailde görüldü",
        "open": "İlanı aç",
        "empty": "Bu sekmede ilan yok.",
        "status": {"new": "yeni", "applied": "başvuruldu", "interview": "mülakat",
                   "offer": "teklif", "rejected": "ret", "ignored": "ilgilenmiyorum"},
        "workplace": {"remote": "uzaktan", "hybrid": "hibrit", "onsite": "iş yerinde"},
        "tracker_hint": "Durumu güncellemek için tracker.csv'deki status sütununu düzenle "
                        "(yeni, başvuruldu, mülakat, teklif, ret, ilgilenmiyorum).",
        "digest_subject": "{n} yeni uygun ilan",
        "digest_intro": "Profiline uyan yeni ilanlar:",
    },
    "en": {
        "title": "Job Alert Filter",
        "generated": "Generated",
        "match": "Match", "maybe": "Maybe", "no": "Filtered out", "tracked": "Tracking",
        "all": "All",
        "search": "Search title, company or location…",
        "score": "Score", "rule": "rules", "llm": "LLM",
        "missing": "Missing / risky",
        "seen": "seen in {n} emails",
        "open": "Open posting",
        "empty": "No jobs in this tab.",
        "status": {"new": "new", "applied": "applied", "interview": "interview",
                   "offer": "offer", "rejected": "rejected", "ignored": "not interested"},
        "workplace": {"remote": "remote", "hybrid": "hybrid", "onsite": "on-site"},
        "tracker_hint": "Update a job's status in the status column of tracker.csv "
                        "(new, applied, interview, offer, rejected, ignored).",
        "digest_subject": "{n} new matching jobs",
        "digest_intro": "New postings that fit your profile:",
    },
}


def labels(language: str) -> dict:
    return LABELS.get(language, LABELS["en"])


def classify(row: JobRow, min_score: int) -> str:
    """'match' | 'maybe' | 'no' — the bucket a job lands in."""
    if row.rejected or row.status == "ignored":
        return "no"
    if row.final_score is None:
        return "maybe"
    if row.final_score >= min_score:
        return "match"
    if row.final_score >= min_score - MAYBE_MARGIN:
        return "maybe"
    return "no"
