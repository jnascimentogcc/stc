"""Screens and the flow between them, from the facts of a COBOL application (no database).

A program *shows* a screen (`DISPLAY`, `SEND MAP`) or *reads* one (`ACCEPT`, `RECEIVE MAP`); when a
program that uses screens calls (or transfers control to) another that does too, the user moves
from the first screen to the second. Both are facts of the code, so the documentation can draw
them and the specification can derive the forms and the navigation of a new frontend from them."""

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
