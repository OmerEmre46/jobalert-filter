"""The once-a-day summary email of new matches."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from jobalert_filter.report import classify, labels
from jobalert_filter.store import JobRow


@dataclass
class Digest:
    subject: str
    text: str
    html: str
    job_ids: list[str]


def pick_jobs(rows: list[JobRow], min_score: int, already_sent: set[str],
              top_n: int) -> list[JobRow]:
    fresh = [r for r in rows
             if r.job_id not in already_sent and r.status == "new"
             and classify(r, min_score) == "match"]
    fresh.sort(key=lambda r: r.final_score or 0, reverse=True)
    return fresh[:top_n]


def build_digest(jobs: list[JobRow], language: str) -> Digest | None:
    if not jobs:
        return None
    t = labels(language)
    subject = f"[{t['title']}] " + t["digest_subject"].format(n=len(jobs))

    text_lines = [t["digest_intro"], ""]
    html_items = []
    for job in jobs:
        where = " · ".join(p for p in (job.company, job.location) if p)
        why = (job.llm_reasons or job.rule_reasons)[:2]
        text_lines += [f"[{job.final_score}] {job.title} — {where}", f"    {job.url}"]
        text_lines += [f"    • {w}" for w in why]
        text_lines.append("")
        html_items.append(
            f'<li style="margin-bottom:12px"><b>{job.final_score}</b> · '
            f'<a href="{escape(job.url)}">{escape(job.title)}</a><br>'
            f'<span style="color:#5f6b7a">{escape(where)}</span>'
            + "".join(f"<br>• {escape(w)}" for w in why)
            + "</li>"
        )
    html = (f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">'
            f"<p>{escape(t['digest_intro'])}</p><ul style=\"padding-left:18px\">"
            + "".join(html_items) + "</ul></div>")
    return Digest(subject, "\n".join(text_lines), html, [j.job_id for j in jobs])
