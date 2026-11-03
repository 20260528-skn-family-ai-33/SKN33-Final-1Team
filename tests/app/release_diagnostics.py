"""Bounded, sanitized evidence from one explicitly owned C15 Compose project."""
import json
import re
import ssl
import subprocess
import time
from urllib.parse import quote, quote_plus

import httpx


def redact(text, secrets):
    forms = set()
    for secret in secrets:
        if secret:
            forms.update((secret, quote(secret, safe=''), quote_plus(secret),
                          json.dumps(secret)[1:-1], repr(secret)[1:-1]))
    for value in sorted(forms, key=len, reverse=True):
        text = text.replace(value, '[REDACTED]')
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                  '[REDACTED PRIVATE KEY]', text, flags=re.S)
    text = re.sub(r'''(?im)\b(?:authorization|cookie|set-cookie)["']?\s*[:=][^\r\n]*''',
                  '[REDACTED HEADER]', text)
    return re.sub(r'''(?i)([\w-]*(?:password|secret|token)[\w-]*["']?\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;]+)''',
                  r'\1[REDACTED]', text)


def wait_ready(url, cert, last_health, secrets, *, timeout=35):
    context = ssl.create_default_context(cafile=str(cert))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url + '/api/v1/health', verify=context,
                                 trust_env=False, timeout=1)
            last_health.clear()
            last_health.update(status=response.status_code,
                               body=redact(response.text, secrets)[:2000])
            if response.status_code == 200:
                return
        except httpx.HTTPError as error:
            last_health.clear()
            last_health.update(exception=type(error).__name__,
                               detail=redact(str(error), secrets)[:2000])
        time.sleep(.1)
    raise AssertionError('C15 API readiness timeout; see c15-diagnostics.json')


def capture(prefix, directory, last_health, secrets):
    report = {'last_health': last_health, 'services': []}
    # Never collect inspect/config/environment, unrelated projects or unbounded logs.
    for name, args in (
        ('services', ['ps', '--all', '--format', 'json', 'api', 'worker', 'proxy']),
        ('logs', ['logs', '--no-color', '--tail', '120', 'api', 'worker', 'proxy']),
    ):
        try:
            result = subprocess.run([*prefix, *args], capture_output=True, text=True, timeout=10)
            if result.returncode:
                report[name + '_error'] = redact(result.stderr, secrets)[-2000:]
            elif name == 'logs':
                report[name] = redact(result.stdout + result.stderr, secrets)[-16000:]
            else:
                output = result.stdout.strip()
                rows = json.loads(output) if output.startswith('[') else [json.loads(line) for line in output.splitlines()]
                report[name] = [{key: row.get(key) for key in ('Service', 'State', 'Health', 'ExitCode')} for row in rows]
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError) as error:
            report[name + '_error'] = type(error).__name__
    def sanitized(value):
        if isinstance(value, str):return redact(value, secrets)
        if isinstance(value, list):return [sanitized(item) for item in value]
        if isinstance(value, dict):return {key: sanitized(item) for key, item in value.items()}
        return value
    path = directory / 'c15-diagnostics.json'
    path.write_text(json.dumps(sanitized(report), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('C15 sanitized failure diagnostics: ' + str(path))
    return path
