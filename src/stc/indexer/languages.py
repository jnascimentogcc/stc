"""File name -> language. Configuration, schemas, scripts and docs count as sources of
knowledge too (IDX-09). A text file with no match is indexed as plain text and flagged (IDX-04)."""

from pathlib import PurePosixPath

_BY_SUFFIX = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".groovy": "groovy",
    ".cs": "csharp", ".vb": "vb", ".fs": "fsharp",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".dart": "dart", ".ex": "elixir", ".exs": "elixir", ".erl": "erlang",
    ".lua": "lua", ".pl": "perl", ".r": "r",
    ".sql": "sql", ".sh": "shell", ".bash": "shell", ".ps1": "powershell", ".bat": "batch",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss", ".less": "less",
    ".vue": "vue", ".svelte": "svelte",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".xml": "xml",
    ".ini": "ini", ".properties": "properties", ".env": "dotenv", ".cfg": "ini",
    ".proto": "protobuf", ".graphql": "graphql", ".gql": "graphql", ".tf": "terraform",
    ".csproj": "xml", ".fsproj": "xml", ".vbproj": "xml", ".sln": "sln", ".gradle": "groovy",
    ".md": "markdown", ".rst": "restructuredtext", ".txt": "text",
}  # fmt: skip

_BY_NAME = {
    "dockerfile": "dockerfile",
    "makefile": "make",
    "jenkinsfile": "groovy",
    "gemfile": "ruby",
    "rakefile": "ruby",
    "readme": "text",
    "license": "text",
}


def language_of(path: PurePosixPath) -> str | None:
    name = path.name.lower()
    if name in _BY_NAME:
        return _BY_NAME[name]
    if name.startswith("dockerfile"):
        return "dockerfile"
    if name.startswith(".env"):
        return "dotenv"
    return _BY_SUFFIX.get(path.suffix.lower())
