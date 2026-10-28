"""Model capabilities and metadata only. Importing this module never loads weights."""
from __future__ import annotations

import copy
import hashlib
from importlib import metadata as packages
import json
import math
import re
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

GENERATION = ['conditions', 'text_extract', 'visual_extract', 'logo_extract', 'logo_compare', 'text_compare', 'visual_compare', 'text_generate']
TF = {'transformers': '>=5.21,<6', 'accelerate': '>=1,<2', 'safetensors': '>=0.6,<1'}
ST = {**TF, 'sentence-transformers': '>=6.2,<7'}

def family(engine, tasks, types=(), options=None, requirements=None, note=''):
    return {'engine': engine, 'tasks': tasks, 'model_types': list(types), 'defaults': options or {}, 'requirements': requirements or TF, 'note': note}

FAMILIES = {
    'qwen_text': family('transformers', ['conditions', 'text_compare', 'visual_compare', 'text_generate'], ['qwen3'], {'max_tokens': 1536, 'thinking': False}),
    'qwen_vision': family('transformers', GENERATION, ['qwen3_vl', 'qwen3_5', 'qwen3_5_moe'], {'max_tokens': 1536, 'thinking': False}),
    'clip': family('transformers', ['image_embed', 'logo_similarity'], ['clip', 'siglip', 'siglip2'], {'pooling': 'projection', 'normalize': True}),
    'dino': family('transformers', ['image_embed', 'logo_similarity'], ['dinov2', 'dinov2_with_registers', 'dinov3_vit', 'dinov3_convnext'], {'pooling': 'cls', 'normalize': True}),
    'open_clip': family('open_clip', ['image_embed', 'logo_similarity'], options={'architecture': '', 'weight_file': '', 'normalize': True}, requirements={'open-clip-torch': '>=3.3,<4', 'timm': '>=1.0.30,<2'}, note='OpenCLIP 설정과 일치하는 EVA/CLIP 가중치가 필요합니다.'),
    'sentence_text': family('sentence_transformers', ['text_embed'], ['bert', 'xlm-roberta', 'qwen3', 'gemma3_text'], {'normalize': True, 'prompt_name': '', 'prefix': '', 'max_length': 0}, ST, 'BGE-M3는 dense 벡터만 지원합니다. max_length=0은 저장소 설정을 유지합니다.'),
    'sentence_multimodal': family('sentence_transformers', ['text_embed', 'image_embed', 'multimodal_embed', 'logo_similarity'], ['qwen3_vl', 'embedding_gemma2'], {'normalize': True, 'prompt_name': '', 'prefix': '', 'max_length': 0}, ST),
    'cross_encoder': family('sentence_transformers', ['rerank'], ['xlm-roberta', 'qwen3', 'qwen3_vl'], {'score_transform': 'raw', 'max_length': 0, 'instruction': ''}, ST, 'raw logit와 sigmoid 점수를 구분합니다. max_length=0은 저장소 설정을 유지합니다.'),
    'owlv2': family('transformers', ['logo_extract'], ['owlv2'], {'labels': ['a brand logo'], 'detection_threshold': 0.2}),
    'grounding_dino': family('transformers', ['logo_extract'], ['grounding-dino', 'grounding_dino'], {'labels': ['logo', 'brand logo'], 'detection_threshold': 0.35, 'text_threshold': 0.25}),
    'yolo': family('ultralytics', ['logo_extract'], options={'weight_file': '', 'labels': [], 'detection_threshold': 0.25, 'image_size': 640}, requirements={'ultralytics': '>=8.4.191,<9', 'onnxruntime': '>=1.20,<2'}, note='ONNX 내보내기 지원. 임의 .pt pickle 로드는 허용하지 않습니다. YOLO-World ONNX는 내보낼 때 고정한 어휘를 사용합니다.'),
    'paddle_vl': family('transformers', ['text_extract'], ['paddleocr_vl'], {'max_tokens': 1536, 'ocr_task': 'ocr'}, note='요소 OCR 텍스트. 좌표·전체 페이지 레이아웃 파싱과 구분합니다.'),
    'paddle_gguf': family('llama.cpp', ['text_extract'], options={'weight_file': '', 'projector_file': ''}, note='GGUF와 mmproj를 같은 revision에서 준비합니다. CUDA 빌드 도구가 필요합니다.'),
    'paddle_ocr': family('paddleocr', ['text_extract'], options={'detector': {}, 'recognizer': {}}, requirements={'paddleocr': '>=3.7,<4', 'paddlepaddle': '>=3.3,<4'}, note='PP-OCRv5 탐지+인식. 두 모델을 모두 지정하면 단어·좌표를 반환합니다.'),
    'easyocr': family('easyocr', ['text_extract'], options={'languages': ['ko', 'en']}, requirements={'easyocr': '>=1.7.2,<2'}, note='등록 저장소에 EasyOCR가 요구하는 탐지·언어별 인식 파일이 있어야 합니다.'),
    'xfeat': family('torch', ['logo_compare'], options={'weight_file': 'xfeat.pt'}, requirements={'opencv-python-headless': '>=4.10,<5'}),
    'lightglue': family('transformers', ['logo_compare'], ['lightglue'], {'match_threshold': 0.2, 'ransac_px': 3.0}, note='Transformers의 SuperPoint+LightGlue 조합. DISK 조합은 일반 처리 노드로 분리합니다.'),
    'vision_classifier': family('transformers', ['image_classify'], ['resnet', 'convnextv2', 'efficientnet', 'dinov2', 'dinov3_vit'], {'score_transform': 'softmax'}),
    'vision_backbone': family('transformers', ['image_embed', 'logo_similarity'], ['resnet', 'convnextv2', 'efficientnet'], {'pooling': 'pooler', 'normalize': True}),
    'timm': family('timm', ['image_embed', 'image_classify', 'logo_similarity'], options={'normalize': True, 'score_transform': 'softmax'}, requirements={'timm': '>=1.0.30,<2'}),
    'text_classifier': family('transformers', ['text_classify'], ['electra', 'roberta', 'bert', 'xlm-roberta'], {'score_transform': 'softmax', 'max_length': 512}),
    'layoutxlm': family('transformers', ['document_classify'], ['layoutlmv2', 'layoutxlm'], {'score_transform': 'softmax', 'max_length': 512}, {**TF, 'detectron2': '>=0.6'}, '학습된 token classification 가중치, 단어·좌표·이미지 및 Detectron2 환경이 필요합니다.'),
    'linear_head': family('torch', ['image_classify', 'text_classify'], options={'encoder_family': '', 'pooling': '', 'normalize': True, 'score_transform': 'softmax', 'prompt_name': '', 'prefix': '', 'max_length': 2048}, note='SigLIP2·EG2 등 고정 특징 위의 safetensors 선형 헤드(weight, bias). 다른 헤드 구조는 추가 연결이 필요합니다.'),
}

