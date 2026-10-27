"""Paired eight-label image evaluation: preserved SigLIP2 vs existing Qwen3.5-4B.

Both receive the same 384px whole-poster RGB input. No training, downloads,
threshold tuning or automatic retries. First repeat scores; all repeats time.
"""
import argparse
import fcntl
import gc
import hashlib
import json
import math
import time
import traceback
import uuid
from pathlib import Path

import evaluate_all_images as siglip_evidence
import evaluate_qwen_zero_shot as vision
import evaluate_requirements_json as requirements
import run_experiment as common
from benchlib import load_test
from qwen_zero_shot_report import metrics, parse_answer, timing
from resultlib import LABELS, digest, inside, read, require, save, sha, write_csv

HERE = Path(__file__).resolve().parent
MODELS = ('siglip2_partial', 'qwen3_5_4b_image')
STAGES = ('decode_preprocess_ms','transfer_generate_ms','decode_parse_ms','total_ms')


def sources():
    return {name:sha(HERE/name) for name in ('evaluate_qwen4b_images.py','run_qwen4b_images.sh',
        'evaluate_qwen_zero_shot.py','qwen_zero_shot_report.py','qwen_zero_shot_prompt.json',
        'evaluate_requirements_json.py','evaluate_all_images.py','run_experiment.py',
        'encoders.py','finetune_siglip.py','resultlib.py','../poster_benchmark/benchlib.py')}


def inputs(args):
    # The 4B download already contains the vision tower and image processor.
    packet = requirements.packet(args,'4b')
    require('preprocessor_config.json' in packet['files'],
            'The existing 4B image processor file is missing; no downloads or installs were performed')
    config = read(requirements.folder(args,'4b')/'config.json')
    require(config.get('vision_config'), 'Expected the multimodal Qwen3.5 checkpoint')
    args.model_run, args.model_dir = args.siglip_run, args.siglip_model_dir
    siglip = siglip_evidence.model_identity(args)
    _,_,rows = common.bundle_data(args.bundle)
    return packet,siglip,[r for r in rows if r['split']=='dev']


def preflight(args):
    packet,siglip,dev = inputs(args)
    print(json.dumps(dict(status='image_comparison_ready',
        qwen_model_id=packet['model_id'], qwen_revision=packet['revision'],
        siglip_checkpoint_sha256=siglip['checkpoint_sha256'],
        dev_images=len(dev), test_images=37, input='same 384px whole-poster RGB images',
        task='image presence of eight visual categories; no requirement text is scored',
        repeats=args.repeats,warmups=args.warmups,max_new_tokens=args.max_new_tokens,
        model_inference_executed=False,training_executed=False,installation_executed=False),
        ensure_ascii=False,indent=2))


def cache_check(record,identity,image_rgb_sha):
    require(all(record.get(k)==v for k,v in identity.items()), 'Cached image request identity differs')
    require(record.get('record_sha256')==digest({k:v for k,v in record.items() if k!='record_sha256'}),
            'Cached image request content changed')
    require(all(type(record[k]) in (int,float) and math.isfinite(record[k]) and record[k]>=0 for k in STAGES),
            'Invalid cached request timing')
    if identity['model']=='qwen3_5_4b_image':
        pred,error=parse_answer(record['raw_text'])
        require(pred==record['predictions'] and error==record['parse_error'] and
                record['input_rgb_sha256']==image_rgb_sha and record['image_grid_thw']==[1,24,24],
                'Cached image response interpretation differs')
    else:
        require(len(record['probabilities'])==len(LABELS) and all(type(p) in (int,float) and
                math.isfinite(p) and 0<=p<=1 for p in record['probabilities']), 'Invalid cached probabilities')


