"""Streamlit frontend for the Agentic Code Reviewer. Runs locally; talks only to your local Ollama.

  streamlit run ui/app.py

Three tabs:
  Review     - review a diff (demo, pasted, uploaded, or a local git branch) with live progress
  Benchmark  - the measured backtest results and how they were measured
  Explore    - browse any backtest run: every case, finding, judge verdict and human label
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # `streamlit run ui/app.py` does not put the repo root on the path
os.chdir(ROOT)  # relative paths (data/, results/, .env) resolve the same as for the CLI

from acr.config import check_model, env, model_label  # noqa: E402
from acr.diff_parser import parse_diff  # noqa: E402
from acr.graph import SPECIALISTS, review_events  # noqa: E402
from acr.render import ICON, TITLE, VERDICT  # noqa: E402

DEMO_REPO = ROOT / "data" / "demo-repo"
SEVERITIES = ["critical", "high", "medium", "low"]

st.set_page_config(page_title="Agentic Code Reviewer", page_icon="🤖", layout="wide")


# ---------------------------------------------------------------- helpers (pure functions are unit-tested)


def git_diff(repo: str, base: str, head: str) -> tuple[str, str]:
    """(diff, error). Three-dot diff = what a PR from `head` into `base` would show."""
    res = subprocess.run(
        ["git", "-C", repo, "diff", f"{base}...{head}"], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return (res.stdout, "") if res.returncode == 0 else ("", res.stderr.strip() or "git diff failed")


def expected_calls(diff_text: str, mode: str) -> int:
    """How many specialist calls the graph will fan out to, for the progress bar."""
    files = [fd for fd in parse_diff(diff_text) if fd.reviewable and not fd.is_test]
    return len(files) * (1 if mode == "single" else len(SPECIALISTS))


def findings_table(findings) -> pd.DataFrame:
    rows = [
        {
            "": ICON[f.severity],
            "severity": f.severity,
            "category": TITLE[f.category],
            "location": f"{f.file}:{f.line}",
            "issue": f.title,
            "confidence": round(f.confidence, 2),
            "agents": ", ".join(f.agents),
        }
        for f in findings
    ]
    return pd.DataFrame(rows, columns=["", "severity", "category", "location", "issue", "confidence", "agents"])


@st.cache_data(ttl=10, show_spinner=False)
def model_status() -> str | None:
    return check_model()


@st.cache_data
def load_run(run_dir: str) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(run_dir).glob("GHSA-*.json"))]


def run_dirs() -> list[str]:
    return sorted(
        {str(p.parent.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "results").rglob("GHSA-*.json")}, reverse=True
    )


def labels_for(run_dir: str) -> tuple[dict, str]:
    """Human labels that belong to a run (apps / reverse / Django), keyed like eval.audit."""
    from eval.audit import load_labels

    if "apps" in run_dir:
        name = "eval/labels/apps_test.csv"
    elif "reverse" in run_dir:
        name = "eval/labels/django_test_reverse.csv"
    else:
        name = "eval/labels/django_test.csv"
    return load_labels(name), name


# ---------------------------------------------------------------- sidebar

with st.sidebar:
    st.title("🤖 Agentic Code Reviewer")
    st.caption("Multi-agent PR review on a local model. $0, nothing leaves your machine.")
    problem = model_status()
    if problem:
        st.error(problem, icon="⚠️")
        if st.button("Check again"):
            model_status.clear()
            st.rerun()
    else:
        st.success(f"Model ready: `{model_label()}`", icon="✅")
    st.subheader("Settings")
    mode = st.radio(
        "Agents",
        ["multi", "single"],
        format_func=lambda m: {"multi": "3 specialists (multi-agent)", "single": "1 prompt (baseline)"}[m],
    )
    verify = st.toggle(
        "Verifier pass", value=True, help="A second, skeptical prompt re-checks each finding. Off = more recall, much more noise."
    )
    min_conf = st.slider("Minimum confidence", 0.0, 1.0, float(env("ACR_MIN_CONFIDENCE", "0.5")), 0.05)
    st.caption(f"Context window `{env('ACR_NUM_CTX', '16384')}` tokens · change the model in `.env`")

tab_review, tab_bench, tab_explore = st.tabs(["🔍 Review", "📊 Benchmark results", "🗂️ Explore runs"])

# ---------------------------------------------------------------- review tab

with tab_review:
    st.header("Review a change")
    source = st.radio(
        "What should be reviewed?",
        ["Demo (planted bugs)", "Local git branch", "Paste a diff", "Upload a .diff file"],
        horizontal=True,
        key="source",
    )
    diff_text, repo_root, ref, err, n_calls = "", None, "HEAD", "", 0

    if source == "Demo (planted bugs)":
        st.caption("A tiny shop app whose latest commit adds a SQL injection, a swallowed exception and untested functions.")
        if not DEMO_REPO.exists():
            if st.button("Create the demo repo"):
                subprocess.run([sys.executable, "examples/build_demo.py"], check=True, cwd=ROOT)
                st.rerun()
            err = "The demo repo doesn't exist yet."
        else:
            diff_text, err = git_diff(str(DEMO_REPO), "HEAD~1", "HEAD")
            repo_root = str(DEMO_REPO)
    elif source == "Local git branch":
        c1, c2, c3 = st.columns([3, 1, 1])
        repo = c1.text_input("Repository folder", placeholder=r"C:\path\to\your\repo")
        base = c2.text_input("Base branch", value="main")
        head = c3.text_input("Your branch", value="HEAD")
        if repo:
            if not (Path(repo) / ".git").exists():
                err = "That folder is not a git repository."
            else:
                diff_text, err = git_diff(repo, base, head)
                repo_root, ref = repo, head
    elif source == "Paste a diff":
        diff_text = st.text_area("Unified diff (`git diff` output)", height=220)
    else:
        up = st.file_uploader("Diff or patch file", type=["diff", "patch", "txt"])
        if up:
            diff_text = up.getvalue().decode("utf-8", errors="replace")

    if err:
        st.warning(err)
    if diff_text.strip():
        n_calls = expected_calls(diff_text, mode)
        files = [fd.path for fd in parse_diff(diff_text) if fd.reviewable and not fd.is_test]
        st.caption(f"{len(files)} Python file(s) to review: {', '.join(files) or 'none'} · {n_calls} specialist call(s)")
        with st.expander("Show the diff"):
            st.code(diff_text[:20000], language="diff")
        if not repo_root:
            st.info(
                "No repository attached, so the agents can't read other files or find existing tests. A local branch gives better reviews.",
                icon="ℹ️",
            )

    run = st.button(
        "Run review", type="primary", disabled=bool(problem) or not diff_text.strip() or not expected_calls(diff_text, mode)
    )
    if run:
        os.environ.update({"ACR_MODE": mode, "ACR_VERIFY": "1" if verify else "0", "ACR_MIN_CONFIDENCE": str(min_conf)})
        total, done, t0 = max(n_calls, 1), 0, time.perf_counter()
        bar = st.progress(0.0, text="Starting…")
        with st.status("Reviewing…", expanded=False) as status:
            final = {}
            for kind, payload in review_events(diff_text, repo_root=repo_root, repo_ref=ref):
                if kind == "done":
                    final = payload
                    continue
                if payload == "specialist":
                    done += 1
                    bar.progress(min(done / total, 1.0), text=f"Specialist calls: {done}/{total}")
                st.write(f"✔ {payload} ({time.perf_counter() - t0:.0f}s)")
            status.update(label=f"Done in {time.perf_counter() - t0:.0f}s", state="complete")
        bar.empty()
        st.session_state["result"] = final

    result = st.session_state.get("result")
    if result:
        st.divider()
        stats = result["stats"]
        st.subheader(VERDICT[result["verdict"]])
        if stats.get("failed_calls"):
            st.error(f"{stats['failed_calls']} agent call(s) failed. This review is incomplete.", icon="⚠️")
        st.write(result["summary"])
        m = st.columns(5)
        m[0].metric("Findings", len(result["findings"]))
        m[1].metric("Raw from agents", stats.get("raw", 0))
        m[2].metric("Dropped: not in diff", stats.get("dropped_ungrounded", 0), help="Quoted evidence didn't match an added line")
        m[3].metric("Merged duplicates", stats.get("merged_duplicates", 0))
        m[4].metric("Rejected by verifier", stats.get("dropped_by_verifier", 0))

        findings = result["findings"]
        if findings:
            f1, f2 = st.columns(2)
            cats = f1.multiselect("Category", list(TITLE), default=list(TITLE), format_func=lambda c: TITLE[c])
            sevs = f2.multiselect("Severity", SEVERITIES, default=SEVERITIES)
            shown = [f for f in findings if f.category in cats and f.severity in sevs]
            st.dataframe(findings_table(shown), hide_index=True, width="stretch")
            for f in shown:
                with st.expander(f"{ICON[f.severity]} {f.title} · {f.file}:{f.line}"):
                    st.code(f.evidence.strip(), language="python")
                    st.markdown(f"**Why:** {f.explanation}")
                    st.markdown(f"**Suggested fix:** {f.suggestion}")
                    st.caption(
                        f"{TITLE[f.category]} · confidence {f.confidence:.2f} · flagged by {', '.join(f.agents)}"
                        + (f" · {f.cwe}" if f.cwe else "")
                    )
        else:
            st.success("No findings.")
        if result.get("rejected"):
            with st.expander(f"Findings the verifier rejected ({len(result['rejected'])})"):
                st.dataframe(findings_table(result["rejected"]), hide_index=True, width="stretch")
        st.download_button("Download the review (Markdown)", result["markdown"], file_name="review.md", mime="text/markdown")

# ---------------------------------------------------------------- benchmark tab

with tab_bench:
    st.header("How good is it on real vulnerabilities?")
    st.markdown(
        "Each case is a real CVE from GitHub Security Advisories. The reviewer sees the vulnerable function(s) as new code, "
        "with no advisory, commit message or fix, and then the **patched** version as a control. Every finding was labelled "
        "by hand. Nothing here was tuned on these cases."
    )
    summary_path = ROOT / "results" / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        df = pd.DataFrame(summary["rows"]).rename(
            columns={
                "label": "configuration",
                "n": "cases",
                "caught": "real CVE caught",
                "false_alarms": "patched code still flagged",
            }
        )
        st.dataframe(df, hide_index=True, width="stretch")
        svg = ROOT / "docs" / "results.svg"
        if svg.exists():
            st.image(svg.read_text(encoding="utf-8"), width="stretch")
    st.subheader("Reports")
    for md in sorted((ROOT / "results").glob("*.md"), reverse=True):
        with st.expander(md.stem):
            st.markdown(md.read_text(encoding="utf-8"))

# ---------------------------------------------------------------- explore tab

with tab_explore:
    st.header("Explore a backtest run")
    dirs = run_dirs()
    if not dirs:
        st.info("No backtest runs found under results/. Run `python -m eval.backtest ...` first.")
    else:
        run_dir = st.selectbox("Run", dirs)
        rows = load_run(run_dir)
        labels, label_file = labels_for(run_dir)
        from eval.audit import key

        def human(r: dict, version: str, j: dict) -> str:
            lab = labels.get(
                key({"case_id": r["id"], "version": version, "file": j["file"], "line": j["line"], "title": j["title"]})
            )
            return lab["human"] if lab else ""

        table = []
        for r in rows:
            entry = {
                "advisory": r.get("cve") or r["id"],
                "cwe": ", ".join(r.get("cwes", [])),
                "published": (r.get("published_at") or "")[:10],
            }
            for v in ("vulnerable", "fixed"):
                if v in r:
                    js = [j for j in r[v].get("judgements", []) if j.get("verifier", "kept") == "kept"]
                    entry[f"{v}: findings"] = r[v]["n_findings"]
                    entry[f"{v}: judge hit"] = bool(r[v]["strict_hit"] if v == "vulnerable" else r[v]["loose_hit"])
                    entry[f"{v}: human hit"] = any(human(r, v, j) == "yes" for j in js)
            table.append(entry)
        st.caption(f"{len(rows)} cases · human labels from `{label_file}`")
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")

        pick = st.selectbox("Case", [r.get("cve") or r["id"] for r in rows])
        r = next(x for x in rows if (x.get("cve") or x["id"]) == pick)
        for v in ("vulnerable", "fixed"):
            if v not in r:
                continue
            st.markdown(f"**{v.title()} version** · {r[v]['n_findings']} finding(s) · stats `{r[v].get('stats', {})}`")
            js = r[v].get("judgements", [])
            if js:
                st.dataframe(
                    pd.DataFrame([
                        {"line": j["line"], "title": j["title"], "category": j.get("category", ""), "verifier": j.get("verifier", "kept"),
                         "judge": "yes" if j["same_issue"] else "no", "human": human(r, v, j), "judge reason": j["reason"]}
                        for j in js
                    ]),
                    hide_index=True,
                    width="stretch",
                )  # fmt: skip
