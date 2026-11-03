"""Model-test workflow inputs and validation; independent of model execution."""
import json
import math
import re
import threading

try:
    from backend.app.modeltest import catalog, prompts
except ImportError:
    import modeltest_catalog as catalog
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
PREVIEW_NAME = r'(?:frame-\d{8,12}\.jpg|logo-(?:[A-Za-z0-9_-]{1,64}-)?\d{1,6}-\d{1,6}\.png|diff-\d{1,6}-\d{1,6}-\d{1,6}(?:-regions)?\.png|preview-(?:poster|reference-\d{1,2})\.jpg)'
LOGO_BBOX_PADDING = 0.10
LOGO_MIN_SIDE = 48
LOGO_DIFF_MIN_INLIERS = 8
LOGO_DIFF_MIN_INLIER_RATIO = 0.35
LOGO_DIFF_MIN_INLIER_COVERAGE = 0.005
# Video logo detection tuned on 19 labelled 720p/1080p videos: loose text queries over the frame and overlapping tiles,
# then SigLIP2 keeps boxes that resemble a reference logo. 4K logos under ~60px are a known limit (tiles are still halved).
OWLV2_THRESHOLD = 0.1
OWLV2_MAX_AREA = 0.4
OWLV2_NMS_IOU = 0.5
OWLV2_INPUT_SIDE = 960
DETECTION_FILTER_MODEL = SIGLIP2_SO400M
DETECTION_FILTER_PADDING = 0.10
DETECTION_FILTER_ACCEPT = 0.75
DETECTION_FILTER_SMALL_ACCEPT = 0.80
DETECTION_FILTER_SMALL_REVIEW = 0.75
DETECTION_FILTER_REFERENCE_SIDES = (20, 28, 40, 56)


