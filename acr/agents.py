"""The specialist agents. Each one reviews ONE file for ONE concern and may ask for more
context (read a file / grep the repo) for up to MAX_TOOL_ROUNDS rounds before answering.

Why a JSON "need_context" field instead of native tool calling: 7B-14B local models are far
more reliable at filling one JSON schema than at multi-turn function calling. Stronger API
models can be swapped to LangGraph's prebuilt ReAct agent later without changing the graph.
"""

import logging
import os

from .config import structured_llm
from .diff_parser import FileDiff
from .schemas import Finding, SpecialistOutput, Synthesis, VerifyVerdict
from .tools import RepoContext, new_symbols

log = logging.getLogger("acr")
MAX_TOOL_ROUNDS = 2

BASE_RULES = """You are a senior {role} reviewing ONE file from a code change (a pull request).

The diff shows each line as: <new line number> <marker> <code>
  '+' = added or changed line (THIS is what you review)
  ' ' = unchanged context (use it to understand, never report on it)
  '-' = removed line (no line number; gone from the new code)

Rules:
- Report only real problems introduced or touched by '+' lines.
- `line` MUST be the number printed at the start of a '+' line.
- `evidence` MUST be copied exactly from that '+' line.
- An empty findings list is a correct, good answer for clean code. Never invent issues to look useful.
- confidence: 0.8+ only when you can describe the concrete attack or failure; 0.5-0.8 if plausible; below 0.5 don't report it.
- Use `need_context` ONLY when your judgement depends on code you cannot see (for example whether a caller already validates the value). At most 3 requests.
- Set `category` to "{category}".
"""

SECURITY_V1 = """Focus ONLY on security vulnerabilities:
- injection: SQL (string-built queries, .raw()/.extra() with user input), OS command (shell=True, os.system), template, LDAP, header/CRLF injection
- XSS: unescaped user data in HTML, mark_safe/|safe on untrusted input
- path traversal, SSRF, open redirect (unvalidated redirect targets)
- unsafe deserialization (pickle, yaml.load without SafeLoader, eval/exec)
- crypto: weak hashes for passwords, non-constant-time secret comparison, predictable randomness for tokens
- hardcoded secrets/keys, missing authentication/authorization checks
- denial of service from user input: catastrophic regex (ReDoS), unbounded sizes/loops/recursion, quadratic algorithms on attacker-controlled input
Only report a vulnerability when the dangerous value is plausibly attacker-controlled (request data, form fields, URLs,
uploaded files, anything the code or docstring says comes from a user). A plain function parameter such as a config
file path, with nothing showing it comes from a user, is NOT a vulnerability by itself.
Set `cwe` when you can. Do NOT report style, performance (unless attacker-triggerable), error handling or tests."""

# v2, tuned on the dev split only (eval/splits/django.json). v1 was silent on framework code: in the first
# 40-case backtest it raised 5 security findings in 80 reviews, because it treated every function argument as trusted.
SECURITY_V2 = """Focus ONLY on security vulnerabilities.

Threat model: this may be application code OR library/framework code (for example Django itself). In library code,
the arguments of public functions and methods, and values that reach it from query strings, form data, headers,
cookies, uploaded files, URLs, model field lookups and template variables, can be attacker-controlled, because the
library cannot know how applications call it. Use the "Callers" context below to see where values come from.

Work through the '+' lines and look for these sinks, asking each time "could a caller make this value malicious?":
- SQL built with string formatting or concatenation (%, f-strings, .format, +) instead of query parameters, including
  identifiers, column/key names, lookup names and SQL fragments
- HTML/JS output built from values without escaping (mark_safe, format_html misuse, |safe, string concatenation into markup)
- file paths built from input (path traversal), outbound URLs (SSRF), redirect targets (open redirect)
- regexes, loops, recursion, string/number parsing or memory use whose cost grows with input size (DoS, ReDoS)
- headers/emails/log lines built from input containing newlines (CRLF/header injection)
- secret or password comparison with ==, timing differences, weak hashing, predictable tokens
- missing permission/authentication checks, information disclosure (secrets or other users' data in output or errors)
- unsafe deserialization (pickle, yaml.load, eval/exec) of input
Report a finding only when you can name the sink line and how an attacker reaches it. If nothing concrete remains after
this check, return an empty list. Set `cwe` when you can. Do NOT report style, error handling or missing tests."""