GENERAL = {
    'text_input': ('text', []), 'vector_compare': ('similarity', ['left', 'right']),
    'phash': ('hash_distance', ['logo_extract']), 'orb': ('matches', ['logo_extract']), 'sift': ('matches', ['logo_extract']),
    'disk_lightglue': ('matches', ['logo_extract']), 'bm25': ('ranking', ['text']), 'rrf': ('ranking', ['left', 'right']),
    'tfidf_lr': ('classification', ['text']),
}
GENERAL['disk_lightglue_prepare'] = ('preparation', [])
EXTRA_STAGES = ('image_embed', 'text_embed', 'multimodal_embed', 'rerank', 'image_classify', 'text_classify', 'document_classify', 'model_prepare', *GENERAL)
OUTPUTS = {'conditions': 'text', 'text_extract': 'text', 'visual_extract': 'text', 'text_generate': 'text', 'text_compare': 'text', 'visual_compare': 'text', 'logo_extract': 'detections', 'logo_compare': 'matches', 'logo_similarity': 'similarity', 'candidate_filter': 'similarity', 'frames': 'images', 'image_embed': 'vectors', 'text_embed': 'vectors', 'multimodal_embed': 'vectors', 'rerank': 'ranking', 'image_classify': 'classification', 'text_classify': 'classification', 'document_classify': 'classification', **{key: value[0] for key, value in GENERAL.items()}}
INPUTS = {'image_embed': {'images': ['detections']}, 'multimodal_embed': {'text': ['text'], 'images': ['detections']}, 'text_embed': {'text': ['text']}, 'rerank': {'text': ['text'], 'candidates': ['ranking']}, 'text_classify': {'text': ['text']}, 'document_classify': {'ocr': ['text']}, 'vector_compare': {'left': ['vectors'], 'right': ['vectors']}, 'bm25': {'text': ['text']}, 'tfidf_lr': {'text': ['text']}, 'rrf': {'left': ['ranking'], 'right': ['ranking']}, **{key: {'logo_extract': ['detections'], 'candidate_filter': ['similarity']} for key in ('phash', 'orb', 'sift', 'disk_lightglue')}}
OUTPUTS['vector_compare'] = 'ranking'
INPUTS['image_classify'] = {'images': ['detections']}
INPUTS['rerank']['images'] = ['detections']
ST_MODULES = {f'sentence_transformers.models.{name}' for name in ('Transformer', 'Pooling', 'Normalize', 'Dense')}
ST_MODULES.update({'sentence_transformers.base.modules.transformer.Transformer', 'sentence_transformers.base.modules.normalize.Normalize', 'sentence_transformers.sentence_transformer.modules.normalize.Normalize', 'sentence_transformers.sentence_transformer.modules.pooling.Pooling', 'sentence_transformers.sentence_transformer.modules.dense.Dense', 'sentence_transformers.cross_encoder.modules.logit_score.LogitScore'})

