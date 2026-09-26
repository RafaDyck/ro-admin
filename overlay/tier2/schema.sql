-- ro-admin Tier 2 schema.
-- Run once:  mysql -u <user> -p <db> < schema.sql
-- Idempotent.
--
-- Tier 2 adds no queue of its own. It consumes `sync_character` rows from
-- ro_admin_commands, which overlay/schema.sql creates, so run that first.
--
-- Two tables, and the first one IS the tier detection.
--
-- ro_admin_tier2 is written by ro_admin_tier2.txt, which calls the compiled
-- hook. A script naming a buildin that was not compiled fails to parse
-- (disp_error_message2 -> longjmp -> parse_script's setjmp handler returns
-- nullptr, src/map/script.cpp:603-608,2503-2518) and npc_parse_script guards
-- its result with `if (script)` (src/map/npc.cpp:4421-4424), so the server
-- keeps running with an inert NPC and no heartbeat ever appears. Absence of a row is therefore real evidence that
-- the hook is not compiled, rather than a setting someone forgot to flip.
--
-- Same shape as ro_admin_overlay, deliberately: a separate table rather than a
-- second row in that one, so that dropping Tier 2 cannot disturb Tier 1's
-- heartbeat and so the two tiers' availability is never inferred from a shared
-- row's age.
CREATE TABLE IF NOT EXISTS `ro_admin_tier2` (
  `id`          TINYINT     NOT NULL,
  `instance_id` BIGINT      NOT NULL,
  `version`     VARCHAR(16) NOT NULL,
  `poll_ms`     INT         NOT NULL,
  `last_seen`   DATETIME    NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- When each character's stored row was last OBSERVED to match live memory.
--
-- Not "when a flush was requested". chrif_save hands packet 0x2b01 to the char
-- server and returns; the commit happens afterwards. So the overlay reads the
-- row back and compares before writing here, and this timestamp means the
-- stored values were verified current at that moment.
--
-- This is what turns `stale` from a guess into a measurement. Without Tier 2
-- the API can only say "this character is online, so the row may be up to
-- autosave_time old".
CREATE TABLE IF NOT EXISTS `ro_admin_sync` (
  `char_id`   INT      NOT NULL,
  `synced_at` DATETIME NOT NULL,
  PRIMARY KEY (`char_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
