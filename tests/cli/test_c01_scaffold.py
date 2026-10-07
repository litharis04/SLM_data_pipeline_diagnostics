from __future__ import annotations

import pytest

from data_pipeline_diagnostics.cli import app

COMMANDS = ["connect", "list", "open", "seed", "create", "delete"]

# NB: all six commands are implemented (C03/C07/C08/C09/C10/C12); the C01 stub
# phase is over and its valid-but-unimplemented test is retired.


def _assert_no_workspace(tmp_path):
    assert not (tmp_path / "pipeline_workspace").exists()


def test_root_help_no_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        app.main(["--help"])
    assert exc.value.code == 0
    out, _ = capsys.readouterr()
    assert "plgen" in out
    _assert_no_workspace(tmp_path)


@pytest.mark.parametrize("command", COMMANDS)
def test_command_help_no_side_effects(tmp_path, monkeypatch, capsys, command):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        app.main([command, "--help"])
    assert exc.value.code == 0
    out, _ = capsys.readouterr()
    assert command in out or "usage" in out.lower()
    _assert_no_workspace(tmp_path)


def test_version_no_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        app.main(["--version"])
    assert exc.value.code == 0
    out, _ = capsys.readouterr()
    assert app.get_version() in out
    _assert_no_workspace(tmp_path)


@pytest.mark.parametrize(
    "argv",
    [
        ["bogus"],
        ["list", "--bogus-flag"],
        ["open"],
        ["seed", "only_one"],
        ["connect", "--provider", "openrouter"],
        ["create"],
        [],
    ],
)
def test_invalid_input_exits_2(tmp_path, monkeypatch, capsys, argv):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        app.main(argv)
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert err.strip() != ""
    assert "Traceback" not in err
    assert "Traceback" not in out
    _assert_no_workspace(tmp_path)


def test_workspace_default_resolves_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict = {}

    def fake_dispatch(parsed, workspace):
        captured["workspace"] = workspace
        return 0

    monkeypatch.setattr(app, "dispatch", fake_dispatch)
    assert app.main(["list"]) == 0
    assert captured["workspace"].is_absolute()
    assert captured["workspace"] == (tmp_path / "pipeline_workspace").resolve()
    assert not (tmp_path / "pipeline_workspace").exists()


def test_workspace_override_persistent_location(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict = {}
    custom = tmp_path / "custom_ws"

    def fake_dispatch(parsed, workspace):
        captured["workspace"] = workspace
        return 0

    monkeypatch.setattr(app, "dispatch", fake_dispatch)
    assert app.main(["--workspace", str(custom), "list"]) == 0
    assert captured["workspace"].is_absolute()
    assert captured["workspace"] == custom.resolve()


def test_keyboard_interrupt_returns_130(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    def boom(parsed, workspace):
        raise KeyboardInterrupt

    monkeypatch.setattr(app, "dispatch", boom)
    code = app.main(["list"])
    assert code == 130
    _, err = capsys.readouterr()
    assert "Traceback" not in err


def test_unexpected_exception_returns_5_with_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    def boom(parsed, workspace):
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "dispatch", boom)
    code = app.main(["list"])
    assert code == 5
    _, err = capsys.readouterr()
    assert "Traceback" in err