def supported_modules(modules):
    return isinstance(modules, list) and bool(modules) and all(isinstance(item, dict) and item.get('type') in ST_MODULES and (not item.get('path') or safe_filename(item['path'])) for item in modules)

def parse_repo(value, revision=None):
    if not isinstance(value, str): raise ValueError('HF 링크 또는 owner/model을 입력해 주세요.')
    value = value.strip()
    if value.startswith('https://'):
        url = urlparse(value)
        if url.hostname not in ('huggingface.co', 'hf.co') or url.username or url.password or url.port: raise ValueError('Hugging Face 모델 저장소 링크만 허용합니다.')
        parts = url.path.strip('/').split('/')
        if len(parts) == 4 and parts[2] == 'tree': revision = unquote(parts[3])
        elif len(parts) != 2: raise ValueError('저장소 또는 /tree/revision 링크를 입력해 주세요.')
        value = '/'.join(parts[:2])
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}', value) or any('..' in p or '--' in p or p.endswith(('.', '-', '.git')) for p in value.split('/')) or value.split('/')[0] in ('spaces', 'datasets'): raise ValueError('소유자가 포함된 owner/model 형식이 필요합니다.')
    revision = revision or 'main'
    if not isinstance(revision, str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,128}', revision) or '..' in revision: raise ValueError('revision 형식을 확인해 주세요.')
    return value, revision

def json_url(url, limit=1024 * 1024):
    try:
        with urlopen(Request(url, headers={'User-Agent': 'CreativeChecker-modeltest/1'}), timeout=15) as response: raw = response.read(limit + 1)
        if len(raw) > limit: raise ValueError('모델 메타데이터 크기 한도를 초과했습니다.')
        return json.loads(raw)
    except HTTPError as error:
        if error.code in (401, 403): raise ValueError('접근 제한 저장소입니다. 공개 메타데이터를 확인할 수 없습니다. HF 접근 권한을 확인해 주세요.') from None
        if error.code == 404: return None
        raise ValueError(f'HF 메타데이터 조회 실패: HTTP {error.code}') from None
    except (URLError, TimeoutError, json.JSONDecodeError): raise ValueError('HF 메타데이터 응답을 확인하지 못했습니다. 네트워크와 저장소를 확인해 주세요.') from None

def inspect_repo(value, revision=None):
    repo, revision = parse_repo(value, revision)
    info = json_url(f'https://huggingface.co/api/models/{repo}/revision/{quote(revision, safe="")}')
    if not isinstance(info, dict) or not re.fullmatch('[a-f0-9]{40}', str(info.get('sha', ''))): raise ValueError('모델 저장소와 revision을 찾지 못했습니다.')
    files = [item['rfilename'] for item in info.get('siblings', []) if isinstance(item, dict) and isinstance(item.get('rfilename'), str)]
    if len(files) > 20000: raise ValueError('파일이 너무 많은 저장소입니다. 모델별 저장소를 사용해 주세요.')
    result = {'model_id': repo, 'revision': revision, 'resolved_revision': info['sha'], 'files': files, 'library': info.get('library_name'), 'pipeline': info.get('pipeline_tag'), 'gated': bool(info.get('gated')), 'configs': {}}
    names = ['config.json', 'preprocessor_config.json', 'processor_config.json', 'modules.json', 'sentence_bert_config.json', 'config_sentence_transformers.json', 'config_sentence_transformers_crossencoder.json', 'open_clip_config.json', 'adapter_config.json']
    names += [name for name in files if re.fullmatch(r'\d+_(Pooling|Normalize|LogitScore)/config.json', name)]
    for name in names:
        if name in files: result['configs'][name] = json_url(f'https://huggingface.co/{repo}/resolve/{info["sha"]}/{name}', 256 * 1024)
    if any(value is not None and not isinstance(value, list if name == 'modules.json' else dict) for name, value in result['configs'].items()): raise ValueError('저장소의 모델 설정 JSON 형식을 해석할 수 없습니다.')
    return result

