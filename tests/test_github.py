"""GitHub as a source: listing the repositories and keeping shallow clones of them (IDX-01).

The API is a fake and the repositories are real git repositories on disk, so cloning, fetching and
resetting are the real thing; only the URL is swapped for a local path."""

import subprocess
import urllib.error
from collections.abc import Mapping
from email.message import Message
from pathlib import Path

import pytest
from typer.testing import CliRunner

import stc.github as github
from stc.cli import app
from stc.config import get_settings
from stc.github import (
    GitHubError,
    git_environment,
    list_repos,
    run_git,
    sync_source,
)
from stc.indexer.pipeline import scan_roots
from stc.sources import GitHubSource, LocalSource, Roots, SourcesError, folders_of, read_sources

runner = CliRunner()
TOKEN = "ghp_SECRET_TOKEN_VALUE_123"
ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def sh(folder: Path, *args: str) -> str:
    import os

    done = subprocess.run(
        ["git", *args], cwd=folder, env={**os.environ, **ENV}, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def commit(repo: Path, files: Mapping[str, str], message: str = "change") -> str:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", message)
    return sh(repo, "rev-parse", "HEAD")


def make_upstream(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / "upstream" / name
    repo.mkdir(parents=True)
    sh(repo, "init", "-q", "-b", "main")
    commit(repo, {"pyproject.toml": f'[project]\nname = "{name}"\n', "app.py": "x = 1\n"})
    return repo


def item(name: str, **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "clone_url": f"https://github.com/acme/{name}.git",
        "default_branch": "main",
        "archived": False,
        "fork": False,
        **extra,
    }


class FakeGitHub:
    """Answers the calls the listing makes and records what it was asked. `me` is the user the
    token belongs to; `/users/<name>/repos` lists only the public repositories, as GitHub does."""

    def __init__(
        self,
        repos: list[dict[str, object]],
        *,
        org: bool = True,
        per_page: int = 100,
        me: str = "someone-else",
    ):
        self.repos, self.org, self.per_page, self.me = repos, org, per_page, me
        self.calls: list[tuple[str, Mapping[str, str]]] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> tuple[object, str | None]:
        self.calls.append((url, headers))
        base = "https://api.github.com"
        if url.startswith(f"{base}/repos/acme/"):
            name = url.rsplit("/", 1)[1]
            found = [r for r in self.repos if r["name"] == name]
            if not found:
                raise GitHubError("404: not found, or the token cannot see it")
            return found[0], None
        if url == f"{base}/user":
            return {"login": self.me}, None
        if url.startswith(f"{base}/orgs/acme/repos"):
            if not self.org:
                raise GitHubError("404: not found, or the token cannot see it")
            return self._page(url, self.repos)
        if url.startswith(f"{base}/users/acme/repos"):
            return self._page(url, [r for r in self.repos if not r.get("private")])
        if url.startswith(f"{base}/user/repos"):
            return self._page(url, self.repos)
        raise AssertionError(f"unexpected call {url}")

    def _page(self, url: str, repos: list[dict[str, object]]) -> tuple[object, str | None]:
        page = int(url.split("&page=")[1]) if "&page=" in url else 1
        start = (page - 1) * self.per_page
        more = start + self.per_page < len(repos)
        link = f'<{url.split("&page=")[0]}&page={page + 1}>; rel="next"' if more else None
        return repos[start : start + self.per_page], link


def source(**fields: object) -> GitHubSource:
    return GitHubSource.model_validate(
        {"name": "acme", "type": "github", "owner": "acme", **fields}
    )


# --- configuration -------------------------------------------------------------------------------


def test_github_sources_are_declared_in_sources_toml(tmp_path: Path) -> None:
    (tmp_path / "sources.toml").write_text(
        '[[sources]]\nname = "acme"\ntype = "github"\nowner = "acme"\ninclude = ["svc-*"]\n'
        'branch = "main"\n\n[[sources]]\nname = "old"\npath = "legacy"\n',
        encoding="utf-8",
    )
    sources = read_sources(tmp_path / "sources.toml")
    acme = sources["acme"]
    assert isinstance(acme, GitHubSource) and acme.include == ["svc-*"] and acme.exclude_archived
    assert isinstance(sources["old"], LocalSource)
    folders = folders_of(sources, tmp_path / "data")
    assert folders["acme"] == tmp_path / "data" / "sources" / "acme"
    assert folders["old"] == (tmp_path / "legacy").resolve()


@pytest.mark.parametrize(
    "fields, message",
    [
        ('owner = "a/b"', "organization or user"),
        ('owner = "acme"\napi_url = "http://ghe.local/api"', "https"),
        ('owner = "acme"\npath = "x"', "path"),
        ("", "owner"),
        ('owner = "acme"\ntoken = "t"', "token"),
    ],
)
def test_a_bad_github_source_says_what_is_wrong(tmp_path: Path, fields: str, message: str) -> None:
    (tmp_path / "sources.toml").write_text(
        f'[[sources]]\nname = "acme"\ntype = "github"\n{fields}\n', encoding="utf-8"
    )
    with pytest.raises(SourcesError, match=message):
        read_sources(tmp_path / "sources.toml")


# --- listing -------------------------------------------------------------------------------------


def test_the_listing_applies_the_filters_and_says_why_a_repository_is_left_out() -> None:
    fake = FakeGitHub(
        [
            item("svc-b"),
            item("svc-a"),
            item("svc-old", archived=True),
            item("svc-fork", fork=True),
            item("web"),
            item("svc-tmp"),
            item("bad name"),
        ]
    )
    listing = list_repos(
        source(include=["svc-*"], exclude=["*-tmp"], exclude_forks=True), TOKEN, fake
    )
    assert [r.name for r in listing.repos] == ["svc-a", "svc-b"]
    assert dict(listing.skipped) == {
        "svc-old": "archived",
        "svc-fork": "fork",
        "web": "not in `include`",
        "svc-tmp": "in `exclude`",
        "bad name": "not a usable folder name",
    }
    url, headers = fake.calls[0]
    assert url.startswith("https://api.github.com/orgs/acme/repos")
    assert headers["Authorization"] == f"Bearer {TOKEN}"


def test_the_listing_follows_pages_and_falls_back_to_a_user() -> None:
    repos = [item(f"r{n:02}") for n in range(7)]
    fake = FakeGitHub(repos, org=False, per_page=3)
    listing = list_repos(source(exclude_archived=False), None, fake)
    assert [r.name for r in listing.repos] == [f"r{n:02}" for n in range(7)]
    assert any("/users/acme/repos" in url for url, _ in fake.calls)
    assert all("Authorization" not in h for _, h in fake.calls)  # no token, none sent


def test_the_private_repositories_of_a_user_need_the_token_of_that_user() -> None:
    repos = [item("open"), item("secret-project", private=True)]

    def names(fake: FakeGitHub, token: str | None) -> list[str]:
        return [r.name for r in list_repos(source(), token, fake).repos]

    # `/users/<name>/repos` only ever lists the public ones...
    assert names(FakeGitHub(repos, org=False), None) == ["open"]
    assert names(FakeGitHub(repos, org=False, me="someone-else"), TOKEN) == ["open"]
    # ...so for the user the token belongs to, `/user/repos` is used.
    mine = FakeGitHub(repos, org=False, me="ACME")
    assert names(mine, TOKEN) == ["open", "secret-project"]
    assert any("/user/repos?per_page=100&affiliation=owner" in url for url, _ in mine.calls)
    assert not any("/users/acme/repos" in url for url, _ in mine.calls)


def test_named_repositories_are_fetched_one_by_one() -> None:
    fake = FakeGitHub([item("a"), item("b"), item("c")])
    listing = list_repos(source(repositories=["c", "a"]), TOKEN, fake)
    assert [r.name for r in listing.repos] == ["a", "c"]
    assert not any("/orgs/" in url for url, _ in fake.calls)
    with pytest.raises(GitHubError, match="404"):
        list_repos(source(repositories=["ghost"]), TOKEN, fake)
    with pytest.raises(GitHubError, match="Not a repository name"):
        list_repos(source(repositories=["../x"]), TOKEN, fake)


def test_pagination_cannot_lead_the_token_to_another_scheme() -> None:
    def fetch(url: str, headers: Mapping[str, str]) -> tuple[object, str | None]:
        return [item("a")], '<http://evil.example/next>; rel="next"'

    with pytest.raises(GitHubError, match="not https"):
        list_repos(source(), TOKEN, fetch)


@pytest.mark.parametrize(
    "code, headers, text",
    [
        (401, {}, "token is missing"),
        (404, {}, "token cannot see it"),
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "99"}, "rate limit"),
        (429, {"Retry-After": "30"}, "retry after 30s"),
        (403, {}, "permissions of the token"),
        (500, {}, "HTTP 500"),
    ],
)
def test_http_errors_are_explained(code: int, headers: dict[str, str], text: str) -> None:
    message = Message()
    for key, value in headers.items():
        message[key] = value
    error = urllib.error.HTTPError("https://api.github.com/x", code, "", message, None)
    assert text in github._http_error(error)


