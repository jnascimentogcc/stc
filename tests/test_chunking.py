from pathlib import PurePosixPath

import pytest

from stc.indexer import chunking
from stc.indexer.chunking import ChunkedFile, chunk_file

SAMPLES: dict[str, tuple[str, str, set[tuple[str, str]], set[str]]] = {
    # language: (file name, source, expected definitions (name, kind), expected calls)
    "python": (
        "a.py",
        "import os\n@dec\ndef f(a):\n    return g(a)\nclass A(B):\n    def m(self):\n        self.x.y(1)\n",
        {("f", "function"), ("A", "class"), ("m", "method")},
        {"g", "y"},
    ),
    "javascript": (
        "a.js",
        "export function f(a){ return g(a); }\nclass A { m(){ this.x.y(1);} }\nconst h = (a)=>a.b(1);\n",
        {("f", "function"), ("A", "class"), ("m", "method"), ("h", "function")},
        {"g", "y", "b"},
    ),
    "typescript": (
        "a.ts",
        "export interface I { a: number }\nexport class A { m(): void { foo.bar(); } }\nfunction f(){ g(); }\nenum E {X}\n",
        {("I", "interface"), ("A", "class"), ("m", "method"), ("f", "function"), ("E", "enum")},
        {"bar", "g"},
    ),
    "java": (
        "A.java",
        "package p;\npublic class A { public void m(){ this.x.y(); foo(); } interface I {} }\n",
        {("A", "class"), ("m", "method"), ("I", "interface")},
        {"y", "foo"},
    ),
    "csharp": (
        "A.cs",
        "namespace N {\n  public class A { public void M(){ x.Y(); Z(); } }\n  interface I {}\n}\n",
        {("A", "class"), ("M", "method"), ("I", "interface")},
        {"Y", "Z"},
    ),
    "go": (
        "a.go",
        "package main\ntype T struct{}\nfunc (t T) M() { fmt.Println(1) }\nfunc f(){ g() }\n",
        {("T", "type"), ("M", "method"), ("f", "function")},
        {"Println", "g"},
    ),
    "rust": (
        "a.rs",
        "struct S;\nimpl S { fn m(&self){ x.y(); z(); } }\nfn f(){ g(); }\ntrait T { fn t(); }\n",
        {("S", "struct"), ("m", "method"), ("f", "function"), ("T", "trait")},
        {"y", "z", "g"},
    ),
    "ruby": (
        "a.rb",
        "module M\n class A < B\n  def m; x.y(1); end\n end\nend\ndef f; g(1); end\n",
        {("M", "module"), ("A", "class"), ("m", "method"), ("f", "function")},
        {"y", "g"},
    ),
    "php": (
        "a.php",
        "<?php\nnamespace N;\nclass A { public function m(){ $this->x->y(); foo(); } }\nfunction f(){ g(); }\n",
        {("A", "class"), ("m", "method"), ("f", "function")},
        {"y", "foo", "g"},
    ),
    "kotlin": (
        "a.kt",
        "package p\nclass A { fun m(){ x.y(); z() } }\nfun f(){ g() }\n",
        {("A", "class"), ("m", "method"), ("f", "function")},
        {"y", "z", "g"},
    ),
    "c": (
        "a.c",
        "#include <a.h>\nstruct S {int a;};\nint f(int a){ return g(a); }\n",
        {("S", "struct"), ("f", "function")},
        {"g"},
    ),
    "cpp": (
        "a.cpp",
        "class A { void m(){ x.y(); z(); } };\nnamespace n { int f(){ return g(); } }\nint A::k(){ return 1; }\n",
        {("A", "class"), ("f", "function"), ("k", "function")},
        {"y", "z", "g"},
    ),
}


def run(name: str, source: str, language: str | None) -> ChunkedFile:
    return chunk_file(PurePosixPath(name), source, language)


def assert_chunks_match_lines(source: str, result: ChunkedFile) -> None:
    lines = source.replace("\r\n", "\n").split("\n")
    previous_end = 0
    for chunk in result.chunks:
        assert 1 <= chunk.start_line <= chunk.end_line <= len(lines)
        assert chunk.start_line > previous_end, "chunks must not overlap"
        assert chunk.content == "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
        previous_end = chunk.end_line


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_language_definitions_and_calls(language: str) -> None:
    name, source, definitions, calls = SAMPLES[language]
    result = run(name, source, language)
    assert result.parsed, f"grammar for {language} unavailable"
    assert_chunks_match_lines(source, result)
    found = {(s.name, s.kind) for s in result.symbols if s.is_definition}
    assert definitions <= found
    assert calls <= {s.name for s in result.symbols if not s.is_definition}
    assert all(s.chunk_index is not None for s in result.symbols)


