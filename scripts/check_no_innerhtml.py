"""Fail if the web UI can parse a string as HTML.

Players choose their character names, and account ids, chat and GM command
strings are all player-influenced. A character named
`<img src=x onerror=...>` is not hypothetical, and if the UI renders that
string AS HTML the attacker's code runs inside an operator's session -- one
that may hold an ADMIN token able to grant items and move zeny.

Choosing no framework makes this worse, not better: React and Svelte escape
by default, while hand-written DOM code is safe only until somebody reaches
for innerHTML to build a table row. So, as elsewhere in this project, the rule
is a gate rather than a guideline. js/dom.js builds every element from text
nodes, and nothing else may parse markup.

Scans the shipped UI and its tests. Node's node_modules/ sits at the
repository root, outside both, so it needs no exclusion -- jsdom certainly
contains innerHTML, and a gate failing on someone else's code is a gate
people learn to loosen.

The pattern matches inside comments too, deliberately. This script does not
parse JavaScript, so it cannot tell a comment from code, and a forbidden call
"kept for reference" in a comment is one accidental uncomment away from
shipping. Delete it instead of commenting it out; the gate has no way to
distinguish the two, and it should not try to.

    python scripts/check_no_innerhtml.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN = [ROOT / "src" / "ro_admin" / "web", ROOT / "tests" / "web"]
SUFFIXES = {".js", ".html", ".mjs"}
FORBIDDEN = re.compile(
    r"\b("
    r"innerHTML|outerHTML|insertAdjacentHTML"  # classic HTML-parsing setters
    r"|document\.write(?:ln)?"  # writes a string into the document's HTML stream
    r"|createContextualFragment"  # Range method that parses a string as HTML
    r"|setHTMLUnsafe|parseHTMLUnsafe"  # newer HTML-parsing APIs, unsanitized by design
    r"|DOMParser"  # parses a string as HTML/XML entirely outside the document
    r"|srcdoc"  # sets an iframe's document from a string of HTML
    r")\b"
)


def offenders(roots: list[pathlib.Path] = SCAN) -> list[tuple[pathlib.Path, int, str]]:
    found = []
    for base in roots:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                if FORBIDDEN.search(line):
                    found.append((path, number, line.strip()))
    return found


def main() -> int:
    found = offenders()
    if found:
        print(f"{len(found)} place(s) where the web UI could parse a string as HTML:\n")
        for path, number, line in found:
            print(f"  {path.relative_to(ROOT)}:{number}  {line}")
        print(
            "\nBuild DOM with js/dom.js, which only ever creates text nodes."
            "\nA player-chosen name rendered as HTML runs inside an admin's session."
            "\nA commented-out match counts too: delete the call, don't comment it out."
        )
        return 1
    print("no HTML parsing found in the web UI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
