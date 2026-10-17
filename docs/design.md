# Design notes

How the reviewer and its evaluation are built, the decisions behind them, and what the measurements changed.
For setup, usage and the result tables, see the [README](../README.md).

## Goals and constraints

- Review Python pull requests for **security**, **error handling** and **test coverage**.
- **$0 to run:** a local model through Ollama, no hosted LLM API, no paid CI. It must work on a laptop GPU with 8 GB of VRAM (`qwen2.5-coder:7b`).
- **Measured, not demoed:** every quality claim comes from a backtest on real CVEs with a held-out test set.

## Pipeline

```
diff ─► parse ─► fan-out (file × specialist) ─► aggregate ─► verify ─► synthesize ─► Markdown / PR comment
                     │ security          ▲
                     │ error handling    │ read_file / grep / callers / related tests (git, read-only)
                     │ test coverage     │
```

| Stage | Code | LLM? | Why it exists |
|---|---|---|---|
| Parse | `acr/diff_parser.py` | no | Prints each line with its real line number. Small models can't count lines. |
| Fan-out | `acr/graph.py` | - | LangGraph `Send`: one task per (file, specialist), run in parallel. One file and one concern per call keeps a 7B model focused. |
| Specialists | `acr/agents.py` | yes | Answer in a strict JSON schema: `analysis`, then findings with a verbatim quote, then optional context requests. |
| Context | `acr/tools.py` | no | `git show` / `git grep` at the PR's commit. Callers of changed functions (two levels up) for security; related tests for coverage. |
| Aggregate | `acr/aggregate.py` | no | Grounding (the quote must be a real added line), a confidence floor, deduplication across agents. |
| Verify | `acr/agents.py` | yes | A skeptical second prompt per finding. Coverage claims are checked against the same tests the specialist saw. |
| Synthesize | `acr/agents.py`, `acr/render.py` | yes | A short summary and a verdict. Never "approve" if any agent call failed. |

### Decisions

- **LangGraph** over CrewAI/AutoGen: the flow is a fixed pipeline with one parallel map step. Explicit typed state, a reducer for parallel results and plain-Python nodes keep it debuggable, and make every ablation an environment variable (`ACR_MODE`, `ACR_VERIFY`).
- **Tool requests as JSON, not native tool calling:** a 7B model fills one schema reliably but is unreliable at multi-turn function calling. The graph executes `need_context` requests and asks again (at most 2 rounds).
- **Deterministic code for anything checkable:** grounding, dedupe, category labels, severity caps, caller tracing and rendering are Python. The model only does judgement.
- **Required schema fields, reasoning first:** with optional fields, Ollama's constrained decoding accepted `{}` and the model took that shortcut on almost every framework file. All fields are required, the reasoning field comes before any verdict, and `analysis` carries a schema-level `maxLength` so generation can't run away.
- **Fail loudly:** a preflight check for Ollama and the model, failed agent calls counted and shown as "review incomplete", and a non-zero exit code.

## Evaluation

| Piece | Code | Purpose |
|---|---|---|
| Dataset | `eval/build_dataset.py` | GitHub Security Advisories whose fix commit is in a local clone and touches 1-3 Python files. |
| Snapshot | `eval/snapshot.py` | Shows the reviewer the vulnerable function(s) as new code: no advisory, commit message or fix. Reverting the fix instead would leak the answer. |
| Control | `eval/snapshot.py` | The same functions after the fix. Flagging those for the same bug counts as a false alarm. |
| Backtest | `eval/backtest.py` | Runs both versions, stores every finding, judge verdict and latency per case, and reports Wilson 95% intervals. `--split`, `--resume`, `--mode reverse`. |
| Judge | `eval/judge.py` | An LLM proposes "same root cause?". Used for triage only. |
| Human labels | `eval/audit.py`, `eval/labels/` | Every judged finding labelled by hand; scores are computed from the labels. |
| Dev loop | `eval/tune_security.py` | Prompt work on the 94 dev cases only; the 40 test cases are never used for tuning. |

### What the measurements changed

1. **The judge alone was not trustworthy.** It both missed real catches and accepted wrong ones (for example a model paraphrasing a docstring), so every reported number uses human labels.
2. **The `{}` shortcut** explained the first, near-silent backtest: 5 security findings in 80 reviews. Fixing the schema made the model speak up, but on Django it flags patched code about as often as vulnerable code.
3. **The verifier removes signal and noise about equally** with a 7B model (on the Django test set it discarded the one consistent real catch).
4. **Dev improvements didn't always transfer.** Two-level caller tracing helped on dev and was neutral on test (within noise). It's reported as-is.
5. **Where it works:** application code (path traversal, SQL injection, leaked error details, bypassable checks) where the attacker input and the sink are in the same function. **Where it doesn't:** framework internals, where the input arrives through several files.
6. **The pipeline is not the bottleneck:** shown the fix being removed, it catches 11/40 Django CVEs vs 1-3/40 unaided.

## Limits and next steps

- A larger local model (14B+) is the biggest lever. The 14B model doesn't fit an 8 GB GPU (measured: ~5x slower, swapping), but on 12-16 GB it's a one-line config change.
- Cross-file data flow from request input to sink for framework code.
- Precision on everyday, non-security PRs (how many comments a human finds useful).
- A self-hosted runner for the GitHub Action, on a private repository only.
