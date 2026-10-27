"""Paired OCR and frozen SigLIP2 resolution pilot; no training or installation."""
import argparse
import csv
import hashlib
import json
import os
import statistics
import sys
import time
import unicodedata
from pathlib import Path

import evaluate_all_images as img_eval
from resultlib import LABELS, percentile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
POSTER = HERE.parent / 'poster_benchmark'
sys.path.insert(0, str(POSTER))
from pipeline_eval import PaddleClient


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def packet(root):
    root = root.resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 'poster-resolution-pilot-v1' or not manifest.get('pairs'):
        raise ValueError('Invalid resolution packet')
    for pair in manifest['pairs']:
        for kind in ('small', 'large'):
            p = (root / pair[kind]['path']).resolve()
            if not p.is_relative_to(root) or not p.is_file() or sha(p) != pair[kind]['sha256']:
                raise ValueError('Input image missing or hash changed: ' + pair['id'] + '/' + kind)
    return manifest


def paths(args):
    if not args.ocr_python.is_file():
        raise FileNotFoundError('Existing Paddle Python not found: ' + str(args.ocr_python))
    for name in ('PP-OCRv5_server_det', 'korean_PP-OCRv5_mobile_rec'):
        if not (args.ocr_models / name).is_dir():
            raise FileNotFoundError('Existing Paddle model directory missing: ' + str(args.ocr_models / name))


def norm(text):
    # Only a partial diagnostic: keep numbers and Korean/Latin characters.
    text = unicodedata.normalize('NFKC', text).casefold()
    return ''.join(c for c in text if c.isalnum())


