from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from jobalert_filter.report import TRACKED, classify, labels
from jobalert_filter.store import JobRow

_env = Environment(
    loader=PackageLoader("jobalert_filter.report", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_report(rows: list[JobRow], min_score: int, language: str,
                  show_rejected: bool = True) -> str:
    items = []
    counts = {"match": 0, "maybe": 0, "no": 0, "tracked": 0}
    for row in rows:
        bucket = classify(row, min_score)
        tracked = row.status in TRACKED
        counts[bucket] += 1
        counts["tracked"] += int(tracked)
        if bucket == "no" and not show_rejected and not tracked:
            continue
        items.append({"row": row, "bucket": bucket, "tracked": tracked})
    return _env.get_template("report.html.j2").render(
        items=items,
        counts=counts,
        total=len(rows),
        t=labels(language),
        lang=language,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        min_score=min_score,
    )


def write_report(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
