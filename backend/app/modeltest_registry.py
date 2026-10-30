"""Local model registrations. HF reads are bounded JSON requests, never inference."""
import copy
import json
import re
from pathlib import Path
import threading
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import modeltest_catalog as catalog


class Registry:
    def __init__(self, path):
        self.path = Path(path); self.lock = threading.RLock()

    def read(self):
        if not self.path.exists(): return []
        if self.path.stat().st_size > 16 * 1024**2: raise ValueError('모델 등록 파일 크기를 확인해 주세요.')
        try:
            result = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(result, list): raise ValueError()
            return result
        except (ValueError, OSError): raise ValueError('저장된 모델 등록 파일을 읽지 못했습니다. 원본을 보존하고 파일을 확인해 주세요.') from None

    def write(self, records):
        content = json.dumps(records, ensure_ascii=False, allow_nan=False)
        if len(content.encode('utf-8')) > 16 * 1024**2: raise ValueError('모델 등록 합계는 16MiB 이하여야 합니다. 기존 등록은 유지됩니다.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        target = self.path.with_suffix('.tmp'); target.write_text(content, encoding='utf-8'); target.replace(self.path)

    def save(self, data, identifier=None):
        if not isinstance(data, dict): raise ValueError('모델 등록 설정이 필요합니다.')
        if data.get('training') is not None and not isinstance(data['training'], dict): raise ValueError('학습 설정은 JSON 객체여야 합니다.')
        with self.lock: previous = next((item for item in self.read() if item['id'] == identifier), None)
        if identifier and previous is None: raise ValueError('등록 모델을 찾지 못했습니다.')
        meta = previous['metadata'] if previous else catalog.inspect_repo(data.get('value'), data.get('revision'))
        if previous and data.get('revision') and data['revision'] not in (previous['revision'], previous['resolved_revision']): meta = catalog.inspect_repo(meta['model_id'], data['revision'])
        elif data.get('refresh'): meta = catalog.inspect_repo(meta['model_id'], data.get('revision') or meta['revision'])
        settings = copy.deepcopy(data)
        if previous and (previous['resolved_revision'] != meta['resolved_revision'] or any(previous.get(key) != settings.get(key) for key in ('family', 'options', 'training'))) and not settings.get('thresholds_confirmed'):
            settings['training'] = {**(settings.get('training') or {}), 'thresholds': {}}
        meta = copy.deepcopy(meta); meta['artifacts'] = {}
        training = settings.get('training') or {}
        if isinstance(training, dict):
            for field in ('checkpoint', 'head', 'lora'):
                source = training.get(field)
                if not isinstance(source, dict) or not re.fullmatch('[a-f0-9]{40}', str(source.get('revision', ''))): continue
                old = (previous or {}).get('metadata', {}).get('artifacts', {}).get(field)
                if old and not old.get('error') and (previous or {}).get('training', {}).get(field) == source: meta['artifacts'][field] = old; continue
                try: meta['artifacts'][field] = catalog.inspect_repo(source.get('model_id'), source['revision'])
                except ValueError as error: meta['artifacts'][field] = {'error': str(error)}
        profile = catalog.build_profile(meta, settings)
        profile.update(id=identifier or str(uuid4()), updated_at=time.time())
        if previous and previous['fingerprint'] == profile['fingerprint'] and profile['status'] == 'unverified':
            for key in ('verification', 'prepared'):
                if key in previous: profile[key] = previous[key]
            if previous['status'] == 'verified': profile['status'] = 'verified'
        with self.lock:
            records = self.read()
            if not identifier and len(records) >= 100: raise ValueError('등록 모델은 최대 100개입니다.')
            self.write([item for item in records if item['id'] != profile['id']] + [profile])
        return profile

    def delete(self, identifier):
        with self.lock: self.write([item for item in self.read() if item['id'] != identifier])

    def hydrate(self, config):
        """Only server-stored profiles may enter a Pod run; browser status is not trusted."""
        if not isinstance(config, dict): raise ValueError('검수 설정 객체가 필요합니다.')
        with self.lock: records = {item['id']: item for item in self.read()}
        for stage in config.get('stages', []):
            if not isinstance(stage, dict): continue
            stage.pop('model_profile', None)
            identifier = stage.get('registered_model')
            if identifier:
                profile = records.get(identifier)
                if not profile: raise ValueError('선택한 등록 모델이 삭제됐습니다. 모델을 다시 선택해 주세요.')
                if stage.get('model_fingerprint') != profile['fingerprint']: raise ValueError('등록 모델 설정이 바뀌었습니다. 목록을 새로 읽고 실행해 주세요.')
                catalog.validate_profile(profile, stage.get('kind', stage['id']))
                stage.update(model_id=profile['model_id'], revision=profile['resolved_revision'], model_profile=profile)
        return config

    def observe(self, job):
        if job.get('status') != 'completed': return
        with self.lock:
            records = self.read(); changed = False
            for stage in job.get('stages', []):
                proof = stage.get('model_execution')
                if stage.get('status') != 'completed' or not isinstance(proof, dict): continue
                record = next((item for item in records if item['id'] == proof.get('registration_id') and item['fingerprint'] == proof.get('fingerprint')), None)
                if record is None: continue
                if stage.get('kind', stage['id']) == 'model_prepare': record['prepared'] = {'run_id': job['id'], 'at': time.time(), 'versions': proof.get('versions', {})}
                elif sum(item.get('calls', 0) for item in stage.get('model_timings', [])) > 0:
                    record.update(status='verified', verification={'run_id': job['id'], 'stage_id': stage['id'], 'at': time.time(), 'versions': proof.get('versions', {}), 'scope': '실행 성공 확인. 품질·정확도 검증을 의미하지 않습니다.'})
                changed = True
            if changed: self.write(records)


def registry_router(registry):
    router = APIRouter(prefix='/models')

    async def body(request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 100000: raise HTTPException(413, '모델 설정은 100KB 이하여야 합니다.')
        try:
            result = json.loads(data)
            if not isinstance(result, dict): raise ValueError()
            return result
        except ValueError: raise HTTPException(400, '모델 설정 JSON을 확인해 주세요.') from None

    @router.get('')
    def listing():
        try: return {'models': registry.read(), 'families': catalog.FAMILIES, 'local_versions': catalog.installed_versions()}
        except ValueError as error: raise HTTPException(400, str(error)) from None

    @router.post('/inspect')
    async def inspect(request: Request):
        data = await body(request)
        try: return catalog.build_profile(await run_in_threadpool(catalog.inspect_repo, data.get('value'), data.get('revision')))
        except ValueError as error: raise HTTPException(400, str(error)) from None

    @router.post('')
    async def create(request: Request):
        data = await body(request)
        try: return await run_in_threadpool(registry.save, data)
        except ValueError as error: raise HTTPException(400, str(error)) from None

    @router.put('/{identifier}')
    async def update(identifier: str, request: Request):
        data = await body(request)
        try: return await run_in_threadpool(registry.save, data, identifier)
        except ValueError as error: raise HTTPException(400, str(error)) from None

    @router.delete('/{identifier}')
    def remove(identifier: str):
        try: registry.delete(identifier); return {'deleted': True}
        except ValueError as error: raise HTTPException(400, str(error)) from None
    return router
