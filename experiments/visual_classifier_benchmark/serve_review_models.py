"""Loopback-only RunPod inference for the frozen SigLIP2 and Qwen 3.5 models."""
import argparse
import base64
import hmac
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

import evaluate_all_images as image_eval
import evaluate_requirements_json as text_eval
import evaluate_requirements_retry as retry
from requirements_compact import STRUCTURE_PROFILE, prompt
from requirements_condition_policy import normalize_exact_duplicates
from resultlib import LABELS

HERE = Path(__file__).resolve().parent
MAX_BODY = 32 * 1024 * 1024
LOCK = threading.Lock()
MODELS = None


def load_models():
    global MODELS
    if MODELS is not None:
        return MODELS
    import torch
    from safetensors.torch import load_file
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
    from encoders import load_encoder
    from finetune_siglip import load_state
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is required')
    args = SimpleNamespace(model_run=HERE / 'runs/siglip-ai-reviewed-v1-fp32',
        model_dir=Path('/workspace/models/siglip2-base-384'), bundle=HERE.parent / 'poster_benchmark/assets',
        models_root=Path('/workspace/models'))
    identity = image_eval.model_identity(args)
    qwen_packet = text_eval.packet(args, '4b')
    encoder = load_encoder('siglip2', args.model_dir, 'cuda:0', 'fp32', 384)
    head = torch.nn.Linear(encoder.dimension, len(LABELS), device='cuda:0', dtype=torch.float32)
    load_state(encoder, head, load_file(identity['checkpoint']))
    encoder.eval().requires_grad_(False)
    head.eval().requires_grad_(False)
    directory = text_eval.folder(args, '4b')
    processor = AutoProcessor.from_pretrained(str(directory), local_files_only=True, trust_remote_code=False)
    processor.tokenizer.padding_side = 'left'
    qwen = Qwen3_5ForConditionalGeneration.from_pretrained(str(directory), local_files_only=True,
        trust_remote_code=False, use_safetensors=True, dtype=torch.bfloat16,
        attn_implementation='sdpa').to('cuda:0').eval().requires_grad_(False)
    MODELS = (encoder, head, identity, processor, qwen, qwen_packet['revision'])
    return MODELS


def infer(payload):
    import torch
    criteria = payload['criteria']
    if (not isinstance(criteria, list) or not 1 <= len(criteria) <= 100 or
        any(not isinstance(c, dict) or set(c) != {'id', 'text'} or
            not isinstance(c['id'], str) or not isinstance(c['text'], str) or
            not 1 <= len(c['text']) <= 500 for c in criteria)):
        raise ValueError('Invalid criteria')
    data = base64.b64decode(payload['image_base64'], validate=True)
    if not 0 < len(data) <= 20 * 1024 * 1024:
        raise ValueError('Invalid image size')
    with LOCK:
        encoder, head, identity, processor, model, revision = load_models()
        with tempfile.TemporaryDirectory(prefix='review-image-') as directory:
            image = Path(directory) / 'input.png'
            image.write_bytes(data)
            with torch.inference_mode():
                p = torch.sigmoid(head(encoder(encoder.prepare(image).to('cuda:0'))))[0].cpu().tolist()
        scores = {label: {'score': float(value), 'threshold': float(threshold),
                          'prediction': int(value >= threshold)}
                  for label, value, threshold in zip(LABELS, p, identity['thresholds'])}
        args = SimpleNamespace(max_input_tokens=2048, max_new_tokens=768, prompt_profile=STRUCTURE_PROFILE)
        instruction = prompt(STRUCTURE_PROFILE)
        extracts = []
        for criterion in criteria:
            text = criterion['text']
            first = text_eval.infer(text, instruction, processor, model, args)
            chosen = first
            retried = False
            if not retry.valid(first['parsed']):
                second = retry.retry_infer(text, first, instruction, processor, model, args)
                retried = True
                if not second['skipped'] and retry.valid(second['parsed']):
                    chosen = second
            valid = retry.valid(chosen['parsed'])
            normalized = normalize_exact_duplicates(chosen, text) if valid else None
            extracts.append({'id': criterion['id'], 'valid': valid, 'retried': retried,
                             'value': normalized['parsed']['value'] if valid else None})
        return {'model': {'text': 'Qwen/Qwen3.5-4B', 'text_revision': revision,
                'text_profile': STRUCTURE_PROFILE, 'image': 'siglip2_previous_partial',
                'image_checkpoint_sha256': identity['checkpoint_sha256']},
                'image_labels': scores, 'criteria': extracts}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        token = os.environ['REVIEW_MODELS_TOKEN']
        if self.path != '/infer' or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                raise ValueError('Invalid request size')
            payload = json.loads(self.rfile.read(length))
            result = infer(payload)
            data = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except Exception as exc:
            print('Inference error:', type(exc).__name__, str(exc), flush=True)
            self.send_error(500, 'Inference failed')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if len(os.environ.get('REVIEW_MODELS_TOKEN', '')) < 32:
        raise SystemExit('Set REVIEW_MODELS_TOKEN to at least 32 characters')
    print('Review model server on 127.0.0.1; models load on the first request.', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
