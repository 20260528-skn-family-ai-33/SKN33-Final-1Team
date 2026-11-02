"""Compare SigLIP, Qwen 4B, and a dev-selected sequential image cascade."""
import argparse
import fcntl
import json
import math
import time
import traceback
import uuid
from pathlib import Path

import analyze_image_complementarity as evidence
import evaluate_qwen4b_images as paired
import evaluate_qwen_zero_shot as vision
import evaluate_requirements_json as requirements
import run_experiment as common
from benchlib import load_test
from qwen_zero_shot_report import metrics
from resultlib import LABELS, digest, inside, percentile, read, require, save, sha, write_csv

HERE = Path(__file__).resolve().parent
SIGLIP = 'siglip2_partial'
QWEN = 'qwen3_5_4b_image'
CASCADE = 'siglip2_selective_qwen'
MODELS = (SIGLIP, QWEN, CASCADE)


def policy_labels(probabilities, thresholds, policy):
    if policy['scope'] == 'none':
        return []
    return [j for j, (p, t) in enumerate(zip(probabilities, thresholds))
            if (policy['scope'] == 'all' or LABELS[j] == 'musical_instrument')
            and abs(p - t) <= policy['margin']]


def merge(siglip, qwen, indices):
    result = list(siglip)
    for j in indices:
        if qwen[j] is not None:
            result[j] = qwen[j]
    return result


def conservative(score):
    values = []
    for label in LABELS:
        x = score['per_label'][label]
        denom = 2*x['tp'] + x['fp'] + x['fn'] + x['unanswered_positive']
        values.append(2*x['tp']/denom if denom else 0.0)
    return sum(values)/len(values)


def dev_selection(source, protocol, dev, thresholds):
    a = read(source/SIGLIP/'dev'/'predictions.json')
    b = read(source/QWEN/'dev'/'predictions.json')
    require(len(a)==len(b)==len(dev)==37, 'Expected 37 matching development images')
    for x, y, gold in zip(a, b, dev):
        require(x['image_id']==y['image_id']==gold['image_id'] and x['labels']==y['labels']==gold['labels']
                and x['image']==y['image']==gold['image'], 'Development data mismatch')
        require(x['predictions']==[int(p>=t) for p,t in zip(x['probabilities'],thresholds)],
                'Saved SigLIP probabilities or thresholds differ')
        parsed, error = evidence.parse_answer(y['raw_text'])
        require(parsed==y['predictions'] and error==y['parse_error'], 'Saved Qwen parse differs')
        for model, expected in ((SIGLIP,x),(QWEN,y)):
            req=read(source/'requests'/model/'dev'/(gold['image_id']+'-r0.json'))
            require(req['protocol_signature']==protocol['signature'] and req['predictions']==expected['predictions']
                    and req['record_sha256']==digest({k:v for k,v in req.items() if k!='record_sha256'})
                    and req['image_sha256']==sha(inside(paired.HERE.parent/'poster_benchmark/assets', gold['image'])),
                    'Saved development request or image differs')
    policies=[dict(scope='none',margin=0.0)] + [dict(scope=s,margin=m)
        for s in ('instrument','all') for m in (0.05,0.10,0.20,0.30,0.50)]
    candidates=[]
    for policy in policies:
        rows=[]; calls=0
        for x,y in zip(a,b):
            indices=policy_labels(x['probabilities'],thresholds,policy)
            calls+=bool(indices)
            rows.append(dict(labels=x['labels'],predictions=merge(x['predictions'],y['predictions'],indices)))
        score=metrics(rows)
        candidates.append(dict(**policy,dev_accuracy=score['accuracy'],dev_macro_f1=conservative(score),
                               dev_calls=calls,dev_call_rate=calls/len(rows)))
    baseline=candidates[0]
    for c in candidates:
        c['eligible']=(c['dev_accuracy']>=baseline['dev_accuracy'] and
                       c['dev_macro_f1']>=baseline['dev_macro_f1'])
        c['dev_goal_met']=(c['dev_accuracy']>=0.90 and c['dev_macro_f1']>=0.89)
    selected=max((c for c in candidates if c['eligible']),
                 key=lambda c:(c['dev_goal_met'],c['dev_macro_f1'],c['dev_accuracy'],-c['dev_calls']))
    return candidates, selected


