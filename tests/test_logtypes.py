from ro_admin.logtypes import PICK_TYPES, decode_pick_type


def test_known_codes_decode_to_human_names():
    assert decode_pick_type("A") == "admin command"
    assert decode_pick_type("N") == "npc script"
    assert decode_pick_type("M") == "monster drop"
    assert decode_pick_type("T") == "trade"


def test_unknown_code_is_reported_not_swallowed():
    """An unmapped code must stay visible rather than becoming a plausible lie."""
    assert decode_pick_type("~") == "unknown (~)"


def test_table_covers_every_code_in_the_enum_columns():
    """Both the zenylog and picklog ENUM columns draw from this same table.

    Codes taken from the live schema; the union is what rAthena can emit.
    """
    zeny_enum = set("TVPMSNDCAEIBKJX02")
    pick_enum = set("MPLTVSNCARGEBOIXDU$FYZQHJW0123")
    assert (zeny_enum | pick_enum) <= set(PICK_TYPES)
