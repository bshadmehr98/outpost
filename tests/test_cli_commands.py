"""The command table in outpost/cli, the backup folder default, and upload's signature warnings."""

import re

import pytest

import outpost.cli
from outpost.cli import build_parser, main
from outpost.cli.main import COMMANDS
from outpost.workspace import STATE_GITEA, Workspace


def test_every_command_module_declares_its_parser_and_run():
    names = []
    for command in COMMANDS:
        assert callable(command.add_parser) and callable(command.run) and command.__doc__
        names.append(command.__name__.rsplit(".", 1)[1])
    choices = build_parser()._subparsers._group_actions[0].choices  # pyright: ignore[reportOptionalMemberAccess]
    assert list(choices) == names  # the parser lists the commands in COMMANDS' order
    for name in names:
        assert build_parser().parse_args(_smallest_command_line(name)).run.__module__ == f"outpost.cli.{name}"


def test_the_help_lists_the_flow_in_the_order_of_the_docstring():
    listed = re.findall(r"^  outpost (\w+)", outpost.cli.__doc__ or "", re.M)
    assert set(listed) == {command.__name__.rsplit(".", 1)[1] for command in COMMANDS}
    assert build_parser().description == outpost.cli.__doc__


def _smallest_command_line(name: str) -> list[str]:
    needs_more = {
        "gitea": ["info"],
        "infra": ["list"],
        "check": ["--infra", "dev"],
        "demo": ["status"],
        "egress": ["gitea", "status"],
        "proxy": ["verify"],
        "lab": ["down"],
        "pki": ["rotate"],
    }
    return [name, *needs_more.get(name, [])]


@pytest.mark.parametrize("out", [None, "elsewhere"])
def test_gitea_backup_goes_to_the_workspace_backups_by_default(workspace_dir, tmp_path, monkeypatch, out):
    import outpost.cli.gitea as gitea_command

    seen = []
    monkeypatch.setattr(gitea_command, "backup", lambda workspace, servers, out_dir, **kw: seen.append(out_dir))
    monkeypatch.chdir(tmp_path)  # not the workspace: the default must not depend on where outpost runs
    argv = ["--dir", str(workspace_dir), "gitea", "backup"] + (["--out", out] if out else [])
    assert main(argv) == 0
    expected = Workspace(workspace_dir).backups_dir if out is None else tmp_path / out
    assert seen[0].resolve() == expected.resolve()


def test_upload_warns_when_it_cannot_check_the_signature(workspace_dir, capsys):
    workspace = Workspace(workspace_dir)
    workspace.box_dir.mkdir()
    assert main(["--dir", str(workspace_dir), "upload"]) == 1  # Gitea is not set up; the warning comes first
    assert "warning: the box is not signed" in capsys.readouterr().out
    (workspace.box_dir / "manifest.sig").write_bytes(b"sig")
    assert main(["--dir", str(workspace_dir), "upload"]) == 1
    captured = capsys.readouterr()
    assert "warning: cannot check the box's signature: pki/box.pub is missing" in captured.out
    assert "gitea setup" in captured.err
    workspace.write_state(STATE_GITEA, {"ip": "10.0.0.5"})  # still no token: fails after the warning
    assert main(["--dir", str(workspace_dir), "upload"]) == 1
