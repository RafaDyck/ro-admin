-- ro-admin Tier 1 overlay schema.
-- Run once against your rAthena database:  mysql -u <user> -p <db> < schema.sql
-- Idempotent: safe to re-run, safe to run before or after loading the NPC script.
--
-- These tables belong to ro-admin. They are prefixed so they can never collide
-- with rAthena's own schema, and so an operator can drop the overlay entirely
-- with two DROP statements and nothing else changes.

-- The command queue. ONE consumer: overlay/ro_admin_overlay.txt.
--
-- Note what is absent: there is no free-text command column. The predecessor
-- queued strings like "@zeny Aldebaran 1000000" and had the NPC parse them,
-- which produced both an injection vector (the character NAME reached SQL) and
-- a class of bug where a failed parse silently substituted a default value.
-- Arguments here are typed integer columns, validated before insert.
CREATE TABLE IF NOT EXISTS `ro_admin_commands` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT,
  `char_id`       INT          NOT NULL,
  `action`        VARCHAR(32)  NOT NULL,
  `arg_int`       INT          NOT NULL DEFAULT 0,
  `arg_int2`      INT          NOT NULL DEFAULT 0,
  `status`        ENUM('pending','processing','executed','failed')
                               NOT NULL DEFAULT 'pending',
  -- Who asked. Present from the first row: an audit trail added later is an
  -- audit trail with a hole in it.
  `requested_by`  VARCHAR(64)  NOT NULL,
  `created_at`    DATETIME     NOT NULL,
  -- Stamped by the claiming overlay instance. Lets a reader prove which
  -- consumer executed a row -- the check that would have caught the impostor.
  `claimed_by`    BIGINT       DEFAULT NULL,
  `claimed_at`    DATETIME     DEFAULT NULL,
  `finished_at`   DATETIME     DEFAULT NULL,
  `error_message` VARCHAR(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_status_id` (`status`, `id`),
  -- Serves the per-character command-queue rows in GET /logs/timeline
  -- (ORDER BY id), and is what _select_characters' write-evidence subquery
  -- falls back to being able to use at all. Without it, either is a full
  -- table scan per character, on every request.
  KEY `idx_char_id` (`char_id`, `id`),
  -- The write-evidence subquery behind `stale` itself
  -- (src/ro_admin/routers/characters.py's written_at_sql) needs more than
  -- idx_char_id above serves. Measured in this project's own lab, where one
  -- character's rows happen to dominate the table (86% of 1,402 rows belong
  -- to one char_id): EXPLAIN ANALYZE on idx_char_id reads every one of that
  -- character's rows through a non-covering index lookup, then filters
  -- status/action against the base row (~1ms observed). This index -- which
  -- carries every column that subquery reads -- lets MySQL answer it as a
  -- covering range scan with no base-row reads at all (~0.4ms observed in
  -- the same lab, and the gap only widens as the table grows, since it is
  -- the base-row I/O that scales with row count, not an index-only scan).
  -- Column order: char_id first (the equality filter), then status and
  -- action (also filtered, and needed in the index for the scan to stay
  -- covering), then the two timestamp columns the subquery actually
  -- aggregates.
  KEY `idx_char_status` (`char_id`, `status`, `action`, `finished_at`, `claimed_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Single-row heartbeat, rewritten by the NPC on every poll.
--
-- This table is the entire basis for reporting Tier 1 as available. Checking
-- that a file exists on disk would be an inference; the API cannot see the
-- game server's disk anyway. A row that updated one second ago is an
-- observation that the script engine is loaded, timing, and reaching MySQL.
CREATE TABLE IF NOT EXISTS `ro_admin_overlay` (
  `id`          TINYINT      NOT NULL,
  `instance_id` BIGINT       NOT NULL,
  `version`     VARCHAR(16)  NOT NULL,
  `poll_ms`     INT          NOT NULL,
  `last_seen`   DATETIME     NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