def validate_model_stage(stage):
    model_id = stage['model_id']; kind = stage.get('kind', stage['id'])
    prompts.text(stage)
    if stage.get('model_profile'):
        catalog.validate_profile(stage['model_profile'], kind)
        if model_id != stage['model_profile']['model_id'] or stage.get('revision') != stage['model_profile']['resolved_revision']: raise ValueError('등록 모델과 실행 모델이 다릅니다.')
        if kind == 'logo_similarity':
            config = stage['model_profile'].get('metadata', {}).get('configs', {}).get('config.json', {})
            if stage['model_profile'].get('family') != 'clip' or config.get('model_type') != 'siglip2': raise ValueError('로고 후보 순위화에는 SigLIP2 계열만 사용할 수 있습니다.')
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
            needed = {'vector_compare': {'left', 'right'}, 'rrf': {'left', 'right'}, 'document_classify': {'ocr'},
                      'logo_diff': {'logo_extract', 'logo_compare', 'text_extract'}}.get(kind, set())
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
            if kind == 'logo_diff':
                compare_id = bindings['logo_compare']; compare = definitions[compare_id]
                if compare.get('model_profile', {}).get('family') != 'xfeat' and compare.get('model_id') != XFEAT:
                    raise ValueError('logo_diff의 기하 정렬에는 XFeat/RANSAC 노드가 필요합니다.')
                if 'candidate_filter' in ancestors[compare_id]: raise ValueError('logo_diff에서는 SigLIP2 점수로 XFeat 후보를 탈락시킬 수 없습니다.')
                ocr = definitions[bindings['text_extract']]
                if ocr.get('model_profile', {}).get('family') != 'paddle_ocr': raise ValueError('logo_diff 문자 검수에는 등록된 PP-OCR PaddleOCR 모델이 필요합니다.')
                if len(config.get('files', {}).get('references', [])) > 1 and 'logo_similarity' not in bindings:
                    raise ValueError('기준 로고가 여러 장이면 SigLIP2 순위화 입력이 필요합니다. 후보를 탈락시키지는 않습니다.')
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
    scope = stage.get('scope', 'references_per_crop'); groups = {}; identities = set(); decisions = {}
    if scope not in FILTER_SCOPES: raise ValueError('후보 선정 기준을 확인해 주세요.')
    for ri, row in enumerate(rows):
        for mi, match in enumerate(row['matches']):
            score = match.get('image_cosine_similarity')
            if type(score) not in (int, float) or not math.isfinite(score):
                if 'LOW_RESOLUTION' in match.get('reason_codes', []):
                    decisions[ri, mi] = (None, 'low_resolution'); continue
                raise ValueError('후보의 이미지 유사도 값이 올바르지 않습니다.')
            identities.add((match.get('similarity_model'), match.get('similarity_revision'), match.get('space_id')))
            key = (ri, match['logo_index']) if scope == 'references_per_crop' else (ri, match['reference_index']) if scope == 'crops_per_reference_frame' else match['reference_index']
            groups.setdefault(key, []).append((ri, mi, match))
    if len(identities) > 1: raise ValueError('모델·revision·전처리가 다른 유사도 결과를 함께 순위화할 수 없습니다.')
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
        allowed_params = {'documents', 'query', 'image_source', 'modality', 'classifier'}
        if stage.get('kind', stage['id']) == 'logo_diff': allowed_params.add('monochrome_policy')
        if stage.get('kind', stage['id']) == 'logo_extract': allowed_params.add('detection_filter')
        if not isinstance(params, dict) or set(params) - allowed_params or len(json.dumps(params, allow_nan=False)) > 50000: raise ValueError('일반 처리 입력 설정은 50KB 이내여야 합니다.')
        if 'monochrome_policy' in params and params['monochrome_policy'] not in ('review', 'allow_monochrome'): raise ValueError('단색 로고 정책은 사람 검토 또는 색상 차이 허용이어야 합니다.')
        if 'detection_filter' in params and params['detection_filter'] not in ('siglip2', 'off'): raise ValueError('로고 후보 필터는 SigLIP2 또는 사용 안 함이어야 합니다.')
        if 'query' in params and (not isinstance(params['query'], str) or len(params['query']) > 12000): raise ValueError('질의는 12,000자 이하여야 합니다.')
        if 'documents' in params and (not isinstance(params['documents'], list) or len(params['documents']) > 256 or any(not isinstance(text, str) or len(text) > 12000 for text in params['documents'])): raise ValueError('텍스트 후보는 256개 이하의 문자열 목록이어야 합니다.')
        if params.get('image_source', 'media') not in ('media', 'references', 'crops') or params.get('modality', 'text') not in ('text', 'image'): raise ValueError('입력 이미지·텍스트 유형을 확인해 주세요.')
        if 'kind' in stage and config.get('workflow') is None: raise ValueError('사용자 정의 단계에는 워크플로우 연결이 필요합니다.')
        similarity_diff = config['flow'] == 'similarity' and stage.get('kind', stage['id']) == 'logo_diff'
        if stage.get('kind', stage['id']) in catalog.EXTRA_STAGES and stage.get('kind', stage['id']) not in ('model_prepare', 'disk_lightglue_prepare') and not similarity_diff and config.get('workflow') is None: raise ValueError('새 처리 노드는 에이전트 워크플로우 연결이 필요합니다.')
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
        if (ids not in (['logo_similarity', 'logo_compare'], ['logo_similarity', 'logo_compare', 'logo_diff']) or stages[1].get('model_id') != XFEAT
                or any(set(stage) - {'id', 'name', 'model_id', 'revision', 'registered_model', 'model_fingerprint', 'model_profile'} for stage in stages[:2])
                or any(set(stage) - {'id', 'name', 'params'} or set(stage.get('params', {})) - {'monochrome_policy'} for stage in stages[2:])):
            raise ValueError('유사도 테스트는 이미지 유사도, XFeat 단계와 선택적인 logo_diff 단계로 실행해야 합니다.')
        for field, label in (('resize', '기준 이미지'), ('reference_resize', '대조 이미지')):
            resize = config.get(field)
            if resize is not None:
                pixels = isinstance(resize, dict) and set(resize) == {'width', 'height'} and all(type(value) is int and 1 <= value <= 8192 for value in resize.values())
                percent = isinstance(resize, dict) and set(resize) == {'percent'} and type(resize['percent']) in (int, float) and math.isfinite(resize['percent']) and 0.01 <= resize['percent'] <= 100
                if not (pixels or percent): raise ValueError(f'{label} 다운사이징은 가로·세로 1~8,192px 정수 또는 비율 0.01~100%로 입력해 주세요.')
        return config
    kinds = [stage.get('kind', stage['id']) for stage in stages]
    if 'logo_diff' in kinds and config['flow'] != 'video': raise ValueError('logo_diff는 OWLv2 프레임 로고 검수에서만 사용할 수 있습니다.')
    if config['flow'] == 'image' and (not files['documents'] or files['references'] or any(name.startswith('logo_') or name == 'candidate_filter' for name in kinds)):
        raise ValueError('이미지 검수에는 문서가 필요하며 영상 전용 단계는 사용할 수 없습니다.')
    if 'conditions' in kinds and not files['documents']:
        raise ValueError('조건 추출에는 문서가 필요합니다.')
    if any(stage.get('kind', stage['id']) == 'conditions' and stage.get('model_profile', {}).get('family') == 'qwen_text' for stage in stages) and any(str(item.get('name', '')).lower().endswith('.pdf') for item in files['documents']):
        raise ValueError('PDF 조건 추출은 페이지 이미지를 사용합니다. 이미지 입력 모델을 선택하거나 텍스트 문서를 첨부해 주세요.')
    if (any(name in kinds for name in PAIR_STAGES) or 'logo_diff' in kinds) and ('logo_extract' not in kinds or not files['references']):
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
