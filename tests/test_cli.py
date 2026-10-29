from typer.testing import CliRunner

from stc import __version__
from stc.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_unimplemented_command_fails_clearly() -> None:
    result = runner.invoke(app, ["index"])
    assert result.exit_code == 1
