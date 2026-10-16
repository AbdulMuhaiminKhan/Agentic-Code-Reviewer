"""Backtest the reviewer on historical vulnerabilities.

For every case we run the reviewer twice:
  vulnerable = code at the parent of the fix commit  -> did it catch the bug? (recall)
  fixed      = same functions at the fix commit      -> does it still flag the bug? (false alarm)

  python -m eval.backtest --cases data/cases.jsonl --clone data/django --n 40 --out results/
  ACR_MODE=single python -m eval.backtest ...      # one-prompt baseline (ablation)
  ACR_VERIFY=0    python -m eval.backtest ...      # no verifier (ablation)
"""

import argparse
import json
import math
import os
import random
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from acr.config import model_label
from acr.graph import review
from eval.judge import judge
from eval.snapshot import build_case_diff


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def near(f, ranges: dict, tol: int) -> bool:
    return any(a - tol <= f.line <= b + tol for a, b in ranges.get(f.file, []))


def score(case: dict, findings, ranges: dict, tol: int, rejected=()) -> dict:
    in_file = [f for f in findings if f.file in ranges]
    judged = [(f, judge(case, f)) for f in in_file]
    matches = [(f, v) for f, v in judged if v.same_issue]
    # Judge the verifier's rejects too: a rejected match is a real catch the verifier threw away.
    rejected_judged = [(f, judge(case, f)) for f in rejected if f.file in ranges]
    return {
        "n_findings": len(findings),
        "verifier_dropped_hit": any(v.same_issue and near(f, ranges, tol) for f, v in rejected_judged),
        "judgements": [
            {"file": f.file, "line": f.line, "title": f.title, "same_issue": v.same_issue, "reason": v.reason, "verifier": kept}
            for kept, pairs in (("kept", judged), ("rejected", rejected_judged))
            for f, v in pairs
        ],
        "loose_hit": bool(matches),  # right issue, anywhere in the file
        "strict_hit": any(near(f, ranges, tol) for f, _ in matches),  # right issue AND right place
        "matches": [{"file": f.file, "line": f.line, "title": f.title, "reason": v.reason} for f, v in matches],
        "findings": [f.model_dump() for f in findings],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--clone", required=True)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mode", choices=["snapshot", "reverse"], default="snapshot")
    ap.add_argument("--tol", type=int, default=5, help="line tolerance for a strict hit")
    ap.add_argument("--after", help="only advisories published after YYYY-MM-DD (contamination control)")
    ap.add_argument("--out", default="results")
    ap.add_argument("--resume", help="existing run directory: skip cases that already have a result file there")
    ap.add_argument("--split", choices=["dev", "test"], help="use a frozen split from --split-file instead of a random sample")
    ap.add_argument("--split-file", default="eval/splits/django.json")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # the report has emoji; Windows consoles default to cp1252

    cases = [json.loads(ln) for ln in open(a.cases, encoding="utf-8")]
    if a.split:
        by_id = {c["id"]: c for c in cases}
        cases = [by_id[i] for i in json.load(open(a.split_file, encoding="utf-8"))[a.split]]
    else:
        random.Random(a.seed).shuffle(cases)  # noqa: S311 (sampling, not crypto)
    if a.after:
        cases = [c for c in cases if (c.get("published_at") or "") > a.after]
    cases = cases[: a.n]

    if a.resume:
        run_dir = Path(a.resume)
    else:
        run_dir = Path(a.out) / f"{datetime.now():%Y%m%d-%H%M%S}-{os.getenv('ACR_MODE', 'multi')}-{a.mode}"
    run_dir.mkdir(parents=True, exist_ok=True)
    rows, latencies = [], []
    for i, case in enumerate(cases, 1):
        done = run_dir / f"{case['id']}.json"
        if a.resume and done.exists():
            row = json.loads(done.read_text(encoding="utf-8"))
            rows.append(row)
            latencies += [row[v]["latency_s"] for v in ("vulnerable", "fixed") if "latency_s" in row.get(v, {})]
            print(f"[{i}/{len(cases)}] {case['id']} (already done, loaded)", flush=True)
            continue
        row = {"id": case["id"], "cve": case["cve"], "cwes": case["cwes"], "published_at": case["published_at"]}
        versions = ["vulnerable"] if a.mode == "reverse" else ["vulnerable", "fixed"]
        for version in versions:
            diff, ref, ranges = build_case_diff(a.clone, case, version, a.mode)
            t0 = time.perf_counter()
            result = review(diff, repo_root=a.clone, repo_ref=ref)  # the reviewer never sees the advisory
            latency = time.perf_counter() - t0
            latencies.append(latency)
            row[version] = score(case, result["findings"], ranges, a.tol, result.get("rejected", []))
            row[version]["latency_s"] = round(latency, 1)
            row[version]["stats"] = result["stats"]
        (run_dir / f"{case['id']}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        rows.append(row)
        v = row["vulnerable"]
        print(
            f"[{i}/{len(cases)}] {case['id']} strict={v['strict_hit']} loose={v['loose_hit']} findings={v['n_findings']}",
            flush=True,
        )

    n = len(rows)
    strict = sum(r["vulnerable"]["strict_hit"] for r in rows)
    loose = sum(r["vulnerable"]["loose_hit"] for r in rows)
    lines = [
        f"# Backtest: {model_label()} · mode={os.getenv('ACR_MODE', 'multi')} · verify={os.getenv('ACR_VERIFY', '1')} · input={a.mode}",
        "",
        "| Metric | Value | 95% CI |",
        "|---|---|---|",
        f"| Recall, strict (right issue, right lines) | **{strict}/{n} = {strict / n:.0%}** | {wilson(strict, n)[0]:.0%}-{wilson(strict, n)[1]:.0%} |",
        f"| Recall, loose (right issue, same file) | {loose}/{n} = {loose / n:.0%} | {wilson(loose, n)[0]:.0%}-{wilson(loose, n)[1]:.0%} |",
    ]
    if a.mode == "snapshot":
        fa = sum(r["fixed"]["loose_hit"] for r in rows)
        disc = sum(r["vulnerable"]["strict_hit"] and not r["fixed"]["loose_hit"] for r in rows)
        lines += [
            f"| False alarm on fixed code (still flags the patched bug) | {fa}/{n} = {fa / n:.0%} | {wilson(fa, n)[0]:.0%}-{wilson(fa, n)[1]:.0%} |",
            f"| Discriminating catches (vulnerable caught AND fixed clean) | **{disc}/{n} = {disc / n:.0%}** | {wilson(disc, n)[0]:.0%}-{wilson(disc, n)[1]:.0%} |",
            f"| Avg findings per fixed (clean) snippet, i.e. noise | {statistics.mean(r['fixed']['n_findings'] for r in rows):.2f} | |",
        ]
    lines += [
        f"| Avg findings per vulnerable snippet | {statistics.mean(r['vulnerable']['n_findings'] for r in rows):.2f} | |",
        f"| Strict catches the verifier rejected (would be hits with ACR_VERIFY=0) | "
        f"{sum(r['vulnerable'].get('verifier_dropped_hit', False) for r in rows)}/{n} | |",
        f"| Latency per review p50 / max | {statistics.median(latencies or [0]):.1f}s / {max(latencies or [0]):.1f}s "
        f"(over {len(latencies)} reviews) | |",
        "",
        "| Advisory | CWE | Caught (strict) | Fixed version flagged |",
        "|---|---|---|---|",
    ]
    for r in rows:
        fixed = "n/a" if "fixed" not in r else ("⚠️ yes" if r["fixed"]["loose_hit"] else "no")
        lines.append(
            f"| {r['cve'] or r['id']} | {', '.join(r['cwes'])} | {'✅' if r['vulnerable']['strict_hit'] else '❌'} | {fixed} |"
        )
    report = "\n".join(lines)
    (run_dir / "REPORT.md").write_text(report, encoding="utf-8")
    print("\n" + report + f"\n\nSaved to {run_dir}")


if __name__ == "__main__":
    main()
