"""Screens and the flow between them, from the facts of a COBOL application (no database).

A program *shows* a screen (`DISPLAY`, `SEND MAP`) or *reads* one (`ACCEPT`, `RECEIVE MAP`); when a
program that uses screens calls (or transfers control to) another that does too, the user moves
from the first screen to the second. Both are facts of the code, so the documentation can draw
them and the specification can derive the forms and the navigation of a new frontend from them."""

import re
from dataclasses import dataclass

from stc.docs.facts import AppFacts, Screen, ScreenField

SHOW = frozenset({"DISPLAY", "SEND MAP"})


@dataclass(frozen=True)
class Step:
    """The user goes from `source` to `target` because `program` calls `callee`."""

    source: str
    target: str
    program: str
    callee: str


def screens_used_by(facts: AppFacts, programs: list[str] | set[str]) -> list[str]:
    """The defined screens the programs show or read, in the order of the programs."""
    order = list(programs)
    used: dict[str, int] = {}
    for use in facts.screen_uses:
        if use.owner.kind == "program" and use.owner.name in order and use.screen in facts.screens:
            used.setdefault(use.screen, order.index(use.owner.name))
    return sorted(used, key=lambda s: (used[s], s))


def programs_of(facts: AppFacts, screen: str) -> list[str]:
    return sorted(
        {
            u.owner.name
            for u in facts.screen_uses
            if u.screen == screen and u.owner.kind == "program"
        }
    )


def navigation(facts: AppFacts, programs: list[str] | set[str] | None = None) -> list[Step]:
    """Screen to screen steps: a program using screens calls one that uses other screens."""
    scope = set(facts.programs) if programs is None else set(programs)
    by_program: dict[str, list[str]] = {}
    for use in facts.screen_uses:
        if use.owner.kind == "program" and use.screen in facts.screens:
            names = by_program.setdefault(use.owner.name, [])
            if use.screen not in names:
                names.append(use.screen)
    steps: list[Step] = []
    for call in facts.calls:
        if call.caller.kind != "program" or call.caller.name not in scope:
            continue
        for source in by_program.get(call.caller.name, []):
            for target in by_program.get(call.target, []):
                step = Step(source, target, call.caller.name, call.target)
                if source != target and step not in steps:
                    steps.append(step)
    return steps


def field_rows(screen: Screen) -> list[ScreenField]:
    return [f for f in screen.fields]


def validation_variables(facts: AppFacts, screens: list[str]) -> list[str]:
    """The program variables behind the input fields of the screens: where the validation is."""
    found: dict[str, None] = {}
    for name in screens:
        screen = facts.screens.get(name)
        for f in screen.fields if screen else []:
            if f.var and f.mode == "input":
                found[f.var] = None
    return list(found)


# --------------------------------------------------------------------------------------------
# what a PIC clause says about a form field
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Picture:
    kind: str  # text | alpha | number
    length: int  # characters (text, alpha) or digits (number)
    decimals: int = 0
    signed: bool = False


_REPEAT = re.compile(r"(.)\((\d+)\)")


def _digits(part: str) -> int:
    return len(re.findall(r"[9Z*]", part))


def parse_picture(pic: str | None) -> Picture | None:
    """`9(7)V99` -> a number of 9 digits with 2 decimals; `X(30)` -> text of 30; None when the
    clause is not understood (the code is then read by the model, with the PIC as evidence)."""
    if not pic:
        return None
    text = _REPEAT.sub(lambda m: m.group(1) * min(int(m.group(2)), 999), pic.upper().rstrip("."))
    if re.fullmatch(r"X+", text):
        return Picture("text", len(text))
    if re.fullmatch(r"A+", text):
        return Picture("alpha", len(text))
    if not re.fullmatch(r"[9SVZ.,+\-*$B0/]+(?:CR|DB)?", text) or not re.search(r"[9Z]", text):
        return None
    signed = text.startswith("S") or any(c in text for c in "+-") or text.endswith(("CR", "DB"))
    point = re.search(r"[V.]", text)
    whole, fraction = (text[: point.start()], text[point.end() :]) if point else (text, "")
    return Picture("number", _digits(whole) + _digits(fraction), _digits(fraction), signed)


def form_rules(f: ScreenField) -> tuple[str, str]:
    """`(input type, rules)` of one field, from its PIC, length and flags."""
    picture = parse_picture(f.pic)
    rules: list[str] = []
    if f.mode == "output":
        kind = "read-only"
    elif "secure" in f.flags:
        kind = "password"
    elif picture is not None and picture.kind == "number":
        kind = "decimal" if picture.decimals else "integer"
    else:
        kind = "text"
    if picture is not None:
        if picture.kind == "number":
            whole = picture.length - picture.decimals
            rules.append(
                f"{whole} integer digits"
                + (f", {picture.decimals} decimals" if picture.decimals else "")
            )
            rules.append("may be negative" if picture.signed else "not negative")
        else:
            rules.append(
                f"up to {picture.length} characters"
                + (", letters only" if picture.kind == "alpha" else "")
            )
    elif f.length:
        rules.append(f"up to {f.length} characters")
    if "required" in f.flags:
        rules.append("required")
    return kind, "; ".join(rules)
