"""Tree-sitter grammars, from the official per-language wheels (pinned in uv.lock, no downloads).
A grammar that cannot be loaded is never fatal: the file is chunked as text instead."""

import importlib
from functools import cache

from tree_sitter import Language, Parser

# grammar name -> (module, function returning the grammar pointer)
_LOADERS: dict[str, tuple[str, str]] = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "java": ("tree_sitter_java", "language"),
    "csharp": ("tree_sitter_c_sharp", "language"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "ruby": ("tree_sitter_ruby", "language"),
    "php": ("tree_sitter_php", "language_php"),
    "kotlin": ("tree_sitter_kotlin", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
}

GRAMMARS = tuple(_LOADERS)


@cache
def get_grammar_parser(grammar: str) -> Parser | None:
    """A parser for the grammar, or None if it is unknown or fails to load."""
    loader = _LOADERS.get(grammar)
    if loader is None:
        return None
    module, function = loader
    try:
        return Parser(Language(getattr(importlib.import_module(module), function)()))
    except Exception:  # noqa: BLE001 - any failure must degrade to text chunking
        return None
