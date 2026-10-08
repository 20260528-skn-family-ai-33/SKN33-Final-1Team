"""Local-only image review API. No external service or reviewer answer keys."""
import hashlib
import io
import json
import math
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from config.bootstrap import setup_django

setup_django()

from django.conf import settings
from django.core.asgi import get_asgi_application
from app.contracts import SUPPORTED_VERDICTS, normalize_item_execution_status, normalize_rules
from app.csrf import enforce_csrf
from app.store import Store
from app.topics import DEFAULT_TOPIC, TopicCatalog

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def now():
    return datetime.now(timezone.utc).isoformat()


class ConfirmRequest(BaseModel):
    version: str
    topic_id: str = DEFAULT_TOPIC
    condition_id: str | None = None


class SampleRequest(BaseModel):
    sample_id: str
    topic_id: str = DEFAULT_TOPIC


class RunRequest(BaseModel):
    asset_id: str
    criteria_version: str
    topic_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=160)


def create_app(runtime_dir=None, criteria_path=None, samples_dir=None, topics_dir=None):
    runtime = Path(runtime_dir or os.getenv('LATTE_RUNTIME_DIR', ROOT / 'data/runtime'))
    criteria_file = Path(criteria_path or ROOT / 'data/criteria.json')
    samples = Path(samples_dir or ROOT / 'data/samples')
    catalog = TopicCatalog(criteria_file, samples,
                           topics_dir if topics_dir is not None else
                           (ROOT / 'data/topics' if criteria_path is None and samples_dir is None else None))
    uploads = runtime / 'uploads'
    uploads.mkdir(parents=True, exist_ok=True)
    store = Store(runtime / 'latte.sqlite3')
    app = FastAPI(title='동작그만 밑장빼기냐 · 로컬 검수', dependencies=[Depends(enforce_csrf)])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)
    app.state.store = store

    def criteria(topic_id=DEFAULT_TOPIC, condition_id=None):
        data = catalog.criteria(topic_id, condition_id)
        return {**data, 'confirmed': data['can_confirm'] and store.confirmed(data['version'])}

    def public_asset(asset):
        return {k: v for k, v in asset.items() if k != 'storage_name'}

    def save_image(raw, name, topic_id=DEFAULT_TOPIC, condition_id=None, sample_id=None):
        context = criteria(topic_id, condition_id)
        if not context['can_confirm']:
            raise HTTPException(409, detail={'code': 'CONDITION_REQUIRED', 'message': '이미지를 접수하기 전에 발주 조건을 선택하세요.'})
        if not raw:
            raise HTTPException(400, detail={'code': 'EMPTY_FILE', 'message': '빈 파일입니다.'})
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, detail={'code': 'FILE_TOO_LARGE', 'message': '20MB 이하 이미지를 제출하세요.'})
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as im:
                    actual_format = im.format
                    if actual_format not in ('PNG', 'JPEG'):
                        raise HTTPException(415, detail={'code': 'UNSUPPORTED_FORMAT', 'message': '실제 PNG 또는 JPEG 파일만 지원합니다.'})
                    width, height = im.size
                    if width * height > MAX_PIXELS:
                        raise HTTPException(413, detail={'code': 'TOO_MANY_PIXELS', 'message': '2천만 픽셀 이하 이미지를 제출하세요.'})
                    im.verify()
                with Image.open(io.BytesIO(raw)) as im:
                    im.load()
        except HTTPException:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise HTTPException(413, detail={'code': 'TOO_MANY_PIXELS', 'message': '이미지 픽셀 상한을 초과했습니다.'})
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
            raise HTTPException(422, detail={'code': 'INVALID_IMAGE', 'message': '손상되었거나 판독할 수 없는 이미지입니다.'})
        suffix = '.png' if actual_format == 'PNG' else '.jpg'
        asset_id = str(uuid4())
        storage_name = asset_id + suffix
        asset = {'id': asset_id, 'name': Path(name or 'image').name, 'url': f'/api/assets/{asset_id}/image',
                 'width': width, 'height': height, 'format': actual_format, 'bytes': len(raw),
                 'sha256': hashlib.sha256(raw).hexdigest(), 'created_at': now(), 'storage_name': storage_name,
                 'topic_id': topic_id, 'topic_label': context['topic_label'], 'condition_id': condition_id,
                 'sample_id': sample_id}
        (uploads / storage_name).write_bytes(raw)
        store.save_asset(asset)
        return public_asset(asset)

    @app.get('/api/health')
    def health():
        capabilities = {'ocr_available': False}
        try:
            from app import inspector
            if hasattr(inspector, 'get_capabilities'):
                capabilities = inspector.get_capabilities()
        except (ImportError, RuntimeError, ValueError):
            capabilities = {'ocr_available': False}
        return {**capabilities, 'mode': 'local', 'single_user': True, 'external_transfer': False,
                'max_bytes': MAX_BYTES, 'max_pixels': MAX_PIXELS,
                'web_framework': 'Django', 'api_framework': 'FastAPI',
                'persistence': 'Django ORM / SQLite'}

    @app.get('/api/criteria')
    def get_criteria(topic_id: str = DEFAULT_TOPIC, condition_id: str | None = None):
        return criteria(topic_id, condition_id)

    @app.get('/api/topics')
    def get_topics():
        return catalog.list()

    @app.post('/api/criteria/confirm')
    def confirm(req: ConfirmRequest):
        current = criteria(req.topic_id, req.condition_id)
        if not current['can_confirm']:
            raise HTTPException(409, detail={'code': 'CONDITION_REQUIRED', 'message': '발주 조건을 먼저 선택하세요.'})
        if req.version != current['version']:
            raise HTTPException(409, detail={'code': 'STALE_CRITERIA', 'message': '최신 조건을 확인해주세요.'})
        store.confirm(req.version, now())
        return criteria(req.topic_id, req.condition_id)

    @app.get('/api/samples')
    def get_samples(topic_id: str = DEFAULT_TOPIC):
        return [{'id': key, 'name': p.name, 'topic_id': topic_id,
                 'url': f'/api/samples/{key}/image?topic_id={topic_id}'}
                for key, p in catalog.sample_paths(topic_id).items()]

    def sample_path(sample_id, topic_id):
        allowed = catalog.sample_paths(topic_id)
        if sample_id not in allowed:
            raise HTTPException(404, detail={'code': 'SAMPLE_NOT_FOUND'})
        return allowed[sample_id]

    @app.get('/api/samples/{sample_id}/image')
    def sample_image(sample_id: str, topic_id: str = DEFAULT_TOPIC):
        return FileResponse(sample_path(sample_id, topic_id), media_type='image/png')

    @app.post('/api/assets/from-sample')
    def from_sample(req: SampleRequest):
        p = sample_path(req.sample_id, req.topic_id)
        condition = req.sample_id if catalog.criteria(req.topic_id).get('sample_conditions') else None
        return save_image(p.read_bytes(), p.name, req.topic_id, condition, req.sample_id)

    @app.post('/api/assets')
    async def upload(file: UploadFile = File(...), topic_id: str = Form(DEFAULT_TOPIC), condition_id: str | None = Form(None)):
        try:
            raw = await file.read(MAX_BYTES + 1)
            return await run_in_threadpool(save_image, raw, file.filename, topic_id, condition_id)
        finally:
            await file.close()

    @app.get('/api/assets/{asset_id}/image')
    def asset_image(asset_id: str):
        asset = store.asset(asset_id)
        if not asset:
            raise HTTPException(404, detail={'code': 'ASSET_NOT_FOUND'})
        return FileResponse(uploads / asset['storage_name'], media_type='image/png' if asset['format'] == 'PNG' else 'image/jpeg')

    @app.post('/api/runs')
    def create_run(req: RunRequest):
        asset = store.asset(req.asset_id)
        if not asset:
            raise HTTPException(404, detail={'code': 'ASSET_NOT_FOUND'})
        topic_id = asset.get('topic_id', DEFAULT_TOPIC)
        if req.topic_id is not None and req.topic_id != topic_id:
            raise HTTPException(409, detail={'code': 'TOPIC_MISMATCH', 'message': '이미지와 검사 주제가 다릅니다. 이미지를 다시 선택하세요.'})
        current = criteria(topic_id, asset.get('condition_id'))
        if req.criteria_version != current['version']:
            raise HTTPException(409, detail={'code': 'STALE_CRITERIA', 'message': '조건 버전이 변경되었습니다.'})
        if not current['confirmed']:
            raise HTTPException(409, detail={'code': 'CRITERIA_NOT_CONFIRMED', 'message': '조건을 먼저 확인·확정하세요.'})
        fingerprint = hashlib.sha256(json.dumps([asset['id'], asset['sha256'], req.criteria_version]).encode()).hexdigest()
        run = {'id': str(uuid4()), 'status': 'running', 'asset_id': asset['id'], 'asset': public_asset(asset),
               'topic_id': topic_id, 'topic_label': current['topic_label'], 'condition_id': asset.get('condition_id'),
               'criteria_version': req.criteria_version, 'criteria_snapshot': current, 'created_at': now(),
               'results': [], 'trace': [], 'mode': 'local', 'scope': '내용 검사 · 개발용 로컬 시연'}
        previous, matches = store.reserve_run(run, req.idempotency_key, fingerprint)
        if not matches:
            raise HTTPException(409, detail={'code': 'IDEMPOTENCY_CONFLICT', 'message': '같은 요청 키를 다른 입력에 사용할 수 없습니다.'})
        if previous:
            return previous
        try:
            from app.mcp_client import inspect_via_mcp
            result = inspect_via_mcp(uploads / asset['storage_name'], current['rules'])
            rows = normalize_rules(result['results'])
            if len(rows) != len(current['rules']) or {r['rule_id'] for r in rows} != {r['rule_id'] for r in current['rules']}:
                raise ValueError('invalid result rule IDs')
            for row in rows:
                if row.get('verdict') not in SUPPORTED_VERDICTS:
                    raise ValueError('invalid verdict')
                row['execution_status'] = normalize_item_execution_status(row.get('execution_status'))
                if row['execution_status'] == 'succeeded' and row.get('verdict') is None:
                    raise ValueError('completed item needs verdict')
                if row['execution_status'] != 'succeeded' and row.get('verdict') in {'충족', '미충족'}:
                    raise ValueError('unexecuted item cannot pass or fail')
                if row['execution_status'] == 'failed' and row.get('verdict') is not None:
                    raise ValueError('failed item must remain unjudged')
                if not all(k in row for k in ('name', 'expected', 'observed', 'reason', 'evidence', 'method')):
                    raise ValueError('missing result field')
                if not isinstance(row['evidence'], list):
                    raise ValueError('invalid evidence')
                for evidence in row['evidence']:
                    if not isinstance(evidence, dict) or not isinstance(evidence.get('text'), str):
                        raise ValueError('invalid evidence text')
                    box = evidence.get('bbox')
                    if box is not None:
                        if not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in box):
                            raise ValueError('invalid normalized evidence box')
                        if box[0] + box[2] > 1.001 or box[1] + box[3] > 1.001:
                            raise ValueError('evidence box outside image')
            completed_count = sum(r['execution_status'] == 'succeeded' for r in rows)
            run_status = 'completed' if completed_count == len(rows) else ('partial' if completed_count else 'failed')
            run.update({'results': rows, 'trace': result.get('trace', []), 'method': result.get('method', 'local'),
                        'status': run_status})
        except Exception as exc:
            run.update({'status': 'failed', 'error': {'code': 'INSPECTION_FAILED', 'type': type(exc).__name__,
                        'message': '검수 실행에 실패했습니다. 새 요청으로 다시 시도하세요.'},
                        'results': [{'rule_id': r['rule_id'], 'name': r['name'], 'expected': r['expected'],
                         'verdict': None, 'execution_status': 'failed', 'observed': None,
                         'reason': '검수 실행 실패', 'evidence': [], 'method': 'local'} for r in current['rules']]})
        run['finished_at'] = now()
        store.save_run(run)
        return run

    @app.get('/api/runs')
    def list_runs():
        return store.runs()

    @app.get('/api/runs/{run_id}')
    def get_run(run_id: str):
        run = store.run(run_id)
        if not run:
            raise HTTPException(404, detail={'code': 'RUN_NOT_FOUND'})
        return run

    # Serve local static files with an ASGI-native handler, including Django admin.
    app.mount('/static', StaticFiles(directory=ROOT / 'web', packages=[('django.contrib.admin', 'static')]), name='static')
    app.mount('/web', StaticFiles(directory=ROOT / 'web'), name='legacy-static')
    # Existing API routes take priority; Django serves templates and admin.
    app.mount('/', get_asgi_application(), name='django')

    return app


app = create_app()