# --- the token -----------------------------------------------------------------------------------


def test_the_token_goes_to_git_through_the_environment_for_its_own_host_only() -> None:
    env = git_environment(TOKEN, "https://github.com/acme/a.git")
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_CONFIG_KEY_0"] == (
        "http.https://github.com/.extraHeader"
    )
    assert TOKEN not in env["GIT_CONFIG_VALUE_0"]  # it is base64 of user:token, not the token
    assert git_environment(None, "https://github.com/acme/a.git").keys() == {"GIT_TERMINAL_PROMPT"}
    assert "GIT_CONFIG_COUNT" not in git_environment(TOKEN, "file:///tmp/a")
    assert "GIT_CONFIG_COUNT" not in git_environment(TOKEN, "http://github.com/a.git")


def test_a_git_failure_never_shows_the_credential(tmp_path: Path) -> None:
    env = git_environment(TOKEN, "https://github.com/acme/a.git")
    credential = env["GIT_CONFIG_VALUE_0"].split("Basic ")[1]
    assert github._scrub(f"fatal: bad header {credential}", env) == "fatal: bad header ***"
    with pytest.raises(GitHubError) as caught:
        run_git(["clone", "--", str(tmp_path / "nowhere"), str(tmp_path / "x")], None, env)
    assert credential not in str(caught.value) and TOKEN not in str(caught.value)