def candidates(meta):
    config = meta['configs'].get('config.json') or {}; model_type = config.get('model_type', '')
    repo = meta['model_id'].lower(); files = meta['files']; architectures = ' '.join(config.get('architectures') or [])
    if 'gguf' in repo or any(name.endswith('.gguf') for name in files): return ['paddle_gguf'] if 'paddleocr-vl' in repo else []
    if 'rerank' in repo or meta.get('pipeline') == 'text-ranking': return ['cross_encoder'] if model_type in FAMILIES['cross_encoder']['model_types'] else []
    if 'embedding' in repo or 'modules.json' in files or meta.get('library') == 'sentence-transformers':
        if model_type in FAMILIES['sentence_multimodal']['model_types']: return ['sentence_multimodal', 'linear_head']
        if model_type in FAMILIES['sentence_text']['model_types']: return ['sentence_text', 'linear_head']
    if 'open_clip_config.json' in files or 'eva-clip' in repo: return ['open_clip']
    if meta.get('library') == 'timm' or config.get('model_type') == 'timm_wrapper': return ['timm']
    if 'pp-ocrv5' in repo: return ['paddle_ocr']
    if 'easyocr' in repo: return ['easyocr']
    if repo == 'vismatch/xfeat': return ['xfeat']
    if 'yolo' in repo or meta.get('library') == 'ultralytics': return ['yolo']
    if model_type in ('layoutlmv2', 'layoutxlm'): return ['layoutxlm']
    if 'ForSequenceClassification' in architectures and model_type in FAMILIES['text_classifier']['model_types']: return ['text_classifier']
    if 'ForImageClassification' in architectures and model_type in FAMILIES['vision_classifier']['model_types']: return ['vision_classifier', 'vision_backbone'] if model_type in FAMILIES['vision_backbone']['model_types'] else ['vision_classifier', 'dino']
    if model_type in FAMILIES['vision_backbone']['model_types']: return ['vision_backbone']
    if model_type in ('electra', 'roberta', 'bert', 'xlm-roberta'): return ['text_classifier']
    for key in ('qwen_text', 'qwen_vision', 'clip', 'dino', 'owlv2', 'grounding_dino', 'paddle_vl', 'lightglue'):
        if model_type in FAMILIES[key]['model_types']: return [key, 'linear_head'] if key in ('clip', 'dino') else [key]
    return []

def defaults(meta, key):
    result = copy.deepcopy(FAMILIES[key]['defaults']); files = meta['files']; config = meta['configs'].get('config.json') or {}
    if key == 'paddle_gguf':
        weights = [name for name in files if name.endswith('.gguf') and 'mmproj' not in name]; projectors = [name for name in files if name.endswith('.gguf') and 'mmproj' in name]
        if len(weights) == 1: result['weight_file'] = weights[0]
        if len(projectors) == 1: result['projector_file'] = projectors[0]
    if key == 'yolo':
        weights = [name for name in files if name.endswith('.onnx')]
        if len(weights) == 1: result['weight_file'] = weights[0]
    if key == 'dino' and config.get('model_type') == 'dinov3_convnext': result['pooling'] = 'pooler'
    if key == 'sentence_text' and 'multilingual-e5' in meta['model_id'].lower(): result.update(prefix='query: ', max_length=512)
    return result

def signature(profile):
    fields = {key: profile.get(key) for key in ('model_id', 'resolved_revision', 'family', 'options', 'training')}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()

def installed_versions():
    result = {}
    for name in {'torch', *(name for item in FAMILIES.values() for name in item['requirements'])}:
        try: result[name] = packages.version(name)
        except packages.PackageNotFoundError: result[name] = None
    return result

def environment_errors(profile, versions):
    from packaging.specifiers import SpecifierSet
    return [f'{name}{spec} 필요 (현재 {versions.get(name) or "미설치"})' for name, spec in profile.get('requirements', {}).items() if not versions.get(name) or versions[name] not in SpecifierSet(spec)]

def safe_filename(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_./-]{1,200}', value)) and not value.startswith('/') and '..' not in value.split('/')

