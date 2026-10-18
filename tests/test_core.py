import json
from pathlib import Path

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
    grounded = ground(_f(line=40), fd)
    assert grounded is not None and grounded.line == 11  # wrong line, right evidence -> snapped
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
    state: graph.ReviewState = {"files": [fd], "findings": [_f(), _f(category="test_coverage")], "stats": {}, "repo_root": None}
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
    state: graph.ReviewState = {"files": [fd], "findings": [_f(), _f(category="error_handling")], "stats": {}, "repo_root": None}
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
            "lib/lookups.py": "from .sql import quote_name\n\n\nclass Lookup:\n    def as_sql(self, key):\n        return quote_name(key)\n",
            "lib/views.py": "def search(request):\n    return Lookup().as_sql(request.GET['k'])\n",
            "tests/test_sql.py": "quote_name('t')\n",
        },
    )
    out = RepoContext(root).callers(["quote_name"], exclude="lib/sql.py")
    assert "lib/lookups.py:6 in as_sql(): return quote_name(key)" in out  # the method, not the class
    assert "<- as_sql() is called at lib/views.py:2 in search(): return Lookup().as_sql(request.GET['k'])" in out
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


# --- CLI / integration paths (model and GitHub are stubbed; nothing here talks to a real service) ----------


class _FakeResponse:
    def __init__(self, payload, links=None, status=200):
        self._payload, self.links, self.status_code = payload, links or {}, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_upsert_pr_comment_edits_our_comment_on_a_later_page(monkeypatch):
    from acr import github_client as gh

    calls = []
    page1 = _FakeResponse([{"body": "lgtm", "url": "u1"}], links={"next": {"url": "page2"}})
    page2 = _FakeResponse([{"body": f"{gh.COMMENT_MARKER}\nold review", "url": "u2"}])
    monkeypatch.setattr(gh.requests, "get", lambda url, **k: calls.append(("get", url)) or (page2 if url == "page2" else page1))
    monkeypatch.setattr(gh.requests, "patch", lambda url, **k: calls.append(("patch", url)) or _FakeResponse({"html_url": "h"}))
    monkeypatch.setattr(gh.requests, "post", lambda *a, **k: pytest.fail("must edit, not create a second comment"))
    assert gh.upsert_pr_comment("me/repo", 3, "new review") == "h"
    assert calls[-1] == ("patch", "u2")


def test_upsert_pr_comment_creates_when_none_exists(monkeypatch):
    from acr import github_client as gh

    monkeypatch.setattr(gh.requests, "get", lambda *a, **k: _FakeResponse([]))
    posted = {}
    monkeypatch.setattr(gh.requests, "post", lambda url, json, **k: posted.update(json) or _FakeResponse({"html_url": "h"}))
    assert gh.upsert_pr_comment("me/repo", 3, "review") == "h"
    assert posted["body"].startswith(gh.COMMENT_MARKER)


def test_check_model_reports_unreachable_and_missing_model(monkeypatch):
    from acr import config

    def down(*a, **k):
        raise config.requests.ConnectionError("refused")

    monkeypatch.setenv("ACR_MODEL", "qwen2.5-coder:7b")
    monkeypatch.setattr(config.requests, "get", down)
    assert "not reachable" in (config.check_model() or "")
    monkeypatch.setattr(config.requests, "get", lambda *a, **k: _FakeResponse({"models": [{"name": "llama3.1:8b"}]}))
    assert "ollama pull qwen2.5-coder:7b" in (config.check_model() or "")
    monkeypatch.setattr(config.requests, "get", lambda *a, **k: _FakeResponse({"models": [{"name": "qwen2.5-coder:7b"}]}))
    assert config.check_model() is None


def test_local_diff_reviews_a_branch_against_its_base(tmp_path):
    import subprocess

    from acr.cli import local_diff

    root = _git_repo(tmp_path, {"app.py": "x = 1\n"})
    git = ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "checkout", "-q", "-b", "feature"], check=True)
    (tmp_path / "app.py").write_text("x = 1\ny = eval(input())\n", encoding="utf-8")
    subprocess.run([*git, "commit", "-qam", "feature"], check=True)
    base = subprocess.run(["git", "-C", root, "rev-parse", "HEAD~1"], capture_output=True, text=True).stdout.strip()
    [fd] = parse_diff(local_diff(root, base, "feature"))
    assert fd.added_lines == {2: "y = eval(input())"}