def csv_write(path, rows):
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def timing(values):
    return dict(n=len(values), mean_ms=statistics.mean(values), median_ms=statistics.median(values),
                p95_ms=percentile(values, .95))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('preflight', 'run', 'show-results'))
    p.add_argument('--packet', type=Path, default=Path('/workspace/poster-resolution-p160-v1'))
    p.add_argument('--out', type=Path, default=HERE/'runs/poster-resolution-p160-v1')
    p.add_argument('--bundle', type=Path, default=POSTER/'assets')
    p.add_argument('--model-dir', type=Path, default=Path('/workspace/models/siglip2-base-384'))
    p.add_argument('--model-run', type=Path, default=HERE/'runs/siglip-ai-reviewed-v1-fp32')
    p.add_argument('--ocr-python', type=Path, default=Path('/workspace/venvs/poster-paddleocr/bin/python'))
    p.add_argument('--ocr-models', type=Path, default=REPO/'.runtime/paddle-models')
    p.add_argument('--ocr-device', choices=('cpu', 'gpu:0'), default='cpu')
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--warmups', type=int, default=2)
    args = p.parse_args()
    if args.command == 'show-results':
        for name in ('summary.json','visual_comparison.csv','visual_per_label.csv','ocr_comparison.csv','ocr_fields.csv'):
            print('\n' + name + '\n' + (args.out/name).read_text(encoding='utf-8'))
        return
    if not 1 <= args.repeats <= 20 or not 1 <= args.warmups <= 20:
        raise ValueError('repeats and warmups must be between 1 and 20')
    data = packet(args.packet)
    identity = img_eval.model_identity(args)
    issue = None
    try:
        paths(args)
    except (ValueError, OSError) as exc:
        issue = str(exc)
    print(json.dumps({'status':'ready' if not issue else 'ocr_environment_missing',
        'pairs':len(data['pairs']), 'siglip_checkpoint_sha256':identity['checkpoint_sha256'],
        'ocr_python':str(args.ocr_python), 'ocr_models':str(args.ocr_models), 'ocr_issue':issue,
        'training_executed':False, 'installation_executed':False, 'inference_executed':False},ensure_ascii=False),flush=True)
    if args.command == 'preflight':
        return
    if issue:
        raise SystemExit('OCR 환경이 없어 중단합니다. 설치하지 않았습니다.')
    if args.out.exists():
        raise SystemExit('출력 폴더가 이미 있습니다. 기존 결과를 보존하려면 새 --out을 지정하세요.')
    args.out.mkdir(parents=True)
    import torch
    from safetensors.torch import load_file
    from encoders import load_encoder
    from finetune_siglip import load_state
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is required for the frozen image model')
    env = {**os.environ, 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1', 'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK':'True'}
    os.environ.update(env)
    ocr = PaddleClient([str(args.ocr_python),str(POSTER/'paddle_service.py'),
        '--device',args.ocr_device,'--models',str(args.ocr_models)],args.out/'paddle_worker.log',timeout=240)
    try:
        encoder=load_encoder('siglip2',args.model_dir,'cuda:0','fp32',384)
        head=torch.nn.Linear(encoder.dimension,len(LABELS),device='cuda:0',dtype=torch.float32)
        load_state(encoder,head,load_file(identity['checkpoint']))
        encoder.eval(); head.eval().requires_grad_(False)
        visual_rows=[]; visual_labels=[]; ocr_rows=[]; field_rows=[]; timing_rows=[]
        for pair in data['pairs']:
            for variant in ('small','large'):
                path=(args.packet/pair[variant]['path']).resolve()
                for _ in range(args.warmups):
                    ocr.predict(path,'warmup')
                    with torch.inference_mode(): head(encoder(encoder.prepare(path).to('cuda:0')))
                    torch.cuda.synchronize()
                times=[]; visual_times=[]; first_ocr=None; first_pred=None
                for repeat in range(args.repeats):
                    started=time.perf_counter(); response=ocr.predict(path,pair['id'])
                    times.append((time.perf_counter()-started)*1000)
                    torch.cuda.synchronize(); started=time.perf_counter()
                    with torch.inference_mode():
                        probability=torch.sigmoid(head(encoder(encoder.prepare(path).to('cuda:0'))))[0].cpu().tolist()
                    torch.cuda.synchronize(); visual_times.append((time.perf_counter()-started)*1000)
                    prediction=[int(v>=threshold) for v,threshold in zip(probability,identity['thresholds'])]
                    timing_rows.append(dict(image_id=pair['id'],variant=variant,repeat=repeat+1,
                                            ocr_wall_ms=times[-1],visual_total_ms=visual_times[-1]))
                    if first_ocr is None:first_ocr=response;first_pred=prediction
                    if repeat == 0: first_probability = probability
                    print(json.dumps({'event':'resolution_progress','image':pair['id'],'variant':variant,'repeat':repeat+1},ensure_ascii=False),flush=True)
                raw=(first_ocr['raw'].get('res') or first_ocr['raw'])
                detected=' '.join(raw['rec_texts'])
                detected_norm=norm(detected)
                found=0; tp=fp=tn=fn=0
                for field in pair['ocr_fields']:
                    expected=norm(field['text'])
                    present=bool(expected and expected in detected_norm)
                    truth=field.get('expected_present')
                    if type(truth) is not bool: raise ValueError('OCR query truth must be explicitly annotated')
                    found+=present and truth
                    tp+=present and truth; fp+=present and not truth
                    tn+=not present and not truth; fn+=not present and truth
                    field_rows.append(dict(image_id=pair['id'],variant=variant,field=field['id'],expected=field['text'],
                        expected_present=truth,found_exact_normalized=present))
                total=tp+fp+tn+fn
                ocr_rows.append(dict(image_id=pair['id'],variant=variant,anchors=len(pair['ocr_fields']),
                    positive_found=found,tp=tp,fp=fp,tn=tn,fn=fn,query_accuracy=(tp+tn)/total if total else None,
                    query_precision=tp/(tp+fp) if tp+fp else None,query_recall=tp/(tp+fn) if tp+fn else None,
                    query_f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,**timing(times)))
                correct=0
                for label,pred,prob in zip(LABELS,first_pred,first_probability):
                    truth=pair['visual_labels'][label]
                    if truth is not None:correct+=int(truth==pred)
                    visual_labels.append(dict(image_id=pair['id'],variant=variant,label=label,truth=truth,prediction=pred,probability=prob))
                known=sum(v is not None for v in pair['visual_labels'].values())
                visual_rows.append(dict(image_id=pair['id'],variant=variant,known_cells=known,correct=correct,
                    label_accuracy=correct/known if known else None,**timing(visual_times)))
                (args.out/f"{pair['id']}-{variant}-ocr.json").write_text(json.dumps(first_ocr,ensure_ascii=False,indent=2),encoding='utf-8')
        csv_write(args.out/'visual_comparison.csv',visual_rows)
        csv_write(args.out/'visual_per_label.csv',visual_labels)
        csv_write(args.out/'ocr_comparison.csv',ocr_rows)
        csv_write(args.out/'ocr_fields.csv',field_rows)
        csv_write(args.out/'timing_raw.csv',timing_rows)
        aggregates={}
        for variant in ('small','large'):
            cells=[x for x in visual_labels if x['variant']==variant and x['truth'] is not None]
            tp=sum(x['truth']==1 and x['prediction']==1 for x in cells)
            fp=sum(x['truth']==0 and x['prediction']==1 for x in cells)
            fn=sum(x['truth']==1 and x['prediction']==0 for x in cells)
            positives={label:sum(x['label']==label and x['truth']==1 for x in cells) for label in LABELS}
            aggregates[variant]={'known_cells':len(cells),'tp':tp,'fp':fp,'fn':fn,
                'micro_f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
                'macro_f1':None,'gold_positive_by_label':positives}
        summary={'status':'complete','pair_count':len(data['pairs']),'packet_manifest_sha256':sha(args.packet/'manifest.json'),
            'siglip_checkpoint_sha256':identity['checkpoint_sha256'], 'GPU':torch.cuda.get_device_name(0),
            'ocr_model':ocr.identity,'training_executed':False,'installation_executed':False,
            'visual_aggregate':aggregates,
            'visual_input':'both input images letterboxed to 384x384; source resolution is not native model resolution',
            'ocr_input':'original source size; worker preprocessing also has a detection-side limit',
            'ocr_query_metrics':'presence-query accuracy/F1 on explicitly annotated positive/negative strings only; '
                                'artificial negatives and joined OCR text can overstate performance; not full OCR accuracy/F1',
            'visual_macro_f1':'not computed: one image has no positive examples for most labels',
            'timing':'warmups excluded; OCR wall includes process IPC; visual includes decode, preprocessing and CUDA sync; model loading excluded',
            'limitations':data['notes'],'independent_test':False}
        (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
