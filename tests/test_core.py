import pytest

from acr import agents, graph
from acr.aggregate import dedupe, ground
from acr.diff_parser import parse_diff
from acr.schemas import Finding, RawFinding, SpecialistOutput, VerifyVerdict
from eval.judge import JudgeVerdict
from eval.snapshot import enclosing_spans, synthetic_added_diff

DIFF = """diff --git a/app/views.py b/app/views.py
--- a/app/views.py
+++ b/app/views.py
@@ -10,3 +10,5 @@ def search(request):
     q = request.GET["q"]
-    rows = Item.objects.filter(name=q)
+    sql = "SELECT * FROM item WHERE name = '%s'" % q
+    rows = Item.objects.raw(sql)
+    return render(request, "x.html", {"rows": rows})
     log.info("done")
"""


def _f(**kw):
    base = dict(
        category="security",
        line=11,
        severity="high",
        title="SQL injection via raw query",
        evidence="sql = \"SELECT * FROM item WHERE name = '%s'\" % q",
        explanation="user input in SQL",
        suggestion="use params",
        confidence=0.9,
        file="app/views.py",
        agents=["security"],
    )
    return Finding(**{**base, **kw})


def test_parse_diff_line_numbers():
    [fd] = parse_diff(DIFF)
    assert fd.path == "app/views.py" and not fd.is_test
    assert sorted(fd.added_lines) == [11, 12, 13]
    assert "   11 +     sql =" in fd.rendered


def test_ground_snaps_to_real_line_and_rejects_hallucinations():
    [fd] = parse_diff(DIFF)
    assert ground(_f(line=40), fd).line == 11  # wrong line, right evidence -> snapped
    assert ground(_f(evidence="os.system(cmd)"), fd) is None  # evidence not in diff -> dropped


def test_dedupe_merges_same_issue_from_two_agents():
    a = _f()
    b = _f(
        category="error_handling",
        agents=["error_handling"],
        severity="medium",
        title="Unvalidated input q used in SQL query",
        explanation="user input q flows into raw SQL",
    )
    [merged] = dedupe([a, b])
    assert merged.severity == "high" and merged.agents == ["error_handling", "security"]


def test_snapshot_diff_keeps_real_line_numbers():
    src = "import os\n\n\ndef f(x):\n    a = 1\n    return eval(x)\n\n\ndef g():\n    pass\n"
    spans = enclosing_spans(src, [(6, 6)])
    assert spans == [(4, 6)]
    [fd] = parse_diff(synthetic_added_diff("m.py", src, spans))
    assert fd.added_lines[6].strip() == "return eval(x)"


# --- Regression tests for bugs found in the first real runs with qwen2.5-coder:7b ---------------------