# --- syncing -------------------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Upstream repositories on disk, a fake API listing them, and a git that swaps the URL."""
    upstream = {name: make_upstream(tmp_path, name) for name in ("svc-a", "svc-b")}
    fake = FakeGitHub([item(name) for name in upstream])
    seen: list[tuple[list[str], Mapping[str, str]]] = []

    def git(args: list[str], folder: Path | None, env: Mapping[str, str]) -> str:
        seen.append((args, env))
        swapped = [
            upstream.get(a.split("/")[-1].removesuffix(".git"), tmp_path / "missing").as_uri()
            if a.startswith("https://github.com/")
            else a
            for a in args
        ]
        # The real thing, minus the network: the header is for https and not needed for a path.
        return run_git(
            swapped, folder, {k: v for k, v in env.items() if k == "GIT_TERMINAL_PROMPT"}
        )

    return upstream, fake, git, seen, tmp_path / "data" / "sources" / "acme"


def test_clone_then_update_then_nothing_to_do(world) -> None:  # type: ignore[no-untyped-def]
    upstream, fake, git, seen, folder = world
    first = sync_source(source(), folder, TOKEN, fetch=fake, git=git)
    assert [(r.name, r.action) for r in first.results] == [("svc-a", "cloned"), ("svc-b", "cloned")]
    assert (folder / "svc-a" / "app.py").read_text(encoding="utf-8") == "x = 1\n"
    assert sh(folder / "svc-a", "rev-parse", "--is-shallow-repository") == "true"
    assert TOKEN not in " ".join(a for args, _ in seen for a in args)  # not on any command line

    again = sync_source(source(), folder, TOKEN, fetch=fake, git=git)
    assert [r.action for r in again.results] == ["unchanged", "unchanged"]

    new = commit(upstream["svc-a"], {"app.py": "x = 2\n"})
    (folder / "svc-b" / "app.py").write_text("tampered\n", encoding="utf-8")
    (folder / "svc-b" / "stray.txt").write_text("left over\n", encoding="utf-8")
    third = sync_source(source(), folder, TOKEN, fetch=fake, git=git)
    actions = {r.name: r for r in third.results}
    assert actions["svc-a"].action == "updated" and actions["svc-a"].detail.endswith(new[:7])
    assert (folder / "svc-a" / "app.py").read_text(encoding="utf-8") == "x = 2\n"
    assert (folder / "svc-b" / "app.py").read_text(encoding="utf-8") == "x = 1\n"  # reset
    assert not (folder / "svc-b" / "stray.txt").exists()


def test_a_branch_can_be_chosen(world) -> None:  # type: ignore[no-untyped-def]
    upstream, fake, git, _, folder = world
    sh(upstream["svc-a"], "checkout", "-q", "-b", "release")
    commit(upstream["svc-a"], {"app.py": "x = 'release'\n"})
    sh(upstream["svc-a"], "checkout", "-q", "main")
    sync_source(source(branch="release"), folder, TOKEN, fetch=fake, git=git)
    assert (folder / "svc-a" / "app.py").read_text(encoding="utf-8") == "x = 'release'\n"


