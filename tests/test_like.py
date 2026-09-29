"""The LIKE escape every search in this service shares.

Parameterisation stops injection; it does not stop `%` and `_` being read as
wildcards inside the bound value. These pin the escape itself. Its effect on
real queries is pinned by the search tests of each router that uses it.
"""
from ro_admin.like import like_literal


def test_percent_is_literal():
    assert like_literal("50%") == "50\\%"


def test_underscore_is_literal():
    assert like_literal("a_b") == "a\\_b"


def test_backslash_is_escaped_before_the_wildcards():
    """Escaped first, or the backslash added in front of `%` would itself be
    escaped, and the `%` would become a wildcard again."""
    assert like_literal("\\%") == "\\\\\\%"


def test_plain_text_is_unchanged():
    assert like_literal("Kami") == "Kami"


def test_mixed_wildcards():
    assert like_literal("%_%") == "\\%\\_\\%"
