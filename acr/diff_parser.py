"""Parse a unified diff into per-file views with explicit line numbers.

Small local models are bad at counting lines. Printing the real new-file line number
next to every line is the single cheapest accuracy win in this project.
"""

from dataclasses import dataclass, field

from unidiff import PatchSet

SKIP_SUFFIXES = (".lock", ".min.js", ".map", ".svg", ".png", ".jpg", ".gif", ".ico", ".po", ".mo", ".pdf")
REVIEWABLE_SUFFIXES = (".py",)  # extend when you add languages


@dataclass
class FileDiff:
    path: str
    is_test: bool
    added_lines: dict[int, str] = field(default_factory=dict)  # new line no -> text
    rendered: str = ""  # annotated hunks shown to the model
    truncated: bool = False

    @property
    def reviewable(self) -> bool:
        return self.path.endswith(REVIEWABLE_SUFFIXES) and bool(self.added_lines)


def is_test_path(path: str) -> bool:
    p = "/" + path.lower()
    name = p.rsplit("/", 1)[-1]
    return "/tests/" in p or "/test/" in p or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def parse_diff(diff_text: str, max_lines_per_file: int = 400) -> list[FileDiff]:
    files: list[FileDiff] = []
    for pf in PatchSet(diff_text):
        if pf.is_removed_file or pf.is_binary_file or pf.path.endswith(SKIP_SUFFIXES):
            continue
        fd = FileDiff(path=pf.path, is_test=is_test_path(pf.path))
        out: list[str] = []
        for hunk in pf:
            out.append(
                f"@@ -{hunk.source_start},{hunk.source_length} +{hunk.target_start},{hunk.target_length} @@ {hunk.section_header}".rstrip()
            )
            for line in hunk:
                text = line.value.rstrip("\n")
                if line.is_added:
                    fd.added_lines[line.target_line_no] = text
                    out.append(f"{line.target_line_no:>5} + {text}")
                elif line.is_removed:
                    out.append(f"      - {text}")
                else:
                    out.append(f"{line.target_line_no:>5}   {text}")
        if len(out) > max_lines_per_file:
            out = out[:max_lines_per_file] + [f"... [diff truncated, {len(out) - max_lines_per_file} more lines]"]
            fd.truncated = True
        fd.rendered = "\n".join(out)
        files.append(fd)
    return files
