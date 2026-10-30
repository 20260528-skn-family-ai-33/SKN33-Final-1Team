"""조건별 재인식 작업자. 기대 문구 없이 로컬 crop과 언어만 받는다."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import time

from scripts.prepare_paddle import MODEL, ENGLISH_MODEL, verify_model

MODEL_NAMES = {'ko': MODEL, 'en': ENGLISH_MODEL}
MAX_CROPS = 8


def run(manifest_path, output_path, model_root):
    os.environ.update(PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True', HF_HUB_OFFLINE='1')
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= MAX_CROPS:
        raise ValueError('Invalid crop count')
    if any(set(item) != {'id', 'path', 'language'} or item['language'] not in MODEL_NAMES for item in manifest):
        raise ValueError('Only crop identity, path and language are accepted')
    if len({item['id'] for item in manifest}) != len(manifest):
        raise ValueError('Duplicate crop IDs')
    from paddleocr import TextRecognition
    import paddle
    from PIL import Image
    models, identities, outputs = {}, {}, []
    started = time.monotonic()
    for item in manifest:
        language = item['language']
        if language not in models:
            name = MODEL_NAMES[language]
            directory = Path(model_root) / name
            identities[language] = verify_model(directory, name)
            models[language] = TextRecognition(model_name=name, model_dir=str(directory), device='cpu',
                                               enable_mkldnn=False, cpu_threads=4)
        path = Path(item['path'])
        with Image.open(path) as image:
            dimensions = list(image.size)
        result = models[language].predict(input=str(path), batch_size=1)
        if len(result) != 1:
            raise ValueError('Expected one recognition per crop')
        raw = result[0].json
        if isinstance(raw, str):
            raw = json.loads(raw)
        value = raw.get('res', raw)
        outputs.append({'id': item['id'], 'text': value['rec_text'], 'confidence': float(value['rec_score']),
                        'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'input_dimensions': dimensions, 'raw': raw})
    payload = {'outputs': outputs, 'model_identity': identities, 'device': paddle.get_device(),
               'paddleocr_version': importlib.metadata.version('paddleocr'), 'paddle_version': paddle.__version__,
               'elapsed_seconds': time.monotonic() - started}
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model-root', required=True)
    args = parser.parse_args()
    run(args.manifest, args.output, args.model_root)
