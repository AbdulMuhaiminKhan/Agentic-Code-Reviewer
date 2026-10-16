"""Usage:
python -m acr.cli --repo django/django --commit <sha>
python -m acr.cli --repo you/yourrepo --pr 12 --local-repo . --post
python -m acr.cli --diff-file change.diff --local-repo path/to/clone --ref HEAD
"""

import argparse
import logging
import sys
from pathlib import Path

from .github_client import fetch_commit_diff, fetch_pr_diff, upsert_pr_comment
from .graph import review


def main() -> int:
    p = argparse.ArgumentParser(description="Agentic Code Reviewer")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--commit")
    src.add_argument("--pr", type=int)
    src.add_argument("--diff-file")
    p.add_argument("--repo", help="owner/name (needed for --commit / --pr)")
    p.add_argument("--local-repo", help="path to a local clone, enables the agents' read_file/grep tools")
    p.add_argument("--ref", default="HEAD", help="git ref the tools read from (the NEW code)")
    p.add_argument("--post", action="store_true", help="post/update the review as a PR comment")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--out", help="also write the Markdown review to this file (UTF-8)")
    a = p.parse_args()
    if a.post and not (a.pr and a.repo):
        p.error("--post needs --repo and --pr")  # check before the slow review, not after
    # Windows consoles default to cp1252, which cannot print the emoji in the review.
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    if a.diff_file:
        diff = Path(a.diff_file).read_text(encoding="utf-8")
    elif not a.repo:
        p.error("--repo is required with --commit/--pr")
    else:
        diff = fetch_commit_diff(a.repo, a.commit) if a.commit else fetch_pr_diff(a.repo, a.pr)

    result = review(diff, repo_root=a.local_repo, repo_ref=a.ref)
    print(result["markdown"])
    if a.out:
        Path(a.out).write_text(result["markdown"], encoding="utf-8")
    if a.post:
        print("\nPosted:", upsert_pr_comment(a.repo, a.pr, result["markdown"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
