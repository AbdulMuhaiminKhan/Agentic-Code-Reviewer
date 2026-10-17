"""Build a small Flask "shop" repo with four pull-request branches, for an end-to-end demo on GitHub.

  python examples/build_demo_prs.py [target_dir]      # default: ../acr-demo-shop next to this project

main                     a small app with tests
pr/order-search          SQL injection (f-string query) + open redirect after login
pr/invoice-download      path traversal in a file download + a swallowed exception
pr/thumbnails            command injection (shell=True) + a new helper with no tests
pr/refactor-pricing      a clean refactor with tests: the control, should get no security findings

Each branch is one commit on top of main, so `git diff main...<branch>` is exactly the PR.
"""

import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent.parent / "acr-demo-shop"

MAIN = {
    "README.md": "# Demo shop\n\nA tiny Flask shop used to demo an automated code reviewer. Contains intentionally vulnerable PRs.\n",
    "shop/__init__.py": "",
    "shop/db.py": """import sqlite3

DB_PATH = "shop.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_product(conn: sqlite3.Connection, product_id: int):
    return conn.execute("SELECT id, name, price FROM products WHERE id = ?", (product_id,)).fetchone()
""",
    "shop/pricing.py": """def line_total(price: float, qty: int) -> float:
    if qty < 0:
        raise ValueError("quantity must be >= 0")
    return round(price * qty, 2)


def order_total(lines: list[tuple[float, int]], tax_rate: float = 0.2) -> float:
    subtotal = sum(line_total(p, q) for p, q in lines)
    return round(subtotal * (1 + tax_rate), 2)
""",
    "shop/app.py": """from flask import Flask, abort, jsonify, redirect, request, session

from .db import get_db, get_product

app = Flask(__name__)
app.secret_key = "change-me"  # loaded from the environment in production


@app.get("/products/<int:product_id>")
def product(product_id: int):
    row = get_product(get_db(), product_id)
    if row is None:
        abort(404)
    return jsonify(dict(row))


@app.post("/login")
def login():
    session["user"] = request.form["user"]
    return redirect("/")
""",
    "tests/__init__.py": "",
    "tests/test_pricing.py": """import pytest

from shop.pricing import line_total, order_total


def test_line_total():
    assert line_total(2.5, 4) == 10.0


def test_line_total_rejects_negative_quantity():
    with pytest.raises(ValueError):
        line_total(1.0, -1)


def test_order_total_adds_tax():
    assert order_total([(10.0, 1)], tax_rate=0.1) == 11.0
""",
    "requirements.txt": "flask>=3\npytest>=8\n",
}

PRS = {
    "pr/order-search": (
        "Add order search and redirect back after login",
        {
            "shop/db.py": MAIN["shop/db.py"]
            + '''

def search_orders(conn: sqlite3.Connection, customer: str, status: str):
    """Orders for the customer typed into the admin search box."""
    query = f"SELECT id, total, status FROM orders WHERE customer = '{customer}' AND status = '{status}'"
    return conn.execute(query).fetchall()
''',
            "shop/app.py": MAIN["shop/app.py"].replace(
                """    session["user"] = request.form["user"]
    return redirect("/")
""",
                """    session["user"] = request.form["user"]
    return redirect(request.args.get("next", "/"))


@app.get("/admin/orders")
def admin_orders():
    from .db import search_orders

    rows = search_orders(get_db(), request.args.get("customer", ""), request.args.get("status", "open"))
    return jsonify([dict(r) for r in rows])
""",
            ),
        },
    ),
    "pr/invoice-download": (
        "Let customers download invoices",
        {
            "shop/invoices.py": """import os

INVOICE_DIR = "/srv/shop/invoices"


def invoice_path(filename: str) -> str:
    return os.path.join(INVOICE_DIR, filename)


def read_invoice(filename: str) -> bytes:
    try:
        with open(invoice_path(filename), "rb") as fh:
            return fh.read()
    except Exception:
        pass
    return b""
""",
            "shop/app.py": MAIN["shop/app.py"]
            + """

@app.get("/invoices/<path:filename>")
def download_invoice(filename: str):
    from .invoices import read_invoice

    return read_invoice(filename), 200, {"Content-Type": "application/pdf"}
""",
        },
    ),
    "pr/thumbnails": (
        "Generate product thumbnails",
        {
            "shop/images.py": '''import subprocess


def make_thumbnail(upload_name: str, size: int = 128) -> str:
    """Resize an uploaded product image with ImageMagick."""
    out = f"thumbs/{upload_name}.png"
    subprocess.run(f"convert uploads/{upload_name} -resize {size}x{size} {out}", shell=True, check=True)
    return out


def thumbnail_url(name: str, size: int = 128) -> str:
    if size not in (64, 128, 256):
        raise ValueError("unsupported thumbnail size")
    return f"/static/thumbs/{name}-{size}.png"
''',
            "shop/app.py": MAIN["shop/app.py"]
            + """

@app.post("/products/<int:product_id>/image")
def upload_image(product_id: int):
    from .images import make_thumbnail

    return jsonify({"thumbnail": make_thumbnail(request.form["filename"])})
""",
        },
    ),
    "pr/refactor-pricing": (
        "Refactor pricing: discounts with validation and tests",
        {
            "shop/pricing.py": '''def line_total(price: float, qty: int) -> float:
    if qty < 0:
        raise ValueError("quantity must be >= 0")
    return round(price * qty, 2)


def apply_discount(amount: float, percent: float) -> float:
    """Reduce `amount` by `percent` (0-100)."""
    if not 0 <= percent <= 100:
        raise ValueError("discount must be between 0 and 100")
    return round(amount * (1 - percent / 100), 2)


def order_total(lines: list[tuple[float, int]], tax_rate: float = 0.2, discount: float = 0.0) -> float:
    subtotal = apply_discount(sum(line_total(p, q) for p, q in lines), discount)
    return round(subtotal * (1 + tax_rate), 2)
''',
            "tests/test_pricing.py": MAIN["tests/test_pricing.py"]
            + """

def test_apply_discount():
    from shop.pricing import apply_discount

    assert apply_discount(100.0, 25) == 75.0


@pytest.mark.parametrize("bad", [-1, 101])
def test_apply_discount_rejects_out_of_range(bad):
    from shop.pricing import apply_discount

    with pytest.raises(ValueError):
        apply_discount(100.0, bad)


def test_order_total_with_discount():
    assert order_total([(10.0, 2)], tax_rate=0.0, discount=50) == 10.0
""",
        },
    ),
}


def main() -> None:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    def write(files: dict[str, str]) -> None:
        for rel, text in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")

    if repo.exists():
        shutil.rmtree(repo, onerror=lambda f, p, _: (Path(p).chmod(0o700), f(p)))
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main")
    git("config", "core.autocrlf", "false")
    # Commit as this project's author (its repo-local identity), not whatever the global git config says.
    for k in ("user.name", "user.email"):
        v = subprocess.run(["git", "config", k], capture_output=True, text=True, cwd=Path(__file__).parent).stdout.strip()
        if v:
            git("config", k, v)
    write(MAIN)
    git("add", "-A")
    git("commit", "-q", "-m", "Initial shop app")
    for branch, (message, files) in PRS.items():
        git("checkout", "-q", "-b", branch, "main")
        write(files)
        git("add", "-A")
        git("commit", "-q", "-m", message)
    git("checkout", "-q", "main")
    print(f"demo repo with {len(PRS)} PR branches: {repo}")


if __name__ == "__main__":
    main()
