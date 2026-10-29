"""Decomposes a source file into meaningful chunks and symbols (IDX-08).

Code is split with tree-sitter by function / class / method, never at fixed sizes; the gaps
between definitions (imports, constants, top-level statements) become `module` chunks. Large
classes are split into a header chunk plus one chunk per member, and anything still above the size
limit is cut at blank lines. Files without a grammar (configuration, docs, SQL, other languages)
are chunked as text, with Markdown split at headings. Every chunk keeps its 1-based line range.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter import Node

from stc.indexer.grammars import get_grammar_parser

CHUNKER_VERSION = 1
MAX_CHUNK_LINES = 120
MAX_CHUNK_CHARS = 6_000


@dataclass(frozen=True)
class ChunkDraft:
    kind: str  # function | method | class | interface | ... | module | section | text
    name: str | None
    start_line: int
    end_line: int
    content: str
    content_sha256: str


@dataclass(frozen=True)
class SymbolDraft:
    name: str
    kind: str  # function | method | class | ... | call
    is_definition: bool
    start_line: int
    end_line: int
    chunk_index: int | None  # index into the chunk list of the chunk that contains it


@dataclass(frozen=True)
class ChunkedFile:
    chunks: list[ChunkDraft]
    symbols: list[SymbolDraft]
    parsed: bool  # False when the file was chunked as text (no grammar / parse failure)


@dataclass(frozen=True)
class LangSpec:
    functions: frozenset[str]
    containers: dict[str, str]  # node type -> chunk/symbol kind
    calls: frozenset[str] = frozenset()
    wrappers: frozenset[str] = frozenset()  # decorated_definition, export_statement, ...
    transparent: frozenset[str] = frozenset()  # namespaces: descend, never a chunk of their own
    var_functions: bool = False  # JS/TS: `const f = () => ...` defines a function


def _spec(
    functions: str,
    containers: dict[str, str],
    calls: str = "",
    wrappers: str = "",
    transparent: str = "",
    var_functions: bool = False,
) -> LangSpec:
    return LangSpec(
        frozenset(functions.split()),
        containers,
        frozenset(calls.split()),
        frozenset(wrappers.split()),
        frozenset(transparent.split()),
        var_functions,
    )


_JS_CONTAINERS = {"class_declaration": "class"}
_TS_CONTAINERS = {
    **_JS_CONTAINERS,
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "type_alias_declaration": "type",
}

SPECS: dict[str, LangSpec] = {
    "python": _spec(
        "function_definition", {"class_definition": "class"}, "call", "decorated_definition"
    ),
    "javascript": _spec(
        "function_declaration generator_function_declaration method_definition",
        _JS_CONTAINERS,
        "call_expression new_expression",
        "export_statement",
        var_functions=True,
    ),
    "typescript": _spec(
        "function_declaration generator_function_declaration method_definition",
        _TS_CONTAINERS,
        "call_expression new_expression",
        "export_statement expression_statement",
        "internal_module module",
        var_functions=True,
    ),
    "java": _spec(
        "method_declaration constructor_declaration",
        {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "record",
        },
        "method_invocation object_creation_expression",
    ),
    "csharp": _spec(
        "method_declaration constructor_declaration local_function_statement",
        {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "struct_declaration": "struct",
            "record_declaration": "record",
            "enum_declaration": "enum",
        },
        "invocation_expression object_creation_expression",
        transparent="namespace_declaration file_scoped_namespace_declaration",
    ),
    "go": _spec(
        "function_declaration method_declaration",
        {"type_declaration": "type"},
        "call_expression",
    ),
    "rust": _spec(
        "function_item function_signature_item",
        {
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
            "impl_item": "impl",
        },
        "call_expression",
        transparent="mod_item",
    ),
    "ruby": _spec("method singleton_method", {"class": "class", "module": "module"}, "call"),
    "php": _spec(
        "function_definition method_declaration",
        {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "trait_declaration": "trait",
            "enum_declaration": "enum",
        },
        "function_call_expression member_call_expression scoped_call_expression "
        "object_creation_expression",
        transparent="namespace_definition",
    ),
    "kotlin": _spec(
        "function_declaration",
        {"class_declaration": "class", "object_declaration": "object"},
        "call_expression",
    ),
    "c": _spec("function_definition", {"struct_specifier": "struct"}, "call_expression"),
    "cpp": _spec(
        "function_definition",
        {"class_specifier": "class", "struct_specifier": "struct"},
        "call_expression",
        transparent="namespace_definition",
    ),
}
SPECS["tsx"] = SPECS["typescript"]

# Nodes that merely hold the members of a container/namespace.
_BODY_TYPES = frozenset(
    {
        "block", "class_body", "declaration_list", "body_statement", "field_declaration_list",
        "enum_body", "interface_body", "statement_block", "compound_statement", "body",
        "enum_member_declaration_list", "object_type",
    }
)  # fmt: skip
# Function-like nodes that are methods even when found outside a container (Go receivers).
_METHOD_TYPES = frozenset({"method_declaration", "method_definition", "constructor_declaration"})
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*$")
_NAME_TYPES = frozenset({"identifier", "field_identifier", "simple_identifier", "type_identifier"})


def grammar_for(path: PurePosixPath, language: str | None) -> str | None:
    if language is None:
        return None
    if path.suffix.lower() == ".tsx":
        return "tsx"
    return language if language in SPECS else None


def chunk_file(path: PurePosixPath, text: str, language: str | None) -> ChunkedFile:
    lines = text.replace("\r\n", "\n").split("\n")
    if not text.strip():
        return ChunkedFile([], [], parsed=False)
    grammar = grammar_for(path, language)
    parser = get_grammar_parser(grammar) if grammar else None
    if grammar and parser:
        source = "\n".join(lines).encode("utf-8")
        tree = parser.parse(source)  # nodes are views into the tree: keep it alive while used
        if tree.root_node.child_count:
            return _chunk_code(tree.root_node, lines, SPECS[grammar])
    return ChunkedFile(_chunk_text(lines, language), [], parsed=False)


# --------------------------------------------------------------------------------------------
# code
# --------------------------------------------------------------------------------------------


@dataclass
class _Span:
    start: int  # 1-based, inclusive
    end: int
    kind: str
    name: str | None


class _Walker:
    def __init__(self, lines: list[str], spec: LangSpec) -> None:
        self.lines = lines
        self.spec = spec
        self.spans: list[_Span] = []

    # -- classification ----------------------------------------------------------------------

    def resolve(self, node: Node) -> tuple[Node, str | None, str | None]:
        """Unwrap decorators/exports. Returns (definition node, category, kind).
        category is 'function' | 'container' | 'transparent' | None."""
        current = node
        while current.type in self.spec.wrappers:
            inner = next(
                (c for c in current.named_children if self._category(c)[0] is not None), None
            )
            if inner is None:
                return node, None, None
            current = inner
        category, kind = self._category(current)
        return current, category, kind

    def category_of(self, node: Node) -> tuple[str | None, str | None]:
        return self._category(node)

    def _category(self, node: Node) -> tuple[str | None, str | None]:
        spec = self.spec
        if node.type in spec.functions:
            return "function", "function"
        if node.type in spec.containers:
            if node.type in ("struct_specifier", "class_specifier") and not any(
                c.type == "field_declaration_list" for c in node.named_children
            ):
                return None, None  # a mere reference such as `struct S *p`
            return "container", spec.containers[node.type]
        if node.type in spec.transparent:
            return "transparent", None
        if (
            spec.var_functions
            and node.type in ("lexical_declaration", "variable_declaration")
            and self._var_function_name(node)
        ):
            return "function", "function"
        return None, None

    @staticmethod
    def _var_function_name(node: Node) -> str | None:
        for declarator in node.named_children:
            value = declarator.child_by_field_name("value")
            if (
                declarator.type == "variable_declarator"
                and value is not None
                and value.type in ("arrow_function", "function_expression", "function")
            ):
                name = declarator.child_by_field_name("name")
                return name.text.decode() if name is not None and name.text else None
        return None

    def name_of(self, node: Node) -> str | None:
        if node.type in ("lexical_declaration", "variable_declaration"):
            return self._var_function_name(node)
        if node.type == "type_declaration":  # Go
            spec = next((c for c in node.named_children if c.type == "type_spec"), None)
            return self.name_of(spec) if spec is not None else None
        if node.type == "impl_item":  # Rust
            target = node.child_by_field_name("type")
            return target.text.decode() if target is not None and target.text else None
        named = node.child_by_field_name("name")
        if named is not None and named.text:
            return named.text.decode().split("::")[-1]
        declarator = node.child_by_field_name("declarator")  # C / C++
        while declarator is not None:
            if declarator.type in _NAME_TYPES:
                return declarator.text.decode() if declarator.text else None
            inner = declarator.child_by_field_name("name")
            if inner is not None and inner.text:
                return inner.text.decode().split("::")[-1]
            declarator = declarator.child_by_field_name("declarator")
        for child in node.named_children:  # Kotlin and others without a `name` field
            if child.type in ("simple_identifier", "type_identifier") and child.text:
                return child.text.decode()
        return None

    # -- spans -------------------------------------------------------------------------------

    def extent(self, node: Node) -> tuple[int, int]:
        start = node.start_point.row + 1
        end = node.end_point.row + 1
        if node.end_point.column == 0 and end > start:
            end -= 1
        previous = node.prev_sibling
        while previous is not None and "comment" in previous.type:
            comment_end = previous.end_point.row + 1 - (previous.end_point.column == 0)
            if comment_end < start - 1:  # a blank line separates it from the definition
                break
            start = previous.start_point.row + 1
            previous = previous.prev_sibling
        return start, end

    def walk_children(self, parent: Node, prefix: str | None, inside_definition: bool) -> None:
        for child in parent.named_children:
            self.handle(child, prefix, inside_definition)

    def handle(self, node: Node, prefix: str | None, inside_definition: bool) -> None:
        definition, category, kind = self.resolve(node)
        if category == "transparent":
            self.walk_children(definition, prefix, True)
        elif category == "function" and kind:
            name = self.name_of(definition)
            start, end = self.extent(node)
            is_method = prefix is not None or definition.type in _METHOD_TYPES
            self.spans.append(
                _Span(start, end, "method" if is_method else kind, _qualify(prefix, name))
            )
        elif category == "container" and kind:
            self.container(node, definition, kind, prefix)
        elif inside_definition and node.type in _BODY_TYPES:
            self.walk_children(node, prefix, True)

    def container(self, outer: Node, definition: Node, kind: str, prefix: str | None) -> None:
        name = self.name_of(definition)
        qualified = _qualify(prefix, name)
        start, end = self.extent(outer)
        size = "\n".join(self.lines[start - 1 : end])
        if end - start + 1 <= MAX_CHUNK_LINES and len(size) <= MAX_CHUNK_CHARS:
            self.spans.append(_Span(start, end, kind, qualified))
            return
        before = len(self.spans)
        self.walk_children(definition, qualified, True)
        members = self.spans[before:]
        if not members:
            self.spans.append(_Span(start, end, kind, qualified))
            return
        first = min(m.start for m in members)
        if first > start:
            self.spans.insert(before, _Span(start, first - 1, kind, qualified))


def _qualify(prefix: str | None, name: str | None) -> str | None:
    if name is None:
        return prefix
    return f"{prefix}.{name}" if prefix else name


def _chunk_code(root: Node, lines: list[str], spec: LangSpec) -> ChunkedFile:
    walker = _Walker(lines, spec)
    walker.walk_children(root, None, False)
    spans = sorted(walker.spans, key=lambda s: (s.start, s.end))

    chunks: list[ChunkDraft] = []
    cursor = 1

    def gap(start: int, end: int) -> None:
        # Trim blank edges; skip stray punctuation such as a closing brace.
        while start <= end and not lines[start - 1].strip():
            start += 1
        while end >= start and not lines[end - 1].strip():
            end -= 1
        if start <= end and re.search(r"\w", "\n".join(lines[start - 1 : end])):
            chunks.extend(_windows(lines, start, end, "module", None))

    for span in spans:
        if span.start > cursor:
            gap(cursor, span.start - 1)
        chunks.extend(_windows(lines, span.start, span.end, span.kind, span.name))
        cursor = max(cursor, span.end + 1)
    if cursor <= len(lines):
        gap(cursor, len(lines))

    return ChunkedFile(chunks, _extract_symbols(root, chunks, walker), parsed=True)


# --------------------------------------------------------------------------------------------
# symbols
# --------------------------------------------------------------------------------------------


def _extract_symbols(root: Node, chunks: list[ChunkDraft], walker: _Walker) -> list[SymbolDraft]:
    spec = walker.spec
    symbols: list[SymbolDraft] = []
    seen_calls: set[tuple[str, int]] = set()

    def chunk_at(line: int) -> int | None:
        best: int | None = None
        for index, chunk in enumerate(chunks):
            inside = chunk.start_line <= line <= chunk.end_line
            if inside and (best is None or chunk.kind == "method"):
                best = index
        return best

    stack: list[tuple[Node, bool]] = [(root, False)]  # (node, inside a container)
    while stack:
        node, in_container = stack.pop()
        category, kind = (None, None) if node is root else walker.category_of(node)
        child_in_container = in_container
        if category in ("function", "container") and kind:
            name = walker.name_of(node)
            if name:
                start = node.start_point.row + 1
                end = max(start, node.end_point.row + 1 - (node.end_point.column == 0))
                is_method = category == "function" and (in_container or node.type in _METHOD_TYPES)
                symbols.append(
                    SymbolDraft(
                        name, "method" if is_method else kind, True, start, end, chunk_at(start)
                    )
                )
            child_in_container = category == "container"
        elif node.type in spec.calls:
            callee = _callee(node)
            line = node.start_point.row + 1
            if callee and (callee, line) not in seen_calls:
                seen_calls.add((callee, line))
                symbols.append(SymbolDraft(callee, "call", False, line, line, chunk_at(line)))
        for child in reversed(node.named_children):
            stack.append((child, child_in_container))
    symbols.sort(key=lambda s: (s.start_line, not s.is_definition, s.name))
    return symbols


def _callee(node: Node) -> str | None:
    target = None
    for field_name in ("function", "name", "method", "constructor", "type"):
        target = node.child_by_field_name(field_name)
        if target is not None:
            break
    if target is None and node.named_children:  # Kotlin: callee is the first child
        target = node.named_children[0]
    if target is None or not target.text:
        return None
    text = target.text.decode(errors="replace").strip()
    last = re.split(r"\.|::|->|\?\.", text)[-1]
    last = last.split("<")[0].strip()
    return last if _IDENTIFIER.match(last) and len(last) <= 100 else None


# --------------------------------------------------------------------------------------------
# text and size limits
# --------------------------------------------------------------------------------------------


def _windows(
    lines: list[str], start: int, end: int, kind: str, name: str | None
) -> list[ChunkDraft]:
    """Emit [start, end] as one chunk, or several cut at blank lines when over the size limit."""
    result: list[ChunkDraft] = []
    cursor = start
    while cursor <= end:
        stop = _window_end(lines, cursor, end)
        first, last = cursor, stop
        while first <= last and not lines[first - 1].strip():
            first += 1
        while last >= first and not lines[last - 1].strip():
            last -= 1
        if first <= last:
            content = "\n".join(lines[first - 1 : last])
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            result.append(ChunkDraft(kind, name, first, last, content, digest))
        cursor = stop + 1
    return result


def _window_end(lines: list[str], start: int, end: int) -> int:
    size = 0
    last = start - 1
    for number in range(start, end + 1):
        size += len(lines[number - 1]) + 1
        if number - start + 1 > MAX_CHUNK_LINES or size > MAX_CHUNK_CHARS:
            if last < start:  # a single line over the limit: keep it whole
                return number
            blank = next(
                (
                    n
                    for n in range(last, start + (last - start) // 2, -1)
                    if not lines[n - 1].strip()
                ),
                None,
            )
            return blank - 1 if blank and blank > start else last
        last = number
    return end


_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _chunk_text(lines: list[str], language: str | None) -> list[ChunkDraft]:
    if language == "markdown":
        return _chunk_markdown(lines)
    return _windows(lines, 1, len(lines), "text", None)


def _chunk_markdown(lines: list[str]) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    fenced = False
    boundaries: list[tuple[int, str | None]] = []
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = None if fenced else _HEADING.match(line)
        if match:
            boundaries.append((number, match.group(2)))
    if not boundaries or boundaries[0][0] > 1:
        boundaries.insert(0, (1, None))
    for index, (start, heading) in enumerate(boundaries):
        end = boundaries[index + 1][0] - 1 if index + 1 < len(boundaries) else len(lines)
        chunks.extend(_windows(lines, start, end, "section", heading))
    return chunks