class _StubLLM:
    """Stands in for structured_llm(...): records the messages and returns a canned answer."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def invoke(self, messages):
        self.calls.append(messages)
        return self.answer


@pytest.mark.parametrize(("schema", "verdict_field"), [(VerifyVerdict, "is_real"), (JudgeVerdict, "same_issue")])
def test_reasoning_field_comes_before_verdict(schema, verdict_field):
    # Models fill JSON in order; with the boolean first the 7B model answered before reasoning
    # (it wrote is_real=false next to a reason saying the bug was real).
    fields = list(schema.model_json_schema()["properties"])
    assert fields.index("reason") < fields.index(verdict_field)


def test_coverage_claims_are_verified_against_the_tests(monkeypatch):
    stub = _StubLLM(VerifyVerdict(reason="no test calls it", is_real=True))
    monkeypatch.setattr(agents, "structured_llm", lambda schema, role="ACR": stub)
    [fd] = parse_diff(DIFF)
    f = _f(category="test_coverage", title="search() is untested")
    assert agents.verify(f, fd, context="## Existing related tests\ntests/test_views.py: nothing\n\n")
    system, human = stub.calls[0][0][1], stub.calls[0][1][1]
    assert system == agents.VERIFY_COVERAGE_PROMPT
    assert "## Existing related tests" in human  # the verifier sees what the specialist saw


def test_verify_node_passes_test_context_only_for_coverage(monkeypatch):
    seen = []
    monkeypatch.setattr(graph, "verify", lambda f, fd, context="": seen.append((f.category, context)) or True)
    [fd] = parse_diff(DIFF)
    state = {"files": [fd], "findings": [_f(), _f(category="test_coverage")], "stats": {}, "repo_root": None}
    out = graph.verify_node(state)
    assert len(out["findings"]) == 2
    assert seen[0] == ("security", "")
    assert seen[1][0] == "test_coverage" and "Test files changed in this pull request" in seen[1][1]


def test_test_coverage_severity_is_capped(monkeypatch):
    raw = RawFinding(category="security", line=11, severity="high", title="no test", evidence="sql =",
                     explanation="x", suggestion="y", confidence=0.9)  # fmt: skip
    monkeypatch.setattr(
        agents,
        "structured_llm",
        lambda schema, role="ACR": _StubLLM(SpecialistOutput(analysis="", findings=[raw], need_context=[])),
    )
    [fd] = parse_diff(DIFF)
    [f] = agents.run_specialist("test_coverage", fd, repo=None)
    assert f.category == "test_coverage" and f.severity == "medium"


def test_cli_rejects_post_without_pr_before_reviewing(monkeypatch, tmp_path):
    from acr import cli

    diff = tmp_path / "x.diff"
    diff.write_text(DIFF, encoding="utf-8")
    monkeypatch.setattr(cli, "review", lambda *a, **k: pytest.fail("review must not run"))
    monkeypatch.setattr("sys.argv", ["acr", "--diff-file", str(diff), "--post"])
    with pytest.raises(SystemExit):
        cli.main()


def test_verify_node_keeps_rejected_findings_for_the_backtest(monkeypatch):
    monkeypatch.setattr(graph, "verify", lambda f, fd, context="": f.category == "security")
    [fd] = parse_diff(DIFF)
    state = {"files": [fd], "findings": [_f(), _f(category="error_handling")], "stats": {}, "repo_root": None}
    out = graph.verify_node(state)
    assert [f.category for f in out["findings"]] == ["security"]
    assert [f.category for f in out["rejected"]] == ["error_handling"]
    assert out["stats"]["dropped_by_verifier"] == 1


def _git_repo(tmp_path, files: dict[str, str]) -> str:
    import subprocess

    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True)
    return str(tmp_path)


def test_callers_lists_call_sites_but_not_defs_tests_or_the_file_itself(tmp_path):
    from acr.tools import RepoContext

    root = _git_repo(
        tmp_path,
        {
            "lib/sql.py": "def quote_name(n):\n    return n\n\nx = quote_name('a')\n",
            "lib/lookups.py": "from .sql import quote_name\nsql = quote_name(request_key)\n",
            "tests/test_sql.py": "quote_name('t')\n",
        },
    )
    out = RepoContext(root).callers(["quote_name"], exclude="lib/sql.py")
    assert "lib/lookups.py:2:sql = quote_name(request_key)" in out
    assert "tests/" not in out and "lib/sql.py" not in out
    assert RepoContext(root).callers(["nothing_calls_me"]) == "[no callers found outside this file]"


def test_security_prompt_version_switch(monkeypatch):
    monkeypatch.delenv("ACR_SECURITY_PROMPT", raising=False)
    assert "Threat model" in agents._system("security")
    monkeypatch.setenv("ACR_SECURITY_PROMPT", "v1")
    assert agents.SECURITY_V1 in agents._system("security") and "Threat model" not in agents._system("security")


def test_specialist_schema_cannot_be_answered_with_an_empty_object():
    # Fields with defaults are not "required" in the JSON schema, and Ollama's constrained decoding then
    # accepts `{}`. qwen2.5-coder:7b answered `{}` on almost every Django file until these were required.
    schema = SpecialistOutput.model_json_schema()
    assert set(schema["required"]) == {"analysis", "findings", "need_context"}
    assert list(schema["properties"])[0] == "analysis"  # reason before answering
    assert schema["properties"]["analysis"]["maxLength"] == 600  # grammar-level cap, see schemas.py
    with pytest.raises(ValueError):
        SpecialistOutput.model_validate({})
