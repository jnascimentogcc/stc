"""Screens as signals: SCREEN SECTION, positioned ACCEPT/DISPLAY, BMS maps, SEND/RECEIVE MAP."""

from pathlib import PurePosixPath

from stc.indexer.mainframe_signals import mainframe_signals
from stc.indexer.screen_signals import parse_field_detail
from stc.indexer.signal_types import Signal

COBOL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. TLRXFER.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-FROM          PIC 9(10).
       01  WS-AMT           PIC 9(7)V99.
       01  WS-PIN           PIC X(4).
       SCREEN SECTION.
       01  TRANSFER-SCREEN.
           05  BLANK SCREEN.
           05  LINE 2 COL 10 VALUE "TRANSFER".
           05  LINE 4 COL 5 VALUE "FROM ACCOUNT:".
           05  SC-FROM LINE 4 COL 20 PIC 9(10) USING WS-FROM REQUIRED.
           05  LINE 5 COL 5 VALUE "AMOUNT:".
           05  SC-AMT LINE 5 COL 20 PIC 9(7)V99 USING WS-AMT.
           05  LINE 6 COL 5 PIC X(4) USING WS-PIN SECURE.
           05  LINE 8 COL 5 PIC X(30) FROM WS-MSG.
       PROCEDURE DIVISION.
       0000-MAIN.
           DISPLAY TRANSFER-SCREEN
           ACCEPT TRANSFER-SCREEN
           DISPLAY 'DONE'
           STOP RUN.
"""


def signals(text: str, language: str = "cobol", name: str = "x.cbl") -> list[Signal]:
    return mainframe_signals(PurePosixPath(name), text, language)


def test_screen_section_defines_a_screen_and_its_fields() -> None:
    found = signals(COBOL)
    (screen,) = [s for s in found if s.kind == "screen" and s.role == "provides"]
    assert screen.key == "TRANSFER-SCREEN" and "4 fields" in screen.detail
    fields = {s.key: parse_field_detail(s.detail) for s in found if s.kind == "screen_field"}
    amount = fields["TRANSFER-SCREEN.SC-AMT"]
    assert amount["mode"] == "input" and amount["pic"] == "9(7)V99" and amount["at"] == "5,20"
    assert amount["label"] == "AMOUNT:" and amount["var"] == "WS-AMT" and amount["numeric"] == "1"
    assert fields["TRANSFER-SCREEN.SC-FROM"]["required"] == "1"
    assert fields["TRANSFER-SCREEN.WS-PIN"]["secure"] == "1"
    assert fields["TRANSFER-SCREEN.WS-MSG"]["mode"] == "output"


def test_display_and_accept_of_a_screen_consume_it() -> None:
    uses = [s for s in signals(COBOL) if s.kind == "screen" and s.role == "consumes"]
    assert [(u.key, u.detail, u.start_line) for u in uses] == [
        ("TRANSFER-SCREEN", "DISPLAY screen TRANSFER-SCREEN", 20),
        ("TRANSFER-SCREEN", "ACCEPT screen TRANSFER-SCREEN", 21),
    ]


def test_positioned_accept_makes_a_console_screen() -> None:
    text = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. ASKNAME.
       PROCEDURE DIVISION.
           DISPLAY 'Your name:' AT LINE 3 COL 2
           ACCEPT WS-NAME AT LINE 3 COL 14
           STOP RUN.
"""
    found = signals(text)
    fields = [s for s in found if s.kind == "screen_field"]
    assert [f.key for f in fields] == ["ASKNAME.WS-NAME"]
    assert parse_field_detail(fields[0].detail)["label"] == "Your name:"


def test_cics_send_and_receive_map() -> None:
    text = """\
       PROCEDURE DIVISION.
           EXEC CICS SEND MAP('TLRM1') MAPSET('TLRSET') ERASE END-EXEC
           EXEC CICS RECEIVE MAP(WS-MAP) MAPSET('TLRSET') END-EXEC
           EXEC CICS LINK PROGRAM('TLRCALC') END-EXEC.
"""
    found = [(s.kind, s.key, s.detail) for s in signals(text)]
    assert ("screen", "TLRM1", "CICS SEND MAP TLRM1") in found
    assert ("screen", "?WS-MAP", "dynamic CICS RECEIVE MAP WS-MAP") in found
    assert ("program", "TLRCALC", "CICS LINK TLRCALC") in found