def test_one_failure_does_not_stop_the_others(world) -> None:  # type: ignore[no-untyped-def]
    upstream, fake, git, _, folder = world
    fake.repos.insert(0, item("svc-0"))  # listed, but there is nothing to clone
    (folder / "svc-b").mkdir(parents=True)  # a folder that is not a clone
    report = sync_source(source(), folder, TOKEN, fetch=fake, git=git)
    by_name = {r.name: r for r in report.results}
    assert by_name["svc-0"].action == "failed" and by_name["svc-b"].action == "failed"
    assert "not a clone made by" in by_name["svc-b"].detail
    assert by_name["svc-a"].action == "cloned"
    assert [r.name for r in report.failed] == ["svc-0", "svc-b"]


def test_dry_run_writes_nothing_and_clones_no_longer_listed_are_reported(world) -> None:  # type: ignore[no-untyped-def]
    _, fake, git, seen, folder = world
    dry = sync_source(source(), folder, TOKEN, fetch=fake, git=git, dry_run=True)
    assert [r.action for r in dry.results] == ["would clone", "would clone"]
    assert not folder.exists() and not seen
    sync_source(source(), folder, TOKEN, fetch=fake, git=git)
    (folder / "gone").mkdir()
    fake.repos[:] = [r for r in fake.repos if r["name"] != "svc-b"]
    report = sync_source(source(), folder, TOKEN, fetch=fake, git=git, dry_run=True)
    assert [r.action for r in report.results] == ["would update"]
    assert report.not_listed == ["gone", "svc-b"]


def test_a_synced_source_is_indexed_like_any_folder_with_the_commit_as_version(world) -> None:  # type: ignore[no-untyped-def]
    upstream, fake, git, _, folder = world
    sync_source(source(), folder, TOKEN, fetch=fake, git=git)
    scanned = scan_roots(Roots({"acme": folder}))
    versions = {a.spec.name: a.code_version for a in scanned}
    assert versions == {
        "svc-a": sh(upstream["svc-a"], "rev-parse", "HEAD"),
        "svc-b": sh(upstream["svc-b"], "rev-parse", "HEAD"),
    }
    assert {a.spec.source for a in scanned} == {"acme"}


# --- command line --------------------------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    (tmp_path / "sources.toml").write_text(
        '[[sources]]\nname = "acme"\ntype = "github"\nowner = "acme"\n\n'
        '[[sources]]\nname = "old"\npath = "legacy"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)  # no .env, so local settings cannot leak in
    monkeypatch.delenv("STC_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("STC_ROOT", raising=False)
    monkeypatch.setenv("STC_SOURCES", str(tmp_path / "sources.toml"))
    monkeypatch.setenv("STC_DATA", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_sync_reports_each_repository_and_fails_when_one_did(cli_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str | None, bool]] = []

    def fake_sync(config, folder, token, *, dry_run=False):  # type: ignore[no-untyped-def]
        calls.append((config.owner, token, dry_run))
        return github.SyncReport(
            results=[
                github.RepoResult("svc-a", "cloned", "abc1234"),
                github.RepoResult("svc-b", "failed", "boom"),
            ],
            skipped=[("old-one", "archived")],
            not_listed=["gone"],
        )

    monkeypatch.setattr(github, "sync_source", fake_sync)
    monkeypatch.setenv("STC_GITHUB_TOKEN", TOKEN)
    get_settings.cache_clear()
    result = runner.invoke(app, ["sync", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "cloned: svc-a abc1234" in result.output and "failed: svc-b boom" in result.output
    assert "skipped: old-one (archived)" in result.output and "gone" in result.output
    assert "1 failure(s)" in result.output
    assert calls == [("acme", TOKEN, True)] and TOKEN not in result.output


def test_sync_complains_about_a_missing_token_and_unknown_sources(cli_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(github, "sync_source", lambda *a, **k: github.SyncReport(), raising=True)
    ok = runner.invoke(app, ["sync"])
    assert ok.exit_code == 0 and "STC_GITHUB_TOKEN is not set" in ok.output
    assert runner.invoke(app, ["sync", "--source", "ghost"]).exit_code == 2
    local = runner.invoke(app, ["sync", "--source", "old"])
    assert local.exit_code == 2 and "No GitHub source to sync" in local.output


def test_indexing_a_source_that_was_never_synced_says_what_to_run(cli_env) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["detect", "--source", "acme"])
    assert result.exit_code == 2 and "stc sync" in result.output