def _stub_llm_factory(answers):
    """structured_llm stand-in that answers by schema; an Exception instance is raised instead."""

    def factory(schema, role="ACR"):
        answer = answers[schema.__name__]

        class _LLM:
            def invoke(self, messages):
                if isinstance(answer, Exception):
                    raise answer
                return answer

        return _LLM()

    return factory


def test_full_graph_end_to_end_with_stubbed_model(monkeypatch):
    from acr.schemas import Synthesis

    raw = RawFinding(category="security", line=11, severity="high", title="SQL injection via raw query",
                     evidence="sql = \"SELECT * FROM item WHERE name = '%s'\" % q", explanation="q is user input",
                     suggestion="use params", confidence=0.9)  # fmt: skip
    monkeypatch.setattr(agents, "structured_llm", _stub_llm_factory({
        "SpecialistOutput": SpecialistOutput(analysis="line 11 builds SQL", findings=[raw], need_context=[]),
        "VerifyVerdict": VerifyVerdict(reason="real", is_real=True),
        "Synthesis": Synthesis(summary="SQL injection in search.", verdict="request_changes"),
    }))  # fmt: skip
    out = graph.review(DIFF)
    assert out["verdict"] == "request_changes" and out["stats"]["failed_calls"] == 0
    assert len(out["findings"]) == 1 and out["findings"][0].line == 11  # 3 agents reported it; dedupe kept one
    assert "app/views.py:11" in out["markdown"]


def test_review_is_marked_incomplete_when_agent_calls_fail(monkeypatch):
    monkeypatch.setattr(agents, "structured_llm", _stub_llm_factory({"SpecialistOutput": ConnectionError("ollama down")}))
    out = graph.review(DIFF)
    assert out["stats"]["failed_calls"] == 3
    assert out["verdict"] == "comment"  # never a clean "approve" when the agents crashed
    assert "Review incomplete" in out["markdown"]


