"""Build a tiny local git repo with planted bugs, for a first end-to-end run of the reviewer.

  python examples/build_demo.py            # creates data/demo-repo and examples/demo.diff
  python -m acr.cli --diff-file examples/demo.diff --local-repo data/demo-repo -v

Commit 1 (base): a small shop app with a parameterized user lookup and tests for `subtotal`.
Commit 2 (the "pull request") plants three problems and one clean change:
  - app/db.py          find_user_by_email builds SQL with an f-string        -> SQL injection (security)
  - app/settings.py    load_settings swallows every exception and returns {} -> error handling
  - app/pricing.py     apply_coupon is new and has no test                   -> test coverage
  - app/pricing.py     subtotal gets a harmless docstring                    -> should NOT be flagged
"""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "data" / "demo-repo"
DIFF_OUT = ROOT / "examples" / "demo.diff"

BASE = {
    "app/__init__.py": "",
    "app/db.py": """import sqlite3


def connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(path)


def find_user(conn: sqlite3.Connection, user_id: int):
    cur = conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,))
    return cur.fetchone()
""",
    "app/settings.py": """import json

DEFAULTS = {"currency": "USD", "tax_rate": 0.2}
""",
    "app/pricing.py": """def subtotal(items: list[dict]) -> float:
    return sum(i["price"] * i["qty"] for i in items)
""",
    "tests/__init__.py": "",
    "tests/test_pricing.py": """from app.pricing import subtotal


def test_subtotal():
    assert subtotal([{"price": 2.0, "qty": 3}]) == 6.0
""",
}

CHANGE = {
    "app/db.py": '''import sqlite3


def connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(path)


def find_user(conn: sqlite3.Connection, user_id: int):
    cur = conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,))
    return cur.fetchone()


def find_user_by_email(conn: sqlite3.Connection, email: str):
    """Look up a user by the email address typed into the login form."""
    query = f"SELECT id, email FROM users WHERE email = '{email}'"
    return conn.execute(query).fetchone()
''',
    "app/settings.py": '''import json

DEFAULTS = {"currency": "USD", "tax_rate": 0.2}


def load_settings(path: str) -> dict:
    """Load shop settings from a JSON file, falling back to defaults."""
    try:
        with open(path) as fh:
            data = json.load(fh)
        return {**DEFAULTS, **data}
    except Exception:
        pass
    return {}
''',
    "app/pricing.py": '''def subtotal(items: list[dict]) -> float:
    """Sum of price * quantity over all line items."""
    return sum(i["price"] * i["qty"] for i in items)


def apply_coupon(total: float, code: str, coupons: dict[str, float]) -> float:
    """Apply a percentage coupon. Unknown codes leave the total unchanged."""
    pct = coupons.get(code.strip().upper())
    if pct is None:
        return total
    if not 0 < pct <= 100:
        raise ValueError(f"invalid coupon percentage: {pct}")
    return round(total * (1 - pct / 100), 2)
''',
}


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def write(files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = REPO / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    if REPO.exists():
        shutil.rmtree(REPO, onerror=lambda f, p, _: (Path(p).chmod(0o700), f(p)))
    REPO.mkdir(parents=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "demo@example.invalid")
    git("config", "user.name", "demo")
    git("config", "core.autocrlf", "false")
    write(BASE)
    git("add", "-A")
    git("commit", "-q", "-m", "base: shop app")
    write(CHANGE)
    git("add", "-A")
    git("commit", "-q", "-m", "feature: email lookup, settings loader, coupons")
    DIFF_OUT.write_text(git("diff", "HEAD~1", "HEAD"), encoding="utf-8", newline="\n")
    print(f"demo repo: {REPO}\ndiff:      {DIFF_OUT}")


if __name__ == "__main__":
    main()
