"""The fixed-format COBOL source reader. Synthetic code only."""

from stc.indexer.cobol_source import is_free_format, mask_literals, read_source, statements


def cbl(*lines: str) -> list[str]:
    return ["       " + line for line in lines]


def test_sequence_area_and_columns_after_72_are_ignored() -> None:
    line = ("000100 MOVE A TO B.").ljust(72) + "IDENT001"
    (read,) = read_source([line])
    assert read.code == "MOVE A TO B." and read.number == 1 and not read.is_comment


def test_comment_indicators() -> None:
    lines = [
        "      * CALL 'GHOST'",
        "      / page break",
        "           MOVE A TO B. *> trailing note",
    ]
    star, slash, inline = read_source(lines)
    assert star.is_comment and star.code == ""
    assert slash.is_comment and slash.code == ""
    assert not inline.is_comment and inline.code == "MOVE A TO B."


def test_in_area_a() -> None:
    area_a, area_b, blank = read_source(["       0000-MAIN.", "           MOVE A TO B.", ""])
    assert area_a.in_area_a and area_a.code == "0000-MAIN."
    assert not area_b.in_area_a
    assert not blank.in_area_a and blank.code == ""


def test_continuation_line_is_joined_by_statements() -> None:
    lines = ["       MOVE A", "      -    TO B."]
    first, second = read_source(lines)
    assert not first.continued and second.continued
    assert statements(read_source(lines)) == [(1, 2, "MOVE A TO B.")]


def test_short_lines_tabs_and_crlf_do_not_raise() -> None:
    lines = ["", "abc", "\tMOVE A TO B.", "       MOVE C TO D.\r"]
    read = read_source(lines)
    assert [r.number for r in read] == [1, 2, 3, 4]
    assert read[3].code == "MOVE C TO D."


def test_free_format_directive() -> None:
    lines = [">>SOURCE FORMAT FREE", "*> a note", "MOVE A TO B " + "X" * 80 + ".", "MAIN-PARA."]
    assert is_free_format(lines)
    assert not is_free_format(cbl("IDENTIFICATION DIVISION."))
    directive, note, long_line, para = read_source(lines)
    assert directive.is_comment and note.is_comment
    assert long_line.code.endswith("X.") and len(long_line.code) > 72
    assert para.in_area_a and para.code == "MAIN-PARA."


def test_mask_literals_blanks_string_contents_only() -> None:
    assert mask_literals("DISPLAY 'CALL X'") == "DISPLAY '      '"
    assert mask_literals('MOVE "A" TO B') == 'MOVE " " TO B'
    assert mask_literals("MOVE 'IT''S' TO B").count("'") == 4


def test_literal_with_comment_marker_is_not_a_comment() -> None:
    (read,) = read_source(cbl("DISPLAY 'A *> B'."))
    assert read.code == "DISPLAY 'A *> B'."


def test_exec_block_across_lines_is_one_statement() -> None:
    lines = cbl(
        "EXEC SQL",
        "   SELECT A INTO :B",
        "     FROM T. X",
        "END-EXEC",
        "MOVE A TO B.",
    )
    found = statements(read_source(lines))
    assert found == [
        (1, 4, "EXEC SQL SELECT A INTO :B FROM T. X END-EXEC"),
        (5, 5, "MOVE A TO B."),
    ]


def test_statements_split_at_periods_and_exec_starts_a_new_one() -> None:
    lines = cbl("IF X = 1", "   MOVE A TO B", "   EXEC CICS RETURN END-EXEC", "END-IF.")
    lines += ["      * MOVE GHOST TO B."] + cbl("DISPLAY 'A. B'.")
    found = statements(read_source(lines))
    assert [(a, b) for a, b, _ in found] == [(1, 2), (3, 3), (4, 4), (6, 6)]
    assert found[3][2] == "DISPLAY 'A. B'."
