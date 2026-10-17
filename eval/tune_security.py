"""Fast inner loop for tuning the SECURITY agent on the dev split only.

Runs just the security specialist (plus grounding and the confidence filter) on the vulnerable and the
fixed version of each dev case, judges every finding, and prints recall and false alarms. About 4x cheaper
than a full backtest, which is what makes prompt iteration practical on one GPU.

  python -m eval.tune_security --n 20
  ACR_SECURITY_PROMPT=v1 python -m eval.tune_security --n 20   # compare against the original prompt

Never point this at the test split: tuning on the cases you report would inflate the result.
"""

import argparse
import json
import os
import time

from acr.agents import run_specialist
from acr.aggregate import aggregate
from acr.config import utf8_stdout
from acr.diff_parser import parse_diff
from acr.tools import RepoContext
from eval.backtest import near, wilson
from eval.judge import judge
from eval.snapshot import build_case_diff


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="data/cases.jsonl")
    ap.add_argument("--clone", default="data/django")
    ap.add_argument("--split-file", default="eval/splits/django.json")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--tol", type=int, default=5)
    a = ap.parse_args()
    utf8_stdout()  # the output has emoji; Windows consoles default to cp1252

    by_id = {c["id"]: c for c in map(json.loads, open(a.cases, encoding="utf-8"))}
    dev = [by_id[i] for i in json.load(open(a.split_file, encoding="utf-8"))["dev"]][: a.n]
    min_conf = float(os.getenv("ACR_MIN_CONFIDENCE", "0.5"))
    hits = alarms = n_vuln = n_fixed = 0
    t0 = time.perf_counter()
    for i, case in enumerate(dev, 1):
        line = [f"[{i}/{len(dev)}] {case['cve'] or case['id']} {','.join(case['cwes'])}"]
        for version in ("vulnerable", "fixed"):
            diff, ref, ranges = build_case_diff(a.clone, case, version)
            files = parse_diff(diff)
            repo = RepoContext(a.clone, ref)
            raw = [f for fd in files if fd.reviewable for f in run_specialist("security", fd, repo)]
            findings, _ = aggregate(raw, files, min_conf)
            verdicts = [(f, judge(case, f)) for f in findings if f.file in ranges]
            hit = any(v.same_issue and near(f, ranges, a.tol) for f, v in verdicts)
            if version == "vulnerable":
                hits += hit
                n_vuln += len(findings)
            else:
                alarms += any(v.same_issue for _, v in verdicts)
                n_fixed += len(findings)
            line.append(f"{version}: {len(findings)} finding(s){' HIT' if hit else ''}")
            line += [f"    - {f.line} {f.title[:70]} [{'match' if v.same_issue else 'no'}]" for f, v in verdicts]
        print("\n".join(line), flush=True)

    n = len(dev)
    lo, hi = wilson(hits, n)
    print(
        f"\nprompt={os.getenv('ACR_SECURITY_PROMPT', 'v2')} dev n={n}: strict recall {hits}/{n} ({lo:.0%}-{hi:.0%}), "
        f"false alarms on fixed {alarms}/{n}, findings/vulnerable {n_vuln / n:.2f}, findings/fixed {n_fixed / n:.2f}, "
        f"{time.perf_counter() - t0:.0f}s"
    )


if __name__ == "__main__":
    main()
