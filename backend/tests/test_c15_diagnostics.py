"""Offline regressions for C15 readiness and safe failure evidence."""
import json
import subprocess
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest

from backend.tests import c15_diagnostics as diagnostics


def test_redaction_covers_known_secret_encodings_headers_and_private_keys():
    secret = 'synthetic-한글-secret /+"'
    raw = '\n'.join((secret, quote(secret, safe=''), json.dumps(secret)[1:-1],
                     repr(secret)[1:-1], 'Authorization: Bearer unlisted-token',
                     'Set-Cookie: session=unlisted-cookie', 'DB_PASSWORD="unlisted password"',
                     '{"Authorization": "Bearer json-token"}', "{'Cookie': 'json-cookie'}",
                     '-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----'))
    result = diagnostics.redact(raw, [secret])
    for value in (secret, quote(secret, safe=''), json.dumps(secret)[1:-1],
                  'unlisted-token', 'unlisted-cookie', 'unlisted password', 'private-material', 'json-token', 'json-cookie'):
        assert value not in result
    assert diagnostics.redact('SSL key read failed: Permission denied', [secret]) == 'SSL key read failed: Permission denied'


@pytest.fixture
def readiness(monkeypatch):
    context = object()
    monkeypatch.setattr(diagnostics.ssl, 'create_default_context', lambda **kwargs: context)
    monkeypatch.setattr(diagnostics.time, 'sleep', lambda _: None)
    def configure(outcomes):
        ticks = iter([0, *[index / 10 for index in range(len(outcomes))], 1])
        monkeypatch.setattr(diagnostics.time, 'monotonic', lambda: next(ticks))
        results = iter(outcomes)
        def get(url, **kwargs):
            assert url == 'https://localhost:12345/api/v1/health'
            assert kwargs == dict(verify=context, trust_env=False, timeout=1)
            value = next(results)
            if isinstance(value, Exception):raise value
            return value
        monkeypatch.setattr(diagnostics.httpx, 'get', get)
    return configure


def test_readiness_recovers_after_transport_failure(readiness):
    readiness([httpx.ConnectError('starting'), SimpleNamespace(status_code=200, text='ready')])
    last = {}
    diagnostics.wait_ready('https://localhost:12345', 'cert.pem', last, [], timeout=1)
    assert last == dict(status=200, body='ready')


@pytest.mark.parametrize('outcome, expected', [
    (SimpleNamespace(status_code=502, text='bad gateway synthetic-password'), dict(status=502, body='bad gateway [REDACTED]')),
    (httpx.ConnectError('connect failed synthetic-password'), dict(exception='ConnectError', detail='connect failed [REDACTED]')),
])
def test_readiness_timeout_retains_last_sanitized_response_or_error(readiness, outcome, expected):
    readiness([outcome])
    last = {'stale': 'must be replaced'}
    with pytest.raises(AssertionError, match='C15 API readiness timeout'):
        diagnostics.wait_ready('https://localhost:12345', 'cert.pem', last, ['synthetic-password'], timeout=1)
    assert last == expected


@pytest.mark.parametrize('json_lines', [False, True])
def test_capture_keeps_only_owned_status_and_redacted_logs(tmp_path, monkeypatch, capsys, json_lines):
    secret = 'synthetic-credential'
    prefix = ['docker', 'compose', '--env-file', 'owned.env', '-p', 'cc-c15-owned', '-f', 'compose.yaml']
    row = dict(Service='proxy', State='exited', Health='', ExitCode=1,
               Command='must-not-export-command', Environment='must-not-export-environment')
    calls = []
    def run(args, **kwargs):
        assert args[:len(prefix)] == prefix
        assert kwargs == dict(capture_output=True, text=True, timeout=10)
        calls.append(args[len(prefix):])
        if calls[-1][0] == 'ps':
            return SimpleNamespace(returncode=0, stdout=json.dumps(row if json_lines else [row]), stderr='')
        return SimpleNamespace(returncode=0, stdout='Permission denied\nDB_PASSWORD=' + secret, stderr='')
    monkeypatch.setattr(diagnostics.subprocess, 'run', run)
    path = diagnostics.capture(prefix, tmp_path, {'status': 502, 'body': secret}, [secret])
    raw = path.read_text(encoding='utf-8');report = json.loads(raw)
    assert report['services'] == [dict(Service='proxy', State='exited', Health='', ExitCode=1)]
    assert report['last_health'] == dict(status=502, body='[REDACTED]')
    assert 'Permission denied' in report['logs']
    for value in (secret, 'must-not-export-command', 'must-not-export-environment'):
        assert value not in raw + capsys.readouterr().out
    assert calls == [['ps', '--all', '--format', 'json', 'api', 'worker', 'proxy'],
                     ['logs', '--no-color', '--tail', '120', 'api', 'worker', 'proxy']]


@pytest.mark.parametrize('failure', ['timeout', 'malformed', 'exit'])
def test_status_collection_failure_still_preserves_logs_and_health(tmp_path, monkeypatch, failure):
    def run(args, **kwargs):
        if args[0] == 'ps':
            if failure == 'timeout':raise subprocess.TimeoutExpired(args, 10)
            return SimpleNamespace(returncode=int(failure == 'exit'), stdout='not json', stderr='secret-value')
        return SimpleNamespace(returncode=0, stdout='SSL key permission denied', stderr='')
    monkeypatch.setattr(diagnostics.subprocess, 'run', run)
    path = diagnostics.capture([], tmp_path, {'exception': 'ConnectError'}, ['secret-value'])
    report = json.loads(path.read_text(encoding='utf-8'))
    assert 'services_error' in report
    assert report['logs'] == 'SSL key permission denied'
    assert report['last_health'] == {'exception': 'ConnectError'}
    assert 'secret-value' not in path.read_text(encoding='utf-8')


def test_capture_redacts_before_bounding_logs(tmp_path, monkeypatch):
    secret = 'synthetic-long-secret-' + 'x' * 18000
    def run(args, **kwargs):
        if args[0] == 'ps':return SimpleNamespace(returncode=0, stdout='[]', stderr='')
        return SimpleNamespace(returncode=0, stdout=secret + '\nuseful SSL error', stderr='')
    monkeypatch.setattr(diagnostics.subprocess, 'run', run)
    path = diagnostics.capture([], tmp_path, {'detail': 'DB_PASSWORD="health-secret"'}, [secret])
    raw = path.read_text(encoding='utf-8')
    assert 'x' * 100 not in raw and 'health-secret' not in raw
    assert json.loads(raw)['logs'] == '[REDACTED]\nuseful SSL error'
