from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["security", "error_handling", "test_coverage"]
Severity = Literal["critical", "high", "medium", "low"]
SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}


class RawFinding(BaseModel):
    """What the LLM fills in. File path and category are set by our code, not the model."""

    category: Category = Field(description="Which kind of problem this is")
    line: int = Field(description="Line number shown at the start of a '+' line in the diff")
    severity: Severity
    title: str = Field(description="One-line summary, at most 80 characters")
    evidence: str = Field(description="The offending code, copied verbatim from ONE '+' line")
    explanation: str = Field(description="Why this is a problem, with the concrete failure or attack path")
    suggestion: str = Field(description="The concrete fix")
    confidence: float = Field(description="0.0-1.0. Use >=0.8 only with a concrete failure path")
    cwe: str | None = Field(default=None, description="CWE id such as CWE-89, security only")


class ContextRequest(BaseModel):
    tool: Literal["read_file", "grep"]
    arg: str = Field(description="read_file: repo-relative path. grep: a literal string to search for")


class SpecialistOutput(BaseModel):
    # All three fields are REQUIRED on purpose. Pydantic leaves fields with defaults out of the JSON schema's
    # "required" list, and Ollama's constrained decoding then allows a bare `{}`. qwen2.5-coder:7b took that
    # shortcut on almost every Django file, which is why the first backtest saw ~no security findings.
    # `analysis` comes first for the same reason as VerifyVerdict.reason: reason before committing to an answer.
    # maxLength goes only into the JSON schema (so Ollama's grammar stops the model rambling, which otherwise
    # ran into the num_predict cap and broke the JSON), not into validation, so a long answer never fails.
    analysis: str = Field(
        description="At most 3 short sentences: which '+' lines are risky for your focus area and why, before listing findings",
        json_schema_extra={"maxLength": 600},
    )
    findings: list[RawFinding] = Field(description="The problems found. An empty list if there are none.")
    need_context: list[ContextRequest] = Field(
        description="Only if you cannot judge without seeing other code. At most 3. Empty list otherwise.",
    )


class Finding(RawFinding):
    file: str
    agents: list[str] = Field(default_factory=list)


# Field order matters: models fill JSON fields in order, so the reasoning comes BEFORE the verdict.
# With the boolean first, qwen2.5-coder:7b often wrote is_real=false and then a reason saying it was real.
class VerifyVerdict(BaseModel):
    reason: str = Field(description="What the cited line actually does and whether the claimed problem follows from it")
    is_real: bool = Field(description="True only if the problem really exists in the shown code")


class Synthesis(BaseModel):
    summary: str = Field(description="2-4 sentence overall assessment of the change")
    verdict: Literal["approve", "comment", "request_changes"]
