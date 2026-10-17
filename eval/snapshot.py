"""Turn a historical bug into a diff the reviewer can review, WITHOUT leaking the fix.

mode="snapshot" (default, realistic): present the whole enclosing function(s) of the vulnerable
  lines as if they were newly added code. The reviewer sees the buggy code, not that something
  was removed. Run it on the parent commit (vulnerable) and on the fix commit (fixed control).
mode="reverse" (easier, leaky): the reverse of the fix diff, i.e. "a PR that removes the fix".
  The '-' lines show the protective code being deleted, which is a strong hint. Report it
  separately, never as your headline number.
"""

import ast
import subprocess


def git(clone: str, *args: str) -> str:
    return subprocess.run(["git", "-C", clone, *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def enclosing_spans(source: str, ranges: list[tuple[int, int]], max_len: int = 250, pad: int = 15) -> list[tuple[int, int]]:
    try:
        funcs = [n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    except SyntaxError:
        funcs = []
    n_lines = source.count("\n") + 1
    spans = []
    for a, b in ranges:
        best = None
        for fn in funcs:
            start = min([fn.lineno] + [d.lineno for d in fn.decorator_list])
            end = fn.end_lineno or fn.lineno
            if start <= a and end >= b and (best is None or end - start < best[1] - best[0]):
                best = (start, end)
        s, e = best or (a - pad, b + pad)
        if e - s + 1 > max_len:  # huge function: window around the change
            s, e = a - max_len // 2, b + max_len // 2
        spans.append((max(1, s), min(n_lines, e)))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def synthetic_added_diff(path: str, source: str, spans: list[tuple[int, int]]) -> str:
    lines = source.splitlines()
    out = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"]
    removed_so_far = 0
    for s, e in spans:
        body = lines[s - 1 : e]
        # source side pretends these lines did not exist; target side keeps REAL line numbers
        out.append(f"@@ -{s - 1 - removed_so_far},0 +{s},{len(body)} @@")
        out += ["+" + ln for ln in body]
        removed_so_far += len(body)
    return "\n".join(out) + "\n"


def build_case_diff(clone: str, case: dict, version: str, mode: str = "snapshot") -> tuple[str, str, dict]:
    """Returns (diff_text, git_ref_for_tools, ranges_to_score_against)."""
    fix = case["fix_sha"]
    ref = f"{fix}^" if version == "vulnerable" else fix
    ranges = case["vuln_ranges"] if version == "vulnerable" else case["fixed_ranges"]
    if mode == "reverse":
        if version != "vulnerable":
            raise ValueError("reverse mode has no 'fixed' control")
        return git(clone, "diff", fix, f"{fix}^", "--", *case["files"]), ref, ranges
    parts = []
    for path in case["files"]:
        src = git(clone, "show", f"{ref}:{path}")
        if src and path in ranges:
            parts.append(synthetic_added_diff(path, src, enclosing_spans(src, [tuple(r) for r in ranges[path]])))
    return "".join(parts), ref, ranges
