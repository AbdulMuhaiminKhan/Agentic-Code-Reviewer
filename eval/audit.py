"""Human audit of a backtest run: replace the LLM judge's verdicts with human labels.

The judge is a small local model, so its "same issue" verdicts are noisy. This tool
  1. exports every judged finding of a run into a CSV labelling sheet (prefilled from earlier labels),
  2. scores the run with the human labels and reports judge-vs-human agreement.

Labels are keyed by (case id, version, file, line, title), so a finding labelled once keeps its label
when it shows up again in another run of the same configuration.

  python -m eval.audit export results/<run> --labels eval/labels/django_test.csv
  #   ...open the CSV, fill the `human` column with yes/no (+ optional `note`)...
  python -m eval.audit score  results/<run> --labels eval/labels/django_test.csv
"""

import argparse
import csv
import glob
import json
import os
from pathlib import Path

from acr.config import utf8_stdout
from eval.backtest import wilson

FIELDS = ["case_id", "cve", "version", "file", "line", "category", "title", "explanation", "evidence",
          "near_changed_lines", "verifier", "judge", "judge_reason", "human", "note"]  # fmt: skip


def key(row: dict) -> tuple:
    return (row["case_id"], row["version"], row["file"], str(row["line"]), row["title"])


def load_labels(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8", newline="") as fh:
        return {key(r): r for r in csv.DictReader(fh)}


def judged_rows(run_dir: str, tol: int = 5, cases_path: str = "data/cases.jsonl") -> list[dict]:
    cases = {c["id"]: c for c in map(json.loads, open(cases_path, encoding="utf-8"))}
    rows = []
    for p in sorted(glob.glob(os.path.join(run_dir, "GHSA-*.json"))):
        r = json.loads(Path(p).read_text(encoding="utf-8"))
        case = cases[r["id"]]
        for version in ("vulnerable", "fixed"):
            if version not in r:
                continue
            ranges = case["vuln_ranges"] if version == "vulnerable" else case["fixed_ranges"]
            by_key = {(f["file"], f["line"], f["title"]): f for f in r[version]["findings"]}
            for j in r[version].get("judgements", []):
                f = by_key.get((j["file"], j["line"], j["title"]), {})
                near = any(a - tol <= j["line"] <= b + tol for a, b in ranges.get(j["file"], []))
                rows.append({
                    "case_id": r["id"], "cve": r.get("cve") or "", "version": version, "file": j["file"],
                    "line": j["line"], "category": j.get("category") or f.get("category", ""), "title": j["title"],
                    "explanation": j.get("explanation") or f.get("explanation", ""), "evidence": f.get("evidence", ""),
                    "near_changed_lines": "yes" if near else "no", "verifier": j.get("verifier", "kept"),
                    "judge": "yes" if j["same_issue"] else "no", "judge_reason": j["reason"], "human": "", "note": "",
                })  # fmt: skip
    return rows


def export(run_dir: str, labels: str, out: str, cases_path: str = "data/cases.jsonl") -> None:
    known = load_labels(labels)
    rows = judged_rows(run_dir, cases_path=cases_path)
    for row in rows:
        if prev := known.get(key(row)):
            row["human"], row["note"] = prev.get("human", ""), prev.get("note", "")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    todo = sum(1 for r in rows if r["human"] not in ("yes", "no"))
    print(f"wrote {len(rows)} judged findings to {out} ({todo} still need a human label)")


def score(run_dir: str, labels: str, cases_path: str = "data/cases.jsonl", ignore_verifier: bool = False) -> dict:
    """ignore_verifier=True scores the same run as if ACR_VERIFY=0: the specialists are deterministic
    (temperature 0) and the verifier's rejects are judged and stored, so one run yields both rows."""
    known = load_labels(labels)
    rows = judged_rows(run_dir, cases_path=cases_path)
    for row in rows:
        row["human"] = known.get(key(row), {}).get("human", "")
    labelled = [r for r in rows if r["human"] in ("yes", "no")]
    missing = len(rows) - len(labelled)
    kept = labelled if ignore_verifier else [r for r in labelled if r["verifier"] == "kept"]
    case_ids = sorted({p.split(os.sep)[-1][:-5] for p in glob.glob(os.path.join(run_dir, "GHSA-*.json"))})
    n = len(case_ids)

    def hit(case_id: str, version: str, strict: bool) -> bool:
        return any(r["case_id"] == case_id and r["version"] == version and r["human"] == "yes"
                   and (r["near_changed_lines"] == "yes" or not strict) for r in kept)  # fmt: skip

    strict = sum(hit(c, "vulnerable", True) for c in case_ids)
    loose = sum(hit(c, "vulnerable", False) for c in case_ids)
    alarms = sum(hit(c, "fixed", False) for c in case_ids)
    disc = sum(hit(c, "vulnerable", True) and not hit(c, "fixed", False) for c in case_ids)
    agree = sum(r["judge"] == r["human"] for r in labelled)
    judge_yes = [r for r in labelled if r["judge"] == "yes"]
    human_yes = [r for r in labelled if r["human"] == "yes"]

    def ci(k: int) -> str:
        lo, hi = wilson(k, n)
        return f"{k}/{n} = {k / max(n, 1):.0%} ({lo:.0%}-{hi:.0%})"

    print(f"# Human-scored: {run_dir}{' (verifier ignored, = ACR_VERIFY=0)' if ignore_verifier else ''}\n")
    if missing:
        print(f"**{missing} judged findings have no human label yet; they count as 'no'.**\n")
    print("| Metric (human labels) | Value (95% CI) |\n|---|---|")
    print(f"| Recall, strict | {ci(strict)} |\n| Recall, loose | {ci(loose)} |")
    print(f"| False alarm on fixed code | {ci(alarms)} |\n| Discriminating catches | {ci(disc)} |")
    print(f"\nJudge vs human: agree on {agree}/{len(labelled)} labelled findings; "
          f"judge said yes {len(judge_yes)}x (human agreed {sum(r['human'] == 'yes' for r in judge_yes)}), "
          f"human said yes {len(human_yes)}x (judge agreed {sum(r['judge'] == 'yes' for r in human_yes)}).")  # fmt: skip
    return {"n": n, "strict": strict, "loose": loose, "false_alarms": alarms, "discriminating": disc, "agree": agree}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["export", "score"])
    ap.add_argument("run_dir")
    ap.add_argument("--labels", default="eval/labels/django_test.csv")
    ap.add_argument("--out", help="export: where to write the sheet (default: <run_dir>/audit.csv)")
    ap.add_argument("--cases", default="data/cases.jsonl")
    ap.add_argument("--ignore-verifier", action="store_true", help="score: count the verifier's rejects too (= ACR_VERIFY=0)")
    a = ap.parse_args()
    utf8_stdout()  # the output has emoji; Windows consoles default to cp1252
    if a.command == "export":
        export(a.run_dir, a.labels, a.out or os.path.join(a.run_dir, "audit.csv"), cases_path=a.cases)
    else:
        score(a.run_dir, a.labels, cases_path=a.cases, ignore_verifier=a.ignore_verifier)


if __name__ == "__main__":
    main()
