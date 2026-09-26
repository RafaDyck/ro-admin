// ro-admin Tier 2 hook -- paste into rAthena's src/custom/script.inc
// https://github.com/RafaDyck/ro-admin
//
// ONE function. It flushes a connected character's in-memory state to the
// database, which no script command can do.
//
//===== Source citations: ====================================
// The src/map/... line numbers below were read against rAthena at commit
// ad04a42 (2025-08-04) -- the revision this project's lab compiles. Upstream
// moves them: rAthena is a live project and these are not stable addresses.
// If a line no longer says what a comment here claims, re-derive it rather
// than assume drift.
//
//===== WHY THIS NEEDS A COMPILED HOOK =======================
//
// The map server owns sd->status in memory and writes it on logout or on the
// autosave cycle (autosave_time, 300s by default). Measured on a live
// session: an in-game +777 zeny change was absent from the `char` table at
// t+0s, +2s, +5s, +10s and +20s, and read exactly +777 after logout.
// `inventory` behaves the same -- empty while online, one row the moment the
// session ended.
//
// So anything reading the database sees a mirror that can be five minutes
// behind, and there is no way around it from SQL or from script:
// chrif_save() has no script binding -- `grep -n chrif_save
// src/map/script.cpp` at ad04a42 returns nothing -- and savepoint/save set a
// respawn point, not persistence.
//
//===== WHY IT IS SAFE ON A CONNECTED PLAYER =================
//
// chrif_save's logout machinery is gated on CSAVE_QUITTING, which is
// CSAVE_QUIT|CSAVE_CHANGE_MAPSERV|CSAVE_AUTOTRADE = 0x01|0x02|0x04 = 0x07
// (src/map/chrif.hpp:21-26):
//
//     if ( (flag&CSAVE_QUITTING) && sd->state.active) { ... chrif_auth_logout ... }
//                                                    (src/map/chrif.cpp:284-291)
//
// and the quitting byte sent to the char server is
//
//     WFIFOB(char_fd,12) = (flag&CSAVE_QUIT) ? 1 : 0;   (src/map/chrif.cpp:323)
//
// CSAVE_INVENTORY|CSAVE_CART is 0x08|0x10 = 0x18 (src/map/chrif.hpp:24-25),
// and 0x18 & 0x07 == 0. Passing those two bits and no others therefore
// reaches neither branch: no chrif_auth_logout, and a quitting byte of 0.
// What is left is exactly the persistence path -- pc_makesavestatus()
// (src/map/chrif.cpp:282), then the whole mmo_charstatus in packet 0x2b01
// (src/map/chrif.cpp:317-328), the inventory (src/map/chrif.cpp:299-300) and
// the cart (src/map/chrif.cpp:301-302).
//
//===== WHY NOT script_rid2sd ================================
//
// The obvious idiom, `if (!script_rid2sd(sd))`, is wrong here. On failure
// script_rid2sd_ logs "fatal error ! player not attached" and sets
// `st->state = END` (src/map/script.cpp:2673-2678), and run_script_main's
// interpreter loop is `while(st->state == RUN)` (src/map/script.cpp:4394).
// The calling script would STOP at this command and never see the 0 we push.
// For the Tier 1 overlay that means a row stranded in 'processing' and a
// timer that is never rescheduled -- the queue consumer would die silently
// the first time it synced a character who had just logged out.
//
// map_id2sd (declared src/map/map.hpp:1169) is the non-fatal lookup.
// rAthena's own buildin_playerattached uses this exact form for this exact
// reason; its comment reads "To avoid 'player not attached' script errors"
// (src/map/script.cpp:11942-11954).
//
//===== WHAT THE RETURN VALUE MEANS ==========================
//
// 1 -- chrif_save returned 0, i.e. the save was handed to the char server.
// 0 -- no attached player, OR chrif_save returned -1. It returns -1 when the
//      char server is not connected, because chrif_check(-1)
//      (src/map/chrif.cpp:293; the macro is src/map/chrif.cpp:124) returns
//      early on !chrif_isconnected(). Reporting 1 in that case would be the
//      exact defect class this project exists to remove: a success code for
//      a write that was dropped.
//
// 1 is NOT "the row is committed in MySQL". The char server performs the SQL
// write after it receives 0x2b01, so this is a request, not a post-condition.
// The caller must still re-read the value it cares about and compare.
//
// No scope is required: script.cpp includes chrif.hpp (src/map/script.cpp:41)
// and map.hpp (src/map/script.cpp:53) long before it includes this file
// (src/map/script.cpp:27765).
//============================================================

BUILDIN_FUNC(ro_admin_sync)
{
	// map_session_data is a class at ad04a42 (src/map/pc.hpp:381). The
	// `struct` keyword older trees carry is unnecessary in C++ and is left
	// off deliberately.
	map_session_data *sd;

	if (st->rid == 0 || (sd = map_id2sd(st->rid)) == nullptr) {
		// Not an error the script should die on -- the caller checks the
		// return value, exactly as it checks isloggedin().
		script_pushint(st, 0);
		return SCRIPT_CMD_SUCCESS;
	}

	// CSAVE_NORMAL is 0x00 (src/map/chrif.hpp:20). Naming the two bits we DO
	// want, and no others, is the whole safety argument; see above. Do not
	// add a bit here without redoing the 0x18 & 0x07 == 0 arithmetic.
	script_pushint(st, chrif_save(sd, CSAVE_INVENTORY | CSAVE_CART) == 0 ? 1 : 0);

	return SCRIPT_CMD_SUCCESS;
}
