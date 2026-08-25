"""Fail if the skill names an endpoint or parameter the API does not have.

The skill is instructions an agent EXECUTES. Prose that has rotted gets read by
a human who notices; instructions that have rotted get acted on -- the agent
calls the parameter that was renamed, gets a 422 or a silently unfiltered
result, and reports success on it.

Checked against the generated OpenAPI document, the same one `roadmin discover`
reads, so this cannot drift the way a hand-maintained endpoint list would.

Deliberately one-directional. It fails when the skill names something that does
NOT exist; it does not require the skill to mention every endpoint. Coverage
always lags a new surface, and a check that fails on the day an endpoint is
added is a check people learn to bypass.

    python scripts/check_skill_matches_api.py
"""
import pathlib
import re
import sys

from ro_admin.main import app

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_FILES = [ROOT / "skill" / "SKILL.md"] + sorted(
    (ROOT / "skill" / "references").glob("*.md")
)

API_PREFIX = "/api/v1/"

# `python -m ro_admin.cli get logs/zeny char_id=1 limit=20`
CLI_CALL = re.compile(
    r"ro_admin\.cli (?:get|post) ([a-z0-9_{}/-]+)((?: +[a-z_]+=[^\s`]+)*)"
)
PARAM = re.compile(r"([a-z_]+)=")


def api_surface() -> tuple[set[str], dict[str, set[str]]]:
    """Paths (with {} placeholders) and the parameters each accepts.

    app.openapi() rather than a fresh get_openapi() call: this is the exact
    document the server serves at /openapi.json and that the skill tells the
    agent to trust over itself, so the check has to read that and not a
    lookalike assembled from the same routes.
    """
    spec = app.openapi()
    paths, params = set(), {}
    for raw, operations in spec["paths"].items():
        if not raw.startswith(API_PREFIX):
            continue
        key = re.sub(r"\{[^}]+\}", "{}", raw[len(API_PREFIX):]).strip("/")
        paths.add(key)
        accepted = set()
        for op in operations.values():
            for p in op.get("parameters", []):
                accepted.add(p["name"])
            for content in op.get("requestBody", {}).get("content", {}).values():
                accepted |= _schema_properties(content.get("schema", {}), spec)
        params[key] = accepted
    return paths, params


def _schema_properties(schema: dict, spec: dict) -> set[str]:
    """Property names of a request body, following $ref and anyOf/oneOf.

    The union arms matter: POST /commands takes a discriminated oneOf, so
    `delta=` lives only on AdjustZeny and `item_id=`/`amount=` only on
    GiveItem. Reading properties off the top-level schema alone would find
    none of them and fail every Tier 1 example in references/tier1.md.
    """
    out = set()
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _schema_properties(spec["components"]["schemas"][name], spec)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key, []):
            out |= _schema_properties(sub, spec)
    out |= set(schema.get("properties", {}))
    return out


def resolve(cited: str, paths: set[str]) -> str | None:
    """The API path an example refers to, or None if the API serves no such path.

    Examples use concrete ids where the API has placeholders -- `commands/135`
    for `commands/{command_id}`, `maps/prontera/cells` for `maps/{name}/cells`
    -- so a cited segment matches either an identical literal segment or a {}.

    Structural, rather than generating a candidate for each single segment
    swapped out. Two things that fixes:

      * `items/types` is both a real literal path and a match for
        `items/{item_id}`. Picking whichever came out of a set first meant
        parameters were checked against the wrong endpoint, at random. The
        literal wins here, and otherwise the fewest placeholders wins.
      * a path with two placeholders would have matched nothing at all. There
        is none today; there was none the day before `maps/{name}/cell` landed
        either.
    """
    if cited in paths:
        return cited
    segments = cited.split("/")
    matches = [
        p for p in paths
        if len(candidate := p.split("/")) == len(segments)
        and all(c in ("{}", s) for c, s in zip(candidate, segments))
    ]
    # sorted() only so a tie reports the same way twice; a tie means two API
    # paths genuinely overlap, which is the API's problem, not this file's.
    return min(sorted(matches), key=lambda p: p.count("{}"), default=None)


def main() -> int:
    paths, params = api_surface()
    problems = []
    checked = 0

    for path_file in SKILL_FILES:
        text = path_file.read_text(encoding="utf-8")
        where = path_file.relative_to(ROOT)
        for cited, arg_text in CLI_CALL.findall(text):
            checked += 1
            # SKILL.md tells the agent the /api/v1 prefix is optional and the
            # examples omit it, but the CLI accepts either, so an example that
            # spells it out is correct and must not be reported as invented.
            cited = cited.strip("/")
            cited = cited[len("api/v1/"):] if cited.startswith("api/v1/") else cited
            match = resolve(cited, paths)
            if match is None:
                problems.append(f"{where}: names `{cited}`, which the API does not serve")
                continue
            for name in PARAM.findall(arg_text):
                if name not in params[match]:
                    problems.append(
                        f"{where}: `{cited}` is called with `{name}=`, "
                        f"which that endpoint does not accept"
                    )

    if problems:
        print(f"{len(problems)} skill/API mismatch(es):\n")
        for p in problems:
            print(f"  {p}")
        print("\nThe skill is executed, not read. Fix it or fix the API.")
        return 1

    print(f"checked {checked} CLI examples across {len(SKILL_FILES)} skill files")
    print("skill and API agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
