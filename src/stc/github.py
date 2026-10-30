"""Keeps shallow clones of GitHub repositories in the folder of a source (`stc sync`).

The code is only ever read: a clone is a copy that STC manages (it is reset to the remote on every
sync) and nothing is pushed or executed. The token (read-only access to the contents is enough)
goes to `git` through the environment of that process and only for the host of the repository: it
is not put in a URL, written to `.git/config` or logged.
"""

import base64
import fnmatch
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from stc.sources import GitHubSource

_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')

Fetch = Callable[[str, Mapping[str, str]], tuple[object, str | None]]
"""(url, headers) -> (decoded JSON, the `Link` header)."""
Git = Callable[[list[str], Path | None, Mapping[str, str]], str]
"""(arguments, folder, extra environment) -> standard output; raises GitHubError."""


class GitHubError(Exception):
    pass


@dataclass(frozen=True)
class Repo:
    name: str
    clone_url: str
    default_branch: str


@dataclass
class Listing:
    repos: list[Repo] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)


@dataclass(frozen=True)
class RepoResult:
    name: str
    action: str  # cloned | updated | unchanged | would clone | would update | failed
    detail: str = ""


@dataclass
class SyncReport:
    results: list[RepoResult] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    not_listed: list[str] = field(default_factory=list)  # clones on disk that are not listed now

    @property
    def failed(self) -> list[RepoResult]:
        return [r for r in self.results if r.action == "failed"]


# --- the API -------------------------------------------------------------------------------------


def default_fetch(url: str, headers: Mapping[str, str]) -> tuple[object, str | None]:
    request = urllib.request.Request(url, headers=dict(headers))  # noqa: S310 (https only)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.load(response), response.headers.get("Link")
    except urllib.error.HTTPError as exc:
        raise GitHubError(_http_error(exc)) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GitHubError(f"GitHub could not be reached: {exc}") from exc


