"""Agent 6 — Personalization / Candidate Profile Memory.

This is deliberately the simplest contract in the system: a typed read of
`profile/utkarsh.yaml`, kept as its own agent module (rather than inlined
config) because the brief treats "know what I'm good at" as long-term memory
that A5 and A7 both query, and because it's the one contract a non-engineer
should be able to edit directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Project(BaseModel):
    name: str
    description: str
    technologies: list[str] = Field(default_factory=list)
    outcome: str | None = None
    url: str | None = None


class CandidateProfile(BaseModel):
    name: str
    headline: str
    email: str
    github_url: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    resume_path: str | None = None

    skills: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    career_goals: str | None = None
    communication_style: str | None = None
    open_to: list[str] = Field(default_factory=list, description="roles/seniority open to")
