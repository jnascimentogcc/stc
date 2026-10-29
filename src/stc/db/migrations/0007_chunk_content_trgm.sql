-- Literal and regular-expression search over chunk content (QRY-07) uses pg_trgm to avoid a full
-- scan of every chunk.
CREATE INDEX chunks_content_trgm_idx ON chunks USING gin (content gin_trgm_ops);
