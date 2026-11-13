"""The target technology a specification is written for (SPC-02).

It is plain text given to the model as the stack to design for, so any stack can be described
without code changes: a file named by `STC_SPEC_STACK` or `--stack`, or this default."""

from pathlib import Path

DEFAULT_STACK = """\
Language and runtime: Java.
Framework: Spring Boot.
Persistence: JPA, on a PostgreSQL database.
API: REST, asynchronous.
Batch work becomes asynchronous services started on demand or on a schedule."""

MAX_STACK_CHARS = 4000


class StackError(Exception):
    pass


def load_stack(path: Path | None) -> str:
    """The stack description: the file's text, or the default when no file is given."""
    if path is None:
        return DEFAULT_STACK
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise StackError(f"Cannot read the target stack file {path}: {exc.strerror}") from exc
    if not text:
        raise StackError(f"The target stack file {path} is empty.")
    if len(text) > MAX_STACK_CHARS:
        raise StackError(
            f"The target stack file {path} is longer than {MAX_STACK_CHARS} characters."
        )
    return text
