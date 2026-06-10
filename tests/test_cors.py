"""
Tests for CORS handling (2026-06-10):

  * Vercel preview origins (both the per-build hash form and the git-branch
    form) are allowed via the allow_origin_regex anchored on our real project
    slug + team suffix.
  * Look-alike origins from other Vercel teams / spoofed prefixes do NOT match.

These exercise Starlette's CORSMiddleware directly through a CORS preflight
(OPTIONS) request, which the middleware answers itself — no route handler and
no database pool are touched, so no fake pool is needed here.
"""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => real pool is never opened.
    return TestClient(main.app)


def _preflight(client, origin):
    """Send a CORS preflight for a GET and return the response."""
    return client.options(
        "/api/tape/recent",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )


# ---------------------------------------------------------------------------
# Allowed: real preview shapes for our project + team.
# ---------------------------------------------------------------------------

def test_cors_allows_preview_hash_origin(client):
    origin = (
        "https://energylake-ma08ur41o-"
        "austinrodriguez221-6328s-projects.vercel.app"
    )
    resp = _preflight(client, origin)
    # Credentialed CORS must echo the exact origin, never "*".
    assert resp.headers.get("access-control-allow-origin") == origin


def test_cors_allows_preview_git_branch_origin(client):
    origin = (
        "https://energylake-git-claude-vercel-preview-cors-"
        "austinrodriguez221-6328s-projects.vercel.app"
    )
    resp = _preflight(client, origin)
    assert resp.headers.get("access-control-allow-origin") == origin


# ---------------------------------------------------------------------------
# Rejected: look-alikes that must NOT match the regex.
# ---------------------------------------------------------------------------

def test_cors_rejects_other_team_preview_origin(client):
    # Same "energylake" slug, different team suffix -> no match.
    origin = "https://energylake-abc123-someoneelse-1234s-projects.vercel.app"
    resp = _preflight(client, origin)
    assert resp.headers.get("access-control-allow-origin") != origin


def test_cors_rejects_spoofed_prefix_origin(client):
    # Anchored start means a prefix can't smuggle our suffix in.
    origin = (
        "https://evil-energylake-x-"
        "austinrodriguez221-6328s-projects.vercel.app"
    )
    resp = _preflight(client, origin)
    assert resp.headers.get("access-control-allow-origin") != origin
