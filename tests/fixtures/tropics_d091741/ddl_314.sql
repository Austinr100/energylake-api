-- pantry migration 314's schema change, verbatim (migrations/314_tropical_display_publish.sql
-- at origin/main 2026-10-15; applied to Neon 2026-10-15 15:03:02Z). The CHECKs the
-- display rows are held to: source 'display' <=> product 'display:%', status banked,
-- vintage_key an advisory or a cycle with an optional r<n> revision; one row per
-- (product, storm_id, vintage_key); and the reader view tropical_display_in_force.
-- Loaded after ddl_297 so the display fixture rows must pass what Neon holds them to.
ALTER TABLE tropical_file_vintage
    DROP CONSTRAINT tropical_file_vintage_source_check,
    ADD CONSTRAINT tropical_file_vintage_source_check CHECK (source IN ('nhc', 'nhc_ftp', 'ecmwf_aws',
        'hafs_aws', 'gfs_aws', 'parsed', 'display')),
    ADD CONSTRAINT tfv_display_is_display_product CHECK ((source = 'display') = (product LIKE 'display:%')),
    ADD CONSTRAINT tfv_display_is_banked CHECK (source <> 'display' OR status = 'banked'),
    ADD CONSTRAINT tfv_display_vintage_key CHECK (source <> 'display'
        OR vintage_key ~ '^([0-9]{3}[A-Z]?|[0-9]{10})(r[1-9][0-9]*)?$');

CREATE UNIQUE INDEX tfv_display_immutable ON tropical_file_vintage (product, storm_id, vintage_key)
    WHERE source = 'display';

CREATE VIEW tropical_display_in_force AS
SELECT DISTINCT ON (v.product, v.storm_id, substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10})'))
       v.vintage_id, v.dataset, v.product, v.storm_id,
       substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10})') AS advisory_or_cycle,
       COALESCE(substring(v.vintage_key FROM 'r([1-9][0-9]*)$')::int, 0) AS revision,
       v.vintage_key, v.fetch_ts AS published_ts, v.issued_ts, v.r2_key, v.byte_size, v.sha256, v.meta
  FROM tropical_file_vintage v
 WHERE v.source = 'display' AND v.status = 'banked'
 ORDER BY v.product, v.storm_id, substring(v.vintage_key FROM '^([0-9]{3}[A-Z]?|[0-9]{10})'),
          COALESCE(substring(v.vintage_key FROM 'r([1-9][0-9]*)$')::int, 0) DESC,
          v.fetch_ts DESC, v.vintage_id DESC;
