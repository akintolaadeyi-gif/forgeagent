import os

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX system directories")
@pytest.mark.parametrize("bad", ["/etc", "/usr", "/var", "/opt", "/System", "/Library", "/Applications"])
def test_system_directories_rejected_as_workspace(bad):
    from fastapi import HTTPException

    from app.api.routes import validate_workspace

    with pytest.raises(HTTPException) as e:
        validate_workspace(bad)
    assert e.value.status_code == 400


@pytest.mark.skipif(os.name == "nt", reason="POSIX defaults")
def test_defaults_are_not_windows_paths():
    from app.config import EnvSettings

    for name in ("llama_server_path", "models_dir"):
        assert not EnvSettings.model_fields[name].default.startswith("C:"), name


@pytest.mark.parametrize("cmd", ["python3 -m pytest -q", "pip3 install requests"])
def test_python3_commands_run_automatically(tmp_path, cmd):
    from app.app_settings import AppSettings
    from app.security import classify_command

    verdict, why = classify_command(cmd, tmp_path, AppSettings().autonomy)
    assert verdict == "auto", why


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell lookup")
def test_find_shell_falls_back_to_sh(monkeypatch):
    from app.tools import proc

    monkeypatch.setattr(proc.shutil, "which", lambda *a, **k: None)
    assert proc.find_shell() == (["/bin/sh", "-c"], "sh")


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv layout")
def test_venv_python_prefers_project_venv_then_python3(monkeypatch, tmp_path):
    from app.tools import exec_tools

    monkeypatch.setattr(exec_tools.shutil, "which", lambda *a, **k: None)
    assert exec_tools._venv_python(tmp_path) == "python3"
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    assert exec_tools._venv_python(tmp_path) == f'"{tmp_path / ".venv" / "bin" / "python"}"'
