-- ro-admin map table.
-- Run once:  mysql -u <user> -p <db> < maps_schema.sql
-- Idempotent. Then populate it with:
--
--     python -m importers.import_maps --rathena-db /path/to/rathena/db
--
-- A DIRECTORY, not a file: rAthena layers THREE map caches -- db/import/,
-- db/<re|pre-re>/ and db/ -- and reads the first one that has a given map
-- (map_readallmaps, rathena/src/map/map.cpp:3910). The importer does the same.
-- Reading only db/map_cache.dat gives 1,263 maps on the reference lab and no
-- prontera, morocc, izlude or alberta: those are in db/re/map_cache.dat.
--
-- Prefixed like the Tier 1 overlay's tables, so it can never collide with
-- rAthena's schema and an operator can drop it with one statement.
--
-- Why this table exists at all: rAthena has NO map list in SQL. Map names
-- appear only as values in log and player rows, so a database-only view sees
-- however many maps something happened on -- ten, on the reference lab, out
-- of 1,241 the server loaded. (Two names in atcommandlog, three in picklog,
-- five in zenylog, eight in char.last_map and nine in char.save_map; ten
-- distinct once unioned. See importers/README.md for the table.) The real list is a file on the game server's
-- disk, and ro-admin does not read the game server's disk. So the operator
-- imports it once, and the service reads a table like it reads everything else.
CREATE TABLE IF NOT EXISTS `ro_admin_maps` (
  -- rAthena's MAP_NAME_LENGTH is 12 (src/common/mmo.hpp:163). Wider here
  -- because names appear elsewhere with extensions, and the cost is nothing.
  `name`           VARCHAR(24)       NOT NULL,
  `width`          SMALLINT UNSIGNED NOT NULL,
  `height`         SMALLINT UNSIGNED NOT NULL,
  -- Counted at import so a listing can show density without decompressing
  -- every blob to answer one query.
  `walkable_cells` MEDIUMINT UNSIGNED NOT NULL,
  -- rAthena's own zlib bytes, verbatim: ~3MB across the reference lab's 1,271
  -- layered maps, against ~100MB decompressed for data that is mostly zeros.
  --
  -- One byte per cell, and each byte is a gat CELL TYPE, not a walkable flag:
  -- 1 (non-walkable ground) and 5 (snipable gap) are blocked, everything else
  -- -- 0, 2, 3, 4, 6 -- is walkable, and 3 is walkable water. The mapping is
  -- map_gat2cell in rathena/src/map/map.cpp:3270. Stored raw rather than
  -- collapsed to a boolean so a consumer can still tell water from ground.
  --
  -- This is GEOMETRY. It is not map artwork. Textures and models live in the
  -- client's GRF archives, are Gravity's copyrighted assets, and are never
  -- imported or served.
  `cells`          MEDIUMBLOB        NOT NULL,
  `imported_at`    DATETIME          NOT NULL,
  -- Which of the layered cache files THIS map came from -- per row, one path,
  -- not all three joined. So an operator can tell a stale import from a current
  -- one without guessing, and can see that prontera came from db/re/ while
  -- payon came from db/, which is the whole fact the layering turns on.
  `source`         VARCHAR(255)      NOT NULL,
  PRIMARY KEY (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