def validate_options(key, options):
    if not isinstance(options, dict) or set(options) - set(FAMILIES[key]['defaults']): raise ValueError('이 계열에서 지원하지 않는 모델 설정입니다.')
    for field, value in options.items():
        default = FAMILIES[key]['defaults'][field]
        if isinstance(default, str) and not isinstance(value, str): raise ValueError(f'{field}는 문자열이어야 합니다.')
        if isinstance(default, dict) and not isinstance(value, dict): raise ValueError(f'{field}는 설정 객체여야 합니다.')
        if field in ('weight_file', 'projector_file') and value and not safe_filename(value): raise ValueError('저장소 안의 가중치 파일명을 입력해 주세요.')
        if field in ('normalize', 'thinking') and type(value) is not bool: raise ValueError(f'{field}는 true 또는 false여야 합니다.')
        if field in ('max_tokens', 'max_length', 'image_size') and (type(value) is not int or not (0 if field == 'max_length' else 1) <= value <= 131072): raise ValueError(f'{field} 길이 설정을 확인해 주세요. max_length=0은 저장소 기본값입니다.')
        if field in ('detection_threshold', 'text_threshold', 'match_threshold') and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1): raise ValueError(f'{field}는 0~1 사이여야 합니다.')
        if field == 'ransac_px' and (type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 20): raise ValueError('RANSAC 오차는 0~20px 범위여야 합니다.')
        if field == 'score_transform' and value not in ('raw', 'sigmoid', 'softmax'): raise ValueError('점수 변환을 확인해 주세요.')
        if field == 'pooling' and value not in ('', 'cls', 'mean', 'pooler', 'projection', 'repository'): raise ValueError('pooling 방식을 확인해 주세요.')
        if field in ('labels', 'languages') and (not isinstance(value, list) or len(value) > 100 or any(not isinstance(s, str) or not 1 <= len(s) <= 200 for s in value)): raise ValueError('라벨·언어 목록을 확인해 주세요.')
        if isinstance(value, str) and len(value) > 2000: raise ValueError('모델 설정 문자열이 너무 깁니다.')
    if key == 'cross_encoder' and options.get('score_transform') == 'softmax': raise ValueError('재정렬 단일 점수는 raw 또는 sigmoid를 선택해 주세요.')
    if key == 'paddle_vl' and options.get('ocr_task') != 'ocr': raise ValueError('현재 PaddleOCR-VL 연결은 OCR 텍스트 인식을 지원합니다. Spotting·전체 문서 파싱은 별도 연결이 필요합니다.')

