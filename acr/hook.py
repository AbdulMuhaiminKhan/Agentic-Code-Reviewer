"""Install the reviewer as a git pre-commit hook in any of your repositories.

  python -m acr.hook install path/to/your/repo [--fail-on high]
  python -m acr.hook uninstall path/to/your/repo

Before each `git commit`, the staged changes are reviewed with your local model. The commit is blocked only
when a finding reaches --fail-on (default: high). If the reviewer can't run (Ollama not started, model
missing), the commit goes through with a warning, so the hook never locks you out of your own repo.
Skip it once with `git commit --no-verify`.
"""

import argparse
import sys
from pathlib import Path

MARKER = "# installed by agentic-code-reviewer"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def hook_script(fail_on: str) -> str:
    python = Path(sys.executable).as_posix()
    return f"""#!/bin/sh
{MARKER}
echo "Agentic Code Reviewer: reviewing staged changes (git commit --no-verify skips this)..."
PYTHONPATH="{PROJECT_ROOT.as_posix()}" "{python}" -m acr.cli --local-repo . --staged --fail-on {fail_on}
status=$?
if [ $status -eq 3 ]; then
  echo "Commit blocked: a finding is {fail_on} severity or worse. Fix it, or commit with --no-verify."
  exit 1
fi
if [ $status -ne 0 ]; then
  echo "Reviewer could not run (exit $status); committing anyway."
fi
exit 0
"""


def _hook_path(repo: Path) -> Path:
    git_dir = repo / ".git"
    if not git_dir.is_dir():
        raise SystemExit(f"{repo} is not a git repository (no .git folder)")
    return git_dir / "hooks" / "pre-commit"


def install(repo: Path, fail_on: str = "high", force: bool = False) -> Path:
    path = _hook_path(repo)
    if path.exists() and MARKER not in path.read_text(encoding="utf-8", errors="replace") and not force:
        raise SystemExit(f"{path} already exists and wasn't written by this tool. Use --force to replace it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(hook_script(fail_on), encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def uninstall(repo: Path) -> bool:
    path = _hook_path(repo)
    if path.exists() and MARKER in path.read_text(encoding="utf-8", errors="replace"):
        path.unlink()
        return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["install", "uninstall"])
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--fail-on", default="high", choices=["low", "medium", "high", "critical"])
    ap.add_argument("--force", action="store_true", help="replace an existing pre-commit hook")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    if a.action == "install":
        print(f"installed: {install(repo, a.fail_on, a.force)}")
    else:
        print("removed" if uninstall(repo) else "no hook from this tool was installed")


if __name__ == "__main__":
    main()
