"""Render results/summary.json as docs/results.svg (no plotting dependency; GitHub renders the SVG in the README).

One row per configuration, two bars each: real CVEs caught (strict, human-labelled) and patched versions
still flagged for the same bug (false alarms). Both are counts out of the same test cases, so they share
one axis. Colors are the first two slots of a colorblind-validated categorical palette, with a dark-mode
variant; every bar is also direct-labelled, so color is never the only cue.

  python -m eval.plot_results
"""

import json
from html import escape
from pathlib import Path

SERIES = [("caught", "Real CVE caught (strict, human-labelled)"), ("false_alarms", "Patched code still flagged")]
ROW_H, BAR_H, GAP, LEFT, RIGHT, TOP = 58, 16, 2, 300, 70, 70
WIDTH = 820


def render(summary: dict) -> str:
    rows = summary["rows"]
    n = max(r["n"] for r in rows)  # axis length; each row prints its own denominator
    height = TOP + ROW_H * len(rows) + 50
    plot_w = WIDTH - LEFT - RIGHT
    scale = plot_w / max(
        n * 0.25, max(r[k] for r in rows for k, _ in SERIES) + 1
    )  # leave headroom, never stretch 1/40 to full width
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" '
        f'role="img" aria-labelledby="t d" font-family="system-ui, -apple-system, Segoe UI, sans-serif">',
        f'<title id="t">{escape(summary["title"])}</title>',
        f'<desc id="d">{escape(summary["subtitle"])}</desc>',
        "<style>"
        ":root{--surface:#fcfcfb;--ink:#1d1d1b;--ink2:#5f5e58;--grid:#e4e3de;--s1:#2a78d6;--s2:#eb6834}"
        "@media (prefers-color-scheme: dark){:root{--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#3a3a37;--s1:#3987e5;--s2:#d95926}}"
        ".bg{fill:var(--surface)}.t{fill:var(--ink);font-size:16px;font-weight:600}.st,.lab{fill:var(--ink2);font-size:12px}"
        ".row{fill:var(--ink);font-size:13px}.val{fill:var(--ink);font-size:12px;font-variant-numeric:tabular-nums}"
        ".grid{stroke:var(--grid);stroke-width:1}.s0{fill:var(--s1)}.s1{fill:var(--s2)}"
        "</style>",
        f'<rect class="bg" width="{WIDTH}" height="{height}"/>',
        f'<text class="t" x="16" y="26">{escape(summary["title"])}</text>',
        f'<text class="st" x="16" y="46">{escape(summary["subtitle"])}</text>',
    ]
    # legend, one row above the plot
    lx = LEFT
    for i, (_, label) in enumerate(SERIES):
        out.append(f'<rect class="s{i}" x="{lx}" y="56" width="12" height="12" rx="3"/>')
        out.append(f'<text class="lab" x="{lx + 18}" y="66">{escape(label)}</text>')
        lx += 18 + 6 * len(label) + 28
    # recessive grid at every 2 cases
    step = 2 if n <= 20 else 5
    for v in range(0, int(plot_w / scale) + 1, step):
        x = LEFT + v * scale
        out.append(f'<line class="grid" x1="{x:.1f}" y1="{TOP + 4}" x2="{x:.1f}" y2="{height - 40}"/>')
        out.append(f'<text class="lab" x="{x:.1f}" y="{height - 24}" text-anchor="middle">{v}</text>')
    out.append(f'<text class="lab" x="{LEFT}" y="{height - 8}">number of CVE cases</text>')
    for r_i, row in enumerate(rows):
        y0 = TOP + r_i * ROW_H + 10
        out.append(f'<text class="row" x="{LEFT - 12}" y="{y0 + BAR_H + 4}" text-anchor="end">{escape(row["label"])}</text>')
        for s_i, (k, label) in enumerate(SERIES):
            v = row[k]
            y = y0 + s_i * (BAR_H + GAP)
            w = max(v * scale, 2)  # a 0 still shows as a hairline at the baseline
            tip = f"{row['label']}: {label} = {v}/{row['n']}"
            out.append(
                f'<rect class="s{s_i}" x="{LEFT}" y="{y}" width="{w:.1f}" height="{BAR_H}" rx="4"><title>{escape(tip)}</title></rect>'
            )
            out.append(f'<text class="val" x="{LEFT + w + 6:.1f}" y="{y + BAR_H - 4}">{v}/{row["n"]}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main():
    summary = json.loads(Path("results/summary.json").read_text(encoding="utf-8"))
    Path("docs/results.svg").write_text(render(summary), encoding="utf-8")
    print("wrote docs/results.svg")


if __name__ == "__main__":
    main()