def test_functions_and_classes_are_chunks_not_fixed_blocks() -> None:
    source = (
        "import os\n\n"
        "# helper used everywhere\n"
        "def helper(x):\n    return x + 1\n\n"
        "@decorator\n"
        "def decorated():\n    pass\n\n"
        "class Service:\n    def run(self):\n        return helper(1)\n\n"
        "VALUE = 3\n"
    )
    result = run("m.py", source, "python")
    assert_chunks_match_lines(source, result)
    summary = [(c.kind, c.name, c.start_line, c.end_line) for c in result.chunks]
    assert summary == [
        ("module", None, 1, 1),
        ("function", "helper", 3, 5),  # the leading comment travels with the function
        ("function", "decorated", 7, 9),  # decorators are part of the definition
        ("class", "Service", 11, 13),
        ("module", None, 15, 15),
    ]


def test_large_class_is_split_into_header_and_members() -> None:
    methods = "".join(
        f"    def m{i}(self):\n" + "".join(f"        x{j} = {j}\n" for j in range(30)) + "\n"
        for i in range(6)
    )
    source = "class Big:\n    attr = 1\n\n" + methods
    result = run("big.py", source, "python")
    assert_chunks_match_lines(source, result)
    kinds = [(c.kind, c.name) for c in result.chunks]
    assert kinds[0] == ("class", "Big")  # header with the attributes
    assert ("method", "Big.m3") in kinds
    assert all(c.end_line - c.start_line + 1 <= chunking.MAX_CHUNK_LINES for c in result.chunks)
    methods_symbols = {s.name for s in result.symbols if s.kind == "method"}
    assert methods_symbols == {f"m{i}" for i in range(6)}


def test_huge_function_is_cut_at_blank_lines() -> None:
    body = "".join("".join(f"    a{i}_{j} = {j}\n" for j in range(20)) + "\n" for i in range(20))
    source = "def huge():\n" + body
    result = run("h.py", source, "python")
    assert_chunks_match_lines(source, result)
    assert len(result.chunks) > 1
    assert {c.name for c in result.chunks} == {"huge"}
    for chunk in result.chunks:
        assert chunk.end_line - chunk.start_line + 1 <= chunking.MAX_CHUNK_LINES
        assert len(chunk.content) <= chunking.MAX_CHUNK_CHARS


def test_crlf_line_numbers_match() -> None:
    source = "def a():\r\n    return 1\r\n\r\ndef b():\r\n    return 2\r\n"
    result = run("c.py", source, "python")
    assert_chunks_match_lines(source, result)
    assert [(c.name, c.start_line, c.end_line) for c in result.chunks] == [("a", 1, 2), ("b", 4, 5)]


def test_markdown_is_split_at_headings_ignoring_fences() -> None:
    source = "intro\n\n# One\ntext\n```\n# not a heading\n```\n## Two\nmore\n"
    result = run("README.md", source, "markdown")
    assert not result.parsed
    assert_chunks_match_lines(source, result)
    assert [(c.kind, c.name) for c in result.chunks] == [
        ("section", None),
        ("section", "One"),
        ("section", "Two"),
    ]


@pytest.mark.parametrize(
    ("name", "language"), [("q.sql", "sql"), ("a.weird", None), ("c.yaml", "yaml"), ("d.vb", "vb")]
)
def test_languages_without_grammar_are_chunked_as_text(name: str, language: str | None) -> None:
    source = "line one\nline two\n"
    result = run(name, source, language)
    assert not result.parsed and result.symbols == []
    assert [(c.kind, c.start_line, c.end_line) for c in result.chunks] == [("text", 1, 2)]


def test_missing_grammar_degrades_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chunking, "get_grammar_parser", lambda _grammar: None)
    result = run("a.py", "def f():\n    pass\n", "python")
    assert not result.parsed
    assert [c.kind for c in result.chunks] == ["text"]


def test_empty_and_blank_files_have_no_chunks() -> None:
    assert run("a.py", "", "python").chunks == []
    assert run("a.py", "\n  \n", "python").chunks == []


def test_tsx_uses_its_own_grammar() -> None:
    source = "export function App() { return <div onClick={go()}/>; }\n"
    result = run("App.tsx", source, "typescript")
    assert result.parsed
    assert [(c.kind, c.name) for c in result.chunks] == [("function", "App")]


def test_chunk_hashes_depend_only_on_content() -> None:
    a = run("a.py", "def f():\n    return 1\n", "python").chunks[0]
    b = run("b.py", "x = 0\n\ndef f():\n    return 1\n", "python").chunks[1]
    assert a.content_sha256 == b.content_sha256 and a.start_line != b.start_line
