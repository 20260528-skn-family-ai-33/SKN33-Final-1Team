"""Standalone Pod runner, sent over SSH. Heavy dependencies are imported only on the Pod."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict, is_dataclass
import copy
import gc
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import threading
import traceback
import zipfile
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen
from urllib.error import URLError
from types import SimpleNamespace

try:
    from . import modeltest_catalog as catalog, modeltest_adapters as adapters, modeltest_prompts as prompts
except ImportError:
    import modeltest_catalog as catalog
    import modeltest_adapters as adapters
    import modeltest_prompts as prompts

CONTRACT = 'modeltest.v1'
STAGES = ('conditions', 'logo_extract', 'text_extract', 'visual_extract', 'logo_similarity', 'candidate_filter', 'logo_compare', 'text_compare', 'visual_compare', 'text_generate')
FORMATS = {'image': ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'],
           'video': ['.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v'],
           'document': ['.pdf', '.docx', '.hwpx', '.txt', '.md'],
           'reference': ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff']}
LIMITS = {'image_bytes': 20 * 1024**2, 'video_bytes': 500 * 1024**2,
          'document_bytes': 20 * 1024**2, 'reference_bytes': 20 * 1024**2, 'total_bytes': 600 * 1024**2}
PACKAGES = ('transformers>=5.11,<6', 'accelerate>=1,<2', 'Pillow>=12.3,<13', 'av>=16,<20', 'pypdfium2>=4,<6', 'safetensors>=0.6,<1',
            'opencv-python-headless>=4.10,<5', 'numpy>=1.26,<3', 'tqdm>=4.66,<5')
MODULES = ('torch', 'transformers', 'accelerate', 'PIL', 'av', 'pypdfium2', 'safetensors', 'cv2', 'numpy', 'tqdm')
OWLV2 = 'google/owlv2-base-patch16'
SIGLIP2 = 'google/siglip2-base-patch16-224'
OWLV2_ENSEMBLE = 'google/owlv2-base-patch16-ensemble'
SIGLIP2_SO400M = 'google/siglip2-so400m-patch14-384'
OWLV2_MODELS = (OWLV2, OWLV2_ENSEMBLE)
SIGLIP2_MODELS = (SIGLIP2, SIGLIP2_SO400M)
FILTER_SCOPES = ('references_per_crop', 'crops_per_reference_frame', 'crops_per_reference_video')
XFEAT = 'vismatch/xfeat'
QWEN = 'Qwen/Qwen3.5-4B'
PADDLE_GGUF = 'PaddlePaddle/PaddleOCR-VL-1.6-GGUF'
LLAMA_COMMIT = '620d2225cc436ab0b001e3200e590e3b286ef9ab'
LLAMA_BUILD_LOCK = threading.Lock()
MODEL_STAGES = {OWLV2: ('logo_extract',), SIGLIP2: ('visual_extract', 'logo_similarity'), XFEAT: ('logo_compare',), QWEN: tuple(name for name in STAGES if name not in ('logo_similarity', 'candidate_filter')), PADDLE_GGUF: ('text_extract',)}
MODEL_STAGES.update({OWLV2_ENSEMBLE: MODEL_STAGES[OWLV2], SIGLIP2_SO400M: MODEL_STAGES[SIGLIP2]})
STAGES += catalog.EXTRA_STAGES
PAIR_STAGES = ('logo_compare', 'logo_similarity', 'phash', 'orb', 'sift', 'disk_lightglue')
XFEAT_SOURCE = 'verlab/accelerated_features:e92685f57f8318b18725c5c8c0bd28c7fe188d9a'
PREVIEW_NAME = r'(?:frame-\d{8,12}\.jpg|logo-(?:[A-Za-z0-9_-]{1,64}-)?\d{1,6}-\d{1,6}\.png|preview-(?:poster|reference-\d{1,2})\.jpg)'


def read_preview(directory, name):
    if not isinstance(name, str) or not re.fullmatch(PREVIEW_NAME, name):
        raise ValueError('지원하지 않는 미리보기 파일입니다.')
    path = directory / name
    if path.resolve().parent != directory.resolve() or not path.is_file():
        raise ValueError('미리보기를 찾을 수 없습니다. 해당 단계가 끝난 뒤 다시 확인해 주세요.')
    image = image_of(path); image.thumbnail((1280, 1280))
    output = io.BytesIO(); image.save(output, format='JPEG', quality=85)
    data = output.getvalue()
    if len(data) > 1536 * 1024: raise ValueError('미리보기 크기 한도를 초과했습니다.')
    return {'mime': 'image/jpeg', 'data': base64.b64encode(data).decode('ascii')}


def validate_model_stage(stage):
    model_id = stage['model_id']; kind = stage.get('kind', stage['id'])
    prompts.text(stage)
    if stage.get('model_profile'):
        catalog.validate_profile(stage['model_profile'], kind)
        if model_id != stage['model_profile']['model_id'] or stage.get('revision') != stage['model_profile']['resolved_revision']: raise ValueError('등록 모델과 실행 모델이 다릅니다.')
        if stage.get('image_similarity'): raise ValueError('등록 모델에는 별도 이미지 유사도 노드를 연결해 주세요.')
        return
    if model_id not in MODEL_STAGES: raise ValueError('미등록 모델입니다. Hugging Face 모델 추가에서 계열과 실행 설정을 확인해 주세요.')
    if kind == 'logo_similarity' and model_id not in SIGLIP2_MODELS:
        raise ValueError('이미지 유사도 단계에는 SigLIP2 모델을 선택해 주세요.')
    if 'image_similarity' in stage and (type(stage['image_similarity']) is not bool or model_id != XFEAT or kind != 'logo_compare'):
        raise ValueError('이미지 유사도 추가 계산은 XFeat 로고 대조에서만 선택할 수 있습니다.')
    if 'image_similarity_model' in stage and (not stage.get('image_similarity') or stage['image_similarity_model'] not in SIGLIP2_MODELS):
        raise ValueError('추가 계산에 사용할 SigLIP2 모델을 확인해 주세요.')
    if model_id in MODEL_STAGES and kind not in MODEL_STAGES[model_id]:
        raise ValueError(f'{model_id}는 {stage["id"]} 단계에서 지원하지 않습니다. 지원 단계: {", ".join(MODEL_STAGES[model_id])}')
    labels = stage.get('labels')
    if model_id in SIGLIP2_MODELS and kind == 'visual_extract':
        if not isinstance(labels, list) or not 1 <= len(labels) <= 32 or any(not isinstance(label, str) or not label.strip() or len(label) > 200 for label in labels):
            raise ValueError('SigLIP2에는 비교할 후보 문구 1~32개가 필요합니다. 문구당 최대 200자입니다.')
        if len(set(label.strip() for label in labels)) != len(labels): raise ValueError('후보 문구가 중복됐습니다.')
    elif labels is not None:
        raise ValueError('후보 문구 설정은 SigLIP2 시각 요소 추출에서만 사용합니다.')


def workflow_dependencies(config):
    ids = [stage['id'] for stage in config['stages']]
    if config['flow'] == 'similarity':
        return {name: set(ids[index - 1:index]) for index, name in enumerate(ids)}
    if config['flow'] == 'video' and config.get('purpose') != 'prepare': ids.insert(1 if ids[0] == 'conditions' else 0, 'frames')
    workflow = config.get('workflow')
    if workflow is None:
        if any(name in ids for name in ('logo_similarity', 'candidate_filter')):
            raise ValueError('이미지 유사도와 후보 필터는 워크플로우 편집을 활성화해 주세요.')
        return {name: set(ids[index - 1:index]) for index, name in enumerate(ids)}
    if not isinstance(workflow, dict) or set(workflow) - {'edges', 'max_parallel', 'agents'} or not {'edges', 'max_parallel'} <= workflow.keys() or type(workflow['max_parallel']) is not int or workflow['max_parallel'] not in (1, 2):
        raise ValueError('워크플로우는 연결 목록과 동시 실행 수 1 또는 2가 필요합니다.')
    agents = workflow.get('agents', [])
    if not isinstance(agents, list) or len(agents) > 32 or any(not isinstance(agent, dict) or set(agent) != {'id', 'name'} or not isinstance(agent['id'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', agent['id']) or not isinstance(agent['name'], str) or not 1 <= len(agent['name']) <= 100 for agent in agents):
        raise ValueError('에이전트 이름과 식별자를 확인해 주세요.')
    agent_ids = {agent['id'] for agent in agents}
    if len(agent_ids) != len(agents) or any(stage.get('agent_id') is not None and (not isinstance(stage['agent_id'], str) or stage['agent_id'] not in agent_ids) for stage in config['stages']): raise ValueError('에이전트 연결 정보가 올바르지 않습니다.')
    edges = workflow['edges']
    if not isinstance(edges, list) or len(edges) > len(ids) ** 2: raise ValueError('워크플로우 연결 목록이 올바르지 않습니다.')
    dependencies = {name: set() for name in ids}
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != {'source', 'target'}: raise ValueError('워크플로우 연결 형식이 올바르지 않습니다.')
        source, target = edge['source'], edge['target']
        if not isinstance(source, str) or not isinstance(target, str) or source not in dependencies or target not in dependencies or source == target or source in dependencies[target]:
            raise ValueError('존재하지 않는 단계, 자기 연결 또는 중복 연결입니다.')
        dependencies[target].add(source)
    ancestors = {}; pending = dict(dependencies)
    while pending:
        ready = [name for name, parents in pending.items() if parents <= ancestors.keys()]
        if not ready: raise ValueError('워크플로우에 순환 연결이 있습니다.')
        for name in ready:
            ancestors[name] = set(pending.pop(name))
            for parent in dependencies[name]: ancestors[name].update(ancestors[parent])
    required = {'logo_similarity': {'logo_extract'}, 'candidate_filter': {'logo_similarity'}, 'logo_compare': {'logo_extract'},
        'text_compare': {'text_extract'}, 'visual_compare': {'visual_extract'}}
    definitions = {stage['id']: stage for stage in config['stages']}
    definitions['frames'] = {'id': 'frames'}
    dynamic = any('kind' in stage for stage in config['stages'])
    for name in ids:
        stage = definitions[name]; kind = stage.get('kind', name); bindings = stage.get('inputs', {})
        if kind in catalog.EXTRA_STAGES:
            allowed = catalog.INPUTS.get(kind, {})
            for role, source in bindings.items():
                upstream = definitions.get(source, {})
                if role not in allowed or source not in ancestors[name] or catalog.OUTPUTS.get(upstream.get('kind', source)) not in allowed[role]: raise ValueError(f'{stage.get("name", name)}: {role} 입력·출력 형식 또는 선행 연결이 맞지 않습니다.')
            needed = {'vector_compare': {'left', 'right'}, 'rrf': {'left', 'right'}, 'document_classify': {'ocr'}}.get(kind, set())
            if kind in PAIR_STAGES: needed = {'logo_extract'}
            if stage.get('params', {}).get('image_source') == 'crops': needed = needed | {'images'}
            if not needed <= bindings.keys(): raise ValueError(f'{stage.get("name", name)}: {", ".join(sorted(needed))} 입력 연결이 필요합니다.')
            if config['flow'] == 'video' and kind not in ('model_prepare', 'disk_lightglue_prepare', 'text_input', 'text_embed', 'text_classify', 'vector_compare', 'bm25', 'rrf', 'tfidf_lr') and 'frames' not in ancestors[name]: raise ValueError(f'{name}: 프레임 추출에서 이어지는 연결이 필요합니다.')
            if kind in PAIR_STAGES and 'candidate_filter' in bindings:
                gate = definitions[bindings['candidate_filter']]
                similarity = definitions[gate.get('inputs', {}).get('logo_similarity', 'logo_similarity')]
                if similarity.get('inputs', {}).get('logo_extract', 'logo_extract') != bindings['logo_extract']: raise ValueError('필터 후보와 대조는 같은 로고 추출 결과를 사용해야 합니다.')
            if kind == 'vector_compare':
                profiles = [definitions[bindings[role]].get('model_profile', {}) for role in ('left', 'right')]
                if not profiles[0].get('fingerprint') or profiles[0]['fingerprint'] != profiles[1].get('fingerprint'): raise ValueError('같은 모델·revision·처리 설정에서 생성한 벡터만 비교할 수 있습니다.')
            if kind == 'document_classify':
                upstream = definitions[bindings['ocr']]
                if upstream.get('model_profile', {}).get('family') not in ('paddle_ocr', 'easyocr'): raise ValueError('LayoutXLM OCR 입력에는 단어와 좌표를 제공하는 PP-OCR 또는 EasyOCR 노드가 필요합니다.')
                if stage.get('params', {}).get('image_source', 'media') != 'media': raise ValueError('LayoutXLM은 OCR과 같은 원본 이미지·프레임을 사용해야 합니다.')
            if kind == 'rerank' and stage.get('params', {}).get('modality') == 'image' and 'image' not in stage.get('model_profile', {}).get('modalities', []): raise ValueError('이 재정렬 모델은 이미지 후보를 지원하지 않습니다.')
            continue
        roles = set(required.get(kind, ()))
        if kind in ('text_compare', 'visual_compare') and ('conditions' in bindings or not config.get('requirements', '').strip() or (not dynamic and 'conditions' in ids)): roles.add('conditions')
        if kind == 'logo_compare' and ('candidate_filter' in bindings or (not dynamic and 'candidate_filter' in ids)): roles.add('candidate_filter')
        for role in roles | bindings.keys():
            source = bindings.get(role, role)
            definition = definitions.get(source, {})
            if source not in ancestors[name] or definition.get('kind', definition.get('id')) != role:
                raise ValueError(f'{stage.get("name", name)}: {role} 입력에 연결된 선행 단계가 필요합니다.')
        if config['flow'] == 'video' and kind not in ('conditions', 'frames', 'text_generate') and 'frames' not in ancestors[name]: raise ValueError(f'{name}: 프레임 추출에서 이어지는 연결이 필요합니다.')
        if kind == 'logo_compare' and 'candidate_filter' in roles:
            filter_stage = definitions[bindings.get('candidate_filter', 'candidate_filter')]
            similarity = definitions[filter_stage.get('inputs', {}).get('logo_similarity', 'logo_similarity')]
            if similarity.get('inputs', {}).get('logo_extract', 'logo_extract') != bindings.get('logo_extract', 'logo_extract'):
                raise ValueError('필터 후보와 로고 대조는 같은 로고 추출 결과를 사용해야 합니다.')
    if not dynamic and 'candidate_filter' in ids and 'logo_compare' not in ids: raise ValueError('후보 필터를 로고 대조 단계에 연결해 주세요.')
    if any(stage.get('kind', stage['id']) == 'logo_similarity' for stage in config['stages']) and any(stage.get('image_similarity') for stage in config['stages']):
        raise ValueError('별도 이미지 유사도 노드와 XFeat의 추가 SigLIP2 계산은 함께 선택할 수 없습니다.')
    return dependencies


def filter_candidates(rows, stage):
    scope = stage.get('scope', 'references_per_crop'); groups = {}; identities = set()
    if scope not in FILTER_SCOPES: raise ValueError('후보 선정 기준을 확인해 주세요.')
    for ri, row in enumerate(rows):
        for mi, match in enumerate(row['matches']):
            score = match.get('image_cosine_similarity')
            if type(score) not in (int, float) or not math.isfinite(score): raise ValueError('후보의 이미지 유사도 값이 올바르지 않습니다.')
            identities.add((match.get('similarity_model'), match.get('similarity_revision'), match.get('space_id')))
            key = (ri, match['logo_index']) if scope == 'references_per_crop' else (ri, match['reference_index']) if scope == 'crops_per_reference_frame' else match['reference_index']
            groups.setdefault(key, []).append((ri, mi, match))
    if len(identities) > 1: raise ValueError('모델·revision·전처리가 다른 유사도 결과를 함께 순위화할 수 없습니다.')
    decisions = {}
    for group in groups.values():
        group.sort(key=lambda item: (-item[2]['image_cosine_similarity'], rows[item[0]].get('index', item[0]), item[2]['logo_index'], item[2]['reference_index']))
        selected = 0
        for rank, (ri, mi, match) in enumerate(group, 1):
            reason = 'below_threshold' if stage.get('threshold') is not None and match['image_cosine_similarity'] < stage['threshold'] else 'outside_top_k' if stage.get('top_k') is not None and selected >= stage['top_k'] else None
            if reason is None: selected += 1
            decisions[ri, mi] = (rank, reason)
    total = len(decisions); selected_total = sum(reason is None for _, reason in decisions.values()); result = []
    for ri, row in enumerate(rows):
        candidates = []; excluded = []
        for mi, match in enumerate(row['matches']):
            rank, reason = decisions[ri, mi]; value = {**match, 'candidate_rank': rank}
            if reason is None: candidates.append(value)
            else: excluded.append({**value, 'exclusion_reason': reason})
        candidates.sort(key=lambda match: (match['logo_index'], -match['image_cosine_similarity'], match['reference_index']))
        result.append({**row, 'matches': candidates, 'excluded_matches': excluded, 'candidate_count': len(row['matches']), 'selected_count': len(candidates),
                       'filter': {'scope': scope, 'threshold': stage.get('threshold'), 'top_k': stage.get('top_k'), 'total_candidates': total, 'total_selected': selected_total}})
    return result


def validate_config(config):
    if not isinstance(config, dict) or config.get('contract') != CONTRACT or config.get('flow') not in ('image', 'video', 'similarity'):
        raise ValueError('검수 요청 형식이 올바르지 않습니다.')
    if 'workflow' in config and not isinstance(config['workflow'], dict): raise ValueError('워크플로우 연결 형식이 올바르지 않습니다.')
    stages = config.get('stages')
    if not isinstance(stages, list) or not 1 <= len(stages) <= 64:
        raise ValueError('실행할 모델 단계를 선택해 주세요.')
    ids = []
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get('id'), str) or stage['id'] == 'frames' or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', stage['id']) or stage.get('kind', stage['id']) not in STAGES:
            raise ValueError('지원하지 않는 모델 단계입니다.')
        common = {'id', 'kind', 'name', 'agent_id', 'inputs', 'params'}
        params = stage.get('params', {})
        if not isinstance(params, dict) or set(params) - {'documents', 'query', 'image_source', 'modality', 'classifier'} or len(json.dumps(params, allow_nan=False)) > 50000: raise ValueError('일반 처리 입력 설정은 50KB 이내여야 합니다.')
        if 'query' in params and (not isinstance(params['query'], str) or len(params['query']) > 12000): raise ValueError('질의는 12,000자 이하여야 합니다.')
        if 'documents' in params and (not isinstance(params['documents'], list) or len(params['documents']) > 256 or any(not isinstance(text, str) or len(text) > 12000 for text in params['documents'])): raise ValueError('텍스트 후보는 256개 이하의 문자열 목록이어야 합니다.')
        if params.get('image_source', 'media') not in ('media', 'references', 'crops') or params.get('modality', 'text') not in ('text', 'image'): raise ValueError('입력 이미지·텍스트 유형을 확인해 주세요.')
        if 'kind' in stage and config.get('workflow') is None: raise ValueError('사용자 정의 단계에는 워크플로우 연결이 필요합니다.')
        if stage.get('kind', stage['id']) in catalog.EXTRA_STAGES and stage.get('kind', stage['id']) not in ('model_prepare', 'disk_lightglue_prepare') and config.get('workflow') is None: raise ValueError('새 처리 노드는 에이전트 워크플로우 연결이 필요합니다.')
        if stage.get('name') is not None and (not isinstance(stage['name'], str) or not 1 <= len(stage['name']) <= 100): raise ValueError('단계 이름은 1~100자여야 합니다.')
        if 'inputs' in stage and (not isinstance(stage['inputs'], dict) or set(stage['inputs']) - set(STAGES) - {'left', 'right', 'text', 'images', 'ocr', 'candidates'} or any(not isinstance(value, str) for value in stage['inputs'].values())): raise ValueError('단계 입력 연결이 올바르지 않습니다.')
        if stage.get('kind', stage['id']) in catalog.GENERAL:
            if set(stage) - common: raise ValueError('일반 처리 노드에는 HF 모델을 지정하지 않습니다.')
            ids.append(stage['id']); continue
        if stage.get('kind', stage['id']) == 'candidate_filter':
            threshold, top_k = stage.get('threshold'), stage.get('top_k')
            if set(stage) - common - {'threshold', 'top_k', 'scope'} or stage.get('scope', 'references_per_crop') not in FILTER_SCOPES or (threshold is None and top_k is None): raise ValueError('필터의 선정 기준·최소 유사도·상위 후보 수를 확인해 주세요.')
            if threshold is not None and (type(threshold) not in (int, float) or not math.isfinite(threshold) or not -1 <= threshold <= 1): raise ValueError('최소 코사인 유사도는 -1~1 사이여야 합니다.')
            if top_k is not None and (type(top_k) is not int or not 1 <= top_k <= 10): raise ValueError('상위 후보 수는 1~10 사이의 정수여야 합니다.')
            ids.append(stage['id']); continue
        if set(stage) - common - {'model_id', 'revision', 'labels', 'image_similarity', 'image_similarity_model', 'prompt', 'prompt_override', 'registered_model', 'model_fingerprint', 'model_profile'}: raise ValueError('지원하지 않는 모델 설정입니다.')
        if stage.get('prompt') is not None and (not isinstance(stage['prompt'], str) or len(stage['prompt']) > 8000): raise ValueError('모델 지시는 8,000자 이하여야 합니다.')
        identifier = stage.get('model_id', '')
        if not isinstance(identifier, str) or not re.fullmatch(r'[\w][\w.-]{0,95}/[\w][\w.-]{0,95}', identifier, re.ASCII) or '..' in identifier or '--' in identifier or any(part.endswith(('.', '-', '.git')) for part in identifier.split('/')) or identifier.split('/')[0] in ('datasets', 'spaces'):
            raise ValueError('소유자/모델명 형식으로 입력해 주세요.')
        revision = stage.get('revision')
        if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r'[\w./-]{1,128}', revision, re.ASCII)):
            raise ValueError('모델 revision을 확인해 주세요.')
        validate_model_stage(stage)
        ids.append(stage['id'])
    if len(set(ids)) != len(ids) or (config.get('workflow') is None and ids != [name for name in STAGES if name in ids]):
        raise ValueError('모델 단계의 순서 또는 중복을 확인해 주세요.')
    requirements = config.get('requirements', '')
    if not isinstance(requirements, str) or len(requirements) > 12000:
        raise ValueError('직접 입력 조건은 12,000자 이하여야 합니다.')
    files = config.get('files')
    if config['flow'] == 'similarity' and config.get('purpose') is not None:
        raise ValueError('유사도 테스트는 별도 준비·샘플 요청 없이 두 모델을 실행합니다.')
    if config.get('purpose') == 'prepare':
        if len(stages) != 1 or stages[0].get('kind', stages[0]['id']) not in ('model_prepare', 'disk_lightglue_prepare') or files != {'media': None, 'documents': [], 'references': []}: raise ValueError('모델 준비는 첨부 없이 준비 단계 하나로 실행해야 합니다.')
        workflow_dependencies(config); return config
    if any(stage.get('kind', stage['id']) in ('model_prepare', 'disk_lightglue_prepare') for stage in stages): raise ValueError('모델 준비는 검수와 별도로 실행해 주세요.')
    if not isinstance(files, dict) or not isinstance(files.get('media'), dict):
        raise ValueError('검수할 파일이 필요합니다.')
    for kind in ('documents', 'references'):
        maximum = 100 if config['flow'] == 'similarity' and kind == 'references' else 10
        if not isinstance(files.get(kind), list) or len(files[kind]) > maximum or not all(isinstance(item, dict) for item in files[kind]):
            raise ValueError('대조 이미지는 최대 100개입니다.' if maximum == 100 else '문서와 레퍼런스는 각각 최대 10개입니다.')
    if config['flow'] == 'similarity':
        if config.get('workflow') is not None or config.get('purpose') is not None or files['documents'] or not files['references']:
            raise ValueError('유사도 테스트는 기준 이미지 1장과 대조 이미지 1~100장으로 실행합니다.')
        if ids != ['logo_similarity', 'logo_compare'] or stages[1].get('model_id') != XFEAT or any(set(stage) - {'id', 'name', 'model_id', 'revision', 'registered_model', 'model_fingerprint', 'model_profile'} for stage in stages):
            raise ValueError('유사도 테스트는 이미지 유사도와 XFeat 두 단계로 실행해야 합니다.')
        for field, label in (('resize', '기준 이미지'), ('reference_resize', '대조 이미지')):
            resize = config.get(field)
            if resize is not None:
                pixels = isinstance(resize, dict) and set(resize) == {'width', 'height'} and all(type(value) is int and 1 <= value <= 8192 for value in resize.values())
                percent = isinstance(resize, dict) and set(resize) == {'percent'} and type(resize['percent']) in (int, float) and math.isfinite(resize['percent']) and 0.01 <= resize['percent'] <= 100
                if not (pixels or percent): raise ValueError(f'{label} 다운사이징은 가로·세로 1~8,192px 정수 또는 비율 0.01~100%로 입력해 주세요.')
        return config
    kinds = [stage.get('kind', stage['id']) for stage in stages]
    if config['flow'] == 'image' and (not files['documents'] or files['references'] or any(name.startswith('logo_') or name == 'candidate_filter' for name in kinds)):
        raise ValueError('이미지 검수에는 문서가 필요하며 영상 전용 단계는 사용할 수 없습니다.')
    if 'conditions' in kinds and not files['documents']:
        raise ValueError('조건 추출에는 문서가 필요합니다.')
    if any(stage.get('kind', stage['id']) == 'conditions' and stage.get('model_profile', {}).get('family') == 'qwen_text' for stage in stages) and any(str(item.get('name', '')).lower().endswith('.pdf') for item in files['documents']):
        raise ValueError('PDF 조건 추출은 페이지 이미지를 사용합니다. 이미지 입력 모델을 선택하거나 텍스트 문서를 첨부해 주세요.')
    if any(name in kinds for name in PAIR_STAGES) and ('logo_extract' not in kinds or not files['references']):
        raise ValueError('로고 대조에는 로고 추출과 원본 로고가 필요합니다.')
    for name in ('text', 'visual'):
        if f'{name}_compare' in kinds and (f'{name}_extract' not in kinds or ('conditions' not in kinds and not requirements.strip())):
            raise ValueError('대조에는 추출 결과와 검수 조건이 필요합니다.')
    if config['flow'] == 'video':
        sample = config.get('sampling', {})
        n, count = sample.get('interval'), sample.get('max_frames')
        if sample.get('unit') not in ('frames', 'seconds') or type(n) not in (int, float) or not math.isfinite(n) or not 0 < n <= 3600 or (sample['unit'] == 'frames' and n != int(n)) or type(count) is not int or not 1 <= count <= 1000:
            raise ValueError('프레임 간격과 최대 추출 수를 확인해 주세요.')
    workflow_dependencies(config)
    return config


def runtime_health():
    missing = [name for name in MODULES if importlib.util.find_spec(name) is None]
    cuda = False
    if 'torch' not in missing:
        import torch
        cuda = torch.cuda.is_available()
    return {'contract': CONTRACT, 'ready': not missing and cuda, 'stages': list(STAGES),
            'formats': FORMATS, 'limits': LIMITS, 'cancel': True, 'similarity_test': 1, 'similarity_resize_percent': True, 'similarity_reference_resize': True, 'candidate_filter_scopes': list(FILTER_SCOPES), 'workflow': {'version': 1, 'max_parallel': 2, 'agents': True}, 'registry_version': 1, 'prompt_overrides': 1, 'library_versions': catalog.installed_versions(),
            'models': [{'id': model_id, 'stages': list(stages)} for model_id, stages in MODEL_STAGES.items()],
            'setup_required': bool(missing), 'can_prepare': 'torch' not in missing and cuda, 'missing': missing, 'cuda': cuda,
            'detail': '준비 완료' if not missing and cuda else 'Pod 패키지 준비가 필요합니다.' if missing else 'Pod에서 CUDA GPU를 확인하지 못했습니다.'}


def diagnostic_text(detail):
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(detail))
    text = re.sub(r'(https?://)[^\s/]+@', r'\1***@', text)
    text = re.sub(r'(https?://[^\s?#]+)[?#][^\s]*', r'\1?***', text)
    text = re.sub(r'\bhf_[A-Za-z0-9_]{20,}\b', '***', text)
    return text[-6000:].strip()


def setup_error(stage, detail):
    return f'{stage} 실패\n{diagnostic_text(detail)}'


def setup_command(stage, args, timeout=180, *, input=None):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', timeout=timeout, input=input)
    except subprocess.TimeoutExpired as error:
        output = error.output or b''
        if isinstance(output, bytes): output = output.decode('utf-8', errors='replace')
        raise ValueError(setup_error(stage, f'{timeout}초 시간 제한 초과\n{output[-5500:]}')) from None
    except OSError as error:
        raise ValueError(setup_error(stage, f'{type(error).__name__}: {error}')) from None
    if result.returncode:
        raise ValueError(setup_error(stage, f'종료 코드 {result.returncode}\n{result.stdout[-5500:]}'))
    return result.stdout


def prepare_runtime(root):
    from importlib.metadata import version, PackageNotFoundError
    root.mkdir(parents=True, exist_ok=True)
    python = root / 'venv/bin/python'
    # Re-run without --clear: interrupted creation may leave Python but omit site access or pip.
    setup_command('가상환경 생성·복구', [sys.executable, '-m', 'venv', '--system-site-packages', str(root / 'venv')])
    expected_torch = version('torch')
    code = ('import torch; '
            f'assert torch.__version__ == {expected_torch!r}, "기존 PyTorch 버전과 다릅니다: " + torch.__version__; '
            'assert torch.cuda.is_available(), "가상환경에서 CUDA를 사용할 수 없습니다."')
    setup_command('가상환경 PyTorch·CUDA 확인', [str(python), '-c', code], timeout=60)
    pinned = []
    for name in ('torch', 'torchvision'):
        try: pinned.append(f'{name}=={version(name)}')
        except PackageNotFoundError: pass
    constraints = root / 'existing-torch.txt'; constraints.write_text('\n'.join(pinned), encoding='utf-8')
    setup_command('패키지 설치', [str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                               '--progress-bar', 'off', '-c', str(constraints), *PACKAGES], timeout=1200)


def probe_runtime(root, source, base_health):
    python = root / 'venv/bin/python'
    if not python.exists():
        return {**base_health, 'ready': False, 'setup_required': True, 'detail': '전용 실행환경을 준비해 주세요. 기존 PyTorch는 재사용합니다.'}
    code = ('import sys,json; exec(sys.stdin.buffer.read().decode("utf-8")); health=runtime_health()\n'
            'if health["ready"]:\n'
            '    for name in MODULES: __import__(name)\n'
            'print(json.dumps(health))')
    # Bundled adapters exceed Linux's per-argument limit; stream source instead of putting it in argv.
    output = setup_command('실행환경 확인', [str(python), '-c', code], timeout=60, input=source)
    health = json.loads(output.strip().splitlines()[-1])
    # A broken dedicated environment must not hide the repair action when the base CUDA runtime works.
    health['can_prepare'] = base_health['can_prepare']
    return health


def image_of(path):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 30_000_000
    with Image.open(path) as source:
        if source.width * source.height > 30_000_000:
            raise ValueError('이미지는 3,000만 픽셀 이하여야 합니다.')
        return ImageOps.exif_transpose(source).convert('RGB')


def similarity_image(path, resize, target, label):
    from PIL import Image
    image = image_of(path); original = image.size
    if resize is not None:
        size = tuple(max(1, math.floor(length * resize['percent'] / 100 + 0.5)) for length in original) if 'percent' in resize else (resize['width'], resize['height'])
        if any(target > source for target, source in zip(size, original)):
            raise ValueError(f'다운사이징 크기는 {label} 원본 {original[0]} × {original[1]}px 이하여야 합니다.')
        image = image.resize(size, Image.Resampling.LANCZOS)
    # Keep the measurement input lossless; the JPEG below is only a preview.
    image.save(target)
    return target, {'original_width': original[0], 'original_height': original[1], 'width': image.width, 'height': image.height,
                    'resized': image.size != original, 'resize_filter': 'Lanczos' if resize is not None else None}


def document_parts(path, directory):
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        try:
            if not 1 <= len(pdf) <= 40:
                raise ValueError('PDF는 문서당 1~40페이지로 첨부해 주세요.')
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    width, height = page.get_size()
                    bitmap = page.render(scale=min(2, 2000 / max(width, height)))
                    try:
                        image = bitmap.to_pil().convert('RGB')
                        target = directory / f'{path.stem}-page-{index}.jpg'; image.save(target)
                    finally: bitmap.close()
                    yield {'page': index + 1, 'text': '', 'images': [target]}
                finally: page.close()
        finally: pdf.close()
        return
    if suffix in ('.txt', '.md'):
        text = path.read_text(encoding='utf-8-sig')
    else:
        with zipfile.ZipFile(path) as archive:
            names = ['word/document.xml'] if suffix == '.docx' else [name for name in archive.namelist() if re.fullmatch(r'Contents/section\d+\.xml', name)]
            if not names or sum(archive.getinfo(name).file_size for name in names) > 8 * 1024**2:
                raise ValueError('문서 내용이 없거나 압축 해제 크기를 초과했습니다.')
            text = '\n'.join(' '.join(ET.fromstring(archive.read(name)).itertext()) for name in names)
    if not text.strip() or len(text) > 200_000:
        raise ValueError('텍스트가 없거나 너무 긴 문서입니다. 이미지 기반 문서는 PDF로 첨부해 주세요.')
    for index in range(0, len(text), 8000):
        yield {'page': index // 8000 + 1, 'text': text[index:index + 8000], 'images': []}


def frames_of(path, sampling, directory, container_format):
    import av
    frames = []; next_time = 0.0; first_time = None
    with av.open(str(path), format=container_format, options={'protocol_whitelist': 'file'}) as container:
        for index, frame in enumerate(container.decode(video=0)):
            if frame.time is None:
                raise ValueError('프레임 타임스탬프를 읽을 수 없습니다.')
            if first_time is None: first_time = float(frame.time)
            seconds = max(0.0, float(frame.time) - first_time)
            keep = index % int(sampling['interval']) == 0 if sampling['unit'] == 'frames' else seconds >= next_time
            if not keep: continue
            if sampling['unit'] == 'seconds': next_time = (math.floor(seconds / sampling['interval']) + 1) * sampling['interval']
            image = frame.to_image(); image.thumbnail((1920, 1920))
            target = directory / f'frame-{index:08d}.jpg'; image.save(target)
            frames.append({'index': index, 'timestamp_seconds': seconds, 'path': target,
                           'preview': target.name, 'width': image.width, 'height': image.height})
            if len(frames) >= sampling['max_frames']: break
    if not frames: raise ValueError('영상에서 프레임을 추출하지 못했습니다.')
    return frames


def ensure_llama_server():
    root = Path(sys.prefix) / 'modeltest-llama' / LLAMA_COMMIT
    binary = root / 'build/bin/llama-server'; marker = root / 'build/modeltest-ready.txt'
    with LLAMA_BUILD_LOCK:
        if binary.is_file() and marker.is_file() and marker.read_text().strip() == LLAMA_COMMIT: return binary
        nvcc = shutil.which('nvcc') or ('/usr/local/cuda/bin/nvcc' if Path('/usr/local/cuda/bin/nvcc').is_file() else None)
        if not shutil.which('git') or not (shutil.which('g++') or shutil.which('clang++')) or not nvcc:
            raise ValueError('PaddleOCR GGUF 준비에는 Pod의 git, C++ 컴파일러와 CUDA nvcc가 필요합니다. CUDA devel 이미지에서 실행하거나 해당 개발 도구를 설치해 주세요.')
        cmake = str(Path(sys.executable).parent / 'cmake')
        if not Path(cmake).is_file():
            setup_command('GGUF 빌드 도구 설치', [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', 'cmake>=3.28,<5'], timeout=300)
        root.mkdir(parents=True, exist_ok=True)
        if not (root / '.git').is_dir(): setup_command('GGUF 실행기 저장소 준비', ['git', 'init', str(root)])
        setup_command('고정 버전 GGUF 실행기 다운로드', ['git', '-C', str(root), 'fetch', '--depth=1', '--no-tags', 'https://github.com/ggml-org/llama.cpp.git', LLAMA_COMMIT], timeout=300)
        setup_command('GGUF 실행기 버전 고정', ['git', '-C', str(root), 'checkout', '--detach', LLAMA_COMMIT])
        setup_command('GGUF CUDA 빌드 설정', [cmake, '-S', str(root), '-B', str(root / 'build'), '-DGGML_CUDA=ON',
            f'-DCMAKE_CUDA_COMPILER={nvcc}', '-DCMAKE_CUDA_ARCHITECTURES=native', '-DCMAKE_BUILD_TYPE=Release',
            '-DLLAMA_BUILD_TESTS=OFF', '-DLLAMA_BUILD_EXAMPLES=OFF', '-DLLAMA_BUILD_APP=OFF', '-DLLAMA_BUILD_SERVER=ON',
            '-DLLAMA_BUILD_UI=OFF', '-DLLAMA_USE_PREBUILT_UI=OFF', '-DLLAMA_OPENSSL=OFF'], timeout=300)
        setup_command('GGUF CUDA 실행기 빌드', [cmake, '--build', str(root / 'build'), '--target', 'llama-server', '-j', '2'], timeout=1800)
        if not binary.is_file(): raise ValueError('GGUF 빌드가 끝났지만 llama-server를 찾지 못했습니다.')
        marker.write_text(LLAMA_COMMIT)
        return binary


class GGUFServer:
    def __init__(self, revision=None, *, model_id=PADDLE_GGUF, weight_file='PaddleOCR-VL-1.6-GGUF.gguf', projector_file='PaddleOCR-VL-1.6-GGUF-mmproj.gguf', local_files_only=False):
        from huggingface_hub import hf_hub_download, model_info
        self.process = None; self.log = None; self.token = secrets.token_hex(32)
        if local_files_only:
            root = Path(sys.prefix) / 'modeltest-llama' / LLAMA_COMMIT
            binary = root / 'build/bin/llama-server'; marker = root / 'build/modeltest-ready.txt'
            if not binary.is_file() or not marker.is_file() or marker.read_text().strip() != LLAMA_COMMIT:
                raise ValueError('GGUF 실행기 준비가 필요합니다. 모델 준비를 다시 실행해 주세요.')
        else: binary = ensure_llama_server()
        if not local_files_only: revision = model_info(model_id, revision=revision).sha
        kwargs = {'local_files_only': True} if local_files_only else {}
        model = hf_hub_download(model_id, weight_file, revision=revision, **kwargs)
        projector = hf_hub_download(model_id, projector_file, revision=revision, **kwargs)
        with socket.socket() as address:
            address.bind(('127.0.0.1', 0)); port = address.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'; self.log = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen([str(binary), '-m', model, '--mmproj', projector, '--host', '127.0.0.1', '--port', str(port),
                '--alias', 'paddle-ocr', '--n-gpu-layers', '99', '--ctx-size', '8192', '--parallel', '1', '--threads', '2', '--temp', '0'],
                stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT, env={**os.environ, 'LLAMA_API_KEY': self.token})
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if self.process.poll() is not None: raise ValueError(self.failure())
                try:
                    if self.request('/health', timeout=3).get('status') == 'ok': return
                except URLError: pass
                time.sleep(0.5)
            raise ValueError('PaddleOCR GGUF 모델 로딩 시간이 초과됐습니다. Pod 메모리와 실행환경을 확인해 주세요.\n' + self.failure())
        except Exception:
            self.close(); raise

    def failure(self):
        self.log.seek(0, io.SEEK_END); size = self.log.tell(); self.log.seek(max(0, size - 4000))
        detail = self.log.read().decode('utf-8', errors='replace').replace(self.token, '[redacted]')
        return setup_error('PaddleOCR GGUF 실행기', detail)

    def request(self, path, payload=None, timeout=180):
        request = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        with urlopen(request, timeout=timeout) as response: raw = response.read(8 * 1024**2 + 1)
        if len(raw) > 8 * 1024**2: raise ValueError('GGUF 응답 크기 한도를 초과했습니다.')
        return json.loads(raw)

    def ocr(self, path, prompt=None):
        output = io.BytesIO(); image_of(path).save(output, format='JPEG', quality=95)
        payload = {'model': 'paddle-ocr', 'temperature': 0, 'max_tokens': 1536, 'stream': False, 'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode('ascii')}}, {'type': 'text', 'text': prompts.DEFAULTS['ocr'] if prompt is None else prompt}]}]}
        try: response = self.request('/v1/chat/completions', payload)
        except (URLError, TimeoutError) as error: raise ValueError('PaddleOCR GGUF 추론 요청에 실패했습니다. Pod 메모리와 실행기 상태를 확인해 주세요.') from error
        choices = response.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict): raise ValueError('PaddleOCR GGUF 응답 형식이 올바르지 않습니다.')
        choice = choices[0]; message = choice.get('message'); content = message.get('content') if isinstance(message, dict) else None
        if isinstance(content, list): content = ''.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text')
        if not isinstance(content, str): raise ValueError('PaddleOCR GGUF가 텍스트 응답을 반환하지 않았습니다.')
        return {'text': content, 'note': '출력 길이 제한에 도달했습니다. 이미지 영역을 나누어 다시 확인해 주세요.' if choice.get('finish_reason') == 'length' else 'OCR 텍스트입니다. 문구 좌표와 문서 레이아웃 분석은 포함하지 않습니다.'}

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=3)
                except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=3)
            self.process = None
        if self.log is not None: self.log.close(); self.log = None


class Models:
    def __init__(self): self.key = None; self.model = None; self.processor = None; self.kind = None; self.gguf = None; self.registered = None; self.classical = {}; self.execution = None

    def close(self):
        self.classical = {}
        if self.registered is not None: self.registered.close(); self.registered = None
        if self.gguf is not None: self.gguf.close(); self.gguf = None
        self.model = None; self.processor = None; self.key = None; self.execution = None

    def load(self, stage):
        key = (stage['model_id'], stage.get('revision'), stage.get('model_profile', {}).get('fingerprint'))
        if self.key == key: return
        import torch
        self.close(); gc.collect(); torch.cuda.empty_cache()
        if stage.get('model_profile'):
            self.registered = adapters.Adapter(stage['model_profile'], self, SimpleNamespace(**globals()))
            self.model = self.registered.model; self.processor = self.registered.processor
            self.kind = 'vision' if self.registered.family == 'qwen_vision' else self.registered.family; self.key = key
            self.execution = {**(self.execution or {}), 'model_id': key[0], 'revision': key[1], 'fingerprint': key[2], 'options': stage['model_profile']['options']}; return
        if key[0] == PADDLE_GGUF:
            self.gguf = GGUFServer(key[1]); self.kind = 'gguf'; self.key = key; return
        if key[0] == XFEAT:
            from huggingface_hub import hf_hub_download
            weights = hf_hub_download(key[0], 'xfeat.pt', revision=key[1], **({'local_files_only': True} if stage.get('local_files_only') else {}))
            # Only this reviewed, pinned implementation is allowed; HF supplies tensor weights only.
            source = XFEAT_SOURCE
            hub_options = {}
            if stage.get('local_files_only'):
                source = str(Path(torch.hub.get_dir()) / ('verlab_accelerated_features_' + XFEAT_SOURCE.split(':')[1]))
                if not Path(source).is_dir(): raise ValueError('XFeat 실행 코드 캐시가 없습니다. 가중치·환경 준비를 다시 실행해 주세요.')
                hub_options['source'] = 'local'
            self.model = torch.hub.load(source, 'XFeat', pretrained=False, top_k=1024, **hub_options,
                                        trust_repo=True, skip_validation=True, verbose=False)
            self.model.net.load_state_dict(torch.load(weights, map_location='cpu', weights_only=True))
            self.model.dev = torch.device('cuda'); self.model.to('cuda').eval()
            resolved = Path(weights).parent.name
            self.execution = {'model_id': XFEAT, 'requested_revision': key[1] or 'main', 'revision': resolved if re.fullmatch(r'[a-f0-9]{40}', resolved) else None,
                              'weights_sha256': hashlib.sha256(Path(weights).read_bytes()).hexdigest(), 'code_source': XFEAT_SOURCE}
            self.kind = 'features'; self.key = key; return
        from transformers import AutoConfig, AutoModel, AutoModelForCausalLM, AutoModelForImageTextToText, AutoModelForZeroShotObjectDetection, AutoProcessor, AutoTokenizer, Owlv2ForObjectDetection
        kwargs = {'revision': key[1], 'trust_remote_code': False}
        config = AutoConfig.from_pretrained(key[0], **kwargs)
        kwargs['revision'] = getattr(config, '_commit_hash', None) or key[1]
        if key[0] in OWLV2_MODELS:
            self.kind = 'owlv2'; cls = Owlv2ForObjectDetection
        elif key[0] in SIGLIP2_MODELS:
            self.kind = 'siglip2'; cls = AutoModel
        elif key[0] == QWEN:
            self.kind = 'vision'; cls = AutoModelForImageTextToText
        else: raise ValueError('미등록 모델입니다. 모델 계열과 실행 설정을 먼저 등록해 주세요.')
        self.processor = (AutoTokenizer if self.kind == 'text' else AutoProcessor).from_pretrained(key[0], **kwargs)
        self.model = cls.from_pretrained(key[0], **kwargs, use_safetensors=True, device_map='cuda', dtype='auto').eval()
        processor = getattr(self.processor, 'image_processor', None)
        preprocessing = {}
        for name in ('size', 'resample', 'image_mean', 'image_std', 'do_resize', 'do_normalize', 'do_rescale'):
            value = getattr(processor, name, None)
            # Transformers 5 image processors expose size as a SizeDict dataclass.
            preprocessing[name] = asdict(value) if is_dataclass(value) else value
        self.execution = {'model_id': key[0], 'revision': kwargs['revision'], 'preprocessing': preprocessing}
        self.key = key

    def generate(self, prompt, images=(), require_vision=False):
        if self.registered is not None: return self.registered.generate(prompt, images, require_vision)
        import torch
        if self.kind not in ('text', 'vision') or (require_vision and self.kind != 'vision'):
            raise ValueError('이 단계에는 이미지 입력을 지원하는 생성 모델이 필요합니다.')
        if self.kind == 'vision':
            content = [{'type': 'image', 'image': image_of(path)} for path in images] + [{'type': 'text', 'text': prompt}]
            messages = [{'role': 'user', 'content': content}]
        else: messages = [{'role': 'user', 'content': prompt}]
        inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors='pt', enable_thinking=False).to(self.model.device)
        with torch.inference_mode(): tokens = self.model.generate(**inputs, max_new_tokens=1536, do_sample=False)
        result = self.processor.batch_decode(tokens[:, inputs['input_ids'].shape[1]:], skip_special_tokens=True)[0]
        return result

    def logos(self, path, instruction=''):
        import torch
        image = image_of(path); width, height = image.size
        if self.kind == 'detection':
            inputs = self.processor(images=image, text='logo. brand logo.', return_tensors='pt').to(self.model.device)
            with torch.inference_mode(): output = self.model(**inputs)
            found = self.processor.post_process_grounded_object_detection(output, inputs.input_ids,
                threshold=0.35, text_threshold=0.25, target_sizes=[(height, width)])[0]
            return [{'bbox': [float(v) for v in box], 'score': float(score)} for box, score in zip(found['boxes'].cpu().tolist(), found['scores'].cpu().tolist())]
        raw = self.generate(instruction or prompts.DEFAULTS['generation']['logo_extract'], [path], True)
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
        boxes = json.loads(text)
        if not isinstance(boxes, list): raise ValueError('로고 모델이 좌표 JSON 배열을 반환하지 않았습니다.')
        result = []
        for box in boxes:
            values = box.get('bbox') if isinstance(box, dict) else None
            if not isinstance(values, list) or len(values) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1000 for v in values):
                raise ValueError('로고 모델의 좌표가 유효하지 않습니다.')
            result.append({'bbox': [values[0] * width / 1000, values[1] * height / 1000, values[2] * width / 1000, values[3] * height / 1000]})
        return result


def run_generated(models, stage, *, evidence='', images=(), require_vision=False):
    if stage.get('kind', stage['id']) == 'logo_extract': return {'logos': models.logos(images[0], prompts.text(stage))}
    return {'text': models.generate(prompts.compose(stage, evidence), images, require_vision)}


def run_qwen(models, stage, **inputs):
    return run_generated(models, stage, **inputs)


def run_owlv2(models, stage, *, images, **_):
    import torch
    image = image_of(images[0]); labels = [prompts.labels(stage)]
    inputs = models.processor(text=labels, images=image, return_tensors='pt').to(models.model.device)
    with torch.inference_mode(): output = models.model(**inputs)
    found = models.processor.post_process_grounded_object_detection(output, threshold=0.2,
        target_sizes=[(image.height, image.width)], text_labels=labels)[0]
    return {'logos': [{'bbox': box, 'score': score} for box, score in
                     zip(found['boxes'].cpu().tolist(), found['scores'].cpu().tolist())]}


def run_siglip2(models, stage, *, images, **_):
    import torch
    labels = [label.strip() for label in stage['labels']]
    inputs = models.processor(text=labels, images=image_of(images[0]), padding='max_length',
                              max_length=64, truncation=True, return_tensors='pt').to(models.model.device)
    with torch.inference_mode(): output = models.model(**inputs)
    scores = torch.sigmoid(output.logits_per_image[0]).float().cpu().tolist()
    return {'label_scores': sorted([{'label': label, 'score': score} for label, score in zip(labels, scores)],
                                   key=lambda item: item['score'], reverse=True),
            'note': '후보 문구별 이미지 유사도입니다. 보정된 확률이나 문구 OCR, 검수 합격 판정이 아닙니다.'}


def run_xfeat(models, stage, *, images, **_):
    import cv2
    import numpy as np
    import torch
    features = []; scales = []; image_sizes = []
    for path in images:
        image = image_of(path); width, height = image.size; image.thumbnail((1024, 1024))
        image_sizes.append([width, height])
        image = image.resize((max(32, image.width), max(32, image.height)))
        scales.append(np.array([width / image.width, height / image.height]))
        tensor = torch.from_numpy(np.asarray(image, dtype=np.float32) / 255).permute(2, 0, 1)[None].to(models.model.dev)
        with torch.inference_mode(): features.append(models.model.detectAndCompute(tensor)[0])
    first, second = features
    result = {'keypoints': [len(first['keypoints']), len(second['keypoints'])], 'matches': 0, 'descriptor_similarity': None,
              'inliers': 0, 'inlier_ratio': 0.0, 'decision': 'uncertain', 'matched_points': [], 'image_sizes': image_sizes,
              'matching_settings': {**(getattr(models, 'execution', None) or {}), 'code_source': XFEAT_SOURCE, 'top_k': 1024, 'match_min_cosine': 0.82,
                                    'resize_max_side': 1024, 'resize_min_side': 32, 'ransac_px': 3.0, 'ransac_max_iters': 2000, 'ransac_confidence': 0.995,
                                    'candidate_min_inliers': 8, 'candidate_min_ratio': 0.5, 'color_comparison': 'not_implemented', 'teammate_reproduction': 'unverified'},
              'note': '특징점과 기하 검증에 따른 참고 결과입니다. 단순한 로고는 특징점 부족으로 판정하지 못할 수 있습니다.'}
    if not all(result['keypoints']): return result
    with torch.inference_mode(): a, b = models.model.match(first['descriptors'], second['descriptors'], min_cossim=0.82)
    points0 = first['keypoints'][a].cpu().numpy(); points1 = second['keypoints'][b].cpu().numpy()
    result['matches'] = len(points0)
    if len(points0):
        result['descriptor_similarity'] = round(float(torch.nn.functional.cosine_similarity(
            first['descriptors'][a], second['descriptors'][b], dim=-1).mean().item()), 4)
    if len(points0) < 4: return result
    matrix, mask = cv2.findHomography(points0, points1, cv2.RANSAC, 3.0, maxIters=2000, confidence=0.995)
    if matrix is None or mask is None or not np.isfinite(matrix).all(): return result
    inliers = mask.ravel().astype(bool); count = int(inliers.sum()); ratio = count / len(points0)
    result.update(inliers=count, inlier_ratio=round(ratio, 4))
    # shortcut: uncalibrated matching heuristic, calibrate on labelled logos before automatic acceptance.
    if count >= 8 and ratio >= 0.5: result['decision'] = 'candidate_match'
    result['matched_points'] = [{'crop': (p * scales[0]).tolist(), 'reference': (q * scales[1]).tolist()}
                                for p, q in zip(points0[inliers][:64], points1[inliers][:64])]
    return result


def image_similarity(models, images):
    if getattr(models, 'registered', None) is not None:
        adapter = models.registered; vectors = adapters.normalized(adapter.embed([str(path) for path in images]))
        return {'image_cosine_similarity': float(vectors[0] @ vectors[1]), 'similarity_model': adapter.profile['model_id'], 'similarity_revision': adapter.profile['resolved_revision'], 'space_id': adapter.profile['fingerprint'], 'metric': 'cosine_similarity'}
    import torch
    inputs = models.processor(images=[image_of(path) for path in images], return_tensors='pt').to(models.model.device)
    with torch.inference_mode():
        features = models.model.get_image_features(**inputs, return_dict=True).pooler_output
        score = torch.nn.functional.cosine_similarity(features[:1].float(), features[1:2].float()).item()
    execution = getattr(models, 'execution', None) or {'model_id': (getattr(models, 'key', None) or (SIGLIP2,))[0]}
    space = hashlib.sha256(json.dumps(execution, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return {'image_cosine_similarity': round(score, 4), 'similarity_model': execution['model_id'], 'similarity_revision': execution.get('revision'), 'space_id': space, 'metric': 'cosine_similarity'}


def run_paddle_gguf(models, stage, *, images, **_):
    if len(images) != 1: raise ValueError('PaddleOCR GGUF 문구 추출에는 이미지 한 장이 필요합니다.')
    return models.gguf.ocr(images[0], prompts.text(stage))


MODEL_HANDLERS = {OWLV2: run_owlv2, SIGLIP2: run_siglip2, XFEAT: run_xfeat, QWEN: run_qwen, PADDLE_GGUF: run_paddle_gguf}
MODEL_HANDLERS.update({OWLV2_ENSEMBLE: run_owlv2, SIGLIP2_SO400M: run_siglip2})


def run_model(models, stage, **inputs):
    kind = stage.get('kind', stage['id'])
    if kind in catalog.GENERAL: return adapters.classical_pair(kind, inputs['images'], SimpleNamespace(**globals()), getattr(models, 'classical', None))
    validate_model_stage(stage)
    if stage.get('model_profile'):
        adapter = models.registered
        if adapter.family in ('owlv2', 'grounding_dino'): return {'logos': adapter.detect(inputs['images'][0], labels=prompts.labels(stage))}
        if adapter.family == 'yolo': return {'logos': adapter.detect(inputs['images'][0])}
        if adapter.family in ('paddle_vl', 'paddle_gguf'): return adapter.ocr(inputs['images'][0], prompt=prompts.text(stage))
        if adapter.family in ('paddle_ocr', 'easyocr'): return adapter.ocr(inputs['images'][0])
        if adapter.family in ('xfeat', 'lightglue'): return adapter.compare(inputs['images'])
        return run_generated(models, stage, **inputs)
    return MODEL_HANDLERS[stage['model_id']](models, stage, **inputs)


def process_start(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError): return None


def read_run(directory):
    result = directory / 'result.json'; exit_file = directory / 'exit.json'
    if not result.is_file() and not exit_file.is_file():
        return {'not_started': not (directory / 'receipt.json').exists()}
    job = json.loads(result.read_text(encoding='utf-8')) if result.is_file() else {
        'contract': CONTRACT, 'id': directory.name, 'status': 'running', 'stages': []}
    diagnosis = json.loads(exit_file.read_text(encoding='utf-8')) if exit_file.is_file() else None
    if diagnosis is not None:
        log = directory / 'runner.log'
        if log.is_file():
            with log.open('rb') as stream:
                stream.seek(max(0, log.stat().st_size - 6000))
                diagnosis['log_tail'] = diagnostic_text(stream.read().decode('utf-8', errors='replace'))
        job['diagnostics'] = diagnosis
    info_file = directory / 'process.json'
    supervisor_file = directory / 'supervisor.json'
    alive = False
    for path in (info_file, supervisor_file):
        if path.is_file():
            info = json.loads(path.read_text())
            alive |= info['start'] is not None and process_start(info['pid']) == info['start']
    interrupted = job['status'] in ('running', 'queued') and (diagnosis is not None or (info_file.is_file() and not alive))
    if interrupted or (diagnosis and diagnosis['returncode'] != 0 and job['status'] == 'completed'):
        finish_interrupted_timings(job, diagnosis['finished_at'] if diagnosis else job.get('updated_at', time.time()))
        reason = 'Pod 실행 프로세스가 종료돼 검수를 완료하지 못했습니다.'
        if diagnosis:
            reason += f" 종료 코드 {diagnosis['returncode']}" + (f" ({diagnosis['signal']})" if diagnosis.get('signal') else '') + '. 종료 진단을 확인해 주세요.'
        else: reason += ' 이 작업에는 종료 코드·오류 로그가 남아 있지 않습니다.'
        cancelled = (directory / 'cancel-requested').is_file()
        job.update(status='cancelled' if cancelled else 'failed', error='사용자가 검수 작업을 중단했습니다.' if cancelled else reason)
        for stage in job['stages']:
            if stage['status'] == 'running': stage.update(status='cancelled' if cancelled else 'failed', error=job['error'])
            elif stage['status'] == 'queued': stage['status'] = 'cancelled' if cancelled else 'skipped'
    return job


def supervise_run(runner, arguments, prefix, emit):
    # Keep diagnostics alive when the SSH channel closes or the model process crashes.
    if hasattr(signal, 'SIGHUP'): signal.signal(signal.SIGHUP, signal.SIG_IGN)
    directory = arguments.parent
    (directory / 'supervisor.json').write_text(json.dumps({'pid': os.getpid(), 'start': process_start(os.getpid())}))
    with (directory / 'runner.log').open('wb') as log:
        try:
            process = subprocess.Popen([sys.executable, '-u', str(runner), str(arguments), prefix],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
                env={**os.environ, 'PYTHONFAULTHANDLER': '1'})
            with process.stdout:
                while raw := process.stdout.readline(16 * 1024**2 + 1):
                    text = raw.decode('utf-8', errors='replace')
                    if text.startswith(prefix):
                        message = json.loads(text[len(prefix):])
                        # Publish terminal results only after recording the child's exit.
                        if message['kind'] != 'run' or message['data'].get('status') not in ('completed', 'failed', 'cancelled'):
                            emit(message['kind'], message['data'])
                    else:
                        if log.tell() > 1024**2: log.seek(0); log.truncate()
                        log.write(diagnostic_text(text).encode('utf-8') + b'\n'); log.flush()
            returncode = process.wait()
        except OSError as error:
            log.write(setup_error('Pod 프로세스 시작', error).encode('utf-8')); log.flush()
            returncode = 1
    name = None
    if returncode < 0:
        try: name = signal.Signals(-returncode).name
        except ValueError: name = f'SIGNAL_{-returncode}'
    temp = directory / 'exit.tmp'
    temp.write_text(json.dumps({'returncode': returncode, 'signal': name, 'finished_at': time.time()}), encoding='utf-8')
    temp.replace(directory / 'exit.json')
    job = read_run(directory)
    temp = directory / 'supervisor-result.tmp'; temp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8'); temp.replace(directory / 'result.json')
    emit('run', job)


def finish_interrupted_timings(job, observed_at):
    # A killed runner cannot report its final monotonic measurement.
    for stage in job['stages']:
        if stage['status'] != 'running' or 'started_at' not in stage: continue
        stage['elapsed_seconds'] = round(max(0, observed_at - stage['started_at']), 3)
        stage['timing_estimated'] = True
        for timing in stage.get('model_timings', []):
            phase = timing.pop('active_phase', None); started = timing.pop('active_started_at', None)
            if phase and started is not None:
                timing[phase + '_seconds'] += max(0, observed_at - started)
                timing['timing_estimated'] = True
    if 'started_at' in job:
        job['elapsed_seconds'] = round(max(0, observed_at - job['started_at']), 3)
        job['timing_estimated'] = True


def cancel_run(directory):
    result = directory / 'result.json'; process = directory / 'process.json'
    if not result.is_file() or not process.is_file():
        raise ValueError('아직 Pod 실행 프로세스가 준비되지 않았습니다. 잠시 후 다시 중단해 주세요.')
    job = json.loads(result.read_text(encoding='utf-8'))
    if job['status'] in ('completed', 'failed', 'cancelled'): return job
    info = json.loads(process.read_text()); pid = info['pid']; start = info['start']
    if type(pid) is not int or pid <= 1 or not isinstance(start, str): raise ValueError('작업 프로세스 정보를 확인하지 못했습니다.')
    if process_start(pid) == start:
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if str(directory / 'runner.py').encode() not in args or str(directory / 'job.json').encode() not in args:
            raise ValueError('프로세스가 해당 검수 작업과 일치하지 않아 중단하지 않았습니다.')
        (directory / 'cancel-requested').write_text(str(time.time()))
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if process_start(pid) != start: break
            try:
                if os.getpgid(pid) == pid: os.killpg(pid, sig)
                else: os.kill(pid, sig)
            except ProcessLookupError: break
            deadline = time.monotonic() + 2
            while process_start(pid) == start and time.monotonic() < deadline: time.sleep(0.1)
        if process_start(pid) == start: raise ValueError('작업 프로세스 중단을 확인하지 못했습니다. 다시 상태를 조회해 주세요.')
    else:
        # Never signal a reused PID or an already exited process.
        job = json.loads(result.read_text(encoding='utf-8'))
        if job['status'] in ('completed', 'failed', 'cancelled'): return job
        job.update(status='failed', error='검수 프로세스가 이미 종료되어 중단 요청을 보내지 않았습니다.')
    latest = json.loads(result.read_text(encoding='utf-8'))
    if latest['status'] in ('completed', 'failed', 'cancelled'): return latest
    if job['status'] == 'failed': latest.update(status='failed', error=job['error'])
    job = latest
    finish_interrupted_timings(job, time.time() if job['status'] != 'failed' else job.get('updated_at', time.time()))
    if job['status'] != 'failed': job.update(status='cancelled', error='사용자가 검수 작업을 중단했습니다.')
    for stage in job['stages']:
        if stage['status'] in ('queued', 'running'): stage['status'] = 'cancelled' if job['status'] == 'cancelled' else 'skipped'
    temp = directory / 'cancel.tmp'; temp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8'); temp.replace(result)
    return job


def execute_run(config, paths, root, emit):
    validate_config(config)
    if hasattr(signal, 'SIGHUP'): signal.signal(signal.SIGHUP, signal.SIG_IGN)
    stages = [{**{key: item[key] for key in ('id', 'kind', 'name', 'agent_id', 'inputs') if key in item}, 'model_id': item.get('model_id'), 'revision': item.get('revision'), 'status': 'queued'} for item in config['stages']]
    if config['flow'] == 'video' and config.get('purpose') != 'prepare': stages.insert(1 if stages[0]['id'] == 'conditions' else 0, {'id': 'frames', 'status': 'queued'})
    job = {'contract': CONTRACT, 'id': root.name, 'status': 'running', 'stages': stages, 'started_at': time.time(), 'agents': config.get('workflow', {}).get('agents', [])}
    run_began = time.monotonic(); worker = threading.local(); lock = threading.RLock()
    def publish():
        with lock:
            job['updated_at'] = time.time()
            job['elapsed_seconds'] = round(time.monotonic() - run_began, 3)
            current = getattr(worker, 'stage', None)
            if current is not None:
                if current['status'] == 'running': current['elapsed_seconds'] = round(time.monotonic() - worker.began, 3)
                stages[next(index for index, stage in enumerate(stages) if stage['id'] == current['id'])] = copy.deepcopy(current)
            data = json.dumps(job, ensure_ascii=False, allow_nan=False)
            temp = root / 'result.tmp'; temp.write_text(data, encoding='utf-8'); temp.replace(root / 'result.json')
            emit('run', json.loads(data))
    (root / 'process.json').write_text(json.dumps({'pid': os.getpid(), 'start': process_start(os.getpid())}))
    publish(); outputs = {}; frames = []; loaded_models = []
    def measure(stage, phase, function, *args, **kwargs):
        current = worker.stage
        timings = current.setdefault('model_timings', [])
        timing = next((item for item in timings if (item['model_id'], item['revision']) == (stage['model_id'], stage.get('revision'))), None)
        if timing is None:
            timing = {'model_id': stage['model_id'], 'revision': stage.get('revision'), 'load_seconds': 0, 'inference_seconds': 0, 'calls': 0}
            timings.append(timing)
        timing.update(active_phase=phase, active_started_at=time.time())
        if phase == 'inference': timing['calls'] += 1
        publish(); started = time.monotonic()
        try: return function(*args, **kwargs)
        finally:
            execution = getattr(getattr(worker, 'models', None), 'execution', None)
            if phase == 'load' and execution and execution.get('model_id') == stage['model_id']: timing['execution'] = copy.deepcopy(execution)
            timing[phase + '_seconds'] = round(timing[phase + '_seconds'] + time.monotonic() - started, 6)
            timing.pop('active_phase', None); timing.pop('active_started_at', None)
    preview_budget = 32 * 1024**2
    def prefetch(name):
        nonlocal preview_budget
        if preview_budget <= 0: return
        try:
            value = read_preview(root, name)
            with lock:
                preview_budget -= len(value['data'])
                if preview_budget >= 0: emit('preview', {'name': name, **value})
        except (OSError, ValueError): pass  # A preview failure must not stop inference; on-demand retrieval remains available.
    try:
        similarity_path = None; similarity_meta = {}; similarity_references = []
        if config['flow'] == 'similarity':
            similarity_path, similarity_meta = similarity_image(paths['media'], config.get('resize'), root / 'similarity-base.png', '기준 이미지')
        job['references'] = []
        for index, path in enumerate(paths['references']):
            reference_meta = {}
            if config['flow'] == 'similarity' and config.get('reference_resize') is not None:
                path, reference_meta = similarity_image(path, config['reference_resize'], root / f'similarity-reference-{index}.png', f'대조 이미지 {index + 1}')
            similarity_references.append(path)
            image = image_of(path); target = root / f'preview-reference-{index}.jpg'
            width, height = image.size; image.thumbnail((1280, 1280)); image.save(target, quality=90)
            job['references'].append({'index': index, 'preview': target.name, 'width': width, 'height': height, **reference_meta})
            prefetch(target.name)
        if config['flow'] in ('image', 'similarity') and config.get('purpose') != 'prepare':
            image = image_of(similarity_path or paths['media']); target = root / 'preview-poster.jpg'
            width, height = image.size; image.thumbnail((1280, 1280)); image.save(target, quality=90)
            job['media'] = {'index': 0, 'preview': target.name, 'width': width, 'height': height, **similarity_meta}
            prefetch(target.name)
        def execute_stage(template):
            nonlocal frames
            current = copy.deepcopy(template); worker.stage = current; worker.began = time.monotonic()
            models = getattr(worker, 'models', None)
            if models is None:
                models = worker.models = Models()
                with lock: loaded_models.append(models)
            try:
                current.update(status='running', started_at=time.time()); began = worker.began; publish()
                node_id = current['id']; name = current.get('kind', node_id); rows = []; crops = []
                stage = next((item for item in config['stages'] if item['id'] == node_id), None)
                def input_rows(role): return outputs[stage.get('inputs', {}).get(role, role)]
                if name in PAIR_STAGES and config['flow'] != 'similarity':
                    crops = [(index, root / logo['preview']) for index, row in enumerate(input_rows('logo_extract')) for logo in row['logos']]
                if config['flow'] == 'similarity':
                    measure(stage, 'load', models.load, stage)
                    current['output'] = {'items': rows}
                    for index, reference in enumerate(similarity_references):
                        current['detail'] = f'대조 이미지 {index + 1} / {len(paths["references"])} 측정 중'; publish()
                        result = measure(stage, 'inference', image_similarity, models, [similarity_path, reference]) if name == 'logo_similarity' else measure(stage, 'inference', run_model, models, stage, images=[similarity_path, reference])
                        rows.append({'reference_index': index, **result}); publish()
                    current.pop('detail', None); outputs[node_id] = rows
                elif name == 'frames':
                    frames = frames_of(paths['media'], config['sampling'], root, paths['container'])
                    current['output'] = {'frames': [{k: v for k, v in item.items() if k != 'path'} for item in frames]}
                    outputs[node_id] = current['output']['frames']
                    for frame in frames:
                        if frame.get('preview'): prefetch(frame['preview'])
                elif name == 'candidate_filter':
                    rows = filter_candidates(input_rows('logo_similarity'), stage)
                    outputs[node_id] = rows; current['output'] = {'items': rows}
                elif name in catalog.EXTRA_STAGES and name not in PAIR_STAGES:
                    rows = adapters.execute_extra(stage, config, paths, root, frames, job, outputs, models, measure, SimpleNamespace(**globals()))
                    outputs[node_id] = rows; current['output'] = {'items': rows}
                else:
                    if name in catalog.GENERAL: models.close()
                    filter_id = stage.get('inputs', {}).get('candidate_filter', 'candidate_filter' if 'kind' not in stage else None)
                    filtered = outputs.get(filter_id) if name in PAIR_STAGES and name != 'logo_similarity' else None
                    if name not in catalog.GENERAL and (filtered is None or any(row['matches'] for row in filtered)): measure(stage, 'load', models.load, stage)
                    if name == 'text_generate':
                        evidence = {'requirements': config.get('requirements', ''), 'inputs': {source: outputs.get(source, []) for source in dependencies[node_id]}}
                        evidence = json.dumps(evidence, ensure_ascii=False)
                        if len(evidence) > 100_000: raise ValueError('모델에 전달할 결과가 너무 큽니다. 입력 단계나 프레임 수를 줄여 주세요.')
                        rows = [measure(stage, 'inference', run_model, models, stage, evidence=evidence)]
                    elif name == 'conditions':
                        parts_root = root if node_id == name else root / ('parts-' + node_id)
                        parts_root.mkdir(exist_ok=True)
                        for doc_index, path in enumerate(paths['documents']):
                            if config.get('purpose') == 'sample' and doc_index > 0: break
                            for part in document_parts(path, parts_root):
                                rows.append({'document_index': doc_index, 'page_or_chunk': part['page'], **measure(stage, 'inference', run_model, models, stage, evidence=part['text'], images=part['images'], require_vision=bool(part['images']))})
                                publish()
                                if config.get('purpose') == 'sample': break
                    else:
                        samples = frames if config['flow'] == 'video' else [{**job['media'], 'path': paths['media']}]
                        for sample_index, sample in enumerate(samples):
                            path = sample['path']; meta = {k: v for k, v in sample.items() if k != 'path'}
                            if name == 'logo_extract':
                                image = image_of(path); found = []
                                for logo_index, logo in enumerate(measure(stage, 'inference', run_model, models, stage, images=[path])['logos']):
                                    x1, y1, x2, y2 = logo['bbox']; x1, y1 = max(0, int(x1)), max(0, int(y1)); x2, y2 = min(image.width, int(x2)), min(image.height, int(y2))
                                    if x2 <= x1 or y2 <= y1: raise ValueError('로고 경계 상자가 이미지 범위를 벗어났습니다.')
                                    prefix = '' if node_id == name else node_id + '-'
                                    target = root / f'logo-{prefix}{sample_index}-{logo_index}.png'; image.crop((x1, y1, x2, y2)).save(target)
                                    prefetch(target.name)
                                    crops.append((sample_index, target)); found.append({**logo, 'logo_index': logo_index, 'preview': target.name, 'bbox': [x1, y1, x2, y2]})
                                rows.append({**meta, 'logos': found})
                            elif name in PAIR_STAGES:
                                matches = []
                                candidates = None if filtered is None else {(match['logo_index'], match['reference_index']): match for match in filtered[sample_index]['matches']}
                                for frame_index, crop in crops:
                                    if frame_index != sample_index: continue
                                    for ref_index, reference in enumerate(paths['references']):
                                        logo_index = int(crop.stem.rsplit('-', 1)[1]); key = (logo_index, ref_index)
                                        if candidates is not None and key not in candidates: continue
                                        timing_stage = stage if stage.get('model_id') else {**stage, 'model_id': 'processing/' + name}
                                        result = measure(timing_stage, 'inference', image_similarity, models, [crop, reference]) if name == 'logo_similarity' else measure(timing_stage, 'inference', run_model, models, stage,
                                            images=[crop, reference], require_vision=True)
                                        matches.append({**(candidates.get(key, {}) if candidates is not None else {}), 'logo_index': logo_index, 'reference_index': ref_index, **result})
                                rows.append({**meta, 'matches': matches, 'detected_logos': sum(i == sample_index for i, _ in crops),
                                    **({'candidate_count': filtered[sample_index]['candidate_count'], 'selected_count': len(matches), 'filter': filtered[sample_index].get('filter'), 'excluded_matches': filtered[sample_index].get('excluded_matches', []), 'note': '필터를 통과한 후보만 대조했습니다.'} if filtered is not None else {})})
                            elif name in ('text_extract', 'visual_extract'):
                                rows.append({**meta, **measure(stage, 'inference', run_model, models, stage, images=[path], require_vision=True)})
                            else:
                                source = input_rows(name.replace('_compare', '_extract'))[sample_index]
                                condition_id = stage.get('inputs', {}).get('conditions', 'conditions' if 'kind' not in stage else None)
                                evidence = {'conditions': outputs.get(condition_id, []), 'additional_conditions': config.get('requirements', ''), 'extracted': source}
                                evidence = json.dumps(evidence, ensure_ascii=False)
                                if len(evidence) > 100_000: raise ValueError('대조할 조건이 너무 깁니다. 문서를 나누어 실행해 주세요.')
                                rows.append({**meta, **measure(stage, 'inference', run_model, models, stage, evidence=evidence, images=[path] if name == 'visual_compare' and models.kind == 'vision' else [])})
                            current['output'] = {'items': rows}; publish()
                    if name == 'logo_compare' and stage.get('image_similarity') and crops:
                        similarity_stage = {'model_id': stage.get('image_similarity_model', SIGLIP2)}
                        current['detail'] = f'{similarity_stage["model_id"]}로 로고 이미지 유사도를 계산하고 있습니다.'; publish()
                        measure(similarity_stage, 'load', models.load, similarity_stage)
                        for sample_index, row in enumerate(rows):
                            for match in row['matches']:
                                crop = next(path for index, path in crops if index == sample_index and int(path.stem.rsplit('-', 1)[1]) == match['logo_index'])
                                match.update(measure(similarity_stage, 'inference', image_similarity, models, [crop, paths['references'][match['reference_index']]]))
                            publish()
                        current.pop('detail', None)
                    outputs[node_id] = rows; current['output'] = {'items': rows}
                if stage and stage.get('model_profile'):
                    current['model_execution'] = {'registration_id': stage['model_profile']['id'], 'fingerprint': stage['model_profile']['fingerprint'], 'versions': catalog.installed_versions()}
                current['status'] = 'completed'; current['elapsed_seconds'] = round(time.monotonic() - began, 3); publish()
            except Exception as error:
                message = setup_error('모델 실행', f'{type(error).__name__}: {error}')
                current.update(status='failed', error=message[:6500], elapsed_seconds=round(time.monotonic() - worker.began, 3))
                publish()
                raise
        dependencies = workflow_dependencies(config)
        pending = {stage['id']: stage for stage in stages}; completed = set(); failure = None
        concurrency = config.get('workflow', {}).get('max_parallel', 1)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {}
            while pending or futures:
                if failure is None:
                    for name in list(pending):
                        if len(futures) >= concurrency: break
                        if dependencies[name] <= completed: futures[pool.submit(execute_stage, pending.pop(name))] = name
                if not futures: break
                finished, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in finished:
                    name = futures.pop(future)
                    try: future.result(); completed.add(name)
                    except Exception as error: failure = failure or error
            if failure is not None: raise failure
        job['status'] = 'completed'
    except Exception as error:
        traceback.print_exc()
        job['status'] = 'failed'
        message = setup_error('모델 실행', f'{type(error).__name__}: {error}')
        job['error'] = message[:6500]
        for stage in stages:
            if stage['status'] == 'queued': stage['status'] = 'skipped'
    finally:
        for models in loaded_models:
            close = getattr(models, 'close', None)
            if close:
                try: close()
                except Exception: job.update(status='failed', error='모델 실행기 종료를 확인하지 못했습니다. Pod의 해당 작업 프로세스를 확인해 주세요.')
        publish()


def remote_main(boot, stream, prefix):
    def emit(kind, data):
        try: stream.write(prefix + json.dumps({'kind': kind, 'data': data}, ensure_ascii=False, allow_nan=False) + '\n'); stream.flush()
        except (BrokenPipeError, OSError): pass
    root = (Path('/workspace') if Path('/workspace').is_dir() else Path.home()) / '.creative-checker-modeltest'
    # /workspace can be a FUSE volume that rejects chmod/rename used by venv and pip.
    runtime = Path.home() / '.cache/creative-checker-modeltest'
    python = runtime / 'venv/bin/python'
    action = boot['action']
    if action in ('probe', 'prepare'):
        try:
            base_health = runtime_health()
            if action == 'prepare':
                if not base_health['can_prepare']: raise ValueError('CUDA PyTorch가 준비된 Pod가 필요합니다.')
                prepare_runtime(runtime)
            try: health = probe_runtime(runtime, boot['source'], base_health)
            except Exception as error:
                if action == 'prepare': raise
                health = {**base_health, 'ready': False, 'setup_required': True,
                          'detail': setup_error('전용 실행환경 확인', error)}
            if action == 'prepare' and not health['ready']:
                raise ValueError('실행환경 확인 실패\n' + health['detail'] + '\n누락 모듈: ' + ', '.join(health['missing']))
            emit('health', health)
        except Exception as error:
            detail = str(error) if isinstance(error, ValueError) else setup_error(
                'Pod 실행환경 준비' if action == 'prepare' else 'Pod 실행환경 확인', f'{type(error).__name__}: {error}')
            emit('error', detail)
        return
    identifier = boot['id']
    if not re.fullmatch(r'[0-9a-f-]{36}', identifier): raise ValueError('Invalid run ID')
    directory = root / 'runs' / identifier
    if action == 'cancel':
        try: emit('run', cancel_run(directory))
        except Exception as error: emit('error', str(error) if isinstance(error, ValueError) else '작업 중단을 확인하지 못했습니다. Pod 상태를 다시 조회해 주세요.')
        return
    if action == 'preview':
        try:
            # Use the prepared environment even when system python lacks Pillow.
            script = "import json,sys;from pathlib import Path;ns={'__name__':'modeltest_preview'};exec(sys.stdin.read(),ns);print(json.dumps(ns['read_preview'](Path(sys.argv[1]),sys.argv[2])))"
            result = subprocess.run([str(python), '-c', script, str(directory), str(boot.get('name', ''))],
                input=boot['source'], capture_output=True, text=True, timeout=30)
            if result.returncode: raise ValueError('미리보기를 읽지 못했습니다. 해당 단계의 완료 상태와 Pod 연결을 확인해 주세요.')
            emit('preview', json.loads(result.stdout))
        except Exception as error: emit('error', str(error) if isinstance(error, ValueError) else 'Pod 미리보기를 읽지 못했습니다.')
        return
    if action == 'status':
        emit('run', read_run(directory)); return
    if not python.exists(): raise ValueError('먼저 Pod 실행환경 준비를 실행해 주세요.')
    validate_config(boot['config']); digest = boot['digest']; receipt = directory / 'receipt.json'
    if receipt.exists():
        stored = json.loads(receipt.read_text())
        if stored['digest'] != digest: raise ValueError('동일 요청 ID의 내용이 다릅니다.')
        emit('run', read_run(directory)); return
    directory.mkdir(parents=True, exist_ok=True)
    paths = {'documents': [], 'references': [], 'container': boot.get('container')}
    for item in boot['uploads']:
        target = directory / item['stored_name']
        if target.parent != directory or not re.fullmatch(r'(media|document|reference)-\d+\.[a-z0-9]+', target.name): raise ValueError('Invalid upload name')
        emit('upload', item['stored_name']); actual = hashlib.sha256(); count = 0
        with target.open('wb') as output:
            if boot.get('binary_upload'):
                while count < item['size']:
                    chunk = sys.stdin.buffer.read(min(1024 * 1024, item['size'] - count))
                    if not chunk: raise ValueError('Upload interrupted')
                    count += len(chunk); actual.update(chunk); output.write(chunk)
            else:
                while True:
                    part = json.loads(sys.stdin.readline())
                    if part.get('end'): break
                    chunk = base64.b64decode(part['chunk'], validate=True); count += len(chunk)
                    if count > item['size']: raise ValueError('Upload too large')
                    actual.update(chunk); output.write(chunk)
        if count != item['size'] or actual.hexdigest() != item['sha256']: raise ValueError('Upload checksum mismatch')
        if item['kind'] == 'media': paths['media'] = target
        else: paths[item['kind']].append(target)
    receipt.write_text(json.dumps({'digest': digest}), encoding='utf-8')
    runner = directory / 'runner.py'; runner.write_text(boot['source'], encoding='utf-8')
    arguments = directory / 'job.json'
    arguments.write_text(json.dumps({'config': boot['config'], 'paths': {key: [str(p) for p in value] if isinstance(value, list) else str(value) if isinstance(value, Path) else value for key, value in paths.items()}}), encoding='utf-8')
    # The dedicated process persists progress on disk so a dropped SSH channel can be queried again.
    subprocess.run([str(python), str(runner), '--supervise', str(arguments), prefix], check=True, start_new_session=True)


if __name__ == '__main__' and len(sys.argv) in (3, 4):
    prefix = sys.argv[-1]
    def event(kind, data):
        try: print(prefix + json.dumps({'kind': kind, 'data': data}, ensure_ascii=False, allow_nan=False), flush=True)
        except (BrokenPipeError, OSError): pass
    if len(sys.argv) == 4 and sys.argv[1] == '--supervise':
        supervise_run(Path(sys.argv[0]), Path(sys.argv[2]), prefix, event)
    else:
        payload = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        paths = {key: [Path(p) for p in value] if isinstance(value, list) else Path(value) if key == 'media' else value for key, value in payload['paths'].items()}
        execute_run(payload['config'], paths, Path(sys.argv[1]).parent, event)
