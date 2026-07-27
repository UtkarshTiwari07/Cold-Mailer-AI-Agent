"""ATS auto-detection: given a company domain and (optionally) a careers URL,
figure out which ATS it uses and what its token is, without needing a human
to look it up.

Two strategies, tried in order:
  1. If the careers URL itself points at an ATS-hosted domain (e.g.
     `jobs.lever.co/acme` or `boards.greenhouse.io/acme`), the token is
     right there in the URL — no request needed.
  2. Otherwise, guess the token from the company domain (`acme.com` ->
     `acme`) and probe each ATS client in turn. This is the same one HTTP
     call per ATS that `fetch_jobs` would make anyway, so "detect" and
     "fetch" collapse into a single probe rather than two round trips.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cold_mailer.contracts.common import JobPosting
from cold_mailer.core.logging import get_logger
from cold_mailer.providers.ats.ashby import AshbyClient
from cold_mailer.providers.ats.base import ATSClient
from cold_mailer.providers.ats.greenhouse import GreenhouseClient
from cold_mailer.providers.ats.lever import LeverClient
from cold_mailer.providers.ats.recruitee import RecruiteeClient
from cold_mailer.providers.ats.smartrecruiters import SmartRecruitersClient
from cold_mailer.providers.ats.workable import WorkableClient

log = get_logger(component="ats.detect")

CLIENTS: dict[str, ATSClient] = {
    "greenhouse": GreenhouseClient(),
    "lever": LeverClient(),
    "ashby": AshbyClient(),
    "workable": WorkableClient(),
    "smartrecruiters": SmartRecruitersClient(),
    "recruitee": RecruiteeClient(),
}

# host-pattern -> (ats_type, token_group_index)
_URL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"boards\.greenhouse\.io/([^/?#]+)"), "greenhouse"),
    (re.compile(r"job-boards\.greenhouse\.io/([^/?#]+)"), "greenhouse"),
    (re.compile(r"jobs\.lever\.co/([^/?#]+)"), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)"), "ashby"),
    (re.compile(r"apply\.workable\.com/([^/?#]+)"), "workable"),
    (re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)"), "smartrecruiters"),
    (re.compile(r"([^.]+)\.recruitee\.com"), "recruitee"),
]


@dataclass
class ATSMatch:
    ats_type: str
    token: str
    postings: list[JobPosting]


def _domain_to_candidate_token(domain: str) -> str:
    root = domain.split(".")[0]
    return re.sub(r"[^a-z0-9-]", "", root.lower())


def token_from_url(url: str) -> tuple[str, str] | None:
    for pattern, ats_type in _URL_PATTERNS:
        m = pattern.search(url)
        if m:
            return ats_type, m.group(1)
    return None


async def detect_and_fetch(domain: str, careers_url: str | None = None) -> ATSMatch | None:
    """Tries the careers URL hint first, then probes every ATS with the
    domain-derived token. Returns None (not an error) if nothing matches —
    that's the normal case for the majority of companies that don't use one
    of these six systems, and Agent 3 falls back to crawling."""
    if careers_url:
        hint = token_from_url(careers_url)
        if hint:
            ats_type, token = hint
            postings = await CLIENTS[ats_type].fetch_jobs(token)
            if postings:
                log.info("ats.detected_via_url", domain=domain, ats_type=ats_type, token=token, count=len(postings))
                return ATSMatch(ats_type=ats_type, token=token, postings=postings)

    token = _domain_to_candidate_token(domain)
    for ats_type, client in CLIENTS.items():
        postings = await client.fetch_jobs(token)
        if postings:
            log.info("ats.detected_via_probe", domain=domain, ats_type=ats_type, token=token, count=len(postings))
            return ATSMatch(ats_type=ats_type, token=token, postings=postings)

    log.info("ats.no_match", domain=domain)
    return None
