"""The gate that keeps player-chosen strings from being parsed as HTML."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_no_innerhtml", ROOT / "scripts" / "check_no_innerhtml.py"
)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


@pytest.mark.parametrize("line", [
    "node.innerHTML = name;",
    "node.outerHTML = name;",
    "node.insertAdjacentHTML('beforeend', name);",
    "document.write(name);",
    "document.writeln(name);",
    "range.createContextualFragment(name);",
    "node.setHTMLUnsafe(name);",
    "Document.parseHTMLUnsafe(name);",
    "new DOMParser().parseFromString(name, 'text/html');",
    "frame.srcdoc = name;",
])
def test_each_html_sink_is_caught(tmp_path, line):
    (tmp_path / "view.js").write_text(line, encoding="utf-8")
    assert checker.offenders([tmp_path]), line


def test_a_commented_out_sink_is_still_caught(tmp_path):
    """The gate does not parse JS, so it cannot tell a comment from code --
    and a call "kept for reference" is one accidental uncomment away from
    shipping."""
    (tmp_path / "view.js").write_text(
        "// node.innerHTML = name;", encoding="utf-8"
    )
    assert checker.offenders([tmp_path])


def test_mjs_files_are_scanned_too(tmp_path):
    (tmp_path / "view.mjs").write_text("node.innerHTML = name;", encoding="utf-8")
    assert checker.offenders([tmp_path])


def test_html_files_are_scanned_too(tmp_path):
    (tmp_path / "index.html").write_text(
        "<script>document.write(1)</script>", encoding="utf-8"
    )
    assert checker.offenders([tmp_path])


def test_text_content_is_allowed(tmp_path):
    (tmp_path / "view.js").write_text("node.textContent = name;", encoding="utf-8")
    assert checker.offenders([tmp_path]) == []


def test_it_scans_the_shipped_ui_and_its_tests():
    assert ROOT / "src" / "ro_admin" / "web" in checker.SCAN
    assert ROOT / "tests" / "web" in checker.SCAN


def test_the_repository_is_clean():
    assert checker.offenders() == []
