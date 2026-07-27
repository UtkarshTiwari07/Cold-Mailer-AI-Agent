"""Small, hand-maintained deny-lists for email validation.

Kept as plain Python sets rather than an external API (e.g. a disposable-
email-lookup service) so A0 has zero network dependency and zero recurring
cost for this check. Not exhaustive — extend `DISPOSABLE_DOMAINS` from a
larger public list (e.g. disposable-email-domains on GitHub) if false
negatives become a problem in practice.
"""

from __future__ import annotations

DISPOSABLE_DOMAINS: frozenset[str] = frozenset(
    {
        "mailinator.com", "guerrillamail.com", "10minutemail.com", "yopmail.com",
        "trashmail.com", "sharklasers.com", "discard.email", "throwawaymail.com",
        "fakeinbox.com", "dispostable.com", "maildrop.cc", "getnada.com",
        "mohmal.com", "moakt.com", "temp-mail.org", "tempmail.com",
        "guerrillamailblock.com", "spamgourmet.com", "mailnesia.com",
        "mintemail.com", "emailondeck.com", "33mail.com",
    }
)

# Local-part prefixes (before the @) that indicate a shared inbox rather than
# a named individual recruiter or hiring manager. Still worth emailing —
# often the only address available — but Agent 4 downweights these and the
# UI should flag them, since "Dear Hiring Team" personalization ceilings out
# fast.
ROLE_ACCOUNT_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "info", "hr", "careers", "career", "recruiting", "recruitment", "jobs",
        "talent", "hiring", "admin", "support", "contact", "sales", "noreply",
        "no-reply", "help", "office", "team", "hello", "people", "peopleteam",
        "humanresources", "resumes", "applications",
    }
)
