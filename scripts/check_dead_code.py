"""Fail if code is unreachable from any entry point.

The predecessor's `enhanced_gm_executor.py` was 55% unreachable -- 335 of 605
lines across 13 methods, a fossil record of abandoned strategies
(`_method_docker_stdin`, `_method_signal_server`, `_send_refresh_packet`). It
survived because deleting code feels riskier than leaving it, and because
nobody could tell at a glance that it was dead.

**Reachability, not reference counting.** An earlier version of this script
asked "is this name mentioned anywhere?" and, run against that same backend,
found only 4 of the 13 dead methods. The dead half referenced *itself* --
`_execute_server_command` was called by `_queue_for_npc_execution`, which was
also dead -- so the cluster kept itself alive on paper. Only walking outward
from real entry points finds it.

Entry points, treated as always-alive because machinery calls them by
something other than name -- getting these wrong is how a checker becomes
noise, and a noisy checker gets switched off:

  * route handlers      registered via @router.get / @app.post
  * pytest fixtures     injected by parameter name
  * tests and scripts   invoked by the runner
  * dunders             called by the language
  * module-level code   executed on import

    python scripts/check_dead_code.py
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
REFERENCE_DIRS = [ROOT / "src", ROOT / "tests", ROOT / "scripts"]

ALWAYS_ALIVE = {"app", "main", "router"}
# `model_validator` belongs beside `field_validator` for exactly the same
# reason: pydantic invokes both, and neither is ever called by name. Its absence
# made commands._destructive_needs_confirmation -- the negative-zeny gate, with
# five tests on it in test_confirmation.py -- report as unreachable.
DECORATOR_HINTS = (
    "router.", "app.", "pytest.fixture", "fixture",
    "field_validator", "model_validator", "property",
)

DEFINITION = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def decorator_names(node: ast.AST) -> list[str]:
    return [ast.unparse(d) for d in getattr(node, "decorator_list", [])]


def references_within(node: ast.AST) -> set[str]:
    """Identifiers mentioned in THIS definition, not in nested ones.

    Nested definitions are skipped deliberately. An earlier version used a
    plain ast.walk, which meant a ClassDef's references included every name in
    every one of its methods -- so the instant the class was reachable, all 13
    of the predecessor's dead methods were "reachable" too, and the check found
    only the 4 dead functions that happened to live outside a class.

    Each method is visited as its own node, so nothing is lost by not
    descending: the edges are recorded, just attributed to the right owner.
    """
    out: set[str] = set()
    stack = list(ast.iter_child_nodes(node))
    while stack:
        sub = stack.pop()
        if isinstance(sub, DEFINITION):
            # Do NOT count this as a reference when `node` is a class: defining
            # a method is not calling it. Counting it made every method of a
            # reachable class reachable, which is how the previous version
            # found zero of the predecessor's 13 dead methods. A closure inside
            # a function is different -- there the enclosing body really does
            # own it.
            if not isinstance(node, ast.ClassDef):
                out.add(sub.name)
            for dec in getattr(sub, "decorator_list", []):
                stack.extend(ast.iter_child_nodes(dec))
            continue
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for alias in sub.names:
                out.add(alias.asname or alias.name.split(".")[-1])
        stack.extend(ast.iter_child_nodes(sub))
    return out


def collect_graph() -> tuple[dict[str, pathlib.Path], dict[str, set[str]], set[str]]:
    """Returns (definitions in src, call graph, entry-point names)."""
    defs: dict[str, pathlib.Path] = {}
    graph: dict[str, set[str]] = {}
    entries: set[str] = set()

    for base in REFERENCE_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            in_src = str(path).startswith(str(SRC))
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))

            # Module-level statements run on import, so what they mention is live.
            for stmt in tree.body:
                if not isinstance(stmt, DEFINITION):
                    entries |= references_within(stmt)

            for node in ast.walk(tree):
                if not isinstance(node, DEFINITION):
                    continue
                name = node.name
                body_refs = references_within(node)
                graph.setdefault(name, set()).update(body_refs)

                is_dunder = name.startswith("__") and name.endswith("__")
                machinery = any(h in " ".join(decorator_names(node)) for h in DECORATOR_HINTS)
                alive_by_machinery = (
                    not in_src or is_dunder or machinery
                    or name in ALWAYS_ALIVE or name.startswith("test_")
                )

                if in_src and not alive_by_machinery:
                    defs[name] = path
                if alive_by_machinery:
                    entries.add(name)
                    entries |= body_refs

    return defs, graph, entries


def main() -> int:
    defs, graph, entries = collect_graph()

    reachable: set[str] = set()
    stack = list(entries)
    while stack:
        name = stack.pop()
        if name in reachable:
            continue
        reachable.add(name)
        stack.extend(graph.get(name, ()))

    dead = sorted((n, p) for n, p in defs.items() if n not in reachable)

    print(f"checked {len(defs)} definitions, {len(entries)} entry points")
    if dead:
        print(f"\n{len(dead)} definition(s) unreachable from any entry point:\n")
        for name, path in dead:
            try:
                shown = path.relative_to(ROOT)
            except ValueError:
                shown = path
            print(f"  {shown}::{name}")
        print("\nDelete them. Git remembers; the working tree should not.")
        return 1

    print("no dead code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