def build_profile(meta, settings=None):
    settings = settings or {}
    if not isinstance(settings, dict) or any(settings.get(field) is not None and not isinstance(settings[field], dict) for field in ('options', 'training')) or (settings.get('family') is not None and not isinstance(settings['family'], str)): raise ValueError('모델 설정·학습 설정은 JSON 객체, 계열은 문자열이어야 합니다.')
    choices = candidates(meta); key = settings.get('family') or (choices[0] if choices else '')
    reasons = []; missing = []; config = meta['configs'].get('config.json') or {}; files = meta['files']
    profile = {'model_id': meta['model_id'], 'revision': meta['revision'], 'resolved_revision': meta['resolved_revision'], 'family': key, 'candidates': choices, 'metadata': meta, 'name': str(settings.get('name') or meta['model_id'])[:100], 'training': copy.deepcopy(settings.get('training') or {})}
    if not key or key not in FAMILIES: reasons.append('설정·파일 형식에 대응하는 실행 계열이 없습니다. 전용 연결 코드가 필요합니다.')
    elif key not in choices and not (key in ('easyocr', 'paddle_ocr', 'open_clip', 'yolo') and not config.get('model_type')): reasons.append('선택한 실행 계열과 저장소 구조가 맞지 않습니다. 생성 모델로 임의 실행하지 않습니다.')
    if reasons:
        profile.update(status='implementation_required', reasons=reasons, tasks=[], options={}, requirements={}); profile['fingerprint'] = signature(profile); return profile
    options = {**defaults(meta, key), **(settings.get('options') or {})}; validate_options(key, options)
    if key == 'vision_backbone' and options['pooling'] != 'pooler': raise ValueError('이 시각 backbone은 공식 pooler 특징을 사용합니다.')
    if key == 'dino' and config.get('model_type') == 'dinov3_convnext' and options['pooling'] != 'pooler': raise ValueError('DINOv3 ConvNeXt는 pooler 특징을 사용합니다.')
    profile.update(options=options, engine=FAMILIES[key]['engine'], requirements=FAMILIES[key]['requirements'], tasks=list(FAMILIES[key]['tasks']))
    training = profile['training']
    if not isinstance(training, dict) or set(training) - {'base_model_id', 'base_revision', 'checkpoint', 'head', 'lora', 'labels', 'preprocessing', 'thresholds', 'trained'}: raise ValueError('학습 모델 설정 필드를 확인해 주세요.')
    if len(json.dumps(training)) > 50000: raise ValueError('학습 설정은 50KB 이하여야 합니다.')
    preprocessing = training.get('preprocessing', {})
    if not isinstance(preprocessing, dict) or set(preprocessing) - {'source', 'size', 'crop_size', 'image_mean', 'image_std', 'do_resize', 'do_center_crop', 'do_normalize', 'rescale_factor'}: raise ValueError('지원하지 않는 학습 전처리 설정입니다.')
    if 'source' in preprocessing and preprocessing != {'source': 'repository'}: raise ValueError('저장소 전처리는 {"source":"repository"}로 지정해 주세요.')
    if preprocessing and preprocessing != {'source': 'repository'} and key not in ('qwen_vision', 'clip', 'dino', 'vision_classifier', 'vision_backbone', 'layoutxlm', 'paddle_vl', 'linear_head'): reasons.append('이 계열의 사용자 전처리 변경은 전용 연결이 필요합니다. 저장소 전처리만 지원합니다.')
    if any(training.get(field) for field in ('checkpoint', 'head', 'lora')) and key in ('open_clip', 'yolo', 'paddle_ocr', 'paddle_gguf', 'easyocr', 'xfeat', 'lightglue'): reasons.append('이 계열은 추가 학습 artifact 결합을 지원하지 않습니다. 계열에 맞는 완전한 가중치 저장소로 등록해 주세요.')
    if training.get('head') and key != 'linear_head': reasons.append('별도 선형 head는 linear_head 실행 계열에서 등록해 주세요.')
    labels = training.get('labels', [])
    if not isinstance(labels, list) or len(labels) > 10000 or any(not isinstance(label, str) or not 1 <= len(label) <= 200 for label in labels) or len(set(labels)) != len(labels): raise ValueError('중복 없는 라벨 순서 목록을 입력해 주세요.')
    thresholds = training.get('thresholds', {})
    if not isinstance(thresholds, dict) or any(label not in labels or type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for label, value in thresholds.items()): raise ValueError('라벨별 판정 임계값은 0~1 사이여야 합니다.')
    for field in ('checkpoint', 'head', 'lora'):
        source = training.get(field)
        if source:
            if not isinstance(source, dict) or set(source) - {'model_id', 'revision', 'filename', 'sha256'}: raise ValueError(f'{field}의 저장소·revision·파일 설정을 확인해 주세요.')
            if field != 'head' and source.get('filename'): raise ValueError('checkpoint·LoRA는 설정과 가중치가 함께 있는 저장소로 지정해 주세요. 단일 파일 지정은 선형 head에서만 지원합니다.')
            parse_repo(source.get('model_id'), source.get('revision'))
            if not re.fullmatch('[a-f0-9]{40}', str(source.get('revision', ''))): missing.append(f'{field}: 재현 가능한 40자리 commit revision 필요')
            if field == 'head' and (not safe_filename(source.get('filename')) or not source['filename'].endswith('.safetensors') or not re.fullmatch('[a-f0-9]{64}', str(source.get('sha256', '')))): missing.append('분류 헤드의 safetensors 파일명과 SHA256 필요')
            artifact = meta.get('artifacts', {}).get(field, {})
            if not artifact or artifact.get('error'): missing.append(f'{field} 저장소 메타데이터 확인 필요: {artifact.get("error", "설정을 저장해 파일 정보를 확인하세요.")}')
            elif artifact.get('resolved_revision') != source.get('revision'): missing.append(f'{field} commit revision 불일치')
            elif field == 'head' and source.get('filename') not in artifact.get('files', []): missing.append('등록한 분류 헤드 파일이 저장소에 없습니다.')
            elif field == 'lora' and not {'adapter_config.json', 'adapter_model.safetensors'} <= set(artifact.get('files', [])): missing.append('LoRA adapter_config.json·adapter_model.safetensors 필요')
            elif field == 'checkpoint' and not any(name.endswith('.safetensors') for name in artifact.get('files', [])): missing.append('checkpoint 저장소에 safetensors 가중치가 없습니다.')
    if training.get('trained') or any(training.get(field) for field in ('checkpoint', 'head', 'lora')):
        if not training.get('base_model_id') or not training.get('base_revision'): missing.append('학습 원본 모델 ID·revision 필요')
        elif training['base_model_id'] != meta['model_id'] or training['base_revision'] != meta['resolved_revision']: missing.append('등록 저장소와 revision은 학습 원본 모델 ID·commit revision과 같아야 합니다.')
        if training.get('trained') and not any(training.get(field) for field in ('checkpoint', 'head', 'lora')): missing.append('추가 학습 checkpoint·head·LoRA 중 하나 필요')
        if training.get('trained') and (not labels or not training.get('preprocessing') or not thresholds): missing.append('학습 라벨 순서·전처리·판정 임계값 필요')
    if key in ('sentence_text', 'sentence_multimodal'):
        modules = meta['configs'].get('modules.json')
        if not isinstance(modules, list) or not modules: missing.append('Sentence Transformers modules.json과 pooling 설정 필요')
        elif not supported_modules(modules): reasons.append('등록되지 않은 Sentence Transformers 사용자 코드 모듈 또는 잘못된 모듈 경로입니다. 전용 연결 검토가 필요합니다.')
    if key == 'cross_encoder':
        modules = meta['configs'].get('modules.json')
        if modules is not None and not supported_modules(modules): reasons.append('지원하지 않는 CrossEncoder 모듈입니다. 전용 연결 검토가 필요합니다.')
        if config.get('model_type') in ('qwen3', 'qwen3_vl') and not modules: missing.append('공식 CrossEncoder LogitScore 모듈 설정이 포함된 revision 필요')
        if not modules and not any('ForSequenceClassification' in arch for arch in config.get('architectures', [])): missing.append('학습된 재정렬 classification 가중치 또는 공식 LogitScore 모듈 필요')
    if key in ('sentence_text', 'sentence_multimodal', 'cross_encoder') and not any(name.endswith('.safetensors') for name in files): missing.append('Sentence Transformers 경로는 safetensors 가중치가 필요합니다.')
    if key == 'cross_encoder' and config.get('model_type') == 'qwen3_vl': profile['modalities'] = ['text', 'image']
    else: profile['modalities'] = ['text', 'image'] if key == 'sentence_multimodal' else ['text']
    if key in ('vision_classifier', 'text_classifier', 'layoutxlm'):
        expected = 'TokenClassification' if key == 'layoutxlm' else 'ImageClassification' if key == 'vision_classifier' else 'SequenceClassification'
        if not any(expected in arch for arch in config.get('architectures', [])) and not training.get('checkpoint'): missing.append('업무용 분류 헤드가 학습된 checkpoint 필요: 원본 backbone을 완성된 분류기로 실행하지 않습니다.')
        label_map = config.get('id2label', {})
        if not labels and label_map and not any(re.fullmatch(r'LABEL_\d+', str(value)) for value in label_map.values()): training['labels'] = [label_map[str(i)] for i in range(len(label_map))] if all(str(i) in label_map for i in range(len(label_map))) else []
        if not training.get('labels'): missing.append('학습된 라벨 목록과 순서 필요')
    if key == 'linear_head':
        encoder = options.get('encoder_family')
        if encoder not in ('clip', 'dino', 'sentence_multimodal', 'sentence_text'): missing.append('분류 헤드의 원본 특징 추출 계열 선택 필요: clip, dino, sentence_multimodal, sentence_text')
        elif encoder not in choices: reasons.append('선형 헤드의 encoder_family가 원본 모델 계열과 맞지 않습니다.')
        else:
            profile['requirements'] = {**profile['requirements'], **FAMILIES[encoder]['requirements']}
            profile['tasks'] = ['text_classify'] if encoder == 'sentence_text' else ['image_classify', 'text_classify'] if encoder == 'sentence_multimodal' else ['image_classify']
            if encoder.startswith('sentence_') and (options['pooling'] != 'repository' or preprocessing != {'source': 'repository'}): missing.append('Sentence Transformers 학습 헤드는 pooling=repository, preprocessing={"source":"repository"}가 필요합니다. 변경된 전처리는 원본 저장소 설정에 포함해 주세요.')
            if encoder == 'clip' and options['pooling'] != 'projection': missing.append('CLIP·SigLIP 분류 헤드는 pooling=projection이 필요합니다.')
        if not training.get('head') or not training.get('trained'): missing.append('학습된 선형 head와 trained 설정 필요')
        if not options.get('pooling') or not training.get('preprocessing'): missing.append('학습 때 사용한 pooling·전처리 설정 필요')
    if key == 'timm':
        if not config.get('architecture') or not config.get('pretrained_cfg'): missing.append('timm architecture·pretrained_cfg 전처리 설정 필요')
        if 'model.safetensors' not in files: missing.append('timm model.safetensors 가중치 필요')
        if not training.get('labels') and config.get('label_names'): training['labels'] = config['label_names']
        if not training.get('labels'): profile['tasks'].remove('image_classify')
    if key in ('qwen_text', 'qwen_vision', 'clip', 'dino', 'lightglue', 'vision_classifier', 'vision_backbone', 'text_classifier', 'layoutxlm', 'paddle_vl') and not any(name.endswith('.safetensors') for name in files) and not training.get('checkpoint'): missing.append('Safetensors 가중치 필요')
    if key == 'open_clip':
        if not options.get('architecture') or options.get('weight_file') not in files: missing.append('OpenCLIP architecture와 저장소 가중치 파일 선택 필요')
        if options.get('weight_file') and not options['weight_file'].endswith('.safetensors'): reasons.append('OpenCLIP 경로는 safetensors만 지원합니다. pickle 가중치는 안전한 형식으로 변환해야 합니다.')
        if 'open_clip_config.json' not in files: missing.append('OpenCLIP 전처리·모델 설정(open_clip_config.json) 필요')
    if key == 'yolo' and (options.get('weight_file') not in files or not options['weight_file'].endswith('.onnx')): missing.append('실행할 ONNX 파일 필요. .pt는 자동으로 역직렬화하지 않습니다. YOLO-World는 어휘를 정해 ONNX로 내보내세요.')
    if key == 'paddle_gguf' and (options.get('weight_file') not in files or options.get('projector_file') not in files): missing.append('GGUF 가중치와 mmproj 파일 선택 필요')
    if key == 'paddle_ocr':
        for role in ('detector', 'recognizer'):
            source = options.get(role)
            if not isinstance(source, dict) or not source.get('model_id') or not re.fullmatch('[a-f0-9]{40}', str(source.get('revision', ''))): missing.append(f'PP-OCRv5 {role} 저장소와 commit revision 필요')
            else: parse_repo(source['model_id'], source['revision'])
    if key == 'easyocr':
        if options['languages'] != ['ko', 'en']: reasons.append('현재 EasyOCR 연결은 한국어·영어 조합을 지원합니다.')
        if not {'craft_mlt_25k.pth', 'korean_g2.pth'} <= set(files): missing.append('EasyOCR craft_mlt_25k.pth·korean_g2.pth 파일 필요')
    if key == 'lightglue' and (config.get('keypoint_detector_config') or {}).get('model_type') != 'superpoint': reasons.append('Transformers LightGlue는 SuperPoint 조합만 연결했습니다. DISK는 별도 일반 처리 노드를 사용하세요.')
    if key == 'xfeat' and options['weight_file'] != 'xfeat.pt': reasons.append('XFeat 실행기는 공식 xfeat.pt tensor 가중치 형식을 사용합니다.')
    if training.get('lora'):
        if key not in ('qwen_text', 'qwen_vision'): reasons.append('이 계열의 LoRA 연결은 추가 구현이 필요합니다.')
        else: profile['requirements'] = {**profile['requirements'], 'peft': '>=0.17,<1'}
    profile.update(status='implementation_required' if reasons else 'needs_preparation' if missing else 'unverified', reasons=reasons + missing)
    profile['fingerprint'] = signature(profile)
    return profile

