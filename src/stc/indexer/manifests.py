"""Package manifests -> `library` signals: the packages an application provides (its own name)
and the packages it depends on. An application that depends on a package another application
provides is related to it (CAT-07, "common internal libraries")."""

import json
import re
import tomllib
from pathlib import PurePosixPath
from typing import Any

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from stc.indexer.signal_types import Signal

_CSPROJ_SUFFIXES = (".csproj", ".fsproj", ".vbproj")
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def is_manifest(path: PurePosixPath) -> bool:
    name = path.name.lower()
    return (
        name
        in ("package.json", "pyproject.toml", "pom.xml", "go.mod", "cargo.toml", "composer.json")
        or name.endswith(_CSPROJ_SUFFIXES)
        or (name.startswith("requirements") and name.endswith(".txt"))
    )


def manifest_signals(path: PurePosixPath, text: str) -> list[Signal]:
    name = path.name.lower()
    try:
        if name == "package.json":
            provides, consumes = _package_json(text)
        elif name == "pyproject.toml":
            provides, consumes = _pyproject(text)
        elif name == "pom.xml":
            provides, consumes = _pom(text)
        elif name.endswith(_CSPROJ_SUFFIXES):
            provides, consumes = _csproj(path, text)
        elif name == "go.mod":
            provides, consumes = _go_mod(text)
        elif name == "cargo.toml":
            provides, consumes = _cargo(text)
        elif name == "composer.json":
            provides, consumes = _composer(text)
        else:
            provides, consumes = [], _requirements(text)
    except (ValueError, tomllib.TOMLDecodeError, DefusedXmlException, ElementTree.ParseError):
        return []  # a broken manifest yields no signals rather than failing the file
    lines = text.replace("\r\n", "\n").split("\n")
    signals = [_signal("provides", n, lines) for n in provides]
    signals += [_signal("consumes", n, lines) for n in consumes]
    return [s for s in signals if s.key]


def _signal(role: str, name: str, lines: list[str]) -> Signal:
    number = next((i + 1 for i, line in enumerate(lines) if name.split(":")[-1] in line), 1)
    verb = "provides package" if role == "provides" else "depends on"
    return Signal("library", role, name.lower(), None, f"{verb} {name}", number, number)


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _package_json(text: str) -> tuple[list[str], list[str]]:
    data: Any = json.loads(text)
    if not isinstance(data, dict):
        return [], []
    consumes: list[str] = []
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = data.get(section)
        if isinstance(deps, dict):
            consumes += [str(k) for k in deps]
    name = data.get("name")
    return ([name] if isinstance(name, str) else []), consumes


def _requirement_names(requirements: list[Any]) -> list[str]:
    names = []
    for item in requirements:
        match = _REQUIREMENT.match(str(item).strip())
        if match:
            names.append(_pep503(match.group(1)))
    return names


def _pyproject(text: str) -> tuple[list[str], list[str]]:
    data = tomllib.loads(text)
    project = data.get("project", {})
    provides = [_pep503(project["name"])] if isinstance(project.get("name"), str) else []
    consumes = _requirement_names(project.get("dependencies", []))
    for group in (project.get("optional-dependencies") or {}).values():
        consumes += _requirement_names(group)
    poetry = (data.get("tool") or {}).get("poetry") or {}
    if isinstance(poetry.get("name"), str):
        provides.append(_pep503(poetry["name"]))
    tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
    tables += [g.get("dependencies") or {} for g in (poetry.get("group") or {}).values()]
    for table in tables:
        consumes += [_pep503(k) for k in table if k.lower() != "python"]
    return provides, consumes


def _requirements(text: str) -> list[str]:
    lines = [line.split("#")[0].strip() for line in text.splitlines()]
    return _requirement_names([line for line in lines if line and not line.startswith("-")])


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: Any, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name and child.text:
            return str(child.text).strip()
    return None


def _pom(text: str) -> tuple[list[str], list[str]]:
    root = ElementTree.fromstring(text)
    provides = [a] if (a := _child_text(root, "artifactId")) else []
    consumes: list[str] = []
    for child in root:
        tag = _local(child.tag)
        if tag == "parent" and (a := _child_text(child, "artifactId")):
            consumes.append(a)
        elif tag == "dependencies":
            consumes += [a for d in child if (a := _child_text(d, "artifactId"))]
    return provides, consumes


def _csproj(path: PurePosixPath, text: str) -> tuple[list[str], list[str]]:
    root = ElementTree.fromstring(text)
    provides = [path.stem]
    consumes: list[str] = []
    for element in root.iter():
        tag = _local(element.tag)
        if tag in ("PackageId", "AssemblyName") and element.text:
            provides.append(element.text.strip())
        elif tag == "PackageReference" and element.get("Include"):
            consumes.append(str(element.get("Include")))
        elif tag == "ProjectReference" and element.get("Include"):
            consumes.append(re.split(r"[\\/]", str(element.get("Include")))[-1].rsplit(".", 1)[0])
    return provides, consumes


def _go_mod(text: str) -> tuple[list[str], list[str]]:
    provides = [m.group(1)] if (m := re.search(r"^module\s+(\S+)", text, re.MULTILINE)) else []
    consumes = re.findall(
        r"^\s*(?:require\s+)?([\w.\-]+(?:/[\w.\-~]+)+)\s+v[\w.\-+]+", text, re.MULTILINE
    )
    return provides, [c for c in consumes if c not in provides]


def _cargo(text: str) -> tuple[list[str], list[str]]:
    data = tomllib.loads(text)
    package = data.get("package", {})
    provides = [package["name"]] if isinstance(package.get("name"), str) else []
    consumes: list[str] = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        consumes += [str(k) for k in (data.get(section) or {})]
    return provides, consumes


def _composer(text: str) -> tuple[list[str], list[str]]:
    data: Any = json.loads(text)
    if not isinstance(data, dict):
        return [], []
    consumes = [
        str(k)
        for section in ("require", "require-dev")
        for k in (data.get(section) or {})
        if k != "php" and not str(k).startswith("ext-")
    ]
    return ([data["name"]] if isinstance(data.get("name"), str) else []), consumes
