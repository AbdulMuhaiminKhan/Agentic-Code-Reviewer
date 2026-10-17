"""LangGraph wiring: parse → fan-out (file × agent) in parallel → aggregate → verify → synthesize → render."""

import operator
import os
import time
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import run_specialist, synthesize, test_context, verify
from .aggregate import aggregate
from .config import model_label
from .diff_parser import FileDiff, parse_diff
from .render import to_markdown
from .schemas import Finding
from .tools import RepoContext

SPECIALISTS = ("security", "error_handling", "test_coverage")


class ReviewState(TypedDict, total=False):
    diff_text: str
    repo_root: str | None
    repo_ref: str
    started: float
    files: list[FileDiff]
    raw_findings: Annotated[list[Finding], operator.add]  # reducer: parallel agents append
    failed_calls: Annotated[list[str], operator.add]  # agent calls that errored (model down, broken JSON)
    findings: list[Finding]
    rejected: list[Finding]  # dropped by the verifier; kept so the backtest can measure what it cost
    stats: dict
    summary: str
    verdict: str
    markdown: str


class SpecialistTask(TypedDict):
    kind: str
    file: FileDiff
    diff_tests: str
    repo_root: str | None
    repo_ref: str


def parse_node(state: ReviewState) -> dict:
    return {"files": parse_diff(state["diff_text"]), "started": time.perf_counter()}


def _diff_tests(files: list[FileDiff]) -> str:
    return "\n\n".join(f"# {fd.path}\n{fd.rendered}" for fd in files if fd.is_test)[:6000]


def fan_out(state: ReviewState):
    files = state["files"]
    diff_tests = _diff_tests(files)
    kinds = ("all",) if os.getenv("ACR_MODE", "multi") == "single" else SPECIALISTS
    sends = [
        Send(
            "specialist",
            SpecialistTask(
                kind=k, file=fd, diff_tests=diff_tests, repo_root=state.get("repo_root"), repo_ref=state.get("repo_ref", "HEAD")
            ),
        )
        for fd in files
        if fd.reviewable and not fd.is_test
        for k in kinds
    ]
    return sends or "aggregate"


def specialist_node(task: SpecialistTask) -> dict:
    repo = RepoContext(root, task["repo_ref"]) if (root := task.get("repo_root")) else None
    errors: list[str] = []
    findings = run_specialist(task["kind"], task["file"], repo, task["diff_tests"], errors=errors)
    return {"raw_findings": findings, "failed_calls": errors}


def aggregate_node(state: ReviewState) -> dict:
    min_conf = float(os.getenv("ACR_MIN_CONFIDENCE", "0.5"))
    findings, stats = aggregate(state.get("raw_findings", []), state["files"], min_conf)
    stats["failed_calls"] = len(state.get("failed_calls", []))
    return {"findings": findings, "stats": stats}


def verify_node(state: ReviewState) -> dict:
    if os.getenv("ACR_VERIFY", "1") != "1":
        return {}
    by_path = {fd.path: fd for fd in state["files"]}
    repo = RepoContext(root, state.get("repo_ref", "HEAD")) if (root := state.get("repo_root")) else None
    diff_tests = _diff_tests(state["files"])
    kept, rejected = [], []
    for f in state["findings"]:
        fd = by_path[f.file]
        context = test_context(fd, repo, diff_tests) if f.category == "test_coverage" else ""
        (kept if verify(f, fd, context) else rejected).append(f)
    stats = {**state["stats"], "dropped_by_verifier": len(state["findings"]) - len(kept)}
    return {"findings": kept, "rejected": rejected, "stats": stats}


def synthesize_node(state: ReviewState) -> dict:
    n_files = sum(1 for fd in state["files"] if fd.reviewable and not fd.is_test)
    syn = synthesize(state["findings"], n_files)
    if failed := state["stats"].get("failed_calls"):
        # Never let a review where agents crashed look like a clean approval.
        syn.summary = f"⚠️ Review incomplete: {failed} agent call(s) failed (run with -v for details). " + syn.summary
        if syn.verdict == "approve":
            syn.verdict = "comment"
    md = to_markdown(
        state["findings"], syn.summary, syn.verdict, state["stats"], model_label(), time.perf_counter() - state["started"]
    )
    return {"summary": syn.summary, "verdict": syn.verdict, "markdown": md}


def build_graph():
    g = StateGraph(ReviewState)
    g.add_node("parse", parse_node)
    g.add_node("specialist", specialist_node)  # pyright: ignore[reportArgumentType] (Send passes a SpecialistTask, not the state)
    g.add_node("aggregate", aggregate_node)
    g.add_node("verify", verify_node)
    g.add_node("synthesize", synthesize_node)
    g.add_edge(START, "parse")
    g.add_conditional_edges("parse", fan_out, ["specialist", "aggregate"])
    g.add_edge("specialist", "aggregate")
    g.add_edge("aggregate", "verify")
    g.add_edge("verify", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


def review(diff_text: str, repo_root: str | None = None, repo_ref: str = "HEAD") -> ReviewState:
    graph = build_graph()
    return graph.invoke(  # pyright: ignore[reportReturnType] (LangGraph returns a plain dict)
        {"diff_text": diff_text, "repo_root": repo_root, "repo_ref": repo_ref},
        config={"max_concurrency": int(os.getenv("ACR_MAX_CONCURRENCY", "2"))},
    )


def review_events(diff_text: str, repo_root: str | None = None, repo_ref: str = "HEAD"):
    """Same as review(), but yields ("node", name) as each graph step finishes and ("done", state) at the end,
    so a UI can show live progress (for example 4 of 9 specialist calls done)."""
    graph = build_graph()
    state: ReviewState = {}
    for mode, data in graph.stream(
        {"diff_text": diff_text, "repo_root": repo_root, "repo_ref": repo_ref},
        config={"max_concurrency": int(os.getenv("ACR_MAX_CONCURRENCY", "2"))},
        stream_mode=["updates", "values"],
    ):
        if mode == "updates":
            for name in data:
                yield "node", name
        else:
            state = data  # pyright: ignore[reportAssignmentType]
    yield "done", state
