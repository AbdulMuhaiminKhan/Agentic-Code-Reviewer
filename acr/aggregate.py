"""Deterministic post-processing. This is where most hallucinations die:
a finding must point at a real added line whose text matches the quoted evidence."""

import logging
import re

from .diff_parser import FileDiff
from .schemas import SEVERITY_RANK, Finding

log = logging.getLogger("acr")
_WS = re.compile(r"\s+")
_WORD = re.compile(r"[a-z_][a-z0-9_]{2,}")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def ground(f: Finding, fd: FileDiff, window: int = 3) -> Finding | None:
    """Return the finding snapped to the real line, or None if it can't be grounded in the diff."""
    ev_lines = [_norm(ln.lstrip("+")) for ln in f.evidence.splitlines() if _norm(ln.lstrip("+"))]
    if ev_lines:
        first = ev_lines[0]
        matches = [
            n for n, text in fd.added_lines.items() if first and (first in _norm(text) or (_norm(text) and _norm(text) in first))
        ]
        if matches:
            f.line = min(matches, key=lambda n: abs(n - f.line))
            return f
    # test-coverage findings often quote a def line loosely; accept if the line itself is added code
    if f.category == "test_coverage" and any(abs(n - f.line) <= window for n in fd.added_lines):
        return f
    return None


def _similar(a: Finding, b: Finding) -> bool:
    wa = set(_WORD.findall((a.title + " " + a.explanation).lower()))
    wb = set(_WORD.findall((b.title + " " + b.explanation).lower()))
    jaccard = len(wa & wb) / max(1, len(wa | wb))
    if a.category == b.category or jaccard >= 0.25:
        return True
    # Another agent straying into security's lane on the very same line ("security risk: shell=True")
    # next to the security agent's "Command injection" is one issue, even when the wording differs.
    if a.line == b.line and "security" in (a.category, b.category):
        other = b if a.category == "security" else a
        return "secur" in (other.title + " " + other.explanation).lower()
    return False


def dedupe(findings: list[Finding], line_window: int = 2) -> list[Finding]:
    kept: list[Finding] = []
    for f in sorted(findings, key=lambda x: (-SEVERITY_RANK[x.severity], -x.confidence)):
        dup = next((k for k in kept if k.file == f.file and abs(k.line - f.line) <= line_window and _similar(k, f)), None)
        if dup:
            dup.agents = sorted(set(dup.agents + f.agents))
            dup.confidence = max(dup.confidence, f.confidence)
        else:
            kept.append(f)
    return sorted(kept, key=lambda x: (-SEVERITY_RANK[x.severity], x.file, x.line))


def aggregate(raw: list[Finding], files: list[FileDiff], min_conf: float) -> tuple[list[Finding], dict]:
    by_path = {fd.path: fd for fd in files}
    grounded, dropped_ungrounded = [], 0
    for f in raw:
        fd = by_path.get(f.file)
        g = ground(f, fd) if fd else None
        if g is None:
            dropped_ungrounded += 1
            log.info("dropped ungrounded %s finding %s:%s %r evidence=%r", f.category, f.file, f.line, f.title, f.evidence)
        else:
            grounded.append(g)
    confident = [f for f in grounded if f.confidence >= min_conf]
    for f in grounded:
        if f.confidence < min_conf:
            log.info("dropped low-confidence (%.2f) %s finding %s:%s %r", f.confidence, f.category, f.file, f.line, f.title)
    final = dedupe(confident)
    stats = {
        "raw": len(raw),
        "dropped_ungrounded": dropped_ungrounded,
        "dropped_low_confidence": len(grounded) - len(confident),
        "merged_duplicates": len(confident) - len(final),
    }
    return final, stats