def run(args):
    import torch
    from safetensors.torch import load_file
    from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration
    from encoders import load_encoder
    from finetune_siglip import load_state

    protocol, source_info=evidence.source_evidence(args.source_run)
    packet, siglip, dev=paired.inputs(args)
    require(protocol['config']['qwen_packet']==packet and protocol['config']['siglip']==siglip,
            'Models do not match the saved development comparison')
    require(protocol['config']['max_new_tokens']==args.max_new_tokens, 'Generation limit differs from dev evidence')
    env=common.cuda_environment('fp32')
    require(torch.cuda.is_bf16_supported(), 'GPU must support BF16')
    prompt=read(vision.PROMPT)
    require(prompt==protocol['config']['prompt'], 'Current prompt differs from saved development comparison')
    require(args.bundle==paired.HERE.parent/'poster_benchmark/assets', 'Expected original benchmark assets')
    candidates,selected=dev_selection(args.source_run,protocol,dev,siglip['thresholds'])
    code={p:sha(HERE/p) for p in ('evaluate_image_cascade.py','run_image_cascade.sh',
          'evaluate_qwen4b_images.py','evaluate_qwen_zero_shot.py','evaluate_requirements_json.py',
          'analyze_image_complementarity.py','resultlib.py','qwen_zero_shot_report.py',
          'run_experiment.py','encoders.py','finetune_siglip.py','../poster_benchmark/benchlib.py')}
    cfg=dict(schema='image-cascade-v1',source=source_info,source_run=str(args.source_run),packet=packet,
             siglip=siglip,prompt=prompt,environment=env,code_hashes=code,repeats=args.repeats,
             warmups=args.warmups,max_new_tokens=args.max_new_tokens,ram_policy=args.ram_policy,
             selection=selected,labels_used_for_test_selection=False)
    signature=digest(cfg)
    args.run_dir.mkdir(parents=True,exist_ok=True)
    with (args.run_dir/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        proto=args.run_dir/'protocol.json'; frozen=args.run_dir/'selection_frozen.json'
        if proto.exists():
            require(args.resume and read(proto)==dict(signature=signature,config=cfg),
                    'Existing run differs; use a new run directory')
            require(read(frozen)==dict(protocol_signature=signature,selection=selected,
                    test_used_for_selection=False), 'Frozen policy differs')
        else:
            require(all(p.name=='.lock' for p in args.run_dir.iterdir()), 'Run directory must be empty')
            save(proto,dict(signature=signature,config=cfg))
            save(frozen,dict(protocol_signature=signature,selection=selected,test_used_for_selection=False))
            write_csv(args.run_dir/'dev_candidates.csv',candidates)
        frozen_sha=sha(frozen)
        status=args.run_dir/'status.json'
        if status.exists() and read(status).get('status')=='complete':
            show(args); return
        save(status,dict(status='running',protocol_signature=signature,training_executed=False))
        # Selection is frozen before accessing test labels.
        rows=load_test(args.bundle,frozen,frozen_sha)
        session=uuid.uuid4().hex
        processor=model=encoder=head=None
        try:
            requirements.resources(args,['4b'])
            processor=AutoProcessor.from_pretrained(str(requirements.folder(args,'4b')),
                local_files_only=True,trust_remote_code=False)
            processor.tokenizer.padding_side='left'
            model=Qwen3_5ForConditionalGeneration.from_pretrained(str(requirements.folder(args,'4b')),
                local_files_only=True,trust_remote_code=False,use_safetensors=True,
                dtype=torch.bfloat16,attn_implementation='sdpa').to('cuda:0').eval().requires_grad_(False)
            encoder=load_encoder('siglip2',args.siglip_model_dir,'cuda:0','fp32',384)
            head=torch.nn.Linear(encoder.dimension,8,device='cuda:0',dtype=torch.float32)
            load_state(encoder,head,load_file(siglip['checkpoint']))
            encoder.eval();head.eval().requires_grad_(False)
            peak_before=torch.cuda.memory_allocated()
            torch.cuda.reset_peak_memory_stats()
            def sig(row):
                p,_=common.timed_prediction(encoder,head,args.bundle,row)
                p=[float(v) for v in p]
                require(len(p)==8 and all(math.isfinite(v) and 0<=v<=1 for v in p),'Invalid SigLIP scores')
                return p,[int(v>=t) for v,t in zip(p,siglip['thresholds'])]
            def qw(row):
                return vision.infer(inside(args.bundle,row['image']),processor,model,prompt,args.max_new_tokens)
            def timed(fn):
                torch.cuda.synchronize();start=time.perf_counter();value=fn();torch.cuda.synchronize()
                return value,(time.perf_counter()-start)*1000
            def cascade(row):
                p,pred=sig(row)
                indices=policy_labels(p,siglip['thresholds'],selected)
                q=qw(row) if indices else None
                return dict(probabilities=p,predictions=merge(pred,q['predictions'],indices) if q else pred,
                            qwen_called=bool(indices),reviewed_labels=[LABELS[j] for j in indices],
                            qwen_raw=q['raw_text'] if q else None,qwen_error=q['parse_error'] if q else None)
            for _ in range(args.warmups):
                sig(dev[0]);qw(dev[0]);cascade(dev[0])
            results=[]
            for i,row in enumerate(rows,1):
                for repeat in range(args.repeats):
                    identity=dict(protocol_signature=signature,image_id=row['image_id'],image=row['image'],
                                  image_sha256=sha(inside(args.bundle,row['image'])),repeat=repeat)
                    path=args.run_dir/'requests'/(row['image_id']+'-r'+str(repeat)+'.json')
                    if path.exists():
                        require(args.resume,'Cached request requires --resume')
                        rec=read(path)
                        require(all(rec.get(k)==v for k,v in identity.items()) and
                                rec['record_sha256']==digest({k:v for k,v in rec.items() if k!='record_sha256'}),
                                'Cached request changed')
                    else:
                        (prob,pred),sig_ms=timed(lambda:sig(row))
                        q,q_ms=timed(lambda:qw(row))
                        c,c_ms=timed(lambda:cascade(row))
                        rec=dict(**identity,labels=row['labels'],siglip=dict(probabilities=prob,predictions=pred),
                                 qwen=q,cascade=c,timings_ms={SIGLIP:sig_ms,QWEN:q_ms,CASCADE:c_ms},
                                 timing_session=session)
                        rec['record_sha256']=digest(rec);save(path,rec)
                    require(rec['labels']==row['labels'],'Cached labels differ')
                    results.append(rec)
                print(json.dumps(dict(event='cascade_progress',completed=i,total=len(rows))),flush=True)
            require(sha(frozen)==frozen_sha and evidence.source_evidence(args.source_run)[1]==source_info,
                    'Frozen selection or source changed during inference')
            summaries=[]; labels=[]; predictions=[]; timings=[]
            for name in MODELS:
                scored=[]; t=[]; call_count=0
                for row in rows:
                    reps=[r for r in results if r['image_id']==row['image_id']]
                    first=reps[0]
                    item=first['siglip'] if name==SIGLIP else first['qwen'] if name==QWEN else first['cascade']
                    scored.append(dict(image_id=row['image_id'],image=row['image'],labels=row['labels'],
                                       predictions=item['predictions']))
                    predictions.append(dict(model=name,**scored[-1]))
                    for r in reps:
                        t.append(r['timings_ms'][name])
                        called=(name==QWEN or (name==CASCADE and r['cascade']['qwen_called']))
                        call_count+=called
                        timings.append(dict(model=name,image_id=row['image_id'],repeat=r['repeat'],
                                            total_ms=r['timings_ms'][name],qwen_called=called,
                                            timing_session=r['timing_session']))
                score=metrics(scored)
                summaries.append(dict(model=name,images=len(rows),known_cells=score['known'],
                    accuracy=score['accuracy'],macro_f1=conservative(score),coverage=score['coverage'],
                    fp=score['fp'],fn=score['fn'],unanswered_positive=score['unanswered_positive'],
                    unanswered_negative=score['unanswered_negative'],qwen_calls=call_count,
                    qwen_call_rate=call_count/len(t),mean_ms=sum(t)/len(t),median_ms=percentile(t,.5),
                    p95_ms=percentile(t,.95),timing_n=len(t),timing_sessions=len({r['timing_session'] for r in results}),
                    accuracy_90=score['accuracy']>=.90,macro_f1_89=conservative(score)>=.89,
                    goal_met=score['accuracy']>=.90 and conservative(score)>=.89))
                for label in LABELS:
                    v=score['per_label'][label]
                    denominator=2*v['tp']+v['fp']+v['fn']+v['unanswered_positive']
                    labels.append(dict(model=name,label=label,**v,
                        conservative_f1=2*v['tp']/denominator if denominator else 0.0))
            write_csv(args.run_dir/'comparison.csv',summaries)
            write_csv(args.run_dir/'per_label.csv',labels)
            write_csv(args.run_dir/'timing_raw.csv',timings)
            save(args.run_dir/'predictions.json',predictions)
            save(args.run_dir/'summary.json',dict(results=summaries,selected=selected,
                GPU_allocated_before_measurement_bytes=peak_before,GPU_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                prior_test_exposed=True,independent_test=False,training_executed=False,installation_executed=False,
                note='First repeat scores; all repeats time. Each cascade request independently executes SigLIP and, '
                     'if routed, Qwen sequentially with both models resident. Wall time includes preprocessing, '
                     'routing, generation and parsing; excludes model loading, warmup, file writes, network, DB and OCR. '
                     'Both models use whole 384px poster; SigLIP FP32, Qwen BF16. Fixed execution order may affect timing. '
                     'Development-based policy selection is exploratory and may overfit 37 images. '
                     'Test has been repeatedly exposed; never use it to select policy.'))
            artifacts={p.relative_to(args.run_dir).as_posix():sha(p) for p in args.run_dir.rglob('*')
                       if p.is_file() and p.name not in ('.lock','status.json','failure.json') and not p.name.endswith('.tmp')}
            save(status,dict(status='complete',protocol_signature=signature,artifact_hashes=artifacts,
                             CUDA_executed=True,training_executed=False,installation_executed=False,
                             image_inference_executed=True,model_deployed=False))
            print(json.dumps(dict(event='image_cascade_complete',run=str(args.run_dir))),flush=True)
        except Exception as exc:
            save(args.run_dir/'failure.json',dict(reason=repr(exc),traceback=traceback.format_exc()))
            save(status,dict(status='failed',protocol_signature=signature,training_executed=False));raise


def show(args):
    status=read(args.run_dir/'status.json')
    require(status.get('status')=='complete' and all(sha(inside(args.run_dir,n))==h
            for n,h in status['artifact_hashes'].items()),'Incomplete or changed output')
    print(json.dumps({k:v for k,v in status.items() if k!='artifact_hashes'},indent=2))
    print(json.dumps(read(args.run_dir/'selection_frozen.json'),ensure_ascii=False,indent=2))
    for name in ('dev_candidates.csv','comparison.csv','per_label.csv'):
        print('\n'+name);print((args.run_dir/name).read_text(encoding='utf-8-sig'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('run','show-results'))
    p.add_argument('--models-root',type=Path,default=Path('/workspace/models'))
    p.add_argument('--siglip-model-dir',type=Path,default=Path('/workspace/models/siglip2-base-384'))
    p.add_argument('--siglip-run',type=Path,default=HERE/'runs/siglip-ai-reviewed-v1-fp32')
    p.add_argument('--bundle',type=Path,default=HERE.parent/'poster_benchmark/assets')
    p.add_argument('--source-run',type=Path,default=HERE/'runs/qwen4b-image-compare-v1')
    p.add_argument('--run-dir',type=Path,default=HERE/'runs/siglip-qwen-cascade-v1')
    p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--warmups',type=int,default=3)
    p.add_argument('--max-new-tokens',type=int,default=192)
    p.add_argument('--ram-policy',choices=('inactive-file','clean-unmapped-file'),default='clean-unmapped-file')
    p.add_argument('--resume',action='store_true')
    args=p.parse_args()
    for key in ('models_root','siglip_model_dir','siglip_run','bundle','source_run','run_dir'):
        setattr(args,key,getattr(args,key).expanduser().resolve())
    require(1<=args.repeats<=20 and 1<=args.warmups<=20,'Invalid repeat/warmup count')
    require(all(not args.run_dir.is_relative_to(r) and not r.is_relative_to(args.run_dir)
                for r in (args.models_root,args.siglip_run,args.bundle,args.source_run)),
            'Output must be separate from inputs')
    if args.command=='run': run(args)
    else: show(args)

if __name__=='__main__': main()
