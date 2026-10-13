"""Launcher regression checks without installing packages or starting a server.

Windows branches are simulated here; native Windows execution belongs to CI.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call
import json
import subprocess
import sys

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


def test_start_stops_recursive_relaunch_when_project_interpreter_is_broken(windows_project, monkeypatch, capsys):
    project = windows_project
    existing_environment(project)
    monkeypatch.setattr(start.sys, "prefix", str(project.root / "django_env"))
    monkeypatch.setenv("BARO_REVIEW_PROJECT_ENV", str(project.environment.resolve()))

    with pytest.raises(SystemExit) as failure:
        start.main()

    assert failure.value.code == 1
    assert "반복 실행을 중단" in capsys.readouterr().out
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


def test_start_missing_project_environment_prints_windows_setup_command(windows_project, capsys):
    with pytest.raises(SystemExit) as failure:
        start.main()

    message = capsys.readouterr().out
    assert ".venv" in message
    assert "uv venv --python 3.12 --seed .venv" in message
    assert r".\.venv\Scripts\python.exe -m pip install -r requirements.txt" in message
    assert failure.value.code == 1
    windows_project.run.assert_not_called()
    windows_project.popen.assert_not_called()


# 설치·모델 로드 없이 실행 검사의 정상 상태를 재현한다.
def ready_start(project, monkeypatch):
    existing_environment(project)
    built_frontend(project)
    model_dir = project.root / ".runtime" / "easyocr"
    model_dir.mkdir(parents=True)
    for name in start.MODEL_FILES:
        (model_dir / name).write_bytes(b"model fixture")
    uvicorn = SimpleNamespace(run=Mock())
    modules = Mock(side_effect=lambda name: uvicorn if name == "uvicorn" else SimpleNamespace())
    monkeypatch.setattr(start.importlib, "import_module", modules)
    monkeypatch.setattr(start.importlib.util, "find_spec", lambda name: SimpleNamespace())
    monkeypatch.setenv("RUN_TARGET", "local")
    monkeypatch.delenv("OCR_MODEL_DIR", raising=False)
    monkeypatch.setattr(start.sys, "argv", ["start.py"])
    return uvicorn, modules


def ready_paddle_start(project, monkeypatch, *, ready=True):
    """EasyOCR가 설치되지 않은 프로젝트에서 Paddle 격리 환경만 준비됐다고 주입한다."""
    uvicorn, modules = ready_start(project, monkeypatch)
    for model in (project.root / ".runtime" / "easyocr").iterdir():
        model.unlink()
    original_import = modules.side_effect

    def server_only_import(name):
        """서버 Python에 OCR 의존성을 불러오는 회귀가 있으면 즉시 실패한다."""
        if name in {module for _, module in start.OCR_PACKAGES}:
            raise AssertionError("Paddle 단독은 EasyOCR 패키지를 검사하지 않습니다.")
        return original_import(name)

    modules.side_effect = server_only_import
    readiness = Mock(return_value=(ready, "Paddle model missing" if not ready else "Paddle ready"))
    monkeypatch.setitem(sys.modules, "backend.app.paddle_ocr", SimpleNamespace(paddle_readiness=readiness))
    monkeypatch.setenv("OCR_PIPELINE", "paddle-only")
    return uvicorn, modules, readiness


@pytest.mark.parametrize("check_only", [True, False])
def test_paddle_only_starts_without_easyocr_packages_or_weights(windows_project, monkeypatch, check_only):
    """단독 시작은 Paddle만 점검하고 기존 host·port 및 검사 전용 동작을 유지한다."""
    uvicorn, modules, readiness = ready_paddle_start(windows_project, monkeypatch)
    monkeypatch.setattr(start.sys, "argv", ["start.py", "--host", "127.0.0.1", "--port", "8124"] + (["--check-only"] if check_only else []))
    start.main()
    readiness.assert_called_once_with(check_import=True)
    assert modules.call_count == len(start.SERVER_PACKAGES)
    if check_only:
        uvicorn.run.assert_not_called()
    else:
        uvicorn.run.assert_called_once_with("backend.app.api:app", host="127.0.0.1", port=8124)
    windows_project.run.assert_not_called()
    windows_project.popen.assert_not_called()


def test_missing_paddle_only_prints_explicit_setup_and_never_executes_it(windows_project, monkeypatch, capsys):
    """준비 부족은 설치·다운로드를 실행하지 않고 필요한 명령만 출력한다."""
    uvicorn, modules, readiness = ready_paddle_start(windows_project, monkeypatch, ready=False)
    with pytest.raises(SystemExit) as failure:
        start.main()
    assert failure.value.code == 1
    output = capsys.readouterr().out
    assert "--include-detection" in output and "requirements-paddle.txt" in output
    assert output.index("venv .runtime/paddle-env") < output.index("ensurepip --upgrade") < output.index("pip install") < output.index("--include-detection")
    assert "backend.app.ocr_runtime --download-models" not in output
    assert all(name not in output for name in start.MODEL_FILES)
    assert modules.call_count == len(start.SERVER_PACKAGES)
    readiness.assert_called_once_with(check_import=True)
    uvicorn.run.assert_not_called()
    windows_project.run.assert_not_called()
    windows_project.popen.assert_not_called()


def test_paddle_only_server_recovery_uses_requirements_without_easyocr(windows_project, monkeypatch, capsys):
    """서버 의존성 복구에서도 EasyOCR·PyTorch 설치를 요구하지 않는다."""
    uvicorn, modules, _ = ready_paddle_start(windows_project, monkeypatch)
    modules.side_effect = ImportError("server dependency unavailable")
    monkeypatch.setattr(start.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(SystemExit):
        start.main()
    output = capsys.readouterr().out
    assert "-m pip install -r requirements-test.txt" in output
    assert "-m pip install -r requirements.txt" not in output
    assert output.index("ensurepip --upgrade") < output.index("pip install")
    uvicorn.run.assert_not_called()


@pytest.mark.parametrize("pipeline", ["paddle-only", "paddle", "qwen", "adaptive"])
def test_runpod_rejects_local_only_pipeline_before_local_checks(windows_project, monkeypatch, capsys, pipeline):
    """RunPod와 지원하지 않는 조합에서 로컬 모델 검사·실행을 하지 않는다."""
    uvicorn, modules, readiness = ready_paddle_start(windows_project, monkeypatch)
    monkeypatch.setenv("OCR_PIPELINE", pipeline)
    monkeypatch.setenv("RUN_TARGET", "runpod")
    monkeypatch.setenv("RUNPOD_API_KEY", "private-key-never-print")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint-1")
    with pytest.raises(SystemExit):
        start.main()
    output = capsys.readouterr().out
    assert "RUN_TARGET=local에서만" in output
    assert "private-key-never-print" not in output
    readiness.assert_not_called()
    assert modules.call_count == len(start.SERVER_PACKAGES)
    uvicorn.run.assert_not_called()


def test_paddle_setup_preserves_existing_environment(windows_project, monkeypatch, capsys):
    """기존 Paddle 환경은 재생성하지 않고 pip 복구부터 안내한다."""
    uvicorn, _, _ = ready_paddle_start(windows_project, monkeypatch, ready=False)
    executable = windows_project.root / ".runtime" / "paddle-env" / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"keep existing environment")
    with pytest.raises(SystemExit):
        start.main()
    output = capsys.readouterr().out
    assert "-m venv .runtime/paddle-env" not in output
    assert executable.read_bytes() == b"keep existing environment"
    uvicorn.run.assert_not_called()


@pytest.mark.parametrize("pipeline", ["paddle", "qwen"])
def test_selected_new_model_missing_stops_server_and_only_prints_commands(windows_project, monkeypatch, capsys, pipeline):
    """선택 모델 누락 시 기존 실행 정책대로 설치 안내만 하고 서버를 시작하지 않는다."""
    from backend.app import model_ocr
    uvicorn, _ = ready_start(windows_project, monkeypatch)
    monkeypatch.setenv("OCR_PIPELINE", pipeline)
    readiness = Mock(return_value=(False, "test missing model"))
    monkeypatch.setattr(model_ocr, "model_readiness", readiness)
    with pytest.raises(SystemExit) as failure:
        start.main()
    assert failure.value.code == 1
    output = capsys.readouterr().out
    assert "test missing model" in output
    assert ("requirements-paddle.txt" if pipeline == "paddle" else "scripts.local_ollama") in output
    readiness.assert_called_once_with(pipeline, check_import=True)
    uvicorn.run.assert_not_called()
    windows_project.run.assert_not_called()


@pytest.mark.parametrize("error", [ImportError("No module named fastapi"), OSError("DLL load failed")])
def test_windows_dependency_failures_have_actionable_recovery(windows_project, monkeypatch, error, capsys):
    project = windows_project
    uvicorn, modules = ready_start(project, monkeypatch)

    def import_module(name):
        if name == "fastapi":
            raise error
        return uvicorn if name == "uvicorn" else SimpleNamespace()

    modules.side_effect = import_module

    with pytest.raises(SystemExit) as failure:
        start.main()

    message = capsys.readouterr().out
    assert str(project.python) in message
    assert str(error) in message
    assert r".\run.cmd" in message
    assert r".\.venv\Scripts\python.exe -m pip install -r requirements.txt" in message
    assert failure.value.code == 1
    uvicorn.run.assert_not_called()
    project.run.assert_not_called()
    project.popen.assert_not_called()


def test_start_missing_react_build_reports_windows_npm_commands(windows_project, monkeypatch, capsys):
    uvicorn, _ = ready_start(windows_project, monkeypatch)
    (windows_project.root / "frontend" / "dist" / "index.html").unlink()

    with pytest.raises(SystemExit) as failure:
        start.main()

    message = capsys.readouterr().out
    assert "npm.cmd --prefix frontend ci" in message
    assert "npm.cmd --prefix frontend run build" in message
    windows_project.run.assert_not_called()
    windows_project.popen.assert_not_called()
    uvicorn.run.assert_not_called()


def test_all_missing_packages_models_and_assets_are_reported_together(windows_project, monkeypatch, capsys):
    project = windows_project
    uvicorn, modules = ready_start(project, monkeypatch)
    monkeypatch.setattr(start.importlib.util, "find_spec", lambda name: None)

    def import_module(name):
        if name in {"torch", "easyocr"}:
            raise ModuleNotFoundError(f"No module named {name}", name=name)
        return uvicorn if name == "uvicorn" else SimpleNamespace()

    modules.side_effect = import_module
    model_dir = project.root / ".runtime" / "easyocr"
    (model_dir / start.MODEL_FILES[0]).unlink()
    (model_dir / start.MODEL_FILES[1]).write_bytes(b"")
    index = project.root / "frontend" / "dist" / "index.html"
    index.write_text('<script src="/assets/missing.js"></script><link rel="stylesheet" href="/assets/missing.css">')
    with pytest.raises(SystemExit) as failure:
        start.main()
    output = capsys.readouterr().out
    for item in ("[누락] torch", "[누락] easyocr", *start.MODEL_FILES, "missing.js", "missing.css"):
        assert item in output
    assert output.index("ensurepip --upgrade") < output.index("pip install") < output.index("--download-models")
    assert modules.call_count == len(start.SERVER_PACKAGES) + len(start.OCR_PACKAGES)
    assert failure.value.code == 1
    uvicorn.run.assert_not_called()
    project.run.assert_not_called()
    project.popen.assert_not_called()


@pytest.mark.parametrize("windows", [True, False])
def test_ready_server_uses_requested_host_port_without_installing(windows_project, monkeypatch, windows):
    project = windows_project
    if not windows:
        project.python = project.environment / "bin" / "python"
        monkeypatch.setattr(start.sys, "platform", "darwin")
        monkeypatch.setattr(start.sys, "executable", str(project.python))
    uvicorn, _ = ready_start(project, monkeypatch)
    monkeypatch.setattr(start.sys, "argv", ["start.py", "--host", "127.0.0.1", "--port", "8124"])
    start.main()
    uvicorn.run.assert_called_once_with("backend.app.api:app", host="127.0.0.1", port=8124)
    project.run.assert_not_called()
    project.popen.assert_not_called()


def test_pip_alone_is_warning_and_does_not_block_start(windows_project, monkeypatch, capsys):
    uvicorn, _ = ready_start(windows_project, monkeypatch)
    monkeypatch.setattr(start.importlib.util, "find_spec", lambda name: None)
    start.main()
    output = capsys.readouterr().out
    assert "pip가 없습니다" in output and "ensurepip --upgrade" in output
    assert "[실행 중단]" not in output and "pip install" not in output
    uvicorn.run.assert_called_once()
    windows_project.run.assert_not_called()


def test_check_only_returns_success_without_server_or_model_loading(windows_project, monkeypatch):
    uvicorn, _ = ready_start(windows_project, monkeypatch)
    monkeypatch.setattr(start.sys, "argv", ["start.py", "--check-only"])
    from backend.app import ocr_runtime
    warmup = Mock(side_effect=AssertionError("No model loading"))
    monkeypatch.setattr(ocr_runtime, "warmup_models", warmup)
    monkeypatch.setattr(ocr_runtime, "recognize_image", warmup)
    start.main()
    uvicorn.run.assert_not_called()
    warmup.assert_not_called()
    windows_project.run.assert_not_called()


def test_check_only_failure_does_not_start_server(windows_project, monkeypatch):
    uvicorn, _ = ready_start(windows_project, monkeypatch)
    monkeypatch.setattr(start.sys, "argv", ["start.py", "--check-only"])
    (windows_project.root / ".runtime" / "easyocr" / start.MODEL_FILES[0]).unlink()
    with pytest.raises(SystemExit) as failure:
        start.main()
    assert failure.value.code == 1
    uvicorn.run.assert_not_called()
    windows_project.run.assert_not_called()


def test_runpod_skips_local_ocr_and_does_not_log_secret(windows_project, monkeypatch, capsys):
    uvicorn, modules = ready_start(windows_project, monkeypatch)
    monkeypatch.setenv("RUN_TARGET", "runpod")
    monkeypatch.setenv("RUNPOD_API_KEY", "secret-not-in-log")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint-1")
    for path in (windows_project.root / ".runtime" / "easyocr").iterdir():
        path.unlink()
    start.main()
    assert modules.call_count == len(start.SERVER_PACKAGES)
    output = capsys.readouterr().out
    assert "secret-not-in-log" not in output and "--download-models" not in output
    uvicorn.run.assert_called_once()


def test_invalid_runpod_configuration_blocks_start_without_secret_values(windows_project, monkeypatch, capsys):
    uvicorn, _ = ready_start(windows_project, monkeypatch)
    monkeypatch.setenv("RUN_TARGET", "runpod")
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "bad/secret-endpoint")
    with pytest.raises(SystemExit):
        start.main()
    output = capsys.readouterr().out
    assert "RUNPOD_API_KEY" in output and "RUNPOD_ENDPOINT_ID" in output
    assert "bad/secret-endpoint" not in output
    uvicorn.run.assert_not_called()


def test_python_version_mismatch_preserves_environment_and_blocks_imports(windows_project, monkeypatch, capsys):
    marker = existing_environment(windows_project)
    monkeypatch.setattr(start.sys, "version_info", (3, 11, 0))
    modules = Mock()
    monkeypatch.setattr(start.importlib, "import_module", modules)
    with pytest.raises(SystemExit):
        start.main()
    assert "Python 3.12" in capsys.readouterr().out
    assert marker.read_text(encoding="utf-8") == "existing environment must be preserved"
    modules.assert_not_called()


def test_custom_model_directory_and_encoded_asset_urls(windows_project, monkeypatch):
    uvicorn, _ = ready_start(windows_project, monkeypatch)
    directory = windows_project.root / "내 모델"
    directory.mkdir()
    for name in start.MODEL_FILES:
        (directory / name).write_bytes(b"model fixture")
    monkeypatch.setenv("OCR_MODEL_DIR", str(directory))
    dist = windows_project.root / "frontend" / "dist"
    assets = dist / "assets"
    assets.mkdir()
    (assets / "한글.js").write_bytes(b"script fixture")
    (dist / "index.html").write_text('<script src="./assets/%ED%95%9C%EA%B8%80.js?v=1"></script>', encoding="utf-8")
    monkeypatch.setattr(start.sys, "argv", ["start.py", "--check-only"])
    start.main()
    uvicorn.run.assert_not_called()


def test_start_preserves_environment_from_another_os(windows_project, monkeypatch, capsys):
    project = windows_project
    other_python = project.environment / "bin" / "python"
    other_python.parent.mkdir(parents=True)
    other_python.write_bytes(b"foreign environment")
    modules = Mock()
    monkeypatch.setattr(start.importlib, "import_module", modules)
    with pytest.raises(SystemExit) as failure:
        start.main()
    assert failure.value.code == 1
    assert "다른 이름으로 보관" in capsys.readouterr().out
    assert other_python.read_bytes() == b"foreign environment"
    modules.assert_not_called()
    project.run.assert_not_called()
    project.popen.assert_not_called()


def test_invalid_target_and_out_of_build_asset_paths_stop_start(windows_project, monkeypatch, capsys):
    uvicorn, _ = ready_start(windows_project, monkeypatch)
    monkeypatch.setenv("RUN_TARGET", "invalid")
    (windows_project.root / "frontend" / "dist" / "index.html").write_text('<script src="../../outside.js"></script>')
    with pytest.raises(SystemExit):
        start.main()
    output = capsys.readouterr().out
    assert "RUN_TARGET은 local 또는 runpod" in output
    assert "outside.js" in output
    assert '$env:RUN_TARGET = "local"' in output
    uvicorn.run.assert_not_called()
