from .schemas import Finding

ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}
TITLE = {"security": "Security", "error_handling": "Error handling", "test_coverage": "Test coverage"}
VERDICT = {"approve": "✅ Looks good", "comment": "💬 Comments", "request_changes": "⛔ Changes requested"}


def to_markdown(findings: list[Finding], summary: str, verdict: str, stats: dict, model: str, seconds: float) -> str:
    out = [f"## 🤖 Agentic Code Review: {VERDICT[verdict]}", "", summary, ""]
    if findings:
        out += ["| | Category | Location | Issue |", "|---|---|---|---|"]
        out += [f"| {ICON[f.severity]} | {TITLE[f.category]} | `{f.file}:{f.line}` | {f.title} |" for f in findings]
        out.append("")
    for cat in ("security", "error_handling", "test_coverage"):
        items = [f for f in findings if f.category == cat]
        if not items:
            continue
        out += [f"### {TITLE[cat]}", ""]
        for f in items:
            cwe = f" · {f.cwe}" if f.cwe else ""
            out += [
                f"<details><summary>{ICON[f.severity]} <b>{f.title}</b> · <code>{f.file}:{f.line}</code>{cwe}</summary>",
                "",
                f"```python\n{f.evidence.strip()}\n```",
                "",
                f.explanation,
                "",
                f"**Suggested fix:** {f.suggestion}",
                "",
                f"<sub>confidence {f.confidence:.2f} · flagged by {', '.join(f.agents)}</sub>",
                "</details>",
                "",
            ]
    out.append(
        f"<sub>{stats.get('raw', 0)} raw findings → {len(findings)} after grounding, confidence filter, "
        f"verification and dedupe · model `{model}` · {seconds:.1f}s</sub>"
    )
    return "\n".join(out)