def test_audit_scores_runs_with_human_labels_not_the_judge(tmp_path):
    import csv

    from eval import audit

    case = {"id": "GHSA-x", "cve": "CVE-1", "vuln_ranges": {"a.py": [[10, 12]]}, "fixed_ranges": {"a.py": [[10, 13]]}}
    (tmp_path / "cases.jsonl").write_text(json.dumps(case) + "\n", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()

    def judged(line, title, same):
        return {"file": "a.py", "line": line, "title": title, "category": "security", "explanation": "x",
                "same_issue": same, "reason": "r", "verifier": "kept"}  # fmt: skip

    row = {"id": "GHSA-x", "cve": "CVE-1",
           "vulnerable": {"findings": [], "judgements": [judged(11, "SQLi via key", False)]},  # judge missed it
           "fixed": {"findings": [], "judgements": [judged(11, "SQLi via key", True)]}}  # judge false alarm  # fmt: skip
    (run / "GHSA-x.json").write_text(json.dumps(row), encoding="utf-8")
    labels = tmp_path / "labels.csv"
    audit.export(str(run), "", str(labels), cases_path=str(tmp_path / "cases.jsonl"))
    rows = list(csv.DictReader(open(labels, encoding="utf-8")))
    rows[0]["human"], rows[1]["human"] = "yes", "no"  # human: real catch, patched code is fine
    with open(labels, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=audit.FIELDS)
        w.writeheader()
        w.writerows(rows)
    out = audit.score(str(run), str(labels), cases_path=str(tmp_path / "cases.jsonl"))
    assert (out["strict"], out["false_alarms"], out["discriminating"], out["agree"]) == (1, 0, 1, 0)


# --- Streamlit UI (streamlit.testing runs the real app script; the model is stubbed) -----------------------


def test_streamlit_app_reviews_a_pasted_diff(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from acr import config
    from acr import graph as graph_mod
    from acr.schemas import Finding

    finding = Finding(category="security", line=11, severity="high", title="SQL injection via raw query",
                      evidence="sql = ...", explanation="q is user input", suggestion="use params", confidence=0.9,
                      file="app/views.py", agents=["security"])  # fmt: skip
    state = {"findings": [finding], "rejected": [], "verdict": "request_changes", "summary": "SQL injection in search.",
             "markdown": "## review", "stats": {"raw": 3, "dropped_ungrounded": 0, "merged_duplicates": 2,
                                                "dropped_by_verifier": 0, "failed_calls": 0}}  # fmt: skip

    def fake_events(diff_text, repo_root=None, repo_ref="HEAD"):
        yield "node", "parse"
        for _ in range(3):
            yield "node", "specialist"
        yield "done", state

    monkeypatch.setattr(config, "check_model", lambda: None)
    monkeypatch.setattr(graph_mod, "review_events", fake_events)
    at = AppTest.from_file("../ui/app.py", default_timeout=30).run()
    assert not at.exception
    at.radio(key="source").set_value("Paste a diff").run()
    at.text_area[0].input(DIFF).run()
    next(b for b in at.button if b.label == "Run review").click().run()
    assert not at.exception
    assert any("Changes requested" in s.value for s in at.subheader)
    assert any("SQL injection" in e.label for e in at.expander)


def test_dedupe_merges_other_agents_security_remark_on_the_same_line():
    sec = _f(line=7, title="Command injection", explanation="shell=True with a user-supplied file name", evidence="x")
    err = _f(category="error_handling", agents=["error_handling"], line=7, severity="high", evidence="x",
             title="Potential security risk due to use of shell=True", explanation="subprocess with shell")  # fmt: skip
    other = _f(category="error_handling", agents=["error_handling"], line=7, severity="medium", evidence="x",
               title="Unchecked return code", explanation="check=True raises CalledProcessError")  # fmt: skip
    out = dedupe([sec, err, other])
    assert [f.title for f in out] == ["Command injection", "Unchecked return code"]
    assert out[0].agents == ["error_handling", "security"]


def test_cwe_only_on_security_findings_and_kept_through_dedupe(monkeypatch):
    raw = RawFinding(category="test_coverage", line=11, severity="medium", title="no test", evidence="sql =",
                     explanation="x", suggestion="y", confidence=0.9, cwe="CWE-78")  # fmt: skip
    monkeypatch.setattr(
        agents,
        "structured_llm",
        lambda schema, role="ACR": _StubLLM(SpecialistOutput(analysis="", findings=[raw], need_context=[])),
    )
    [fd] = parse_diff(DIFF)
    [f] = agents.run_specialist("test_coverage", fd, repo=None)
    assert f.cwe is None
    sec = _f(cwe=None)
    twin = _f(category="error_handling", agents=["error_handling"], severity="medium", cwe="CWE-89",
              title="SQL injection risk", explanation="user input in SQL")  # fmt: skip
    [merged] = dedupe([sec, twin])
    assert merged.cwe == "CWE-89"


# --- static scanner -------------------------------------------------------------------------------


SCAN_DIFF = """diff --git a/shop/app.py b/shop/app.py
--- a/shop/app.py
+++ b/shop/app.py
@@ -0,0 +1,14 @@
+import os
+import subprocess
+from flask import redirect, request
+
+
+def login(conn, customer):
+    conn.execute(f"SELECT * FROM orders WHERE customer = '{customer}'")
+    subprocess.run(f"convert {customer}", shell=True)
+    return redirect(request.args.get("next", "/"))
+
+
+def invoice(filename):
+    with open(os.path.join("/srv/invoices", filename), "rb") as fh:
+        return fh.read()
"""


def test_scanner_leads_cover_bandit_and_our_rules_on_added_lines():
    from acr.scanner import leads_for

    [fd] = parse_diff(SCAN_DIFF)
    leads = {(lead.line, lead.cwe) for lead in leads_for(fd, repo=None)}
    assert (7, "CWE-89") in leads  # bandit B608: SQL from an f-string
    assert (8, "CWE-78") in leads  # bandit B602: shell=True
    assert (9, "CWE-601") in leads  # our rule: redirect(request.args...)
    assert (13, "CWE-22") in leads  # our rule: path built from a parameter, never normalised
    assert not any(line in (1, 2) for line, _ in leads)  # "import subprocess" is not a finding


def test_path_rule_stays_quiet_when_the_path_is_sanitised():
    from acr.scanner import ast_rules

    src = "import os\nfrom werkzeug.utils import secure_filename\n\ndef f(name):\n    return open(os.path.join('/d', secure_filename(name)))\n"
    assert not [lead for lead in ast_rules(src) if lead.cwe == "CWE-22"]


def test_leads_reach_the_security_prompt_and_scanner_mode_skips_the_model(monkeypatch):
    seen = []

    class _Recorder:
        def invoke(self, messages):
            seen.append(messages[1][1])
            return SpecialistOutput(analysis="", findings=[], need_context=[])

    monkeypatch.setenv("ACR_SCANNER", "1")
    monkeypatch.setattr(agents, "structured_llm", lambda schema, role="ACR": _Recorder())
    graph.review(SCAN_DIFF)
    assert any("Leads from a static scanner" in human and "CWE-601" in human for human in seen)

    monkeypatch.setenv("ACR_MODE", "scanner")
    monkeypatch.setenv("ACR_VERIFY", "0")
    monkeypatch.setattr(agents, "structured_llm", lambda *a, **k: pytest.fail("scanner mode must not call the model"))
    out = graph.review(SCAN_DIFF)
    assert {f.cwe for f in out["findings"]} >= {"CWE-89", "CWE-78", "CWE-601", "CWE-22"}
    assert all(f.agents == ["scanner"] for f in out["findings"])


def test_dedupe_keeps_different_vulnerabilities_on_adjacent_lines():
    sqli = _f(line=7, cwe="CWE-89", title="SQL injection")
    cmdi = _f(line=8, cwe="CWE-78", title="Command injection", evidence="x")
    assert len(dedupe([sqli, cmdi])) == 2


# --- reviewing your own staged changes -----------------------------------------------------------------


def test_staged_mode_reads_the_index_not_the_last_commit(tmp_path):
    import subprocess

    from acr.cli import staged_diff
    from acr.tools import STAGED, RepoContext

    root = _git_repo(tmp_path, {"app.py": "def f(x):\n    return x\n"})
    (tmp_path / "app.py").write_text("def f(x):\n    return eval(x)\n", encoding="utf-8")
    subprocess.run(["git", "-C", root, "add", "app.py"], check=True)
    [fd] = parse_diff(staged_diff(root))
    assert fd.added_lines == {2: "    return eval(x)"}
    repo = RepoContext(root, STAGED)
    assert "eval(x)" in repo.file_text("app.py")  # the staged version, not HEAD
    assert "app.py:2:" in repo.grep("eval(")


def test_hook_install_and_uninstall_only_touch_our_own_hook(tmp_path):
    from acr import hook

    root = Path(_git_repo(tmp_path, {"a.py": "x = 1\n"}))
    path = hook.install(root, fail_on="high")
    text = path.read_text(encoding="utf-8")
    assert hook.MARKER in text and "--staged --fail-on high" in text
    assert hook.uninstall(root) and not path.exists()
    path.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        hook.install(root)  # never overwrite someone else's hook without --force
    assert not hook.uninstall(root) and path.exists()


def test_paths_mode_reviews_whole_files_and_skips_virtualenvs(tmp_path):
    from acr.cli import files_as_diff

    (tmp_path / "app.py").write_text("import os\n\nos.system(input())\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "dep.py").write_text("x = 1\n", encoding="utf-8")
    fds = parse_diff(files_as_diff([str(tmp_path)], root=str(tmp_path)))
    assert [fd.path for fd in fds] == ["app.py"]
    assert fds[0].added_lines[3] == "os.system(input())"


def test_grounding_ignores_requoted_strings():
    diff = (
        'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+    return redirect(request.args.get("next", "/"))\n'
    )
    [fd] = parse_diff(diff)
    f = _f(file="a.py", line=1, evidence="return redirect(request.args.get('next', '/'))")
    assert ground(f, fd) is not None


def test_dedupe_prefers_the_security_agents_version():
    err = _f(category="error_handling", agents=["error_handling"], title="SQL injection risk", explanation="user input in SQL")
    sec = _f(title="SQL injection via f-string", explanation="user input in SQL", cwe="CWE-89")
    [merged] = dedupe([err, sec])
    assert merged.category == "security" and merged.cwe == "CWE-89"
