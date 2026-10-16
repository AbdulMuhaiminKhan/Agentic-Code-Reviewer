"""Read-only repository tools the agents can call. Everything goes through `git` at a fixed ref,
so the backtest can read the codebase exactly as it was at the buggy commit without a checkout."""

import re
import subprocess
from pathlib import PurePosixPath

MAX_TOOL_CHARS = 6000


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

    def callers(self, symbols: list[str], exclude: str = "", per_symbol: int = 6) -> str:
        """Where the changed functions are called from (non-test code), so the security agent can judge
        whether an argument can carry attacker input. Deterministic: no model involved in choosing what to read."""
        chunks = []
        for sym in symbols[:4]:
            out = self._git("grep", "-n", "-I", "-F", "-e", f"{sym}(", self.ref, "--", "*.py")
            hits = [h.removeprefix(f"{self.ref}:") for h in out.splitlines()]
            hits = [h for h in hits if not h.startswith(f"{exclude}:") and "test" not in h.split(":", 1)[0].lower()]
            hits = [h for h in hits if not re.search(r"\bdef\s+" + re.escape(sym) + r"\(", h)][:per_symbol]
            if hits:
                chunks.append(f"# {sym}()\n" + "\n".join(h[:200] for h in hits))
        return ("\n\n".join(chunks) or "[no callers found outside this file]")[:MAX_TOOL_CHARS]


DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)|^\s*class\s+([A-Za-z_]\w*)")


def new_symbols(added_lines: dict[int, str]) -> list[str]:
    syms = []
    for text in added_lines.values():
        if m := DEF_RE.match(text):
            name = m.group(1) or m.group(2)
            if not name.startswith("__"):
                syms.append(name)
    return list(dict.fromkeys(syms))
