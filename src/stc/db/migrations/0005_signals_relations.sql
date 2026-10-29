-- Cross-application signals extracted from files (endpoints, calls, topics, databases, packages)
-- and the relations derived from them (CAT-07).

CREATE TABLE signals (
    id             bigserial PRIMARY KEY,
    file_id        bigint  NOT NULL REFERENCES files (id) ON DELETE CASCADE,
    application_id bigint  NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    kind           text    NOT NULL CHECK (kind IN ('http', 'messaging', 'database', 'library')),
    role           text    NOT NULL
                   CHECK (role IN ('provides', 'consumes', 'publishes', 'subscribes', 'uses')),
    key            text    NOT NULL,    -- normalized identity: path, topic, engine://host:port/db, package
    host           text,                -- http consumers: host or env-var token, when known
    detail         text    NOT NULL,    -- what the code says, without credentials
    start_line     integer NOT NULL CHECK (start_line >= 1),
    end_line       integer NOT NULL CHECK (end_line >= start_line)
);
CREATE INDEX signals_file_idx ON signals (file_id);
CREATE INDEX signals_lookup_idx ON signals (kind, role, key);
CREATE INDEX signals_app_idx ON signals (application_id);

-- A file's signals were extracted from this content (and extractor version); NULL = not yet.
ALTER TABLE files ADD COLUMN signals_key text;

-- One relation per (source, target, type). `shared_database` is undirected (source id < target id).
ALTER TABLE relations
    ADD COLUMN origin text NOT NULL DEFAULT 'static'
        CHECK (origin IN ('static', 'llm', 'manual')),
    ADD COLUMN evidence_count integer NOT NULL DEFAULT 1,
    ADD CONSTRAINT relations_unique UNIQUE (source_app_id, target_app_id, type);
