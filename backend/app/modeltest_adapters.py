"""Reviewed family adapters used by the shared runner. Heavy imports stay inside calls."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import sys

try:
    from . import modeltest_catalog as catalog
except ImportError:
    import modeltest_catalog as catalog


def cache_root(profile):
    return Path.home() / '.cache/creative-checker-modeltest/models' / profile['fingerprint']

def snapshot(source, *, prepare=False, files=None):
    from huggingface_hub import snapshot_download
    patterns = files or ['*.safetensors', '*.json', '*.txt', '*.model', '*.vocab', '*.merges', '*.jinja', 'vocab.*', 'merges.*']
    return Path(snapshot_download(source['model_id'], revision=source.get('resolved_revision', source.get('revision')), allow_patterns=patterns, local_files_only=not prepare))

def prepare(profile, remote):
    catalog.validate_profile(profile, 'model_prepare')
    versions = catalog.installed_versions(); errors = catalog.environment_errors(profile, versions)
    if 'detectron2' in profile['requirements'] and not versions.get('detectron2'):
        raise ValueError('LayoutXLM에는 CUDA·PyTorch와 호환되는 Detectron2를 별도로 준비해야 합니다. 임의 Git 설치 명령을 실행하지 않습니다.')
    root = cache_root(profile); root.mkdir(parents=True, exist_ok=True)
    if errors:
        constraints = root / 'existing-torch.txt'
        from importlib.metadata import version, PackageNotFoundError
        pins = []
        for name in ('torch', 'torchvision'):
            try: pins.append(f'{name}=={version(name)}')
            except PackageNotFoundError: pass
        constraints.write_text('\n'.join(pins), encoding='utf-8')
        remote.setup_command('등록 모델 라이브러리 준비', [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '-c', str(constraints), *[name + spec for name, spec in profile['requirements'].items()]], timeout=1200)
    errors = catalog.environment_errors(profile, catalog.installed_versions())
    if errors: raise ValueError('; '.join(errors))
    options = profile['options']; key = profile['family']
    extras = [options.get(name) for name in ('weight_file', 'projector_file') if options.get(name)]
    if key == 'easyocr': extras += ['craft_mlt_25k.pth', 'korean_g2.pth']
    source = snapshot(profile, prepare=True, files=['*.safetensors', '*.json', '*.txt', '*.model', '*.vocab', '*.merges', '*.jinja', *extras])
    for value in profile['training'].values():
        if isinstance(value, dict) and value.get('model_id'): snapshot(value, prepare=True, files=[value['filename']] if value.get('filename') else None)
    if key == 'paddle_ocr':
        for role in ('detector', 'recognizer'): snapshot(options[role], prepare=True, files=['*.json', '*.yml', '*.yaml', '*.pdiparams'])
    if key == 'paddle_gguf': remote.ensure_llama_server()
    if key == 'xfeat':
        model = remote.Models()
        try: model.load({'model_id': remote.XFEAT, 'revision': profile['resolved_revision']})
        finally: model.close()
    (root / 'prepared.json').write_text(json.dumps({'fingerprint': profile['fingerprint'], 'source': str(source), 'versions': catalog.installed_versions()}), encoding='utf-8')
    return {'text': '가중치와 라이브러리 준비 완료. 실제 추론은 아직 실행하지 않았습니다.', 'versions': catalog.installed_versions()}

def normalized(vectors):
    import numpy as np
    values = np.asarray(vectors, dtype=np.float32)
    if values.ndim != 2 or not np.isfinite(values).all(): raise ValueError('임베딩은 유한한 2차원 벡터여야 합니다.')
    lengths = np.linalg.norm(values, axis=1, keepdims=True)
    if (lengths == 0).any(): raise ValueError('영벡터는 코사인 비교에 사용할 수 없습니다.')
    return values / lengths

def geometry(first, second, *, ransac_px=3.0, scores=None):
    import numpy as np
    import cv2
    a, b = np.asarray(first, dtype=np.float32).reshape(-1, 2), np.asarray(second, dtype=np.float32).reshape(-1, 2)
    result = {'matches': len(a), 'inliers': 0, 'inlier_ratio': None, 'decision': 'uncertain', 'geometry_verified': False,
        'metric': 'feature_matches', 'matched_points': [{'crop': p.tolist(), 'reference': q.tolist()} for p, q in zip(a[:64], b[:64])],
        'note': '대응점 매칭과 RANSAC 기하 검증은 별도 지표입니다. 자동 일치 판정을 수행하지 않았습니다.'}
    if scores is not None and len(scores): result['matching_confidence'] = float(np.asarray(scores).mean())
    if len(a) >= 4:
        matrix, mask = cv2.findHomography(a, b, cv2.RANSAC, ransac_px, maxIters=2000, confidence=0.995)
        if matrix is not None and mask is not None and np.isfinite(matrix).all():
            result.update(geometry_verified=True, inliers=int(mask.sum()), inlier_ratio=float(mask.mean()), ransac_px=ransac_px)
    return result


class Adapter:
    def __init__(self, profile, legacy, remote):
        self.profile = profile; self.legacy = legacy; self.remote = remote
        self.family = profile['family']; self.options = profile['options']; self.training = profile['training']; self.model = None; self.processor = None; self.head = None
        errors = catalog.environment_errors(profile, catalog.installed_versions())
        if errors: raise ValueError('등록 모델 준비를 먼저 실행해 주세요. ' + '; '.join(errors))
        marker = cache_root(profile) / 'prepared.json'
        if not marker.is_file(): raise ValueError('이 설정의 가중치를 먼저 준비해 주세요. 등록만으로 다운로드하지 않습니다.')
        self.source = snapshot(self.training.get('checkpoint') or profile)
        self.load()

    def load(self):
        import torch
        key = self.family; options = self.options; source = str(self.source)
        kw = {'local_files_only': True, 'trust_remote_code': False}
        if key in ('sentence_text', 'sentence_multimodal', 'cross_encoder', 'linear_head'):
            effective = options.get('encoder_family') if key == 'linear_head' else key
            if effective in ('sentence_text', 'sentence_multimodal', 'cross_encoder'):
                config = json.loads((self.source / 'config.json').read_text(encoding='utf-8'))
                if config.get('model_type') not in catalog.FAMILIES[effective]['model_types']: raise ValueError('checkpoint의 모델 계열이 등록 설정과 다릅니다.')
                modules = self.source / 'modules.json'
                if modules.is_file() and not catalog.supported_modules(json.loads(modules.read_text(encoding='utf-8'))): raise ValueError('지원하지 않는 Sentence Transformers 모듈입니다.')
                if effective == 'cross_encoder' and not modules.is_file() and not any('ForSequenceClassification' in arch for arch in config.get('architectures', [])): raise ValueError('학습된 재정렬 가중치 또는 LogitScore 모듈이 필요합니다.')
        if key == 'paddle_gguf':
            self.model = self.remote.GGUFServer(self.profile['resolved_revision'], model_id=self.profile['model_id'], weight_file=options['weight_file'], projector_file=options['projector_file'], local_files_only=True); return
        if key == 'xfeat':
            self.legacy.load({'model_id': self.remote.XFEAT, 'revision': self.profile['resolved_revision'], 'local_files_only': True}); self.model = self.legacy.model; return
        if key in ('sentence_text', 'sentence_multimodal', 'cross_encoder'):
            from sentence_transformers import SentenceTransformer, CrossEncoder
            cls = CrossEncoder if key == 'cross_encoder' else SentenceTransformer
            extra = {'max_length': options['max_length']} if key == 'cross_encoder' and options['max_length'] else {}
            if key == 'cross_encoder' and options.get('instruction'): extra.update(prompts={'classification': options['instruction']}, default_prompt_name='classification')
            self.model = cls(source, device='cuda', model_kwargs={'use_safetensors': True}, **kw, **extra)
            if key != 'cross_encoder' and options['max_length']: self.model.max_seq_length = options['max_length']
            return
        if key == 'open_clip':
            import open_clip
            from safetensors.torch import load_file
            config = self.profile['metadata']['configs']['open_clip_config.json']
            if options['architecture'] not in open_clip.list_models() or open_clip.get_model_config(options['architecture']) != config.get('model_cfg'): raise ValueError('OpenCLIP architecture와 저장소 model_cfg가 일치해야 합니다.')
            cfg = config.get('preprocess_cfg', {})
            prep = {target: cfg[source] for source, target in [('mean', 'image_mean'), ('std', 'image_std'), ('interpolation', 'image_interpolation'), ('resize_mode', 'image_resize_mode'), ('size', 'force_image_size')] if source in cfg}
            self.model, _, self.processor = open_clip.create_model_and_transforms(options['architecture'], pretrained=None, pretrained_text=False, device='cuda', **prep)
            self.model.load_state_dict(load_file(str(self.source / options['weight_file'])), strict=True); self.model.eval(); return
        if key == 'timm':
            import timm
            from timm.data import resolve_model_data_config, create_transform
            config = json.loads((self.source / 'config.json').read_text(encoding='utf-8')); architecture = config.get('architecture')
            if self.training.get('labels') and config.get('label_names') and self.training['labels'] != config['label_names']: raise ValueError('timm checkpoint와 등록한 라벨 순서가 다릅니다.')
            self.model = timm.create_model(architecture, pretrained=False, num_classes=config.get('num_classes', 0), pretrained_cfg=config.get('pretrained_cfg'))
            from safetensors.torch import load_file
            self.model.load_state_dict(load_file(str(self.source / 'model.safetensors')), strict=True)
            self.model = self.model.to('cuda').eval()
            self.processor = create_transform(**resolve_model_data_config(self.model, use_test_size=True), is_training=False); return
        if key == 'yolo':
            from ultralytics import YOLO
            self.model = YOLO(str(self.source / options['weight_file']), task='detect'); return
        if key == 'paddle_ocr':
            from paddleocr import PaddleOCR
            detector, recognizer = (snapshot(options[role], files=['*.json', '*.yml', '*.yaml', '*.pdiparams']) for role in ('detector', 'recognizer'))
            self.model = PaddleOCR(text_detection_model_dir=str(detector), text_recognition_model_dir=str(recognizer), use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, device='cpu', enable_mkldnn=False, cpu_threads=2); return
        if key == 'easyocr':
            import easyocr
            self.model = easyocr.Reader(options['languages'], gpu=True, model_storage_directory=source, download_enabled=False); return
        if key == 'linear_head':
            from safetensors.torch import load_file
            head = self.training['head']; path = snapshot(head, files=[head['filename']]) / head['filename']
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024**2), b''): digest.update(block)
            if digest.hexdigest() != head['sha256']: raise ValueError('분류 헤드 SHA256이 등록 값과 다릅니다.')
            self.head = load_file(str(path), device='cuda')
            if set(self.head) != {'weight', 'bias'} or self.head['weight'].ndim != 2 or self.head['weight'].shape[0] != len(self.training['labels']) or self.head['bias'].shape != (len(self.training['labels']),): raise ValueError('선형 head의 weight·bias·라벨 순서가 맞지 않습니다.')
            key = options['encoder_family']
            if key.startswith('sentence_'):
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(source, device='cuda', model_kwargs={'use_safetensors': True}, **kw)
                if options['max_length']: self.model.max_seq_length = options['max_length']
                return
        from transformers import AutoConfig, AutoModel, AutoModelForCausalLM, AutoModelForImageTextToText, AutoModelForZeroShotObjectDetection, AutoModelForImageClassification, AutoModelForSequenceClassification, AutoModelForTokenClassification, AutoProcessor, AutoImageProcessor, AutoTokenizer
        config = AutoConfig.from_pretrained(source, **kw)
        if config.model_type not in catalog.FAMILIES[key]['model_types']: raise ValueError('캐시의 모델 계열이 등록 정보와 다릅니다.')
        if key in ('vision_classifier', 'text_classifier', 'layoutxlm'):
            expected = {'vision_classifier': 'ImageClassification', 'text_classifier': 'SequenceClassification', 'layoutxlm': 'TokenClassification'}[key]
            if not any(expected in arch for arch in (config.architectures or [])): raise ValueError('학습된 분류 모델 checkpoint가 필요합니다. 원본 backbone에는 분류 헤드를 생성하지 않습니다.')
            mapping = config.id2label
            if mapping and not all(re.fullmatch(r'LABEL_\d+', str(label)) for label in mapping.values()) and [mapping[i] for i in range(len(mapping))] != self.training['labels']: raise ValueError('checkpoint 라벨 순서와 등록 라벨 순서가 다릅니다.')
        cls = {'qwen_text': AutoModelForCausalLM, 'qwen_vision': AutoModelForImageTextToText, 'paddle_vl': AutoModelForImageTextToText,
            'owlv2': AutoModelForZeroShotObjectDetection, 'grounding_dino': AutoModelForZeroShotObjectDetection,
            'vision_classifier': AutoModelForImageClassification, 'text_classifier': AutoModelForSequenceClassification, 'layoutxlm': AutoModelForTokenClassification}.get(key, AutoModel)
        self.model, loading = cls.from_pretrained(source, **kw, use_safetensors=True, device_map='cuda', dtype='auto', output_loading_info=True)
        if loading.get('missing_keys') or loading.get('mismatched_keys'): raise ValueError('필요한 가중치가 없어 임의 초기화되는 모델은 실행하지 않습니다.')
        self.model.eval()
        if key in ('qwen_text', 'text_classifier'): self.processor = AutoTokenizer.from_pretrained(source, **kw)
        elif key in ('dino', 'vision_classifier', 'vision_backbone', 'lightglue'): self.processor = AutoImageProcessor.from_pretrained(source, **kw)
        else: self.processor = AutoProcessor.from_pretrained(source, **kw)
        if key == 'layoutxlm': self.processor.image_processor.apply_ocr = False
        preprocessing = self.training.get('preprocessing', {})
        if preprocessing and preprocessing != {'source': 'repository'}:
            allowed = {'size', 'crop_size', 'image_mean', 'image_std', 'do_resize', 'do_center_crop', 'do_normalize', 'rescale_factor'}
            if set(preprocessing) - allowed: raise ValueError('지원하지 않는 학습 전처리 설정입니다.')
            processor = getattr(self.processor, 'image_processor', self.processor)
            for name, value in preprocessing.items(): setattr(processor, name, value)
        if self.training.get('lora'):
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, str(snapshot(self.training['lora'])), is_trainable=False, local_files_only=True).eval()

    def close(self):
        if self.family == 'paddle_gguf' and self.model is not None: self.model.close()
        self.model = None; self.processor = None; self.head = None

    def embed(self, values, modality='image'):
        import torch
        key = self.options.get('encoder_family', self.family) if self.family == 'linear_head' else self.family
        with torch.inference_mode():
            if key.startswith('sentence_'):
                # Explicit text dicts keep user strings from becoming URL or local-file inputs.
                if modality == 'text': values = [{'text': value} for value in values]
                kwargs = {'normalize_embeddings': self.options.get('normalize', True), 'convert_to_numpy': True, 'show_progress_bar': False}
                if self.options.get('prompt_name'): kwargs['prompt_name'] = self.options['prompt_name']
                if self.options.get('prefix'): kwargs['prompt'] = self.options['prefix']
                return self.model.encode(values, **kwargs)
            images = [self.remote.image_of(path) for path in values]
            if key in ('open_clip', 'timm'):
                inputs = torch.stack([self.processor(image) for image in images]).to('cuda')
                vectors = self.model.encode_image(inputs, normalize=False) if key == 'open_clip' else self.model.forward_head(self.model.forward_features(inputs), pre_logits=True)
            else:
                inputs = self.processor(images=images, return_tensors='pt').to('cuda')
                if key == 'clip':
                    vectors = self.model.get_image_features(**inputs)
                    if not isinstance(vectors, torch.Tensor): vectors = vectors.pooler_output
                else:
                    output = self.model(**inputs); pooling = self.options.get('pooling', 'cls')
                    registers = getattr(self.model.config, 'num_register_tokens', 0)
                    vectors = output.pooler_output if pooling == 'pooler' else output.last_hidden_state[:, 1 + registers:].mean(dim=1) if pooling == 'mean' else output.last_hidden_state[:, 0]
                    if vectors.ndim > 2: vectors = vectors.flatten(start_dim=1)
            values = vectors.float().cpu().numpy()
        return normalized(values) if self.options.get('normalize', True) else values

    def generate(self, prompt, images=(), require_vision=False):
        import torch
        if self.family == 'qwen_text' and (images or require_vision): raise ValueError('텍스트 전용 모델에 이미지 입력을 전달할 수 없습니다.')
        content = [{'type': 'image', 'image': self.remote.image_of(path)} for path in images] + [{'type': 'text', 'text': 'OCR:' if self.family == 'paddle_vl' else prompt}]
        messages = [{'role': 'user', 'content': prompt if self.family == 'qwen_text' else content}]
        kwargs = {'enable_thinking': self.options.get('thinking', False)} if self.family.startswith('qwen_') else {}
        inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors='pt', **kwargs).to('cuda')
        with torch.inference_mode(): output = self.model.generate(**inputs, max_new_tokens=self.options.get('max_tokens', 1536), do_sample=False)
        return self.processor.batch_decode(output[:, inputs['input_ids'].shape[1]:], skip_special_tokens=True)[0]

    def detect(self, image):
        import torch
        image = self.remote.image_of(image); options = self.options
        if self.family == 'yolo':
            result = self.model.predict(image, conf=options['detection_threshold'], imgsz=options['image_size'], verbose=False)[0]
            selected = set(options['labels'])
            return [{'bbox': box.tolist(), 'score': float(score), 'label': result.names[int(label)]} for box, score, label in zip(result.boxes.xyxy.cpu(), result.boxes.conf.cpu(), result.boxes.cls.cpu()) if not selected or result.names[int(label)] in selected]
        labels = options['labels']; text = '. '.join(labels) + '.' if self.family == 'grounding_dino' else [labels]
        inputs = self.processor(text=text, images=image, return_tensors='pt').to('cuda')
        with torch.inference_mode(): output = self.model(**inputs)
        kwargs = {'threshold': options['detection_threshold'], 'target_sizes': [(image.height, image.width)]}
        if self.family == 'grounding_dino': result = self.processor.post_process_grounded_object_detection(output, inputs.input_ids, text_threshold=options['text_threshold'], **kwargs)[0]
        else: result = self.processor.post_process_grounded_object_detection(output, **kwargs)[0]
        found_labels = result['text_labels'] if 'text_labels' in result else result['labels']
        return [{'bbox': box.tolist(), 'score': float(score), 'label': str(label) if self.family == 'grounding_dino' else labels[int(label)]} for box, score, label in zip(result['boxes'].cpu(), result['scores'].cpu(), found_labels)]

    def ocr(self, image):
        if self.family == 'paddle_gguf': return self.model.ocr(image)
        if self.family == 'paddle_vl': return {'text': self.generate('OCR:', [image], True), 'ocr_mode': 'recognition', 'note': '좌표·페이지 레이아웃 분석은 포함하지 않습니다.'}
        import numpy as np
        pixels = np.asarray(self.remote.image_of(image))
        if self.family == 'easyocr':
            rows = self.model.readtext(pixels, detail=1)
            words = [{'text': text, 'polygon': np.asarray(polygon).tolist(), 'score': float(score)} for polygon, text, score in rows]
        else:
            result = list(self.model.predict(input=pixels[:, :, ::-1].copy()))[0].json
            if isinstance(result, str): result = json.loads(result)
            result = result.get('res', result)
            words = [{'text': text, 'polygon': np.asarray(poly).tolist(), 'score': float(score)} for text, poly, score in zip(result['rec_texts'], result['rec_polys'], result['rec_scores'])]
        for word in words:
            points = np.asarray(word['polygon']); word['bbox'] = [float(points[:, 0].min()), float(points[:, 1].min()), float(points[:, 0].max()), float(points[:, 1].max())]
        return {'text': '\n'.join(word['text'] for word in words), 'ocr_words': words, 'ocr_mode': 'detection_recognition'}

    def compare(self, images):
        if self.family == 'xfeat': return self.remote.run_xfeat(self.legacy, {}, images=images)
        import torch
        pictures = [self.remote.image_of(path) for path in images]
        inputs = self.processor(pictures, return_tensors='pt').to('cuda')
        with torch.inference_mode(): output = self.model(**inputs)
        found = self.processor.post_process_keypoint_matching(output, [[(image.height, image.width) for image in pictures]], threshold=self.options['match_threshold'])[0]
        return geometry(found['keypoints0'].cpu(), found['keypoints1'].cpu(), ransac_px=self.options['ransac_px'], scores=found['matching_scores'].cpu())

    def classify(self, values, modality, ocr=None):
        import torch
        labels = self.training.get('labels') or []
        if not labels: raise ValueError('학습된 라벨 순서를 등록해 주세요.')
        with torch.inference_mode():
            if self.head is not None:
                vector = torch.as_tensor(self.embed(values, modality), device='cuda')
                if vector.shape[-1] != self.head['weight'].shape[-1]: raise ValueError('학습 헤드와 특징 차원이 다릅니다.')
                logits = torch.nn.functional.linear(vector, self.head['weight'].float(), self.head['bias'].float())
            elif self.family == 'timm': logits = self.model(torch.stack([self.processor(self.remote.image_of(path)) for path in values]).to('cuda'))
            else:
                if modality == 'text': inputs = self.processor(values, return_tensors='pt', truncation=True, padding=True, max_length=self.options.get('max_length', 512))
                elif self.family == 'layoutxlm':
                    image = self.remote.image_of(values[0]); words = ocr or []
                    if not words or any(not word.get('bbox') for word in words): raise ValueError('LayoutXLM에는 OCR 단어와 좌표가 필요합니다.')
                    boxes = [[max(0, min(1000, round(v / (image.width if i % 2 == 0 else image.height) * 1000))) for i, v in enumerate(word['bbox'])] for word in words]
                    inputs = self.processor(image, [word['text'] for word in words], boxes=boxes, return_tensors='pt', truncation=True, max_length=self.options['max_length'] or 512)
                else: inputs = self.processor(images=[self.remote.image_of(path) for path in values], return_tensors='pt')
                logits = self.model(**inputs.to('cuda')).logits.float()
            transform = self.options.get('score_transform', 'softmax')
            scores = logits.softmax(-1) if transform == 'softmax' else logits.sigmoid() if transform == 'sigmoid' else logits
            if scores.shape[-1] != len(labels): raise ValueError('가중치의 클래스 수와 등록한 라벨 수가 다릅니다.')
        if self.family == 'layoutxlm':
            result = []; seen = set()
            for token, word_index in enumerate(inputs.word_ids(0)):
                if word_index is None or word_index in seen: continue
                seen.add(word_index); row = scores[0, token].cpu().tolist(); best = max(range(len(row)), key=row.__getitem__)
                result.append({'word': words[word_index]['text'], 'bbox': words[word_index]['bbox'], 'label': labels[best], 'classification_score': row[best]})
            return {'word_labels': result, 'metric': 'token_classification', 'score_transform': transform, 'total_words': len(words), 'classified_words': len(result), 'note': f'입력 {len(words)}단어 중 {len(result)}단어 분류. 최대 토큰 길이를 넘는 뒷부분은 포함하지 않습니다.'}
        return {'class_scores': [{'label': label, 'score': float(score), 'threshold': self.training.get('thresholds', {}).get(label)} for label, score in zip(labels, scores[0].cpu())], 'metric': 'classification_score', 'score_transform': transform}

    def rerank(self, query, documents):
        import torch
        activation = torch.nn.Sigmoid() if self.options['score_transform'] == 'sigmoid' else torch.nn.Identity()
        kwargs = {'activation_fn': activation, 'show_progress_bar': False}
        scores = self.model.predict([({'text': query}, document) for document in documents], **kwargs)
        return [float(value) for value in scores]


def compare_vectors(left, right):
    if not left or not right: return []
    spaces = {row.get('space_id') for row in [*left, *right]}
    if len(spaces) != 1 or None in spaces: raise ValueError('모델·revision·전처리·pooling·정규화 설정이 다른 벡터는 비교할 수 없습니다.')
    a, b = normalized([row['vector'] for row in left]), normalized([row['vector'] for row in right])
    if a.shape[1] != b.shape[1]: raise ValueError('벡터 차원이 다릅니다.')
    values = a @ b.T
    return sorted([{**{key: value for key, value in q.items() if key not in ('vector', 'dimension', 'space_id')}, 'left_id': p['record_id'], 'right_id': q['record_id'], 'cosine_similarity': float(values[i, j]), 'metric': 'cosine_similarity'} for i, p in enumerate(left) for j, q in enumerate(right)], key=lambda row: -row['cosine_similarity'])


def prepare_disk(remote):
    from importlib.metadata import version, PackageNotFoundError
    from packaging.version import Version
    if Version(version('torch')) < Version('2.5.1'): raise ValueError('Kornia 0.9.0에는 PyTorch 2.5.1 이상이 필요합니다. 현재 PyTorch를 자동으로 교체하지 않습니다.')
    try: available = version('kornia')
    except PackageNotFoundError: available = None
    try: rust_available = Version(version('kornia-rs')) >= Version('0.2.0')
    except PackageNotFoundError: rust_available = False
    if available != '0.9.0' or not rust_available: remote.setup_command('DISK·LightGlue 라이브러리 준비', [sys.executable, '-m', 'pip', 'install', '--no-deps', 'kornia==0.9.0', 'kornia-rs>=0.2,<0.3'], timeout=600)
    import kornia.feature as KF
    # Same reviewed API as the project's DISK verifier; constructors download their published weights.
    KF.DISK.from_pretrained('depth'); KF.LightGlueMatcher('disk')
    marker = Path.home() / '.cache/creative-checker-modeltest/disk-lightglue-ready'
    marker.parent.mkdir(parents=True, exist_ok=True); marker.write_text('0.9.0')
    return {'text': 'DISK·LightGlue 가중치 준비 완료. 실제 대조는 별도로 실행하세요.'}

def lexical_rank(query, rows):
    terms = lambda text: re.findall(r'\w+', text.lower())
    docs = [terms(row['text']) for row in rows]; average = sum(map(len, docs)) / max(1, len(docs)); result = []
    for row, doc in zip(rows, docs):
        score = 0.0
        for word in set(terms(query)):
            frequency = doc.count(word); df = sum(word in other for other in docs)
            if frequency: score += math.log(1 + (len(docs) - df + .5) / (df + .5)) * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * len(doc) / max(1, average)))
        result.append({**row, 'ranking_score': score, 'metric': 'bm25'})
    return sorted(result, key=lambda row: -row['ranking_score'])

def fuse_rankings(left, right, k=60):
    result = {}
    for ranking in (left, right):
        seen = set()
        for rank, row in enumerate(ranking, 1):
            identity = row.get('record_id')
            if not identity or identity in seen: raise ValueError('RRF 입력에는 중복 없는 후보 record_id가 필요합니다.')
            seen.add(identity)
            target = result.setdefault(identity, {**row, 'ranking_score': 0, 'metric': 'rrf'})
            target['ranking_score'] += 1 / (k + rank)
    return sorted(result.values(), key=lambda row: -row['ranking_score'])

def classical_pair(kind, images, remote, cache=None):
    import cv2
    import numpy as np
    pictures = [remote.image_of(path) for path in images]
    gray = [cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY) for image in pictures]
    if kind == 'phash':
        bits = []
        for image in gray:
            low = cv2.dct(cv2.resize(image, (32, 32)).astype(np.float32))[:8, :8].flatten()[1:]
            bits.append(low > np.median(low))
        return {'metric': 'phash_hamming', 'hash_distance': int(np.count_nonzero(bits[0] != bits[1])), 'hash_bits': 63, 'decision': 'uncertain'}
    if kind == 'disk_lightglue':
        import torch
        if not (Path.home() / '.cache/creative-checker-modeltest/disk-lightglue-ready').is_file(): raise ValueError('모델 라이브러리의 DISK·LightGlue 준비 버튼을 먼저 실행해 주세요.')
        from importlib.metadata import version
        if version('kornia') != '0.9.0': raise ValueError('DISK·LightGlue 준비를 다시 실행해 주세요. 등록한 실행 환경과 버전이 다릅니다.')
        checkpoints = Path(torch.hub.get_dir()) / 'checkpoints'
        if not all((checkpoints / name).is_file() for name in ('depth-save.pth', 'disk_lightglue_v0-1_arxiv-pth')): raise ValueError('DISK·LightGlue 가중치 캐시가 없습니다. 준비를 다시 실행해 주세요.')
        import kornia.feature as KF
        # The same DISK + LightGlue API is used in logo_pipeline.GeometricVerifier.
        if cache is None: cache = {}
        if 'disk' not in cache: cache['disk'] = (KF.DISK.from_pretrained('depth').to('cuda').eval(), KF.LightGlueMatcher('disk').to('cuda').eval())
        disk, matcher = cache['disk']
        features = []
        with torch.inference_mode():
            for image in pictures:
                tensor = torch.from_numpy(np.asarray(image).copy()).permute(2, 0, 1)[None].float().to('cuda') / 255
                features.append(disk(tensor, 1024, pad_if_not_divisible=True)[0])
            a, b = features
            _, ids = matcher(a.descriptors, b.descriptors, KF.laf_from_center_scale_ori(a.keypoints[None]), KF.laf_from_center_scale_ori(b.keypoints[None]))
        return geometry(a.keypoints[ids[:, 0]].cpu(), b.keypoints[ids[:, 1]].cpu())
    extractor = cv2.ORB_create(nfeatures=1024) if kind == 'orb' else cv2.SIFT_create(nfeatures=1024)
    a, b = [extractor.detectAndCompute(image, None) for image in gray]
    if a[1] is None or b[1] is None or len(b[0]) < 2: return geometry([], [])
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING if kind == 'orb' else cv2.NORM_L2)
    matches = [pair[0] for pair in matcher.knnMatch(a[1], b[1], k=2) if len(pair) == 2 and pair[0].distance < .75 * pair[1].distance]
    return geometry([a[0][match.queryIdx].pt for match in matches], [b[0][match.trainIdx].pt for match in matches])

def tfidf_lr(rows, config):
    import numpy as np
    vocabulary, idf, coef, bias, labels = (config.get(key) for key in ('vocabulary', 'idf', 'coef', 'intercept', 'labels'))
    if not isinstance(vocabulary, dict) or not isinstance(labels, list) or not labels: raise ValueError('학습한 TF-IDF vocabulary·idf·LR coef·intercept·라벨이 필요합니다.')
    weights = np.asarray(coef, dtype=float); offsets = np.asarray(bias, dtype=float); inverse = np.asarray(idf, dtype=float)
    if sorted(vocabulary.values()) != list(range(len(vocabulary))) or inverse.shape != (len(vocabulary),) or weights.shape != (len(labels), len(vocabulary)) or offsets.shape != (len(labels),) or not all(np.isfinite(value).all() for value in (weights, offsets, inverse)): raise ValueError('학습한 TF-IDF/LR 파라미터 차원이 맞지 않습니다. 이진 LR도 클래스별 가중치 행이 필요합니다.')
    result = []
    for row in rows:
        vector = np.zeros(len(vocabulary))
        for word in re.findall(r'\b\w\w+\b', row['text'].lower()):
            if word in vocabulary: vector[vocabulary[word]] += 1
        vector *= inverse; norm = np.linalg.norm(vector)
        if norm: vector /= norm
        logits = weights @ vector + offsets; scores = np.exp(logits - logits.max()); scores /= scores.sum()
        result.append({**row, 'class_scores': [{'label': label, 'score': float(score)} for label, score in zip(labels, scores)], 'metric': 'classification_score', 'note': '공백·단어 unigram, raw TF, L2 TF-IDF, 다중 클래스 softmax LR 규격입니다.'})
    return result


def execute_extra(stage, config, paths, root, frames, job, outputs, models, measure, remote):
    kind = stage.get('kind', stage['id']); params = stage.get('params', {}); inputs = stage.get('inputs', {})
    def source(role): return outputs.get(inputs.get(role), [])
    def texts():
        rows = source('text') or [{'text': text} for text in params.get('documents', [])]
        return [{**row, 'record_id': row.get('record_id') or hashlib.sha256(row.get('text', '').encode()).hexdigest()[:24]} for row in rows if isinstance(row.get('text'), str) and row['text'].strip()]
    def samples():
        mode = params.get('image_source', 'media')
        if mode == 'references': return [{**meta, 'path': paths['references'][meta['index']], 'record_id': f'reference-{meta["index"]}'} for meta in job['references']]
        if mode == 'crops': return [{**row, **logo, 'path': root / logo['preview'], 'record_id': f'crop-{inputs.get("images")}-{row.get("index", 0)}-{logo["logo_index"]}'} for row in source('images') for logo in row['logos']]
        rows = frames if config['flow'] == 'video' else [{**job['media'], 'path': paths['media']}]
        return [{**row, 'record_id': f'frame-{row.get("index", 0)}'} for row in rows]
    if kind == 'text_input': return texts()
    if kind == 'disk_lightglue_prepare': return [prepare_disk(remote)]
    if kind == 'vector_compare': return compare_vectors(source('left'), source('right'))
    if kind == 'rrf': return fuse_rankings(source('left'), source('right'))
    if kind == 'bm25': return lexical_rank(params.get('query') or config.get('requirements', ''), texts())
    if kind == 'tfidf_lr': return tfidf_lr(texts(), params.get('classifier', {}))
    if kind == 'model_prepare': return [measure(stage, 'load', prepare, stage['model_profile'], remote)]
    measure(stage, 'load', models.load, stage)
    adapter = models.registered
    if kind == 'rerank':
        rows = source('candidates') or (samples() if params.get('modality') == 'image' else texts())
        if not rows: raise ValueError('재정렬할 후보가 없습니다.')
        if params.get('modality') == 'image' and 'image' not in adapter.profile.get('modalities', []): raise ValueError('이 재정렬 모델은 텍스트 쌍만 지원합니다.')
        if params.get('modality') == 'image':
            values = []
            for row in rows:
                path = row.get('path') or (root / row['preview'] if row.get('preview') and re.fullmatch(remote.PREVIEW_NAME, row['preview']) else None)
                if path is None: raise ValueError('이미지 재정렬 입력에는 프레임 또는 크롭 미리보기 정보가 필요합니다.')
                values.append(str(path))
        else:
            if any(not isinstance(row.get('text'), str) for row in rows): raise ValueError('텍스트 재정렬 후보에는 text가 필요합니다.')
            values = [{'text': row['text']} for row in rows]
        scores = measure(stage, 'inference', adapter.rerank, params.get('query') or config.get('requirements', ''), values)
        return sorted([{**{k: v for k, v in row.items() if k != 'path'}, 'rerank_score': score, 'metric': 'rerank_score', 'score_transform': adapter.options['score_transform']} for row, score in zip(rows, scores)], key=lambda row: -row['rerank_score'])
    if kind in ('image_embed', 'text_embed', 'multimodal_embed'):
        rows = texts() if kind == 'text_embed' else samples()
        if not rows or len(rows) > 256: raise ValueError('임베딩 입력은 1~256개로 설정해 주세요.')
        if kind == 'text_embed': values = [row['text'] for row in rows]
        elif kind == 'multimodal_embed': values = [{'image': str(row['path']), 'text': '\n'.join(item['text'] for item in texts()) or params.get('query') or config.get('requirements', '')} for row in rows]
        else: values = [str(row['path']) for row in rows]
        vectors = measure(stage, 'inference', adapter.embed, values, 'text' if kind == 'text_embed' else 'image')
        return [{**{k: v for k, v in row.items() if k != 'path'}, 'vector': vector.tolist(), 'dimension': len(vector), 'space_id': adapter.profile['fingerprint'], 'metric': 'embedding', 'model_id': adapter.profile['model_id'], 'revision': adapter.profile['resolved_revision']} for row, vector in zip(rows, vectors)]
    if kind in ('image_classify', 'text_classify', 'document_classify'):
        rows = texts() if kind == 'text_classify' else samples(); results = []
        for row in rows:
            ocr = next((item for item in source('ocr') if item.get('index') == row.get('index')), {})
            value = row['text'] if kind == 'text_classify' else row['path']
            results.append({**{k: v for k, v in row.items() if k != 'path'}, **measure(stage, 'inference', adapter.classify, [value], 'text' if kind == 'text_classify' else 'image', ocr.get('ocr_words'))})
        if not results: raise ValueError('분류할 입력이 없습니다.')
        return results
    raise ValueError('지원하지 않는 일반 처리 단계입니다.')
