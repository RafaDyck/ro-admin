"""Search text inside SQL LIKE patterns.

Parameterisation stops injection; it does NOT stop `%` and `_` being read as
wildcards inside the bound value. Unescaped, searching for "50%" matches every
row beginning "50", and a name prefix of "a_" matches "ab". The query looks
safe, is safe, and quietly returns the wrong rows.

One definition for every router. It was duplicated in the item and map
routers, and the people searches would have made four copies.
"""


def like_literal(text: str) -> str:
    """Escape `text` so that LIKE matches it literally.

    The backslash is escaped first, or it would escape the escapes.

    The caller must pair the result with an explicit `ESCAPE '\\'` on the SQL
    side (`ESCAPE '\\\\'` in Python source, as items.py and maps.py do). That
    clause does not depend on the server's sql_mode for a default escape
    character: under NO_BACKSLASH_ESCAPES, LIKE has none, so a value escaped
    here but queried without the clause would silently match nothing it
    should. With the clause, the same mismatch fails loudly instead.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
