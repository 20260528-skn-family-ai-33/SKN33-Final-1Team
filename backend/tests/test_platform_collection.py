"""Collection stays portable across OS modules and subprocess environment limits."""
from pathlib import Path
import os
import subprocess
import sys


def test_management_cli_tests_collect_without_posix_terminal_modules():
    script = """
import importlib.abc
import sys
import pytest

blocked = {'pty', 'tty', 'termios'}
for name in blocked:
    sys.modules.pop(name, None)

class NoPosixTerminal(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise ModuleNotFoundError('POSIX terminal module deliberately unavailable', name=fullname)

sys.meta_path.insert(0, NoPosixTerminal())
raise SystemExit(pytest.main([
    '--collect-only', '-q', '-o', 'addopts=', '-m', 'db_integration',
    'backend/tests/test_v1_management_cli_integration.py',
]))
"""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('TEST_DB_')}
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[2],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '::test_real_cli_bootstrap_protected_creation_reset_email_membership_activation' in result.stdout
    assert '::test_cli_never_accepts_password_argv_or_noninteractive_secret_input' in result.stdout


def test_collected_test_ids_are_bounded_for_subprocess_environments():
    # pytest exports node IDs as PYTEST_CURRENT_TEST, inherited by child processes.
    # Collect without a terminal reporter so a regression never prints huge payloads.
    script = """
import pytest

class BoundedIds:
    count = 0
    oversized = []

    def pytest_collection_finish(self, session):
        self.count = len(session.items)
        self.oversized = [
            (item.path.name, item.name.split('[', 1)[0], len(item.nodeid.encode('utf-8')))
            for item in session.items if len(item.nodeid.encode('utf-8')) > 1024
        ]

audit = BoundedIds()
code = pytest.main([
    '--collect-only', '-p', 'no:terminal', '-o', 'addopts=', 'backend/tests', 'tests',
], plugins=[audit])
assert code == 0, f'Collection failed with exit code {code}'
assert audit.count > 0, 'No test IDs were checked'
assert not audit.oversized, f'Use short explicit parameter IDs: {audit.oversized}'
"""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('TEST_DB_')}
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[2],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_offline_contracts_run_without_posix_storage_primitives():
    script = """
import os
import pytest

for name in ('O_DIRECTORY', 'O_NOFOLLOW'):
    if hasattr(os, name):
        delattr(os, name)

class Results:
    skipped = []
    passed = []

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)
        elif report.when == 'call' and report.passed:
            self.passed.append(report.nodeid)

results = Results()
code = pytest.main([
    '-q', '--tb=short',
    'backend/tests/test_c09_contracts_storage.py',
    'backend/tests/test_c13_worker.py',
    'backend/tests/test_c14_operations.py',
], plugins=[results])
assert code == 0, f'Offline tests failed without POSIX storage primitives: {code}'
assert len(results.skipped) == 27, f'Expected only the 27 private POSIX filesystem cases to skip: {results.skipped}'
assert any('test_minimal_worker_needs_no_browser_or_session_secrets' in node for node in results.passed)
assert any('test_web_worker_admission_preserves_developer_login_without_local_model' in node for node in results.passed)
assert any('test_cli_checks_configuration_without_db_or_model' in node for node in results.passed)
"""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('TEST_DB_')}
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[2],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