FOCUS = {
    "security": ("application security engineer", SECURITY_V2),
    "error_handling": (
        "reliability engineer",
        """Focus ONLY on robustness and error handling:
- `except:` / `except Exception: pass` that silently swallows errors
- external input (request data, files, env, network) used without validating type, range, None or emptiness
- resources not released on error (files, sockets, locks, DB connections): prefer `with`
- unchecked return values or None that will crash later
- error messages or logs leaking secrets or internals
Python idiom: do NOT suggest wrapping code in try/except just because it *could* raise. Flag only a specific, realistic failure that would be mishandled.
Do NOT report security vulnerabilities (another agent does) or missing tests.""",
    ),
    "test_coverage": (
        "test engineer",
        """Focus ONLY on test coverage of the changed logic.
You also get (a) test files changed in the same pull request and (b) existing tests that mention the changed module or its new functions.
Report when a new or changed public function, method or branch has no test exercising it, or when a risky edge case of the NEW logic (empty input, None, boundary values, the error path) is untested.
Point `line` at the def line or the branch. In `suggestion` name the concrete test and what it asserts.
Do not report on code that is clearly covered by the tests you were shown.""",
    ),
    "all": (
        "senior software engineer",
        """Review for security vulnerabilities, missing/poor error handling, and missing tests for the new logic.
Set `category` per finding to security, error_handling or test_coverage.""",
    ),
}


def _system(kind: str) -> str:
    role, focus = FOCUS[kind]
    if kind == "security" and os.getenv("ACR_SECURITY_PROMPT", "v2") == "v1":
        focus = SECURITY_V1  # reproduces the first backtest
    category = "security, error_handling or test_coverage as appropriate" if kind == "all" else kind
    return BASE_RULES.format(role=role, category=category) + "\n" + focus


def _human(fd: FileDiff, extra: str) -> str:
    parts = [f"File: {fd.path}", "", "Diff:", fd.rendered]
    if extra:
        parts += ["", "Additional context:", extra]
    return "\n".join(parts)


def _gather(repo: RepoContext, requests) -> str:
    out = []
    for req in requests[:3]:
        if req.tool == "read_file":
            out.append(f"## read_file({req.arg})\n{repo.read_file(req.arg)}")
        else:
            out.append(f"## grep({req.arg})\n{repo.grep(req.arg)}")
    return "\n\n".join(out)


def test_context(fd: FileDiff, repo: RepoContext | None, diff_tests: str) -> str:
    """The tests a coverage claim is judged against. Shared by the specialist and the verifier,
    so the verifier never rejects a coverage finding just because it was not shown the tests."""
    extra = f"## Test files changed in this pull request\n{diff_tests or '[none]'}\n\n"
    if repo:
        extra += f"## Existing related tests\n{repo.related_tests(fd.path, new_symbols(fd.added_lines))}\n\n"
    return extra


def run_specialist(
    kind: str, fd: FileDiff, repo: RepoContext | None, diff_tests: str = "", errors: list[str] | None = None
) -> list[Finding]:
    """Review one file for one concern. A failed model call returns [] and is recorded in `errors`,
    so the review can say it is incomplete instead of looking like a clean approval."""
    llm = structured_llm(SpecialistOutput)
    extra = test_context(fd, repo, diff_tests) if kind in ("test_coverage", "all") else ""
    if kind == "security" and repo and os.getenv("ACR_SECURITY_PROMPT", "v2") != "v1":
        callers = repo.callers(new_symbols(fd.added_lines), exclude=fd.path)
        extra += f"## Callers of the functions in this change\n{callers}\n\n"

    out: SpecialistOutput | None = None
    for round_ in range(MAX_TOOL_ROUNDS + 1):
        try:
            out = llm.invoke([("system", _system(kind)), ("human", _human(fd, extra))])
        except Exception as e:  # malformed JSON after retries, provider down, etc.
            log.warning("%s agent failed on %s: %s", kind, fd.path, e)
            if errors is not None:
                errors.append(f"{kind} on {fd.path}: {type(e).__name__}")
            return []
        if out is None:
            return []
        if out.need_context and repo and round_ < MAX_TOOL_ROUNDS:
            log.info("%s agent requested context on %s: %s", kind, fd.path, out.need_context)
            extra += _gather(repo, out.need_context) + "\n\n"
            continue
        break

    if out is None:
        return []
    log.info("%s agent on %s returned %d finding(s)", kind, fd.path, len(out.findings))
    findings = []
    for raw in out.findings:
        data = raw.model_dump()
        if kind != "all":
            data["category"] = kind  # never trust the model on its own lane
        if data["category"] == "test_coverage" and data["severity"] in ("critical", "high"):
            data["severity"] = "medium"  # the 7B model rated every missing test "high"; a missing test never blocks a merge alone
        if data["category"] != "security":
            data["cwe"] = None  # seen: a CWE-78 label on a missing-test finding; CWEs describe vulnerabilities only
        findings.append(Finding(**data, file=fd.path, agents=[kind]))
    return findings


