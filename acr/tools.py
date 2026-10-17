"""Read-only repository tools the agents can call. Everything goes through `git` at a fixed ref,
so the backtest can read the codebase exactly as it was at the buggy commit without a checkout."""

import re
import subprocess
import tempfile
from functools import cache
from pathlib import Path, PurePosixPath

MAX_TOOL_CHARS = 6000
MAX_CALL_SITES = 25  # more call sites than this = a generic name (as_sql, get), not worth tracing


class RepoContext:
    def __init__(self, root: str, ref: str = "HEAD"):
        self.root, self.ref = root, ref

    def _git(self, *args: str) -> str:
        try:
            res = subprocess.run(
                ["git", "-C", self.root, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
            )
        except subprocess.TimeoutExpired:
            return ""  # a tool failing must never crash the review
        return res.stdout

    @staticmethod
    def _safe_path(path: str) -> str | None:
        p = PurePosixPath(path.strip().lstrip("/"))
        return None if ".." in p.parts or not str(p) else str(p)

    def read_file(self, path: str, start: int = 1, end: int | None = None) -> str:
        safe = self._safe_path(path)
        if not safe:
            return f"[refused path: {path}]"
        text = self._git("show", f"{self.ref}:{safe}")
        if not text:
            return f"[file not found at {self.ref}: {safe}]"
        lines = text.splitlines()
        end = min(end or len(lines), start + 199)
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, min(end, len(lines)) + 1))
        return body[:MAX_TOOL_CHARS]

    def grep(self, literal: str, max_hits: int = 30) -> str:
        if not literal.strip():
            return "[empty pattern]"
        # -F literal match, -e so a pattern starting with '-' is never parsed as an option
        out = self._git("grep", "-n", "-I", "-F", "-e", literal, self.ref, "--")
        hits = [h.removeprefix(f"{self.ref}:") for h in out.splitlines()[:max_hits]]
        return ("\n".join(hits) or "[no matches]")[:MAX_TOOL_CHARS]

    def related_tests(self, src_path: str, symbols: list[str]) -> str:
        """Deterministic test discovery: test files named after the module + tests mentioning new symbols."""
        stem = PurePosixPath(src_path).stem
        all_files = self._git("ls-tree", "-r", "--name-only", self.ref).splitlines()
        by_name = [f for f in all_files if f.endswith(".py") and f"test_{stem}" in f or f.endswith(f"/{stem}_test.py")]
        mentions = []
        for sym in symbols[:8]:
            out = self._git("grep", "-l", "-F", "-e", sym, self.ref, "--")
            mentions += [h.removeprefix(f"{self.ref}:") for h in out.splitlines() if "test" in h.lower()]
        candidates = list(dict.fromkeys(by_name + mentions))[:5]
        if not candidates:
            return "[no existing test files reference this module or its new symbols]"
        chunks = []
        for f in candidates:
            hits = [h for sym in symbols[:8] for h in self._git("grep", "-n", "-F", "-e", sym, self.ref, "--", f).splitlines()]
            chunks.append(
                f"# {f}\n"
                + (
                    "\n".join(h.removeprefix(f"{self.ref}:") for h in hits[:15])
                    or "(file exists, no direct mention of the new symbols)"
                )
            )
        return "\n\n".join(chunks)[:MAX_TOOL_CHARS]

    def _call_sites(self, sym: str, exclude: str = "") -> list[tuple[str, str, str, str]]:
        """(path, line, enclosing function, code) for each non-test call of `sym`, via `git grep -p`,
        which also prints the header line (def/class) that encloses every match."""
        # Without `diff=python`, git's default heuristic reports the enclosing *class* instead of the method.
        attrs = f"core.attributesFile={_python_gitattributes()}"
        out = self._git("-c", attrs, "grep", "-n", "-p", "-I", "-F", "-e", f"{sym}(", self.ref, "--", "*.py")
        sites, func = [], "<module>"
        for raw in out.splitlines():
            m = _GREP_LINE.match(raw.removeprefix(f"{self.ref}:"))
            if not m:
                continue
            path, sep, line, text = m.groups()
            if sep == "=":  # header line: remember the enclosing def/class
                d = DEF_RE.match(text)
                func = (d.group(1) or d.group(2)) if d else "<module>"
                continue
            if path == exclude or "test" in path.lower() or re.search(r"\bdef\s+" + re.escape(sym) + r"\(", text):
                continue
            sites.append((path, line, func, text.strip()))
        return sites

    def callers(self, symbols: list[str], exclude: str = "", per_symbol: int = 5, depth: int = 2) -> str:
        """Where the changed functions are called from (non-test code), and who calls THOSE functions,
        so the security agent can see whether an argument can carry attacker input (for example a value
        that starts in request.GET two calls up). Deterministic: no model involved in choosing what to read."""
        chunks = []
        for sym in [s for s in symbols if not s.startswith("__")][:4]:
            all_sites = self._call_sites(sym, exclude)
            if not all_sites:
                continue
            if len(all_sites) > MAX_CALL_SITES:
                # A name like as_sql() or get() matches hundreds of unrelated calls: listing them is noise.
                chunks.append(f"# {sym}() has {len(all_sites)} call sites (name too common to trace)")
                continue
            lines = [f"# {sym}() is called from:"]
            for path, line, func, text in all_sites[:per_symbol]:
                lines.append(f"{path}:{line} in {func}(): {text[:160]}")
                if depth > 1 and func not in ("<module>", sym) and not func.startswith("__"):
                    up = self._call_sites(func)
                    if len(up) <= MAX_CALL_SITES:
                        lines += [f"    <- {func}() is called at {p2}:{l2} in {f2}(): {t2[:140]}" for p2, l2, f2, t2 in up[:2]]
            chunks.append("\n".join(lines))
        return ("\n\n".join(chunks) or "[no callers found outside this file]")[:MAX_TOOL_CHARS]


DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)|^\s*class\s+([A-Za-z_]\w*)")


@cache
def _python_gitattributes() -> str:
    """A gitattributes file that turns on git's built-in Python function-header detection."""
    path = Path(tempfile.gettempdir()) / "acr-gitattributes"
    path.write_text("*.py diff=python\n", encoding="utf-8")
    return path.as_posix()


# `git grep -n -p` lines after the ref prefix: path:line:code for matches, path=line=header for enclosing defs
_GREP_LINE = re.compile(r"^([^:=]+)([:=])(\d+)\2(.*)$")


def new_symbols(added_lines: dict[int, str]) -> list[str]:
    syms = []
    for text in added_lines.values():
        if m := DEF_RE.match(text):
            name = m.group(1) or m.group(2)
            if not name.startswith("__"):
                syms.append(name)
    return list(dict.fromkeys(syms))