def score_comparison(args,populations,requests,cutoffs):
    comparisons,per_label,all_predictions,errors = [],[],[],[]
    for split,truth in populations.items():
        for name in MODELS:
            values=requests[(name,split)]
            predictions=[]
            for row in truth:
                repeats=[values[(row['image_id'],r)] for r in range(args.repeats)]
                first=repeats[0]
                predictions.append(dict(image_id=row['image_id'],image=row['image'],labels=row['labels'],
                    predictions=first['predictions'],parse_error=first.get('parse_error'),
                    probabilities=first.get('probabilities'),raw_text=first.get('raw_text'),
                    repeat_disagreement=any(x['predictions']!=first['predictions'] for x in repeats[1:])))
            score=metrics(predictions)
            conservative=[]
            for label in LABELS:
                value=score['per_label'][label]
                denominator=2*value['tp']+value['fp']+value['fn']+value['unanswered_positive']
                f1=2*value['tp']/denominator if denominator else 0.
                conservative.append(f1)
                per_label.append(dict(split=split,model=name,label=label,**value,
                    f1_including_positive_unanswered=f1))
            times=timing(list(values.values()))
            summary=dict(split=split,model=name,cases=len(truth),known_cells=score['known'],
                accuracy=score['accuracy'],coverage=score['coverage'],covered_accuracy=score['covered_accuracy'],
                macro_f1=sum(conservative)/8,macro_f1_covered=score['macro_f1_covered'],
                fp=score['fp'],fn=score['fn'],unanswered_positive=score['unanswered_positive'],
                unanswered_negative=score['unanswered_negative'],invalid_images=sum(bool(r['parse_error']) for r in predictions),
                repeat_disagreement_images=sum(r['repeat_disagreement'] for r in predictions),
                mean_ms=times['total_ms']['mean_ms'],median_ms=times['total_ms']['median_ms'],
                p95_ms=times['total_ms']['p95_ms'],timing_n=times['total_ms']['n'],
                timing_sessions=len({r['timing_session'] for r in values.values()}),
                accuracy_90=score['accuracy']>=.90,p95_50=times['total_ms']['p95_ms']<=50,
                precision='fp32' if name=='siglip2_partial' else 'bf16')
            comparisons.append(summary)
            all_predictions.extend(dict(split=split,model=name,**r) for r in predictions)
            folder=args.run_dir/name/split
            folder.mkdir(parents=True,exist_ok=True)
            save(folder/'predictions.json',predictions)
            save(folder/'metrics.json',dict(**score,macro_f1_including_positive_unanswered=sum(conservative)/8))
            save(folder/'timing_summary.json',times)
            write_csv(folder/'timing_raw.csv',[dict(image_id=r['image_id'],repeat=r['repeat'],
                timing_session=r['timing_session'],**{k:r[k] for k in STAGES}) for r in values.values()])
            for r in predictions:
                for j,label in enumerate(LABELS):
                    y,p=r['labels'][label],r['predictions'][j]
                    if y is not None and p!=y:
                        errors.append(dict(split=split,model=name,image_id=r['image_id'],image=r['image'],
                            label=label,truth=y,prediction=p,error='unanswered' if p is None else 'FN' if y else 'FP',
                            parse_error=r['parse_error']))
    write_csv(args.run_dir/'comparison.csv',comparisons)
    write_csv(args.run_dir/'per_label.csv',per_label)
    write_csv(args.run_dir/'errors.csv',errors,
              fields=['split','model','image_id','image','label','truth','prediction','error','parse_error'])
    write_csv(args.run_dir/'instrument_errors.csv',[r for r in errors if r['label']=='musical_instrument'],
              fields=['split','model','image_id','image','label','truth','prediction','error','parse_error'])
    save(args.run_dir/'predictions.json',all_predictions)
    save(args.run_dir/'summary.json',dict(results=comparisons,training_executed=False,
        labels_passed_to_model=False,previously_exposed_test=True,independent_test=False,
        note='Image presence only; no count/layout or final requirement-compliance verdict. '
             'Same 384px external input; internal processor rescaling is enforced by the pinned grid. '
             'SigLIP FP32 and Qwen BF16 are different deployment configurations. '
             'First repeat scores; all repeats time. No retries, repairs, vote or threshold tuning. '
             'Timings include image decoding/preprocessing, transfer, inference and full Qwen JSON generation/parsing. '
             'Loading, warmup, hashing, network, DB, external OCR and file writes excluded. '
             'Resumed timing sessions may experience different load.'))


