-- What a catalog was generated from, so unchanged applications are not regenerated, and whether
-- every section could be produced.
ALTER TABLE catalogs
    ADD COLUMN inputs_sha256 text,
    ADD COLUMN status text NOT NULL DEFAULT 'complete' CHECK (status IN ('complete', 'partial'));
