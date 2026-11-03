"""Loopback-only SSH bridge for the model test page; never starts inference on the laptop."""
from __future__ import annotations

import base64
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from ipaddress import ip_address
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from uuid import UUID, uuid4

from backend.app.modeltest import runner as remote
from backend.app.modeltest import catalog as modeltest_catalog
from backend.app.modeltest import adapters as modeltest_adapters
from backend.app.modeltest import prompts as modeltest_prompts
from backend.app.modeltest import contracts as modeltest_contracts
from backend.app.modeltest import logo_comparison as modeltest_logo_comparison
from backend.app.modeltest.registry import Registry
from backend.app.video.frames import sniff_container


@dataclass(frozen=True)
class SSHConnection:
    user: str
    host: str
    port: int
    identity: str | None

    @property
    def proxy(self): return self.host == 'ssh.runpod.io'


def parse_ssh(command: str) -> SSHConnection:
    if not isinstance(command, str) or not 1 <= len(command) <= 2048 or any(char in command for char in '\r\n\x00'):
        raise ValueError('SSH 접속 명령 한 줄을 입력해 주세요.')
    lexer = shlex.shlex(command, posix=True); lexer.whitespace_split = True; lexer.commenters = ''; lexer.escape = ''
    try: args = list(lexer)
    except ValueError: raise ValueError('SSH 명령의 따옴표를 확인해 주세요.') from None
    if not args or args.pop(0).lower() not in ('ssh', 'ssh.exe'):
        raise ValueError('ssh 사용자@주소 -p 포트 -i 키경로 형식으로 입력해 주세요.')
    target = None; identity = None; port = 22; options = set()
    while args:
        value = args.pop(0)
        if value in ('-p', '-i'):
            if value in options or not args: raise ValueError('SSH 옵션이 중복됐거나 값이 없습니다.')
            options.add(value); argument = args.pop(0)
            if value == '-p':
                if not argument.isdecimal() or not 1 <= int(argument) <= 65535: raise ValueError('SSH 포트는 1~65535여야 합니다.')
                port = int(argument)
            else:
                key = Path(argument).expanduser()
                if not key.is_absolute() or not key.is_file(): raise ValueError('개인키의 로컬 절대 경로 또는 ~/.ssh/ 경로를 확인해 주세요.')
                identity = str(key.resolve())
        elif not value.startswith('-') and target is None:
            target = value
        else: raise ValueError('접속 대상과 -p, -i 옵션만 허용합니다. 원격 명령·터널·추가 옵션은 제외해 주세요.')
    if not target or target.count('@') != 1: raise ValueError('사용자@주소가 필요합니다.')
    user, host = target.split('@'); host = host.lower()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', user): raise ValueError('SSH 사용자명을 확인해 주세요.')
    if host != 'ssh.runpod.io':
        try: ip_address(host.strip('[]'))
        except ValueError: raise ValueError('RunPod 기본 SSH 주소 또는 SSH over exposed TCP의 IP 주소를 입력해 주세요.') from None
    return SSHConnection(user, host, port, identity)


