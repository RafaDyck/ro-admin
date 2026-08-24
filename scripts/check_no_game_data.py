"""Fail if the codebase starts carrying its own copy of game data.

The predecessor's web panel resolved item names from a hardcoded 285-entry map
inside a React component, while item_db held 28,525 rows. Item 909 rendered as
"Unknown Item" despite the database knowing exactly what it was, and every
transcendent job showed as a raw id.

Nobody decided to duplicate the game's data. It arrived one literal at a time,
each defensible on its own, and there was never a moment where adding one more
felt like the wrong call. That is why this is a check and not a guideline.

Detects id-to-name literal maps -- runs of `<number>: {` or `<number>: "..."`.
A handful is fine (real lookup tables exist); a pile is the failure mode.

    python scripts/check_no_game_data.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# importers/ is scanned too: it is the one place that reads the game server's
# files, so it is exactly where a hardcoded fallback map list would be added
# "just until the import runs" and then never removed.
SCAN = [ROOT / "src", ROOT / "web", ROOT / "importers"]
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}
ENTRY = re.compile(r"^\s*\"?\d+\"?\s*:\s*[\{\"']")
THRESHOLD = 20  # consecutive-ish numeric-key entries in one file


def main() -> int:
    offenders = []
    for base in SCAN:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            hits = sum(
                1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if ENTRY.match(line)
            )
            if hits >= THRESHOLD:
                offenders.append((path.relative_to(ROOT), hits))

    if offenders:
        print(f"{len(offenders)} file(s) look like bundled game data:\n")
        for path, hits in offenders:
            print(f"  {path}  ({hits} numeric-key entries)")
        print(
            "\nGame data belongs on the server. If a client needs a name, label,"
            "\ncategory or enum, that is a MISSING ENDPOINT -- add it to the API"
            "\nand fetch it. See the 'no privileged UI knowledge' rule in the spec."
        )
        return 1

    print("no bundled game data found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
