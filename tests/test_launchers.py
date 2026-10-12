"""Launcher regression checks without installing packages or starting a server.

Windows branches are simulated here; native Windows execution belongs to CI.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call
import json
import subprocess

import pytest

import setup_local
import start


@pytest.fixture
def windows_project(tmp_path, monkeypatch):
    root = tmp_path / "검수 project"
    root.mkdir()
    environment = root / ".venv"
    python = environment / "Scripts" / "python.exe"
    monkeypatch.setattr(setup_local, "ROOT", root)
    monkeypatch.setattr(start, "ROOT", root)
    monkeypatch.setattr(start.sys, "platform", "win32")
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(start.sys, "prefix", str(environment))
    monkeypatch.setattr(start.sys, "executable", str(python))
    monkeypatch.setattr(start.sys, "argv", ["setup_local.py"])
    npm = r"C:\Program Files\nodejs\npm.cmd"
    which = Mock(return_value=npm)
    monkeypatch.setattr(setup_local.shutil, "which", which)
    probe = json.dumps({"version": [3, 12], "prefix": str(environment), "base_prefix": str(root / "base_python")})
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=probe))
    monkeypatch.setattr(subprocess, "run", run)
    child = Mock()
    child.wait.return_value = 0
    popen = Mock(return_value=child)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.delenv("BARO_REVIEW_PROJECT_ENV", raising=False)
    builder = Mock()
    monkeypatch.setattr(setup_local.venv, "EnvBuilder", Mock(return_value=builder))
    return SimpleNamespace(root=root, environment=environment, python=python,
                           npm=npm, which=which, run=run, builder=builder,
                           probe=probe, child=child, popen=popen)


def existing_environment(project):
    project.python.parent.mkdir(parents=True)
    project.python.write_bytes(b"existing virtual environment")
    marker = project.environment / "keep.txt"
    marker.write_text("existing environment must be preserved", encoding="utf-8")
    return marker


def built_frontend(project):
    entry = project.root / "frontend" / "dist" / "index.html"
    entry.parent.mkdir(parents=True)
    entry.write_text("<!doctype html>", encoding="utf-8")


def test_setup_rejects_existing_python_311_before_any_install(windows_project):
    project = windows_project
    marker = existing_environment(project)
    details = json.loads(project.probe)
    details["version"] = [3, 11]
    project.run.return_value.stdout = json.dumps(details)

    with pytest.raises(SystemExit, match="Python 3.11"):
        setup_local.main()

    assert project.run.call_count == 1
    assert project.run.call_args.args[0][:2] == [str(project.python), "-c"]
    project.builder.create.assert_not_called()
    assert marker.read_text(encoding="utf-8") == "existing environment must be preserved"


@pytest.mark.parametrize("invalid_prefix", ["external_environment", "same_as_base"])
def test_setup_never_installs_into_an_external_or_nonvirtual_python(windows_project, invalid_prefix):
    project = windows_project
    marker = existing_environment(project)
    details = json.loads(project.probe)
    if invalid_prefix == "external_environment":
        details["prefix"] = str(project.root / "django_env")
    else:
        details["base_prefix"] = details["prefix"]
    project.run.return_value.stdout = json.dumps(details)

    with pytest.raises(SystemExit, match="외부 Python 환경에는 패키지를 설치하지 않습니다"):
        setup_local.main()

    assert project.run.call_count == 1
    assert project.run.call_args.args[0][:2] == [str(project.python), "-c"]
    project.builder.create.assert_not_called()
    assert marker.read_text(encoding="utf-8") == "existing environment must be preserved"


def test_setup_preserves_environment_from_another_os(windows_project):
    project = windows_project
    other_python = project.environment / "bin" / "python"
    other_python.parent.mkdir(parents=True)
    other_python.write_bytes(b"Unix environment")

    with pytest.raises(SystemExit, match="자동으로 삭제하지 않습니다"):
        setup_local.main()

    assert other_python.read_bytes() == b"Unix environment"
    project.builder.create.assert_not_called()
    project.run.assert_not_called()


def test_setup_preserves_environment_with_unusable_interpreter(windows_project):
    project = windows_project
    marker = existing_environment(project)
    project.run.side_effect = OSError("interpreter is not executable")

    with pytest.raises(SystemExit, match="Python을 실행하지 못했습니다"):
        setup_local.main()

    assert marker.is_file()
    assert project.run.call_count == 1
    project.builder.create.assert_not_called()


def test_windows_setup_uses_scripts_python_and_npm_cmd(windows_project, capsys):
    project = windows_project

    def create_environment(path):
        assert Path(path) == project.environment
        project.python.parent.mkdir(parents=True)
        project.python.touch()

    project.builder.create.side_effect = create_environment
    setup_local.main()

    project.which.assert_called_once_with("npm.cmd")
    project.builder.create.assert_called_once_with(project.environment)
    commands = [call.args[0] for call in project.run.call_args_list]
    assert commands[1:] == [
        [str(project.python), "-m", "pip", "install", "-r", "requirements-dev.txt"],
        [project.npm, "--prefix", "frontend", "ci"],
        [project.npm, "--prefix", "frontend", "run", "build"],
    ]
    assert all(call.kwargs["cwd"] == project.root for call in project.run.call_args_list)
    assert r".\run.cmd" in capsys.readouterr().out


def test_install_failure_stops_build_and_model_download(windows_project, monkeypatch, capsys):
    project = windows_project
    existing_environment(project)
    monkeypatch.setattr(setup_local.sys, "argv", ["setup_local.py", "--download-models"])
    project.run.side_effect = [
        subprocess.CompletedProcess([], 0, stdout=project.probe),
        subprocess.CalledProcessError(1, [str(project.python), "-m", "pip", "install"]),
    ]

    with pytest.raises(SystemExit, match="설치가 중단되었습니다"):
        setup_local.main()

    assert project.run.call_count == 2
    assert project.run.call_args.args[0][1:4] == ["-m", "pip", "install"]
    assert "설치 완료" not in capsys.readouterr().out


def test_start_uses_project_environment_instead_of_activated_one(windows_project, monkeypatch):
    project = windows_project
    existing_environment(project)
    monkeypatch.setattr(start.sys, "prefix", str(project.root / "django_env"))
    arguments = ["--host", "0.0.0.0", "--port", "8001"]
    monkeypatch.setattr(start.sys, "argv", ["start.py", *arguments])
    project.child.wait.return_value = 7
    monkeypatch.setenv("RUN_TARGET", "local")
    load_dependency = Mock(side_effect=AssertionError("Do not import server packages in the wrong environment"))
    monkeypatch.setattr(start.importlib, "import_module", load_dependency)

    with pytest.raises(SystemExit) as failure:
        start.main()

    assert failure.value.code == 7
    project.popen.assert_called_once()
    assert project.popen.call_args.args[0] == [str(project.python), str(project.root / "start.py"), *arguments]
    assert project.popen.call_args.kwargs["cwd"] == project.root
    child_env = project.popen.call_args.kwargs["env"]
    assert child_env["BARO_REVIEW_PROJECT_ENV"] == str(project.environment.resolve())
    assert child_env["RUN_TARGET"] == "local"
    project.child.wait.assert_called_once_with()
    project.child.terminate.assert_not_called()
    project.run.assert_not_called()
    load_dependency.assert_not_called()


def test_start_stops_recursive_relaunch_when_project_interpreter_is_broken(windows_project, monkeypatch):
    project = windows_project
    existing_environment(project)
    monkeypatch.setattr(start.sys, "prefix", str(project.root / "django_env"))
    monkeypatch.setenv("BARO_REVIEW_PROJECT_ENV", str(project.environment.resolve()))

    with pytest.raises(SystemExit, match="반복 실행을 중단합니다") as failure:
        start.main()

    assert r"py -3.12 .\setup_local.py --download-models" in str(failure.value)
    project.popen.assert_not_called()
    project.run.assert_not_called()


def test_first_ctrl_c_waits_for_graceful_server_shutdown(windows_project, monkeypatch):
    project = windows_project
    existing_environment(project)
    monkeypatch.setattr(start.sys, "prefix", str(project.root / "django_env"))
    project.child.wait.side_effect = [KeyboardInterrupt(), 0]

    with pytest.raises(SystemExit) as failure:
        start.main()

    assert failure.value.code == 130
    assert project.child.wait.call_count == 2
    project.child.terminate.assert_not_called()
    project.child.kill.assert_not_called()


def test_second_ctrl_c_explicitly_terminates_and_reaps_child(windows_project, monkeypatch):
    project = windows_project
    existing_environment(project)
    monkeypatch.setattr(start.sys, "prefix", str(project.root / "django_env"))
    project.child.wait.side_effect = [KeyboardInterrupt(), KeyboardInterrupt(), 1]

    with pytest.raises(SystemExit) as failure:
        start.main()

    assert failure.value.code == 130
    assert project.child.mock_calls == [call.wait(), call.wait(), call.terminate(), call.wait()]
    project.child.kill.assert_not_called()


def test_start_missing_project_environment_prints_windows_setup_command(windows_project):
    with pytest.raises(SystemExit) as failure:
        start.main()

    message = str(failure.value)
    assert ".venv" in message
    assert r"py -3.12 .\setup_local.py --download-models" in message
    windows_project.run.assert_not_called()
    windows_project.popen.assert_not_called()


@pytest.mark.parametrize("error", [ImportError("No module named fastapi"), OSError("DLL load failed")])
def test_windows_dependency_failures_have_actionable_recovery(windows_project, monkeypatch, error):
    project = windows_project
    existing_environment(project)
    built_frontend(project)
    uvicorn = SimpleNamespace(run=Mock())
    load_dependency = Mock(side_effect=[uvicorn, error])
    monkeypatch.setattr(start.importlib, "import_module", load_dependency)

    with pytest.raises(SystemExit) as failure:
        start.main()

    message = str(failure.value)
    assert str(project.python) in message
    assert str(error) in message
    assert r".\run.cmd" in message
    assert r"py -3.12 .\setup_local.py --download-models" in message
    uvicorn.run.assert_not_called()
    project.run.assert_not_called()
    project.popen.assert_not_called()


def test_start_missing_react_build_reports_windows_npm_commands(windows_project):
    existing_environment(windows_project)

    with pytest.raises(SystemExit) as failure:
        start.main()

    message = str(failure.value)
    assert "npm.cmd --prefix frontend ci" in message
    assert "npm.cmd --prefix frontend run build" in message
    windows_project.run.assert_not_called()
    windows_project.popen.assert_not_called()
