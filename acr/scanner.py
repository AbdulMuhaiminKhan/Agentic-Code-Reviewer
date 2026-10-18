"""Static "leads" for the security agent: Bandit plus a few precise AST rules.

Rule-based scanners are good at patterns (shell=True, SQL built from strings) and bad at judgement (is
this value attacker-controlled? is it already validated?). LLMs are the opposite. So the scanner runs
first and only on the lines a change adds, and its hits are handed to the security agent as leads to
confirm or dismiss. Nothing here calls a model, and a scanner failure never breaks a review.
"""

import ast
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .diff_parser import FileDiff
from .schemas import Severity
from .tools import RepoContext

log = logging.getLogger("acr")

# Bandit checks that only say "this module is imported" or fire on every test: noise for a reviewer.
IGNORED_BANDIT = {"B101", "B403", "B404", "B405", "B406", "B408", "B410", "B311"}
REQUEST_ATTRS = {"args", "form", "values", "GET", "POST", "json", "data", "cookies", "headers", "referrer", "files"}
SAFE_PATH_CALLS = {"secure_filename", "basename", "normpath", "realpath", "abspath", "is_relative_to", "commonpath"}
FETCH_CALLS = {"get", "post", "put", "request", "urlopen", "get_url", "urlretrieve"}


@dataclass
class Lead:
    line: int
    rule: str
    cwe: str
    message: str
    severity: Severity

    def render(self) -> str:
        return f"- line {self.line}: [{self.rule}, {self.cwe}] {self.message}"


def _source_for(fd: FileDiff, repo: RepoContext | None) -> str:
    """The whole new file when we have the repo (so it parses); otherwise the added lines at their real
    line numbers, with blank lines in between (enough for snapshot-style diffs of whole functions)."""
    if repo:
        text = repo.file_text(fd.path)
        if text:
            return text
    if not fd.added_lines:
        return ""
    last = max(fd.added_lines)
    return "\n".join(fd.added_lines.get(n, "") for n in range(1, last + 1)) + "\n"


def run_bandit(source: str) -> list[Lead]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snippet.py"
        path.write_text(source, encoding="utf-8")
        try:
            res = subprocess.run(
                [sys.executable, "-m", "bandit", "-q", "-f", "json", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )  # fmt: skip
            results = json.loads(res.stdout or "{}").get("results", [])
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            log.warning("bandit failed: %s", e)
            return []
    return [
        Lead(r["line_number"], f"bandit {r['test_id']}", f"CWE-{r.get('issue_cwe', {}).get('id', '?')}", r["issue_text"],
             cast(Severity, r["issue_severity"].lower()))
        for r in results
        if r["test_id"] not in IGNORED_BANDIT
    ]  # fmt: skip


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _from_request(node: ast.AST) -> bool:
    """True if the expression reads request data: request.args, request.form["x"], request.GET.get(...)."""
    return any(
        isinstance(n, ast.Attribute) and n.attr in REQUEST_ATTRS and isinstance(n.value, ast.Name) and n.value.id == "request"
        for n in ast.walk(node)
    )


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def ast_rules(source: str) -> list[Lead]:
    """Patterns Bandit doesn't cover, kept deliberately narrow: they are leads for the model, not verdicts."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    leads: list[Lead] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]:
        params = {a.arg for a in fn.args.args + fn.args.kwonlyargs} - {"self", "cls"}
        sanitized = any(isinstance(c, ast.Call) and _call_name(c) in SAFE_PATH_CALLS for c in ast.walk(fn))
        for call in [c for c in ast.walk(fn) if isinstance(c, ast.Call)]:
            name, args = _call_name(call), call.args + [k.value for k in call.keywords]
            tainted = [a for a in args if _from_request(a) or (_names(a) & params)]
            if name in ("redirect", "HttpResponseRedirect") and any(_from_request(a) for a in args):
                leads.append(Lead(call.lineno, "acr open-redirect", "CWE-601",
                                  "Redirect target comes from the request without an allow-list check", "medium"))  # fmt: skip
            elif name in ("open", "join", "send_file", "send_from_directory") and tainted and not sanitized:
                leads.append(Lead(call.lineno, "acr path", "CWE-22",
                                  f"{name}() builds a file path from caller/request input without normalising it", "low"))  # fmt: skip
            elif (
                name in FETCH_CALLS
                and args
                and (_from_request(args[0]) or _names(args[0]) & params)
                and "url" in ast.unparse(args[0]).lower()
            ):
                leads.append(Lead(call.lineno, "acr ssrf", "CWE-918",
                                  f"{name}() fetches a URL that comes from caller/request input", "low"))  # fmt: skip
    return leads


def leads_for(fd: FileDiff, repo: RepoContext | None) -> list[Lead]:
    """Scanner leads on the lines this change adds, de-duplicated by (line, CWE)."""
    source = _source_for(fd, repo)
    if not source.strip():
        return []
    seen, out = set(), []
    for lead in sorted(run_bandit(source) + ast_rules(source), key=lambda x: x.line):
        if lead.line in fd.added_lines and (lead.line, lead.cwe) not in seen:
            seen.add((lead.line, lead.cwe))
            out.append(lead)
    return out
