"""LLM-as-judge: does a reviewer finding describe the same root cause as the advisory?
Audit every 'match' and a sample of 'no match' by hand and report the agreement rate."""

from pydantic import BaseModel, Field

from acr.config import structured_llm
from acr.schemas import Finding


class JudgeVerdict(BaseModel):
    reason: str = Field(description="Compare the finding's root cause with the advisory's before deciding")
    same_issue: bool = Field(description="True only if the finding identifies the same root-cause weakness")


JUDGE_PROMPT = """You grade an automated code reviewer against a REAL, known vulnerability.

same_issue=true when the finding points at the same weak spot the advisory describes (the same check, sink or code
path) AND names the same class of vulnerability (for example both say SQL injection through the column alias). It does
NOT need the exact payload, trigger or every detail of the advisory.

same_issue=false when: the vulnerability class differs, the finding is about a different code path, it is generic advice
("validate input") without a concrete flaw, or it merely repeats a code comment/docstring without explaining an actual flaw."""


def judge(case: dict, f: Finding) -> JudgeVerdict:
    truth = f"Advisory: {case['summary']}\nCWE: {', '.join(case['cwes']) or 'n/a'}\nDetails: {case['description']}"
    claim = f"{f.title} ({f.cwe or f.category}) at {f.file}:{f.line}\n{f.explanation}\nEvidence: {f.evidence}"
    llm = structured_llm(JudgeVerdict, role="ACR_JUDGE")
    try:
        return llm.invoke([("system", JUDGE_PROMPT), ("human", f"Ground truth:\n{truth}\n\nReviewer finding:\n{claim}")])
    except Exception as e:
        return JudgeVerdict(same_issue=False, reason=f"judge error: {e}")
