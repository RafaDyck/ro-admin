"""Decoding for rAthena's single-character log type codes.

Both `zenylog.type` and `picklog.type` are ENUM columns of one-letter codes.
Raw, they are close to useless to a human and worse to an agent: `A` is not
self-describing, and guessing is how "who did this?" gets answered wrongly.
The API decodes them so callers never have to carry this table.

Source of truth: `log_picktype2char` in `src/map/log.cpp:59-99` (rathena
@ 2023-07). The letters are chosen mnemonically from the enum name, which is
why several look arbitrary -- 'S' is NPC (S)hop while 'N' is (N)PC script, and
'O' is Pr(O)duced. Do not infer a code from its letter; read the table.
"""

PICK_TYPES: dict[str, str] = {
    "T": "trade",
    "V": "vending",
    "P": "player pick/drop",
    "M": "monster drop",
    "S": "npc shop",
    "N": "npc script",
    "D": "steal",
    "C": "consumed",
    "O": "produced",
    "U": "mvp reward",
    "A": "admin command",
    "R": "storage",
    "G": "guild storage",
    "E": "mail attachment",
    "I": "auction",
    "B": "buying store",
    "L": "loot",
    "K": "bank transaction",
    "X": "other",
    "$": "cash",
    "F": "bound item removal",
    "Y": "roulette",
    "Z": "merged item",
    "Q": "quest item",
    "H": "private airship",
    "J": "barter shop",
    "W": "laphine",
    "0": "enchantgrade ui",
    "1": "reform ui",
    "2": "enchant ui",
    "3": "item package",
}


def decode_pick_type(code: str) -> str:
    """Human-readable name for a log type code.

    An unmapped code returns `unknown (<code>)` rather than a guess or a
    silent default -- a new rAthena version adding a code should be visible
    in the output, not disguised as an existing one.
    """
    return PICK_TYPES.get(code, f"unknown ({code})")
