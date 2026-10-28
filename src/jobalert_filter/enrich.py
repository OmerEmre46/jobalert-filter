"""Optional: fetch the full description of shortlisted jobs from LinkedIn's public
(logged-out) job page, so the LLM can check the actual requirements.

Off by default. LinkedIn's User Agreement restricts automated access to its site;
enable ``enrich.enabled`` only if you accept that yourself. The fetcher never logs in,
only touches jobs that already passed the keyword rules, waits ``delay_seconds``
between requests, stops after ``max_per_run`` and backs off on HTTP 429.
"""

from __future__ import annotations

import logging
import time

import httpx
from bs4 import BeautifulSoup

from jobalert_filter import __version__
from jobalert_filter.config import EnrichConfig
from jobalert_filter.text import clean_lines

log = logging.getLogger(__name__)

GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
USER_AGENT = f"jobalert-filter/{__version__} (personal job-alert filter)"


def extract_description(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    parts: list[str] = []
    body = soup.select_one(".show-more-less-html__markup, .description__text")
    if body:
        parts.append("\n".join(clean_lines(body.get_text("\n"))))
    for item in soup.select(".description__job-criteria-item"):
        label = item.select_one(".description__job-criteria-subheader")
        value = item.select_one(".description__job-criteria-text")
        if label and value:
            parts.append(f"{label.get_text(strip=True)}: {value.get_text(strip=True)}")
    return "\n".join(parts).strip()


class DescriptionFetcher:
    def __init__(self, config: EnrichConfig, client: httpx.Client | None = None,
                 sleep=time.sleep):
        self.config = config
        self.client = client or httpx.Client(
            timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self.sleep = sleep
        self.fetched = 0
        self.blocked = False

    def fetch(self, job_id: str) -> str:
        if self.blocked or self.fetched >= self.config.max_per_run:
            return ""
        if self.fetched:
            self.sleep(self.config.delay_seconds)
        self.fetched += 1
        try:
            resp = self.client.get(GUEST_URL.format(job_id=job_id))
        except httpx.HTTPError as exc:
            log.warning("Description fetch failed for %s: %s", job_id, exc)
            return ""
        if resp.status_code in (403, 429, 999):
            log.warning("Job page access refused (HTTP %s); stopping enrichment for this run",
                        resp.status_code)
            self.blocked = True
            return ""
        if resp.status_code != 200:
            return ""
        return extract_description(resp.text)
