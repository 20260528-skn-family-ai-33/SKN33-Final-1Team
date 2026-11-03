"""Loopback-only model-test HTTP endpoints; SSH transport lives in ssh.py."""

import base64
from collections import OrderedDict
import hashlib
from ipaddress import ip_address
import json
from pathlib import Path
import re
import tempfile
import threading
import time
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from backend.app.modeltest import runner as remote, ssh
from backend.app.modeltest.registry import Registry, registry_router
from backend.app.video.frames import sniff_container


def modeltest_ssh_router(storage_root: Path):
    sessions = {}; runs = {}; guard = threading.Lock(); busy = threading.Lock()
    previews = OrderedDict(); preview_bytes = 0; preview_guard = threading.Lock()
    def cached_preview(run_id, name, value=None):
        nonlocal preview_bytes
        key = (run_id, name)
        if value is not None:
            if not isinstance(name, str) or not re.fullmatch(remote.PREVIEW_NAME, name) or not isinstance(value, dict) or value.get('mime') != 'image/jpeg': raise ValueError('미리보기 형식이 올바르지 않습니다.')
            data = value.get('data')
            if not isinstance(data, str) or len(data) > 2 * 1024**2 or not base64.b64decode(data, validate=True).startswith(b'\xff\xd8\xff'):
                raise ValueError('미리보기 응답을 확인하지 못했습니다.')
            value = {'mime': 'image/jpeg', 'data': data}
        with preview_guard:
            if value is not None:
                old = previews.pop(key, None)
                if old: preview_bytes -= len(old['data'])
                previews[key] = value; preview_bytes += len(value['data'])
                while preview_bytes > 64 * 1024**2 or len(previews) > 1024:
                    _, old = previews.popitem(last=False); preview_bytes -= len(old['data'])
            if key in previews:
                previews.move_to_end(key); return previews[key]
    def local(request: Request, response: Response):
        try: loopback = request.client and ip_address(request.client.host).is_loopback
        except ValueError: loopback = False
        if not loopback or request.url.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise HTTPException(404, '로컬 SSH 연결 기능입니다.')
        allowed = {str(request.base_url).rstrip('/'), 'http://localhost:5173', 'http://127.0.0.1:5173'}
        if request.headers.get('X-Modeltest-Local') != '1' or request.headers.get('Origin', str(request.base_url).rstrip('/')) not in allowed:
            raise HTTPException(403, '로컬 화면에서만 SSH를 요청할 수 있습니다.')
        response.headers['Cache-Control'] = 'no-store'
    router = APIRouter(prefix='/api/modeltest/ssh', dependencies=[Depends(local)])
    registry = Registry(storage_root / 'models.json')
    router.include_router(registry_router(registry))
    def session(identifier):
        item = sessions.get(identifier)
        if item is None: raise HTTPException(404, 'SSH 연결을 다시 확인해 주세요.')
        return item
    def status(item):
        return {**item['health'], 'connection_id': item['id'], 'transport': 'ssh_proxy' if item['ssh'].proxy else 'ssh_tcp',
                'preparing': item.get('preparing', False), 'detail': item.get('error') or item['health'].get('detail', '')}

    def release_run(run):
        with guard:
            if run.get('owns_busy'):
                run['owns_busy'] = False; busy.release()

    @router.post('/connections')
    async def connect(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096: raise HTTPException(413, 'SSH 명령이 너무 깁니다.')
        try:
            data = json.loads(raw); connection = ssh.parse_ssh(data.get('command'))
            health = await run_in_threadpool(ssh.exchange, connection, 'probe')
        except ValueError as error: raise HTTPException(400, str(error)[:6500]) from None
        except AttributeError: raise HTTPException(400, 'SSH 접속 명령을 입력해 주세요.') from None
        with guard:
            for key, item in list(sessions.items()):
                if time.monotonic() - item['created'] > 3600 and not item.get('preparing') and not any(run['connection'] == key and run['job']['status'] in ('queued', 'running') for run in runs.values()): sessions.pop(key)
            if len(sessions) >= 32: raise HTTPException(429, '연결이 너무 많습니다. 로컬 서버를 다시 시작해 주세요.')
            identifier = str(uuid4()); sessions[identifier] = {'id': identifier, 'ssh': connection, 'health': health, 'created': time.monotonic()}
        return status(sessions[identifier])

    @router.get('/connections/{identifier}/health')
    def health(identifier: str): return status(session(identifier))

    @router.post('/connections/{identifier}/prepare', status_code=202)
    def prepare(identifier: str):
        item = session(identifier)
        if not item['health'].get('can_prepare'): raise HTTPException(409, 'CUDA PyTorch가 준비된 Pod가 필요합니다.')
        if any(run['job']['status'] in ('queued', 'running') for run in runs.values()): raise HTTPException(409, '검수가 진행 중입니다.')
        if not busy.acquire(blocking=False): raise HTTPException(409, '다른 환경 준비 또는 검수가 진행 중입니다.')
        item.update(preparing=True, error=''); item['health'] = {**item['health'], 'ready': False}
        def setup():
            try: item['health'] = ssh.exchange(item['ssh'], 'prepare', timeout=1500)
            except ValueError as error: item['error'] = str(error)
            finally: item['preparing'] = False; busy.release()
        threading.Thread(target=setup, daemon=True).start()
        return status(item)

    @router.post('/connections/{identifier}/runs', status_code=202)
    async def start(identifier: str, request: Request):
        item = session(identifier)
        if not item['health'].get('ready') or item.get('preparing'): raise HTTPException(409, '먼저 Pod 실행환경을 준비해 주세요.')
        try: run_id = str(UUID(request.headers.get('Idempotency-Key', '')))
        except ValueError: raise HTTPException(422, '유효한 요청 ID가 필요합니다.') from None
        # Bound the incoming stream before Starlette spools multipart files to disk.
        count = 0; receive = request._receive
        async def limited_receive():
            nonlocal count
            message = await receive(); count += len(message.get('body', b''))
            if count > remote.LIMITS['total_bytes'] + 1024 * 1024: raise HTTPException(413, '업로드 합계가 너무 큽니다.')
            return message
        request._receive = limited_receive
        storage_root.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='upload-', dir=storage_root)
        try:
            async with request.form(max_files=101, max_fields=1, max_part_size=256 * 1024) as form:
                config = remote.validate_config(registry.hydrate(json.loads(form.get('config', ''))))
                expected = [('media', [config['files']['media']] if config['files']['media'] else [], 'image' if config['flow'] == 'similarity' else config['flow']), ('documents', config['files']['documents'], 'document'), ('references', config['files']['references'], 'reference')]
                if any(key not in ('config', 'media', 'documents', 'references') for key in form): raise ValueError('지원하지 않는 업로드 필드입니다.')
                uploads = []; total = 0; container = None
                for kind, metadata, file_kind in expected:
                    files = form.getlist(kind)
                    if len(files) != len(metadata): raise ValueError('첨부 파일 수가 요청 설정과 다릅니다.')
                    for index, (file, info) in enumerate(zip(files, metadata)):
                        if not isinstance(file, UploadFile) or file.filename != info.get('name'): raise ValueError('첨부 파일 이름이 요청 설정과 다릅니다.')
                        suffix = Path(file.filename).suffix.lower()
                        if suffix not in remote.FORMATS[file_kind]: raise ValueError('SSH 실행기에서 지원하지 않는 파일 형식입니다.')
                        name = f'{kind.rstrip("s")}-{index}{suffix}'; path = Path(temp.name) / name; size = 0; digest = hashlib.sha256(); head = b''
                        with path.open('wb') as output:
                            while chunk := await file.read(1024 * 1024):
                                if not head: head = chunk[:32]
                                size += len(chunk); total += len(chunk)
                                if size > remote.LIMITS[f'{file_kind}_bytes'] or total > remote.LIMITS['total_bytes']: raise HTTPException(413, '첨부 파일 크기 한도를 초과했습니다.')
                                digest.update(chunk); output.write(chunk)
                        if size == 0 or size != info.get('size'): raise ValueError('빈 파일 또는 파일 크기가 다른 요청입니다.')
                        if kind == 'media' and config['flow'] == 'video':
                            container = sniff_container(head)
                            if container is None: raise ValueError('지원하는 영상 컨테이너가 아닙니다.')
                        uploads.append({'kind': kind, 'stored_name': name, 'path': str(path), 'size': size, 'sha256': digest.hexdigest()})
            digest = hashlib.sha256(json.dumps([config, [entry['sha256'] for entry in uploads]], sort_keys=True).encode()).hexdigest()
            with guard:
                previous = runs.get(run_id)
                if previous:
                    if previous['digest'] != digest or previous['connection'] != identifier: raise HTTPException(409, '같은 요청 ID에 다른 설정이 연결돼 있습니다.')
                    return previous['job']
                if any(run['job']['status'] in ('queued', 'running') for run in runs.values()) or not busy.acquire(blocking=False): raise HTTPException(409, '다른 환경 준비 또는 검수가 진행 중입니다.')
                stages = [{**{key: stage[key] for key in ('id', 'kind', 'name', 'agent_id', 'inputs') if key in stage}, 'model_id': stage.get('model_id'), 'status': 'queued'} for stage in config['stages']]
                if config['flow'] == 'video' and config.get('purpose') != 'prepare': stages.insert(1 if stages[0]['id'] == 'conditions' else 0, {'id': 'frames', 'status': 'queued'})
                run = {'connection': identifier, 'digest': digest, 'job': {'contract': remote.CONTRACT, 'id': run_id, 'status': 'queued', 'stages': stages}, 'disconnected': False, 'owns_busy': True}
                runs[run_id] = run
            owned_temp = temp; temp = None
            def work():
                def event(kind, data):
                    if kind == 'preview':
                        try: cached_preview(run_id, data.get('name', ''), data)
                        except (ValueError, AttributeError): pass
                    elif kind == 'progress':
                        if run['job']['status'] == 'queued': run['job'] = {**run['job'], 'preparation': data}
                    elif kind == 'run' and data is not None and run['job']['status'] not in ('completed', 'failed', 'cancelled'):
                        run['job'] = {**data, **({'preparation': run['job']['preparation']} if 'preparation' in run['job'] else {})}
                        try: registry.observe(data)
                        except (ValueError, OSError): run['job']['registry_notice'] = '실행 기록을 모델 목록에 저장하지 못했습니다. 결과 JSON은 유지됩니다.'
                try:
                    ssh.exchange(item['ssh'], 'run', uploads=uploads, on_event=event, timeout=4 * 3600,
                        id=run_id, config=config, digest=digest, container=container)
                except ValueError:
                    run['disconnected'] = run['job']['status'] not in ('completed', 'failed', 'cancelled')
                finally:
                    owned_temp.cleanup()
                    if not run['disconnected']: release_run(run)
            threading.Thread(target=work, daemon=True).start()
            return run['job']
        except (ValueError, TypeError, AttributeError) as error: raise HTTPException(422, str(error)[:300]) from None
        finally:
            if temp is not None: temp.cleanup()

    @router.get('/connections/{identifier}/runs/{run_id}')
    async def run_status(identifier: str, run_id: str):
        item = session(identifier); run = runs.get(run_id)
        try:
            if str(UUID(run_id)) != run_id: raise ValueError()
        except ValueError: raise HTTPException(404, '작업을 찾을 수 없습니다.') from None
        if run is None:
            try: value = await run_in_threadpool(ssh.exchange, item['ssh'], 'status', id=run_id)
            except ValueError: raise HTTPException(503, 'Pod에서 이전 작업을 조회하지 못했습니다.') from None
            if not value or value.get('id') != run_id: raise HTTPException(404, '이 Pod에서 이전 작업을 찾을 수 없습니다.')
            with guard:
                run = runs.setdefault(run_id, {'connection': identifier, 'digest': None, 'job': value,
                    'disconnected': value['status'] in ('queued', 'running'), 'owns_busy': False})
        if run['connection'] != identifier:
            previous = sessions.get(run['connection'])
            if previous is None or previous['ssh'] != item['ssh']: raise HTTPException(409, '이 작업을 실행한 Pod에 다시 연결해 주세요.')
            run['connection'] = identifier
        if run['disconnected']:
            try:
                value = await run_in_threadpool(ssh.exchange, item['ssh'], 'status', id=run_id)
                if value is None or value.get('not_started') is False: raise ValueError()
                if value.get('not_started') is True:
                    value = {**run['job'], 'status': 'failed', 'error': 'SSH 전송이 중단됐으며 Pod에서 추론이 시작되지 않았습니다.',
                             'stages': [{**stage, 'status': 'skipped'} for stage in run['job']['stages']]}
                run['job'] = value
                if value['status'] in ('completed', 'failed', 'cancelled'):
                    run['disconnected'] = False; release_run(run)
            except ValueError: raise HTTPException(503, 'SSH 연결이 끊겨 원격 상태가 미확인입니다. Pod를 확인한 후 같은 작업의 조회를 재개해 주세요.') from None
        try: registry.observe(run['job'])
        except (ValueError, OSError): run['job']['registry_notice'] = '모델 실행 확인 기록을 저장하지 못했습니다.'
        return run['job']

    @router.post('/connections/{identifier}/runs/{run_id}/cancel')
    async def cancel(identifier: str, run_id: str):
        item = session(identifier)
        await run_status(identifier, run_id)
        run = runs[run_id]
        if run['job']['status'] in ('completed', 'failed', 'cancelled'): return run['job']
        try: value = await run_in_threadpool(ssh.exchange, item['ssh'], 'cancel', id=run_id)
        except ValueError as error: raise HTTPException(409, str(error)[:500]) from None
        if not value or value.get('id') != run_id or value.get('status') not in ('completed', 'failed', 'cancelled'):
            raise HTTPException(502, '작업 중단 결과를 확인하지 못했습니다.')
        run['job'] = value; run['disconnected'] = False; release_run(run)
        return value
    @router.get('/connections/{identifier}/runs/{run_id}/previews/{name}')
    async def preview(identifier: str, run_id: str, name: str):
        item = session(identifier); run = runs.get(run_id)
        if run is None or run['connection'] != identifier: raise HTTPException(404, '작업을 찾을 수 없습니다.')
        if not re.fullmatch(remote.PREVIEW_NAME, name): raise HTTPException(400, '지원하지 않는 미리보기 파일입니다.')
        value = cached_preview(run_id, name)
        if value is not None: return value
        try:
            value = await run_in_threadpool(ssh.exchange, item['ssh'], 'preview', id=run_id, name=name)
            if not isinstance(value, dict): raise ValueError('미리보기 응답을 확인하지 못했습니다.')
            return cached_preview(run_id, name, value)
        except (ValueError, AttributeError) as error: raise HTTPException(502, str(error)[:300]) from None

    return router
