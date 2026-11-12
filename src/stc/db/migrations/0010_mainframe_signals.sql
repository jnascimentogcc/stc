-- COBOL, copybook, JCL and DDL signals (CAT-07): the programs, copybooks, DB2 tables and datasets
-- that tie mainframe applications together. `relations.type` has no check, so the new relation
-- types (calls, batch, copybook, table) need nothing else.

ALTER TABLE signals DROP CONSTRAINT signals_kind_check;
ALTER TABLE signals ADD CONSTRAINT signals_kind_check
    CHECK (kind IN ('http', 'messaging', 'database', 'library',
                    'program', 'dataset', 'copybook', 'table'));