def validate_profile(profile, task):
    if not isinstance(profile, dict) or profile.get('family') not in FAMILIES: raise ValueError('등록한 모델 실행 설정이 필요합니다.')
    if profile.get('status') in ('needs_preparation', 'implementation_required'): raise ValueError('; '.join(profile.get('reasons') or ['모델 준비가 필요합니다.']))
    if task != 'model_prepare' and task not in profile.get('tasks', []): raise ValueError('등록 모델이 지원하지 않는 작업입니다.')
    parse_repo(profile.get('model_id'), profile.get('resolved_revision'))
    if not re.fullmatch('[a-f0-9]{40}', str(profile.get('resolved_revision', ''))): raise ValueError('모델 commit revision이 고정되지 않았습니다.')
    validate_options(profile['family'], profile.get('options', {}))
    if signature(profile) != profile.get('fingerprint'): raise ValueError('모델 설정이 변경됐습니다. 등록 정보를 다시 확인해 주세요.')
    rebuilt = build_profile(profile['metadata'], profile)
    if rebuilt['status'] not in ('unverified', 'verified') or any(profile.get(key) != rebuilt.get(key) for key in ('fingerprint', 'tasks', 'requirements')): raise ValueError('등록 모델 지원 규격이 변경됐습니다. 모델 설정을 다시 저장해 주세요.')
