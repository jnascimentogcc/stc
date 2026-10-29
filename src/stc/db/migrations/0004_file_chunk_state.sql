-- Remembers which content (and chunker version) a file's chunks were produced from, so only new
-- or changed files are re-chunked. NULL = not chunked yet.
ALTER TABLE files ADD COLUMN chunked_key text;
