from outpost.cli import main


def test_init_takes_a_folder_argument(tmp_path):
    target = tmp_path / "my-platform"
    assert main(["init", str(target)]) == 0
    assert (target / "servers.yml").exists()
    assert (target / "needs" / "demo.yml").exists()


def test_init_creates_platform(tmp_path, capsys):
    assert main(["--dir", str(tmp_path), "init"]) == 0
    assert (tmp_path / "servers.yml").exists() and (tmp_path / "needs" / "demo.yml").exists()
    assert (tmp_path / "checks" / "hello-python" / "Dockerfile").exists()
    assert main(["--dir", str(tmp_path), "init"]) == 0
    assert "already has" in capsys.readouterr().out


def test_needs_lists_platform_files(workspace_dir, capsys):
    assert main(["--dir", str(workspace_dir), "needs"]) == 0
    out = capsys.readouterr().out
    assert "image  python:3.12-slim" in out and "file   gitea" in out and "file   docker " in out
    assert "k3s" not in out  # template only has a compose infrastructure


def test_missing_platform_is_explained(tmp_path, capsys):
    assert main(["--dir", str(tmp_path), "needs"]) == 1
    assert "outpost init" in capsys.readouterr().err


def test_infra_needs_name(workspace_dir, capsys):
    assert main(["--dir", str(workspace_dir), "infra", "setup"]) == 2
    assert main(["--dir", str(workspace_dir), "infra", "list"]) == 0
    assert "dev" in capsys.readouterr().out
    assert main(["--dir", str(workspace_dir), "infra", "verify", "nope"]) == 1
    assert "No infrastructure named 'nope'" in capsys.readouterr().err


def test_check_without_gitea_state(workspace_dir, capsys):
    assert main(["--dir", str(workspace_dir), "check", "--infra", "dev"]) == 1
    assert "gitea setup" in capsys.readouterr().err


def test_upload_explains_what_is_missing(workspace_dir, capsys):
    assert main(["--dir", str(workspace_dir), "upload"]) == 1
    assert "outpost fetch" in capsys.readouterr().err
    (workspace_dir / "box").mkdir()
    assert main(["--dir", str(workspace_dir), "upload"]) == 1
    assert "gitea setup" in capsys.readouterr().err
