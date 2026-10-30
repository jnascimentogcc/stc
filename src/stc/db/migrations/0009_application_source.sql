-- Where an application's files are read from: the name of a source (STC_ROOT is `default`; the
-- others are declared in sources.toml). `applications.path` stays relative to the source's folder.
ALTER TABLE applications ADD COLUMN source text NOT NULL DEFAULT 'default';
