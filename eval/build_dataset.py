"""Build backtest cases from GitHub Security Advisories (free, no scraping, real ground truth).

Each advisory gives: summary, description, CWE ids, publish date, and reference URLs that often
include the fix commit. We keep advisories whose fix commit exists in our local clone and touches
1-3 non-test Python files, then record exactly which lines the fix changed (the "vulnerable region").

  git clone https://github.com/django/django.git data/django   # once
  python -m eval.build_dataset --repo django/django --package django --clone data/django --out data/cases.jsonl
"""

import argparse
import json
import re
import subprocess

import requests
from unidiff import PatchSet

from acr.diff_parser import is_test_path
from acr.github_client import API, _headers


def advisories(package: str, ecosystem: str = "pip"):
    url, params = f"{API}/advisories", {"ecosystem": ecosystem, "affects": package, "per_page": 100}
    while url:
        r = requests.get(url, headers=_headers(), params=params, timeout=60)
        r.raise_for_status()
        yield from r.json()
        url, params = r.links.get("next", {}).get("url"), None


def git(clone: str, *args: str) -> str:
    return subprocess.run(["git", "-C", clone, *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def changed_ranges(clone: str, sha: str, paths: list[str]) -> dict:
    """With -U0 each hunk is exactly the modified lines. Returns per-file (start, end) ranges on
    the parent (vulnerable) side and on the fixed side."""
    diff = git(clone, "diff", "-U0", f"{sha}^", sha, "--", *paths)
    vuln, fixed = {}, {}
    for pf in PatchSet(diff):
        for h in pf:
            s, n = h.source_start, h.source_length
            t, m = h.target_start, h.target_length
            vuln.setdefault(pf.path, []).append((s, s + n - 1) if n else (s, s + 1))
            fixed.setdefault(pf.path, []).append((t, t + m - 1) if m else (t, t + 1))
    return {"vuln_ranges": vuln, "fixed_ranges": fixed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)  # e.g. django/django
    ap.add_argument("--package", required=True)  # e.g. django
    ap.add_argument("--clone", required=True)  # local clone path
    ap.add_argument("--out", default="data/cases.jsonl")
    ap.add_argument("--max-files", type=int, default=3)
    a = ap.parse_args()

    commit_re = re.compile(rf"github\.com/{re.escape(a.repo)}/commit/([0-9a-f]{{7,40}})", re.I)
    n = 0
    with open(a.out, "w", encoding="utf-8") as fh:
        for adv in advisories(a.package):
            refs = " ".join(adv.get("references") or [])
            for sha in dict.fromkeys(commit_re.findall(refs)):
                full = git(a.clone, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}").strip()
                if not full:
                    continue  # commit not in this clone (e.g. only on a fork)
                files = git(a.clone, "diff", "--name-only", f"{full}^", full).split()
                src = [f for f in files if f.endswith(".py") and not is_test_path(f)]
                if not src or len(src) > a.max_files:
                    continue
                case = {
                    "id": adv["ghsa_id"],
                    "cve": adv.get("cve_id"),
                    "cwes": [c["cwe_id"] for c in adv.get("cwes") or []],
                    "severity": adv.get("severity"),
                    "published_at": adv.get("published_at"),
                    "summary": adv.get("summary"),
                    "description": (adv.get("description") or "")[:1500],
                    "fix_sha": full,
                    "files": src,
                    **changed_ranges(a.clone, full, src),
                }
                fh.write(json.dumps(case) + "\n")
                n += 1
                break  # one fix commit per advisory (skip backports to older branches)
    print(f"wrote {n} cases to {a.out}")


if __name__ == "__main__":
    main()