BMS = """\
TLRSET   DFHMSD TYPE=&SYSPARM,MODE=INOUT,LANG=COBOL,CTRL=FREEKB
TLRM1    DFHMDI SIZE=(24,80),LINE=1,COLUMN=1
         DFHMDF POS=(1,30),LENGTH=8,ATTRB=ASKIP,INITIAL='TRANSFER'
         DFHMDF POS=(5,5),LENGTH=8,ATTRB=ASKIP,INITIAL='Account:'
ACCT     DFHMDF POS=(5,20),LENGTH=10,ATTRB=(UNPROT,NUM),PICIN='9(10)'
PIN      DFHMDF POS=(6,20),LENGTH=4,ATTRB=(UNPROT,DRK)
MSG      DFHMDF POS=(8,5),LENGTH=40,ATTRB=(ASKIP,BRT)
         DFHMSD TYPE=FINAL
"""


def test_bms_map_and_fields() -> None:
    found = signals(BMS, "bms", "TLRSET.bms")
    (screen,) = [s for s in found if s.kind == "screen"]
    assert (
        screen.key == "TLRM1" and "mapset TLRSET" in screen.detail and "3 fields" in screen.detail
    )
    assert (screen.start_line, screen.end_line) == (2, 7)
    fields = {s.key: parse_field_detail(s.detail) for s in found if s.kind == "screen_field"}
    assert fields["TLRM1.ACCT"]["mode"] == "input" and fields["TLRM1.ACCT"]["len"] == "10"
    assert fields["TLRM1.ACCT"]["label"] == "Account:" and fields["TLRM1.ACCT"]["numeric"] == "1"
    assert fields["TLRM1.PIN"]["secure"] == "1"
    assert fields["TLRM1.MSG"]["mode"] == "output"


def test_field_detail_round_trip_keeps_a_label_with_a_semicolon() -> None:
    from stc.indexer.screen_signals import ScreenField, field_detail

    detail = field_detail(ScreenField("A", "input", 1, 2, label="Name; first", var="WS-A"))
    parsed = parse_field_detail(detail)
    assert parsed["label"] == "Name; first" and parsed["at"] == "1,2" and parsed["var"] == "WS-A"


def test_teller_sample_screens() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "examples" / "cobol-bank-root" / "teller"
    program = (root / "src" / "TLRXFER.cbl").read_text()
    found = signals(program, name="TLRXFER.cbl")
    screens = [s for s in found if s.kind == "screen" and s.role == "provides"]
    assert [(s.key, s.detail) for s in screens] == [
        ("TRANSFER-SCREEN", "screen TRANSFER-SCREEN; 5 fields")
    ]
    fields = [s.key for s in found if s.kind == "screen_field"]
    assert fields[:3] == [
        "TRANSFER-SCREEN.SC-FROM",
        "TRANSFER-SCREEN.SC-TO",
        "TRANSFER-SCREEN.SC-AMOUNT",
    ]
    bms = signals((root / "bms" / "TLRSET.bms").read_text(), "bms", "TLRSET.bms")
    assert [s.key for s in bms if s.kind == "screen"] == ["TLRQRY"]
    assert {s.key for s in bms if s.kind == "screen_field"} == {
        "TLRQRY.ACCT",
        "TLRQRY.BAL",
        "TLRQRY.MSG",
    }
    query = signals((root / "src" / "TLRQRY.cbl").read_text(), name="TLRQRY.cbl")
    assert {s.detail for s in query if s.kind == "screen"} == {
        "CICS RECEIVE MAP TLRQRY",
        "CICS SEND MAP TLRQRY",
    }


def test_clause_words_inside_a_field_name_are_not_clauses() -> None:
    text = COBOL.replace("SC-AMT LINE 5", "SC-FROM LINE 5").replace(
        "USING WS-AMT.", "USING WS-AMT."
    )
    found = {s.key: parse_field_detail(s.detail) for s in signals(text) if s.kind == "screen_field"}
    assert found["TRANSFER-SCREEN.SC-FROM"]["var"] == "WS-AMT"
    assert found["TRANSFER-SCREEN.SC-FROM"]["mode"] == "input"