def ssh_arguments(connection, command):
    executable = shutil.which('ssh')
    if not executable: raise ValueError('노트북에 OpenSSH 클라이언트가 필요합니다.')
    # Ignore user SSH config commands; accept only the explicitly supplied target/key.
    args = [executable, '-F', 'NUL' if os.name == 'nt' else '/dev/null', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-o', 'LogLevel=ERROR', '-p', str(connection.port)]
    if connection.identity: args += ['-o', 'IdentitiesOnly=yes', '-i', connection.identity]
    args += ['-tt' if connection.proxy else '-T', f'{connection.user}@{connection.host}']
    if not connection.proxy: args.append(command)
    return args


def ssh_failure(text):
    if 'host key verification failed' in text.lower() or 'remote host identification has changed' in text.lower():
        return 'SSH 호스트 키 확인이 필요합니다. 먼저 터미널에서 같은 접속 명령으로 서버 지문을 확인해 주세요. 변경된 키는 자동으로 신뢰하지 않습니다.'
    if 'permission denied' in text.lower():
        return 'SSH 키 인증에 실패했습니다. 키 경로·Pod 공개키 등록·암호화된 키의 ssh-agent 등록을 확인해 주세요.'
    return 'SSH 연결 또는 Pod 실행에 실패했습니다. 접속 명령, Pod 실행 상태와 python3 설치를 확인해 주세요.'


def runner_source():
    # Only reviewed local modules are bundled; HF repositories never provide executable source.
    lines = ['import sys, types']
    for name, module in (('modeltest_prompts', modeltest_prompts),
                         ('modeltest_catalog', modeltest_catalog),
                         ('modeltest_contracts', modeltest_contracts),
                         ('modeltest_logo_comparison', modeltest_logo_comparison),
                         ('modeltest_adapters', modeltest_adapters)):
        lines.append(f'module = types.ModuleType({name!r}); sys.modules[{name!r}] = module')
        if module is modeltest_prompts: lines.append(f'module.DEFAULTS = {modeltest_prompts.DEFAULTS!r}')
        lines.append(f'exec(compile({Path(module.__file__).read_text(encoding="utf-8")!r}, {name!r}, "exec"), module.__dict__)')
    lines.append(Path(remote.__file__).read_text(encoding='utf-8').replace('from __future__ import annotations', ''))
    return '\n'.join(lines)


def exchange(connection, action, *, uploads=(), on_event=None, timeout=90, **payload):
    began = time.monotonic(); connected_at = None; transfer_started = None; transfer_finished = None
    sent = 0; total = sum(item['size'] for item in uploads); last_progress = 0
    def progress(phase, force=False):
        nonlocal last_progress
        now = time.monotonic()
        if action != 'run' or on_event is None or (not force and now - last_progress < 0.5): return
        last_progress = now
        on_event('progress', {'phase': phase, 'bytes_sent': sent, 'total_bytes': total,
            'elapsed_seconds': round(now - began, 2),
            'connection_seconds': round(connected_at - began, 2) if connected_at else None,
            'transfer_seconds': round((transfer_finished or now) - transfer_started, 2) if transfer_started else None,
            'startup_seconds': round(now - transfer_finished, 2) if transfer_finished else None})
    progress('connecting', True)
    prefix = 'CC_MODELTEST_' + secrets.token_hex(12) + ':'
    bootstrap = "import sys,json;print(" + repr(prefix + 'READY') + ",flush=True);boot=json.loads(sys.stdin.readline());space={'__name__':'modeltest_remote'};exec(boot['source'],space);space['remote_main'](boot,sys.stdout," + repr(prefix) + ')'
    command = 'python3 -u -c ' + shlex.quote(bootstrap)
    args = ssh_arguments(connection, command)
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    events = queue.Queue(); logs = []; closed = threading.Event()
    timer = threading.Timer(timeout, process.kill); timer.daemon = True; timer.start()
    def read():
        try:
            while not closed.is_set():
                raw = process.stdout.readline(16 * 1024**2 + 1)
                if not raw: break
                if len(raw) > 16 * 1024**2: events.put(('error', 'Pod 응답이 너무 큽니다.')); break
                text = raw.decode('utf-8', errors='replace').strip()
                if text.startswith(prefix): events.put(('message', text[len(prefix):]))
                else:
                    logs.append(text[-500:])
                    if len(logs) > 8: logs.pop(0)
        finally: events.put(('end', None))
    reader = threading.Thread(target=read, daemon=True); reader.start()
    source = runner_source()
    upload_map = {item['stored_name']: item for item in uploads}; result = None
    def send(value): process.stdin.write((json.dumps(value, ensure_ascii=True) + '\n').encode()); process.stdin.flush()
    try:
        if connection.proxy:
            # Basic RunPod SSH only exposes a terminal, so stream framed data with echo/canonical mode disabled.
            process.stdin.write(('stty -echo -icanon min 1 time 0; ' + command + '\n').encode()); process.stdin.flush()
        while True:
            kind, value = events.get(timeout=timeout + 5)
            if kind == 'error': raise ValueError(value)
            if kind == 'end':
                process.wait(timeout=5)
                if process.returncode != 0 or result is None: raise ValueError(ssh_failure('\n'.join(logs)))
                return result
            if value == 'READY':
                connected_at = time.monotonic()
                send({'source': source, 'action': action, **payload, 'binary_upload': not connection.proxy,
                      'uploads': [{key: value for key, value in item.items() if key != 'path'} for item in uploads]})
                continue
            message = json.loads(value)
            if message['kind'] == 'error':
                raise ValueError(str(message['data'])[:6500])
            if message['kind'] == 'upload':
                item = upload_map[message['data']]
                if transfer_started is None: transfer_started = time.monotonic()
                progress('uploading', True)
                with Path(item['path']).open('rb') as stream:
                    while chunk := stream.read(192 * 1024 if connection.proxy else 1024 * 1024):
                        if connection.proxy: send({'chunk': base64.b64encode(chunk).decode('ascii')})
                        else: process.stdin.write(chunk); process.stdin.flush()
                        sent += len(chunk); progress('uploading')
                if connection.proxy: send({'end': True})
                if sent == total:
                    transfer_finished = time.monotonic(); progress('starting', True)
            elif message['kind'] == 'preview' and action == 'run':
                if on_event: on_event('preview', message['data'])
            elif message['kind'] in ('health', 'run', 'preview'):
                if action == 'run' and result is None: progress('ready', True)
                result = message['data']
                if on_event: on_event(message['kind'], result)
                if message['kind'] in ('health', 'preview') or action == 'status':
                    # A basic SSH terminal can remain open after python exits; the framed final response is sufficient.
                    return result
                if result and result.get('status') in ('completed', 'failed', 'cancelled'): return result
    except (OSError, subprocess.SubprocessError, queue.Empty, ValueError, KeyError) as error:
        if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError): raise
        raise ValueError(ssh_failure('\n'.join(logs))) from None
    finally:
        timer.cancel(); closed.set()
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        process.stdin.close(); process.stdout.close()


def modeltest_ssh_router(storage_root: Path):
    """Keep the existing router factory import available to local callers."""
    from .routes import modeltest_ssh_router as create_router
    return create_router(storage_root)
