-- Screens of COBOL applications (SCREEN SECTION, BMS maps) and their fields as signals.

ALTER TABLE signals DROP CONSTRAINT signals_kind_check;
ALTER TABLE signals ADD CONSTRAINT signals_kind_check
    CHECK (kind IN ('http', 'messaging', 'database', 'library',
                    'program', 'dataset', 'copybook', 'table',
                    'screen', 'screen_field'));
