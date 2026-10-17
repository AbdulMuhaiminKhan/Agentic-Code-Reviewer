"""Usage:
python -m acr.cli --diff-file change.diff --local-repo path/to/clone --ref HEAD
python -m acr.cli --local-repo . --base main --ref my-feature-branch      # review a local branch, no GitHub needed
python -m acr.cli --repo django/django --commit <sha>
python -m acr.cli --repo you/yourrepo --pr 12 --local-repo . --post
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from .config import check_model, utf8_stdout
from .github_client import fetch_commit_diff, fetch_pr_diff, upsert_pr_comment
from .graph import review


def local_diff(repo: str, base: str, head: str) -> str:
    """What a PR from `head` into `base` would show: changes since their merge base (git's three-dot diff)."""
    res = subprocess.run(
        ["git", "-C", repo, "diff", f"{base}...{head}"], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if res.returncode != 0:
        raise SystemExit(f"git diff {base}...{head} failed in {repo}: {res.stderr.strip()}")
    return res.stdout


def main() -> int:
    p = argparse.ArgumentParser(description="Agentic Code Reviewer")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--commit", help="review a commit on GitHub (needs --repo)")
    src.add_argument("--pr", type=int, help="review a pull request on GitHub (needs --repo)")
    src.add_argument("--diff-file", help="review a unified diff file")
    src.add_argument("--base", help="review the local branch --ref against this base branch (needs --local-repo)")
    p.add_argument("--repo", help="owner/name (needed for --commit / --pr)")
    p.add_argument("--local-repo", help="path to a local clone, enables the agents' read_file/grep tools")
    p.add_argument("--ref", default="HEAD", help="git ref the tools read from (the NEW code)")
    p.add_argument("--post", action="store_true", help="post/update the review as a PR comment")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--out", help="also write the Markdown review to this file (UTF-8)")
    a = p.parse_args()
    # Validate everything cheap before the slow review.
    if a.post and not (a.pr and a.repo):
        p.error("--post needs --repo and --pr")
    if (a.commit or a.pr) and not a.repo:
        p.error("--repo is required with --commit/--pr")
    if a.base and not a.local_repo:
        p.error("--base needs --local-repo")
    if problem := check_model():
        p.exit(2, f"error: {problem}\n")
    utf8_stdout()
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    if a.diff_file:
        diff = Path(a.diff_file).read_text(encoding="utf-8")
    elif a.base:
        diff = local_diff(a.local_repo, a.base, a.ref)
    else:
        diff = fetch_commit_diff(a.repo, a.commit) if a.commit else fetch_pr_diff(a.repo, a.pr)
    if not diff.strip():
        p.exit(0, "Nothing to review: the diff is empty.\n")

    result = review(diff, repo_root=a.local_repo, repo_ref=a.ref)
    print(result["markdown"])
    if a.out:
        Path(a.out).write_text(result["markdown"], encoding="utf-8")
    if a.post:
        print("\nPosted:", upsert_pr_comment(a.repo, a.pr, result["markdown"]), file=sys.stderr)
    return 1 if result["stats"].get("failed_calls") else 0


if __name__ == "__main__":
    raise SystemExit(main())
