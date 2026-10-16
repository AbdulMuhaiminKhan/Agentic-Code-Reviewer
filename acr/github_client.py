"""Minimal GitHub REST client. Free: a personal access token gives 5,000 requests/hour."""

import os

import requests

API = "https://api.github.com"
COMMENT_MARKER = "<!-- agentic-code-reviewer -->"


def _headers(diff: bool = False) -> dict:
    h = {
        "Accept": "application/vnd.github.diff" if diff else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.getenv("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_commit_diff(repo: str, sha: str) -> str:
    r = requests.get(f"{API}/repos/{repo}/commits/{sha}", headers=_headers(diff=True), timeout=60)
    r.raise_for_status()
    return r.text


def fetch_pr_diff(repo: str, number: int) -> str:
    r = requests.get(f"{API}/repos/{repo}/pulls/{number}", headers=_headers(diff=True), timeout=60)
    r.raise_for_status()
    return r.text


def upsert_pr_comment(repo: str, number: int, body: str) -> str:
    """Post the review, or edit our previous comment so re-runs don't spam the PR."""
    body = f"{COMMENT_MARKER}\n{body}"
    url = f"{API}/repos/{repo}/issues/{number}/comments"
    r = requests.get(url, headers=_headers(), params={"per_page": 100}, timeout=30)
    r.raise_for_status()
    for c in r.json():
        if COMMENT_MARKER in (c.get("body") or ""):
            r = requests.patch(c["url"], headers=_headers(), json={"body": body}, timeout=30)
            r.raise_for_status()
            return r.json()["html_url"]
    r = requests.post(url, headers=_headers(), json={"body": body}, timeout=30)
    r.raise_for_status()
    return r.json()["html_url"]
