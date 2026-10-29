-- LLM confirmation of uncertain relations (CAT-07) and maintainers' approval of catalog
-- corrections proposed from chat feedback (CHT-10).

-- The verdict is about the evidence the relation had when it was checked: sync_relations clears
-- it when the evidence changes. A rejected relation stays here (so it is not found and judged
-- again on every run) but is hidden from every reader through `active_relations`.
ALTER TABLE relations
    ADD COLUMN llm_verdict text CHECK (llm_verdict IN ('confirmed', 'rejected', 'unsure')),
    ADD COLUMN llm_reason  text;

-- What the knowledge base shows: no rejected relations, and a confirmed one counts as high
-- confidence. The stored `confidence` stays what the static analysis derived, so re-deriving
-- relations does not see a difference and does not discard the verdict.
CREATE VIEW active_relations AS
SELECT id, source_app_id, target_app_id, type, detail, evidence_file_id, evidence_start_line,
       evidence_end_line,
       CASE WHEN llm_verdict = 'confirmed' THEN 'high' ELSE confidence END AS confidence,
       origin, evidence_count, llm_verdict, llm_reason
FROM relations
WHERE llm_verdict IS DISTINCT FROM 'rejected';

CREATE TABLE catalog_corrections (
    id             bigserial PRIMARY KEY,
    feedback_id    bigint REFERENCES feedback (id) ON DELETE SET NULL,
    application_id bigint NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    section        text   NOT NULL,
    text           text   NOT NULL CHECK (length(btrim(text)) > 0),
    status         text   NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ('proposed', 'approved', 'rejected')),
    decision_note  text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    decided_at     timestamptz
);
CREATE INDEX catalog_corrections_status_idx ON catalog_corrections (status, id);
-- One open proposal per piece of feedback.
CREATE UNIQUE INDEX catalog_corrections_open_feedback_idx
    ON catalog_corrections (feedback_id) WHERE status = 'proposed';