def _http_error(exc: urllib.error.HTTPError) -> str:
    if exc.code == 401:
        return "401: the token is missing, expired or invalid (STC_GITHUB_TOKEN)"
    if exc.code == 404:
        return "404: not found, or the token cannot see it"
    if exc.code in (403, 429):
        retry = exc.headers.get("Retry-After")
        reset = exc.headers.get("X-RateLimit-Reset")
        if exc.headers.get("X-RateLimit-Remaining") == "0" or retry:
            when = f"retry after {retry}s" if retry else f"limit resets at epoch {reset}"
            return f"{exc.code}: GitHub rate limit reached ({when})"
        return f"{exc.code}: access denied (permissions of the token, or SSO not authorized)"
    return f"HTTP {exc.code} from GitHub"


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "stc",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _pages(fetch: Fetch, url: str, headers: Mapping[str, str]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    next_url: str | None = url
    while next_url:
        data, link = fetch(next_url, headers)
        if not isinstance(data, list):
            raise GitHubError("Unexpected answer from GitHub (a list was expected)")
        items += [d for d in data if isinstance(d, dict)]
        match = _NEXT.search(link or "")
        next_url = match.group(1) if match else None
        if next_url and not next_url.startswith("https://"):
            raise GitHubError("GitHub paginated to a URL that is not https")
    return items


def list_repos(source: GitHubSource, token: str | None, fetch: Fetch = default_fetch) -> Listing:
    """The repositories of the source that pass its filters."""
    api, owner, headers = source.api_url, source.owner, _headers(token)
    raw: list[dict[str, object]] = []
    if source.repositories:
        for name in source.repositories:
            if not _REPO_NAME.match(name):
                raise GitHubError(f"Not a repository name: {name!r}")
            data, _ = fetch(f"{api}/repos/{owner}/{urllib.parse.quote(name)}", headers)
            if not isinstance(data, dict):
                raise GitHubError(f"Unexpected answer from GitHub for {owner}/{name}")
            raw.append(data)
    else:
        try:
            raw = _pages(fetch, f"{api}/orgs/{owner}/repos?per_page=100&type=all", headers)
        except GitHubError as exc:
            if not str(exc).startswith("404"):
                raise
            raw = _pages(fetch, _user_url(api, owner, token, headers, fetch), headers)
    out = Listing()
    for item in raw:
        name = str(item.get("name", ""))
        reason = _skip_reason(source, name, item)
        if reason:
            out.skipped.append((name, reason))
            continue
        out.repos.append(
            Repo(name, str(item["clone_url"]), str(item.get("default_branch") or "HEAD"))
        )
    out.repos.sort(key=lambda r: r.name)
    return out


def _user_url(
    api: str, owner: str, token: str | None, headers: Mapping[str, str], fetch: Fetch
) -> str:
    """`/users/{name}/repos` lists only public repositories. When the token belongs to this very
    user, `/user/repos` lists the private ones too."""
    if token:
        me, _ = fetch(f"{api}/user", headers)
        if isinstance(me, dict) and str(me.get("login", "")).lower() == owner.lower():
            return f"{api}/user/repos?per_page=100&affiliation=owner"
    return f"{api}/users/{owner}/repos?per_page=100&type=owner"


def _skip_reason(source: GitHubSource, name: str, item: Mapping[str, object]) -> str | None:
    if not _REPO_NAME.match(name) or name in (".", ".."):
        return "not a usable folder name"
    if not str(item.get("clone_url", "")).startswith("https://"):
        return "no https clone URL"
    if source.exclude_archived and item.get("archived"):
        return "archived"
    if source.exclude_forks and item.get("fork"):
        return "fork"
    if source.include and not any(fnmatch.fnmatchcase(name, p) for p in source.include):
        return "not in `include`"
    if any(fnmatch.fnmatchcase(name, p) for p in source.exclude):
        return "in `exclude`"
    return None


# --- git -----------------------------------------------------------------------------------------


def git_environment(token: str | None, clone_url: str) -> dict[str, str]:
    """The environment of one git process: no prompts, and the token as an HTTP header for the
    host of this repository only."""
    env = {"GIT_TERMINAL_PROMPT": "0"}
    if token and clone_url.startswith("https://"):
        origin = "https://" + urllib.parse.urlsplit(clone_url).netloc.split("@")[-1] + "/"
        credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env |= {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"http.{origin}.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credential}",
        }
    return env


def run_git(args: list[str], folder: Path | None, env: Mapping[str, str]) -> str:
    import os

    try:
        done = subprocess.run(  # noqa: S603
            ["git", "-c", "core.longpaths=true", *args],  # noqa: S607
            cwd=folder,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubError(f"git could not run: {type(exc).__name__}") from exc
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip().splitlines()
        text = message[-1] if message else f"git exited with {done.returncode}"
        raise GitHubError(_scrub(text, env))
    return done.stdout.strip()


def _scrub(text: str, env: Mapping[str, str]) -> str:
    for value in env.values():
        if value.startswith("Authorization: "):
            text = text.replace(value.split("Basic ", 1)[-1], "***")
    return text


# --- the sync ------------------------------------------------------------------------------------


def sync_source(
    source: GitHubSource,
    folder: Path,
    token: str | None,
    *,
    fetch: Fetch = default_fetch,
    git: Git = run_git,
    dry_run: bool = False,
) -> SyncReport:
    """List the repositories and clone or update each one under `folder/<name>`. A repository that
    fails does not stop the others. With `dry_run` nothing is written."""
    listing = list_repos(source, token, fetch)
    report = SyncReport(skipped=listing.skipped)
    wanted = {r.name for r in listing.repos}
    if folder.is_dir():
        report.not_listed = sorted(
            p.name for p in folder.iterdir() if p.is_dir() and p.name not in wanted
        )
    if not dry_run:
        folder.mkdir(parents=True, exist_ok=True)
    for repo in listing.repos:
        try:
            report.results.append(_sync_repo(source, folder, repo, token, git, dry_run))
        except GitHubError as exc:
            report.results.append(RepoResult(repo.name, "failed", str(exc)))
    return report


def _sync_repo(
    source: GitHubSource, folder: Path, repo: Repo, token: str | None, git: Git, dry_run: bool
) -> RepoResult:
    target = folder / repo.name
    env = git_environment(token, repo.clone_url)
    branch = source.branch
    if not target.exists():
        if dry_run:
            return RepoResult(repo.name, "would clone")
        args = ["clone", "--depth", "1", "--no-tags", "--single-branch"]
        if branch:
            args += ["--branch", branch]
        git([*args, "--", repo.clone_url, str(target)], None, env)
        return RepoResult(repo.name, "cloned", _head(git, target)[:7])
    if not (target / ".git").exists():
        raise GitHubError(f"{target} exists and is not a clone made by `stc sync`")
    if dry_run:
        return RepoResult(repo.name, "would update")
    before = _head(git, target)
    git(["fetch", "--depth", "1", "--no-tags", "origin", branch or "HEAD"], target, env)
    git(["reset", "--hard", "-q", "FETCH_HEAD"], target, env)
    git(["clean", "-fdq"], target, env)
    after = _head(git, target)
    if before == after:
        return RepoResult(repo.name, "unchanged", after[:7])
    return RepoResult(repo.name, "updated", f"{before[:7]} -> {after[:7]}")


def _head(git: Git, folder: Path) -> str:
    return git(["rev-parse", "HEAD"], folder, {})