VERIFY_PROMPT = """You are a careful staff engineer double-checking another reviewer's claim about a code change.
Decide whether the claimed problem REALLY exists in the code shown. First explain what the cited line does, then decide.

is_real=false when: the code already handles the problem, the line does not do what the claim says,
the claim is generic advice ("could add validation") rather than a concrete defect, or the claim depends on
an assumption the code contradicts (for example calling a value attacker-controlled when nothing shows it comes from a user).
is_real=true when the defect is visible in the code: e.g. user input concatenated into SQL, or an exception swallowed with `pass`."""

# A missing test is not a defect "in the code", so the generic prompt rejected every coverage claim as
# "generic advice". Coverage claims get their own question, answered against the tests the specialist saw.
VERIFY_COVERAGE_PROMPT = """You are a careful test engineer double-checking another reviewer's claim that new code is untested.
You get the diff, the test files changed in the same pull request, and existing tests that mention the module or its new functions.
First list which of the shown tests (if any) call the function or exercise the branch named in the claim, then decide.

is_real=true when none of the shown tests call that new function or exercise that branch.
is_real=false when a shown test clearly exercises it, or the claim points at trivial code (a docstring, a constant, a re-export)."""


def verify(finding: Finding, fd: FileDiff, context: str = "") -> bool:
    llm = structured_llm(VerifyVerdict)
    prompt = VERIFY_COVERAGE_PROMPT if finding.category == "test_coverage" else VERIFY_PROMPT
    claim = f"[{finding.category}/{finding.severity}] line {finding.line}: {finding.title}\n{finding.explanation}"
    human = f"File: {fd.path}\n\n{fd.rendered}\n\n{context}Claim:\n{claim}"
    try:
        v = llm.invoke([("system", prompt), ("human", human)])
        if v and not v.is_real:
            log.info("verifier rejected %s %s:%s %r: %s", finding.category, finding.file, finding.line, finding.title, v.reason)
        return bool(v and v.is_real)
    except Exception as e:
        log.warning("verifier failed, keeping finding: %s", e)
        return True


SYNTH_PROMPT = """You write the summary of an automated code review. You get the list of verified findings.
Write a 2-4 sentence summary for the pull request author: the most important risk first, plain language.
verdict: request_changes if any critical/high security issue, comment if only medium/low issues, approve if none.
Do not add new issues."""


def synthesize(findings: list[Finding], n_files: int) -> Synthesis:
    if not findings:
        return Synthesis(
            summary=f"Reviewed {n_files} file(s); no security, error-handling or test-coverage issues found.", verdict="approve"
        )
    listing = "\n".join(f"- [{f.severity}] {f.category} {f.file}:{f.line} {f.title}" for f in findings)
    try:
        out = structured_llm(Synthesis).invoke([("system", SYNTH_PROMPT), ("human", listing)])
        if out:
            return out
    except Exception as e:
        log.warning("synthesizer failed, using fallback summary: %s", e)
    worst = max(findings, key=lambda f: ("low", "medium", "high", "critical").index(f.severity))
    verdict = "request_changes" if worst.severity in ("critical", "high") else "comment"
    return Synthesis(summary=f"{len(findings)} issue(s) found; most serious: {worst.title}.", verdict=verdict)