def run(args):
    import torch
    from safetensors.torch import load_file
    from transformers import AutoProcessor,Qwen3_5ForConditionalGeneration
    from encoders import load_encoder
    from finetune_siglip import load_state
    packet,siglip,dev=inputs(args)
    env=common.cuda_environment('fp32')
    require(torch.cuda.is_bf16_supported(),'The existing GPU must support Qwen BF16')
    prompt=read(vision.PROMPT)
    require(list(prompt['definitions'])==LABELS,'Prompt label order differs')
    code=sources()
    cfg=dict(schema='qwen4b-siglip-image-comparison-v1',qwen_packet=packet,siglip=siglip,
        environment=env,code_hashes=code,prompt=prompt,split=args.split,repeats=args.repeats,
        warmups=args.warmups,max_new_tokens=args.max_new_tokens,ram_policy=args.ram_policy,
        model_order=list(MODELS),image_size=384,qwen_grid=[1,24,24],batch_size=1,
        thinking=False,do_sample=False,precision={'siglip2_partial':'fp32','qwen3_5_4b_image':'bf16'},
        training_executed=False,threshold_tuning=False,prompt_selection=False,labels_passed_to_model=False)
    signature=digest(cfg)
    args.run_dir.mkdir(parents=True,exist_ok=True)
    with (args.run_dir/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        protocol=dict(signature=signature,config=cfg)
        path=args.run_dir/'protocol.json'
        if path.exists():
            require(args.resume and read(path)==protocol,'Use a new run directory when settings or code change')
        else:
            require(all(p.name=='.lock' for p in args.run_dir.iterdir()),'Choose a new empty output directory')
            save(path,protocol)
        frozen=args.run_dir/'selection_frozen.json'
        selection=dict(protocol_signature=signature,selection='fixed zero-shot comparison; no test-based selection')
        if frozen.exists(): require(read(frozen)==selection,'Frozen protocol changed')
        else: save(frozen,selection)
        frozen_sha=sha(frozen)
        status_path=args.run_dir/'status.json'
        if status_path.exists() and read(status_path)['status']=='complete':
            require(all(sha(inside(args.run_dir,n))==h for n,h in read(status_path)['artifact_hashes'].items()),
                    'Completed outputs changed')
            print('already_complete: no inference repeated');return
        save(status_path,dict(status='running',protocol_signature=signature,training_executed=False))
        populations={s:dev if s=='dev' else load_test(args.bundle,frozen,frozen_sha)
                     for s in (('dev','test') if args.split=='all' else (args.split,))}
        session=uuid.uuid4().hex
        requests={}
        encoder=head=processor=model=None
        try:
            for name in MODELS:
                # Models are loaded sequentially, never resident together.
                if name=='siglip2_partial':
                    encoder=load_encoder('siglip2',args.siglip_model_dir,'cuda:0','fp32',384)
                    head=torch.nn.Linear(encoder.dimension,8,device='cuda:0',dtype=torch.float32)
                    load_state(encoder,head,load_file(siglip['checkpoint']))
                    encoder.eval();head.eval().requires_grad_(False)
                    for _ in range(args.warmups): common.timed_prediction(encoder,head,args.bundle,dev[0])
                else:
                    requirements.resources(args,['4b'])
                    processor=AutoProcessor.from_pretrained(str(requirements.folder(args,'4b')),
                        local_files_only=True,trust_remote_code=False)
                    processor.tokenizer.padding_side='left'
                    model=Qwen3_5ForConditionalGeneration.from_pretrained(str(requirements.folder(args,'4b')),
                        local_files_only=True,trust_remote_code=False,use_safetensors=True,
                        dtype=torch.bfloat16,attn_implementation='sdpa').to('cuda:0').eval().requires_grad_(False)
                    for _ in range(args.warmups):
                        vision.infer(inside(args.bundle,dev[0]['image']),processor,model,prompt,args.max_new_tokens)
                for split,rows in populations.items():
                    folder=args.run_dir/'requests'/name/split
                    folder.mkdir(parents=True,exist_ok=True)
                    requests[(name,split)]={}
                    for index,row in enumerate(rows,1):
                        image=inside(args.bundle,row['image'])
                        image_sha=sha(image)
                        with vision.letterbox(image,384) as rgb:
                            rgb_sha=hashlib.sha256(rgb.tobytes()).hexdigest()
                        for repeat in range(args.repeats):
                            identity=dict(protocol_signature=signature,model=name,split=split,
                                image_id=row['image_id'],image_sha256=image_sha,repeat=repeat)
                            cached=folder/(row['image_id']+'-r'+str(repeat)+'.json')
                            if cached.exists():
                                require(args.resume,'Cached inference needs --resume')
                                result=read(cached);cache_check(result,identity,rgb_sha)
                            else:
                                if name=='siglip2_partial':
                                    p,t=common.timed_prediction(encoder,head,args.bundle,row)
                                    probabilities=[float(x) for x in p]
                                    require(all(math.isfinite(x) and 0<=x<=1 for x in probabilities),'Invalid SigLIP scores')
                                    result=dict(probabilities=probabilities,
                                        predictions=[int(x>=c) for x,c in zip(probabilities,siglip['thresholds'])],
                                        parse_error=None,decode_preprocess_ms=t['decode_preprocess_ms'],
                                        transfer_generate_ms=t['transfer_model_head_ms'],decode_parse_ms=0.,total_ms=t['total_ms'])
                                else:
                                    result=vision.infer(image,processor,model,prompt,args.max_new_tokens)
                                    require(result['input_rgb_sha256']==rgb_sha,'Actual Qwen image input differs')
                                result=dict(**identity,**result,timing_session=session)
                                result['record_sha256']=digest(result)
                                save(cached,result)
                            if name=='siglip2_partial':
                                require(result['predictions']==[int(x>=c) for x,c in zip(result['probabilities'],siglip['thresholds'])],
                                        'Cached SigLIP thresholds differ')
                            requests[(name,split)][(row['image_id'],repeat)]=result
                        print(json.dumps(dict(event='image_comparison_progress',model=name,split=split,
                                              completed=index,total=len(rows))),flush=True)
                encoder=head=processor=model=None
                gc.collect();torch.cuda.empty_cache()
            require(sources()==code and inputs(args)[:2]==(packet,siglip) and sha(frozen)==frozen_sha,
                    'Model, data or source code changed during comparison')
            score_comparison(args,populations,requests,siglip['thresholds'])
            excluded={'.lock','status.json','failure.json'}
            artifacts={p.relative_to(args.run_dir).as_posix():sha(p) for p in args.run_dir.rglob('*')
                       if p.is_file() and p.name not in excluded and not p.name.endswith('.tmp')}
            save(status_path,dict(status='complete',protocol_signature=signature,artifact_hashes=artifacts,
                CUDA_executed=True,training_executed=False,image_inference_executed=True,model_deployed=False))
            print(json.dumps(dict(event='qwen4b_image_comparison_complete',run=str(args.run_dir))))
        except Exception as exc:
            save(args.run_dir/'failure.json',dict(reason=repr(exc),traceback=traceback.format_exc()))
            save(status_path,dict(status='failed',protocol_signature=signature,training_executed=False));raise
        finally:
            encoder=head=processor=model=None
            gc.collect();torch.cuda.empty_cache()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('preflight','run','show-results'))
    p.add_argument('--models-root',type=Path,default=Path('/workspace/models'))
    p.add_argument('--siglip-model-dir',type=Path,default=Path('/workspace/models/siglip2-base-384'))
    p.add_argument('--siglip-run',type=Path,default=HERE/'runs/siglip-ai-reviewed-v1-fp32')
    p.add_argument('--bundle',type=Path,default=HERE.parent/'poster_benchmark/assets')
    p.add_argument('--run-dir',type=Path,default=HERE/'runs/qwen4b-image-compare-v1')
    p.add_argument('--split',choices=('dev','test','all'),default='all')
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--warmups',type=int,default=3)
    p.add_argument('--max-new-tokens',type=int,default=192)
    p.add_argument('--ram-policy',choices=('inactive-file','clean-unmapped-file'),default='clean-unmapped-file')
    p.add_argument('--resume',action='store_true')
    args=p.parse_args()
    for key in ('models_root','siglip_model_dir','siglip_run','bundle','run_dir'):
        setattr(args,key,getattr(args,key).expanduser().resolve())
    require(1<=args.repeats<=20 and 1<=args.warmups<=20 and 96<=args.max_new_tokens<=512,'Invalid inference budgets')
    require(all(not args.run_dir.is_relative_to(root) and not root.is_relative_to(args.run_dir)
                for root in (args.models_root,args.siglip_run,args.bundle)), 'Output must be separate from inputs')
    if args.command=='preflight': preflight(args)
    elif args.command=='run': run(args)
    else:
        status=read(args.run_dir/'status.json')
        require(status['status']=='complete' and all(sha(inside(args.run_dir,n))==h
                for n,h in status['artifact_hashes'].items()),'Incomplete or changed results')
        print(json.dumps({k:v for k,v in status.items() if k!='artifact_hashes'},indent=2))
        print((args.run_dir/'comparison.csv').read_text(encoding='utf-8-sig'))
        print((args.run_dir/'per_label.csv').read_text(encoding='utf-8-sig'))


if __name__=='__main__': main()
