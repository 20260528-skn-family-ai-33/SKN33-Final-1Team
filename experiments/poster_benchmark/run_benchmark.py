"""CUDA-only RunPod entrypoint. validate is CPU-only; no paid resources created."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import gc
import importlib.metadata as metadata
import json
import math
import os
import random
import sys
import time
import traceback

from benchlib import (ARMS, LABELS, MODEL_ID, REVISION, SEED, contained, csv_rows,
                      digest, load_test, log, metrics, read, save, sha, targets,
                      thresholds, timing_orders, verify_bundle)

HERE = Path(__file__).resolve().parent
PACKAGES = ('torch','torchvision','numpy','pillow','scikit-learn','transformers','sentence-transformers','huggingface-hub')


def get_model_dir(root, supplied, cache):
    expected = read(root/'references/base_model_files.json')
    if supplied:
        folder = Path(supplied).resolve(strict=True)
    else:
        from huggingface_hub import snapshot_download
        folder = Path(snapshot_download(repo_id=MODEL_ID, revision=REVISION, cache_dir=str(cache),
                                       allow_patterns=list(expected['files'])))
    for name, checksum in expected['files'].items():
        p = contained(folder, name)
        if not p.is_file() or sha(p) != checksum:
            raise ValueError('Downloaded model revision/file differs: '+name)
    return folder


def cuda_environment(args):
    # Must be set before torch import. Use a dedicated venv; no CPU fallback.
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA GPU required. Run validate/tests locally; use a GPU Pod for smoke/train/evaluate.')
    if args.precision == 'bf16' and not torch.cuda.is_bf16_supported():
        raise RuntimeError('This GPU does not support BF16')
    torch.set_num_threads(4)
    torch.set_float32_matmul_precision('highest')
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
    torch.cuda.set_device(0)
    prop = torch.cuda.get_device_properties(0)
    return dict(device='cuda:0', precision=args.precision, parameter_dtype='float32',
                GPU=prop.name, GPU_total_bytes=prop.total_memory, compute_capability=list(torch.cuda.get_device_capability()),
                CUDA_runtime=torch.version.cuda, versions={p:metadata.version(p) for p in PACKAGES},
                python=sys.version, torch_threads=4, TF32=False, cudnn_benchmark=False,
                deterministic_seed=SEED, strict_bitwise_determinism_not_guaranteed=True)


def seed(value):
    import numpy as np
    import torch
    random.seed(value); np.random.seed(value); torch.manual_seed(value); torch.cuda.manual_seed_all(value)


def memory():
    import torch
    torch.cuda.synchronize()
    return dict(allocated_bytes=torch.cuda.memory_allocated(), reserved_bytes=torch.cuda.memory_reserved(),
                peak_allocated_bytes=torch.cuda.max_memory_allocated(), peak_reserved_bytes=torch.cuda.max_memory_reserved())


def release():
    import torch
    gc.collect(); torch.cuda.empty_cache(); torch.cuda.synchronize()


def torch_save(path, value):
    import torch
    path = Path(path); tmp = path.with_name(path.name+'.tmp')
    torch.save(value, tmp); tmp.replace(path)


def checkpoints_valid(selection, run):
    for c in selection['candidates']:
        p = contained(run, c['checkpoint'])
        if sha(p) != c['checkpoint_sha256']: raise ValueError('Selected checkpoint changed')


def train_eg2(root, out, args, model_dir, td):
    import numpy as np
    import torch
    from models import EG, image, masked_bce, parameter_hashes, precision_context, representative_parameters
    device = torch.device('cuda:0')
    train = [x for x in td if x['split']=='train']; dev = [x for x in td if x['split']=='dev']
    ydev = targets(dev); heads = read(root/'references/eg2_heads.json')['heads']
    results = []
    for lr in args.backbone_lrs:
        name = 'lr'+format(lr,'.0e')
        folder = out/'candidates'/name; folder.mkdir(parents=True, exist_ok=True)
        done = folder/'candidate_complete.json'
        if done.exists():
            result = read(done)
            if result['protocol_signature']!=args.signature or result['backbone_lr']!=lr:raise ValueError('Completed candidate config changed')
            if sha(contained(out,result['checkpoint'])) != result['checkpoint_sha256']: raise ValueError('Completed checkpoint changed')
            saved=np.load(folder/f"dev_epoch_{result['best_epoch']:03d}.npy",allow_pickle=False)
            if thresholds(ydev,saved)!=result['thresholds'] or metrics(ydev,saved,result['thresholds'])!=result['dev_metrics']:
                raise ValueError('Completed candidate dev selection changed')
            results.append(result); continue
        seed(SEED); model = EG(model_dir,device,heads)
        for p in model.parameters(): p.requires_grad_(True)
        parameters = list(model.st.parameters())
        optimizer = torch.optim.AdamW([{'params':parameters,'lr':lr},{'params':list(model.classifier.parameters()),'lr':1e-3}],
                                     weight_decay=1e-4,foreach=False)
        del parameters, p
        latest = folder/'latest.pt'
        evidence_path = folder/'initial_parameter_hashes.json'
        if latest.exists():
            state = torch.load(latest,map_location='cpu',weights_only=True)
            if state['protocol_signature'] != args.signature: raise ValueError('Resume protocol differs')
            model.load_state_dict(state['model']); optimizer.load_state_dict(state['optimizer'])
            completed, steps, stale, best = state['epoch'],state['steps'],state['stale'],state['best']
            if sha(folder/best['filename']) != best['sha256']: raise ValueError('Resume best checkpoint differs')
            del state
            if not evidence_path.exists(): raise ValueError('Missing initial parameter evidence')
            log(out,'candidate_resume',candidate=name,completed_epoch=completed)
        else:
            completed = steps = stale = 0; best = None
            save(evidence_path,parameter_hashes(model))
        representatives = representative_parameters(model)
        save(folder/'model_scope.json',dict(backbone_parameters=sum(p.numel() for p in model.st.parameters()),
             trainable_parameters=sum(p.numel() for p in model.parameters()),
             optimizer_parameter_count=sum(p.numel() for g in optimizer.param_groups for p in g['params']),
             representative_names=list(representatives),full_backbone=True,audio_loaded=False))
        # Epoch-indexed logs are replaced on retry. Resume discards an interrupted partial epoch.
        for epoch in range(completed+1,args.max_epochs+1):
            if stale >= args.patience: break
            seed(SEED+epoch); order=list(range(len(train))); random.Random(SEED+epoch).shuffle(order)
            model.set_training(True); torch.cuda.reset_peak_memory_stats()
            total_loss=0.; total_known=0; start_time=time.perf_counter()
            with (folder/f'train_steps_epoch_{epoch:03d}.jsonl').open('w',encoding='utf-8') as trace:
                for start in range(0,len(order),args.effective_batch):
                    group=[train[i] for i in order[start:start+args.effective_batch]]
                    known=sum(v is not None for row in group for v in row['labels'].values())
                    optimizer.zero_grad(set_to_none=True); group_loss=0.; seen=[]
                    for offset in range(0,len(group),args.microbatch):
                        rows=group[offset:offset+args.microbatch]
                        ims=[image(root,row,augment=True) for row in rows]
                        try:
                            raw=torch.tensor(targets(rows),device=device)
                            with precision_context(args.precision,device): logits=model(ims,device)
                            loss,numerator=masked_bce(logits,raw,known)
                            if not torch.isfinite(loss): raise FloatingPointError('Nonfinite training loss')
                            loss.backward(); value=float(numerator.detach().item()); group_loss+=value
                            total_loss+=value; total_known+=int(torch.isfinite(raw).sum().item());seen.extend(x['image_id'] for x in rows)
                        finally:
                            for im in ims: im.close()
                        del raw,logits,loss,numerator,ims
                    if any(p.grad is None for p in model.parameters()):
                        raise FloatingPointError('Missing active parameter gradient')
                    torch.nn.utils.clip_grad_norm_(model.parameters(),float('inf'),error_if_nonfinite=True,foreach=True)
                    if not (folder/'first_gradients.json').exists():
                        grads={n:float(p.grad.float().norm().item()) for n,p in representatives.items()}
                        embedding=next(p for n,p in model.named_parameters() if n.endswith('.language_model.embed_tokens.weight'))
                        grads['text_embedding_weight']=float(embedding.grad.float().norm().item())
                        if any(v<=0 or not math.isfinite(v) for v in grads.values()): raise ValueError('Full-body gradient evidence failed')
                        save(folder/'first_gradients.json',grads);del embedding
                    optimizer.step();steps+=1;optimizer.zero_grad(set_to_none=True)
                    trace.write(json.dumps(dict(epoch=epoch,step=steps,image_ids=seen,known_cells=known,loss=group_loss/known))+'\n');trace.flush()
                    log(out,'train_step',candidate=name,epoch=epoch,optimizer_step=steps,images_done=min(start+len(group),len(train)),images_total=len(train),loss=group_loss/known)
            if total_known != 861: raise ValueError('Unknown mask/epoch population changed')
            model.set_training(False); probs=[]
            with torch.inference_mode():
                for row in dev:
                    im=image(root,row)
                    with precision_context(args.precision,device): p=torch.sigmoid(model(im,device).float())
                    probs.append(p.float().cpu().numpy()[0]);im.close();del p
            pdev=np.asarray(probs);np.save(folder/f'dev_epoch_{epoch:03d}.npy',pdev)
            score=metrics(ydev,pdev,[.5]*8);ap=score['macro_average_precision']
            previous_best=best
            if best is None or ap > best['dev_macro_AP']:
                filename=f'best_epoch_{epoch:03d}.pt'
                torch_save(folder/filename,model.state_dict())
                hashfile=f'best_epoch_{epoch:03d}_parameter_hashes.json'
                best=dict(epoch=epoch,filename=filename,sha256=sha(folder/filename),dev_macro_AP=ap,hashes_filename=hashfile)
                save(folder/hashfile,parameter_hashes(model));stale=0
            else: stale+=1
            save(folder/f'epoch_{epoch:03d}.json',dict(epoch=epoch,loss=total_loss/total_known,known_cells=total_known,
                 dev_metrics_at05=score,best=best,stale_epochs=stale,seconds=time.perf_counter()-start_time,memory=memory()))
            # Commit after data/log files, retaining the previous valid latest on interruption.
            torch_save(latest,dict(protocol_signature=args.signature,epoch=epoch,steps=steps,stale=stale,best=best,
                                   model=model.state_dict(),optimizer=optimizer.state_dict()))
            if previous_best and previous_best['filename'] != best['filename']:
                (folder/previous_best['filename']).unlink(missing_ok=True)
            log(out,'epoch_complete',candidate=name,epoch=epoch,dev_macro_AP=ap,best_epoch=best['epoch'],memory=memory())
        if best is None: raise ValueError('No completed training epoch')
        pdev=np.load(folder/f"dev_epoch_{best['epoch']:03d}.npy",allow_pickle=False)
        cutoffs=thresholds(ydev,pdev);score=metrics(ydev,pdev,cutoffs)
        initial=read(evidence_path);final=read(folder/best['hashes_filename'])
        if set(initial)!=set(final): raise ValueError('Trainable scope changed')
        changed={n:initial[n]!=final[n] for n in initial}
        if not all(changed[n] for n in representatives) or not any(changed[n] for n in changed if '.embed_tokens.' in n):
            raise ValueError('Body/head learned weights did not change')
        save(folder/'weight_changes.json',dict(changed=changed,changed_tensor_count=sum(changed.values()),initialization='Original base + original frozen LR heads'))
        result=dict(id=name,backbone_lr=lr,best_epoch=best['epoch'],checkpoint=str((folder/best['filename']).relative_to(out)),
                    checkpoint_sha256=best['sha256'],thresholds=cutoffs,dev_metrics=score,epoch0_selection_eligible=False,protocol_signature=args.signature)
        save(done,result);results.append(result)
        del representatives,model,optimizer,initial,final;release()
    selected=max(results,key=lambda x:(x['dev_metrics']['macro_f1'],x['dev_metrics']['macro_average_precision'],-x['backbone_lr']))
    return dict(kind='dev_selected_fresh_EG2_training',candidates=results,selected=selected,
                ResNet18_retrained=False,test_used_for_selection=False,previously_exposed_test=True)


def evaluate(root,out,args,model_dir,selection,selection_hash):
    import numpy as np
    import torch
    from models import EG,ResNet,image,precision_context
    device=torch.device('cuda:0');rows=load_test(root,out/'selection_frozen.json',selection_hash)
    y=targets(rows);heads=read(root/'references/eg2_heads.json')['heads']
    reference=read(root/'references/local_results.json');rs=read(root/'references/resnet_selection.json')
    if reference['test_ids'] != [x['image_id'] for x in rows]: raise ValueError('Reference test IDs differ')
    log(out,'test_access_after_selection',selection_sha256=selection_hash,images=len(rows))
    results={};fingerprints={}
    for arm in ARMS:
        folder=out/'arms'/arm;folder.mkdir(parents=True,exist_ok=True)
        done=folder/'arm_complete.json'
        if done.exists() and (folder/'arm_complete_commit.json').exists():
            if read(folder/'arm_complete_commit.json')['sha256']!=sha(done):raise ValueError('Cached arm commit changed')
            cached=read(done)
            if cached['selection_sha256']!=selection_hash: raise ValueError('Cached arm selection mismatch')
            for rel,h in cached['file_hashes'].items():
                if sha(contained(folder,rel))!=h: raise ValueError('Cached arm output changed')
            for image_id,fingerprint in read(folder/'shared_input_fingerprints.json').items():
                if fingerprints.setdefault(image_id,fingerprint)!=fingerprint:raise ValueError('Cached RGB inputs differ')
            results[arm]=cached['result'];continue
        model=None
        try:
            torch.cuda.reset_peak_memory_stats();load_start=time.perf_counter()
            if arm==ARMS[0]:
                model=ResNet();model.load_state_dict(torch.load(root/'references/resnet18_full_finetune.pt',map_location='cpu',weights_only=True));model.to(device)
                cutoffs=rs['thresholds']
            else:
                model=EG(model_dir,device,heads);model.set_training(False)
                if arm==ARMS[2]:
                    selected=selection['selected'];checkpoint=contained(out,selected['checkpoint']) if selection['kind']=='dev_selected_fresh_EG2_training' else root/'references/eg2_full_finetune.pt'
                    if sha(checkpoint)!=selected['checkpoint_sha256']: raise ValueError('EG2 checkpoint changed')
                    state=torch.load(checkpoint,map_location='cpu',weights_only=True);model.load_state_dict(state);del state
                    cutoffs=selected['thresholds']
                else:cutoffs=[heads[k]['threshold'] for k in LABELS]
            model.eval()
            for p in model.parameters():p.requires_grad_(False)
            torch.cuda.synchronize();load_seconds=time.perf_counter()-load_start
            model_metadata=dict(active_parameters=sum(p.numel() for p in model.parameters()),
                inference_gradient_checkpointing=False,parameter_dtype=sorted({str(p.dtype) for p in model.parameters()}),
                attention_implementation=str(getattr(getattr(getattr(model,'st',[None])[0],'model',None),'config',None)._attn_implementation) if hasattr(model,'st') else 'torchvision_resnet18')

            def predict(row):
                torch.cuda.synchronize();begin=time.perf_counter();im=image(root,row);prepared=time.perf_counter()
                try:
                    with torch.inference_mode(),precision_context(args.precision,device):
                        emb=model.embed(im,device)
                        if arm==ARMS[1]:
                            features=emb.float().cpu().numpy().astype(np.float64)
                            logits=np.stack([features@np.asarray(heads[k]['coef'])+heads[k]['intercept'] for k in LABELS],axis=1)
                            prob=(1/(1+np.exp(-np.clip(logits,-60,60))))[0]
                        else:prob=torch.sigmoid(model.classifier(emb).float()).cpu().numpy()[0]
                    torch.cuda.synchronize();end=time.perf_counter()
                    return prob,dict(decode_shared_preprocess_ms=(prepared-begin)*1000,model_and_head_ms=(end-prepared)*1000,total_ms=(end-begin)*1000),hashlib_image(im)
                finally:im.close()

            probabilities=[]
            for row in rows:
                prob,_,fingerprint=predict(row);probabilities.append(prob)
                if fingerprints.setdefault(row['image_id'],fingerprint)!=fingerprint:raise ValueError('Shared RGB input changed')
            probs=np.asarray(probabilities);pred=(probs>=np.asarray(cutoffs)).astype(int)
            np.save(folder/'test_probs.npy',probs);np.save(folder/'test_preds.npy',pred)
            save(folder/'test_rows.json',[dict(image_id=r['image_id'],group_id=r['group_id']) for r in rows])
            save(folder/'test_predictions.json',[dict(image_id=r['image_id'],truth=r['labels'],probabilities=probs[n].tolist(),predictions=pred[n].tolist()) for n,r in enumerate(rows)])
            score=metrics(y,probs,cutoffs);save(folder/'test_metrics.json',score)
            comparison=None
            if arm!=ARMS[2] or selection['kind']=='existing_MPS_selected_checkpoint':
                oldp=np.load(root/f'references/{arm}_test_probs.npy',allow_pickle=False)
                oldd=np.load(root/f'references/{arm}_test_preds.npy',allow_pickle=False)
                comparison=dict(max_probability_abs_difference=float(np.max(np.abs(oldp-probs))),changed_binary_decisions=int((oldd!=pred).sum()),
                    same_checkpoint=True,cross_backend_variation_reported=True,no_threshold_retuning_on_test=True)
                save(folder/'cross_backend_comparison.json',comparison)
            for n in range(3):predict(rows[n])
            raw=[]
            with (folder/'timing_raw.csv').open('w',newline='',encoding='utf-8') as stream:
                import csv
                writer=csv.DictWriter(stream,fieldnames=['image_id','repeat','decode_shared_preprocess_ms','model_and_head_ms','total_ms','probability_max_abs_difference','predictions_equal']);writer.writeheader()
                for repeat,n in timing_orders(len(rows),args.repeats):
                    prob,stages,_=predict(rows[n]);decision=(prob>=np.asarray(cutoffs)).astype(int)
                    diff=float(np.max(np.abs(prob-probs[n])));equal=bool(np.array_equal(decision,pred[n]))
                    record=dict(image_id=rows[n]['image_id'],repeat=repeat,**stages,probability_max_abs_difference=diff,predictions_equal=equal)
                    writer.writerow(record);stream.flush();raw.append(record)
                    if not equal or diff>1e-5:raise ValueError('Same-device repeated inference changed')
            timing={key:dict(n=len(raw),mean_ms=float(np.mean([r[key] for r in raw])),median_ms=float(np.median([r[key] for r in raw])),p95_ms=float(np.percentile([r[key] for r in raw],95)))
                    for key in ('decode_shared_preprocess_ms','model_and_head_ms','total_ms')}
            result=dict(test_metrics=score,timing=timing,load_seconds_excluded=load_seconds,model_metadata=model_metadata,memory=memory(),cross_backend_comparison=comparison)
            save(folder/'timing_summary.json',dict(**timing,warmups=3,repeats=args.repeats,batch=1,OCR_included=False,CPU_float64_frozen_LR_head=arm==ARMS[1]))
            save(folder/'shared_input_fingerprints.json',{row['image_id']:fingerprints[row['image_id']] for row in rows})
            required=['test_probs.npy','test_preds.npy','test_rows.json','test_predictions.json','test_metrics.json','timing_raw.csv','timing_summary.json','shared_input_fingerprints.json']
            save(done,dict(selection_sha256=selection_hash,result=result,file_hashes={f:sha(folder/f) for f in required}))
            save(folder/'arm_complete_commit.json',dict(sha256=sha(done)))
            results[arm]=result;log(out,'arm_complete',arm=arm,metrics=score['label_wise_accuracy'],mean_ms=timing['total_ms']['mean_ms'])
        finally:
            if model is not None:del model
            release()
    checkpoints_valid(selection,out) if selection['kind']=='dev_selected_fresh_EG2_training' else None
    if sha(out/'selection_frozen.json')!=selection_hash:raise ValueError('Selection mutated')
    save(out/'shared_RGB448_fingerprints.json',fingerprints)
    summary=dict(status='complete',results=results,selected=selection['selected'],precision=args.precision,
        test_images=37,known_cells=282,unknown_excluded=14,previously_exposed_test=True,test_used_for_selection=False,
        PaddleOCR_modified=False,OCR_in_timing=False,ResNet18_retrained=False,selection_sha256=selection_hash,
        caveat='Same exposed37 test posters. Existing ResNet budget differs. CUDA results are measured here, never copied from MPS. No90% accuracy guarantee.')
    save(out/'comparison_summary.json',summary)
    table=[]
    for arm in ARMS:
        s=results[arm]['test_metrics'];t=results[arm]['timing']['total_ms']
        table.append(dict(model=arm,accuracy=s['label_wise_accuracy'],macro_f1=s['macro_f1'],micro_f1=s['micro_f1'],macro_AP=s['macro_average_precision'],mean_ms=t['mean_ms'],p95_ms=t['p95_ms'],known_cells=282,unknown_excluded=14))
    csv_rows(out/'comparison.csv',table);log(out,'complete',run=str(out),arms=3)


def hashlib_image(im):
    import hashlib
    return hashlib.sha256(im.tobytes()).hexdigest()


def smoke(root,out,args,model_dir,td):
    import torch
    from models import EG,image,masked_bce,precision_context,representative_parameters
    rows=[x for x in td if x['split']=='train'][:max(2,args.microbatch)];seed(SEED)
    model=EG(model_dir,'cuda:0',read(root/'references/eg2_heads.json')['heads']);model.set_training(True)
    optimizer=torch.optim.AdamW(model.parameters(),lr=1e-6,foreach=False)
    known=sum(v is not None for row in rows for v in row['labels'].values());optimizer.zero_grad(set_to_none=True)
    for start in range(0,len(rows),args.microbatch):
        batch=rows[start:start+args.microbatch];ims=[image(root,row) for row in batch]
        with precision_context(args.precision,'cuda:0'):logits=model(ims,'cuda:0')
        loss,_=masked_bce(logits,torch.tensor(targets(batch),device='cuda'),known)
        if not torch.isfinite(loss):raise ValueError('Nonfinite smoke loss')
        loss.backward()
        for im in ims:im.close()
        del logits,loss,ims
    params=representative_parameters(model);grads={n:float(p.grad.float().norm().item()) for n,p in params.items()}
    if not all(v>0 and math.isfinite(v) for v in grads.values()):raise ValueError('Smoke gradient failed')
    name=next(iter(params));before=params[name].detach().clone();optimizer.step()
    changed=bool(torch.any(before!=params[name]).item())
    if not changed:raise ValueError('Smoke optimizer did not change backbone')
    save(out/'smoke_result.json',dict(status='passed',train_images=[r['image_id'] for r in rows],gradients=grads,known_cells=known,
          backbone_weight_changed=True,weights_discarded=True,test_used=False,memory=memory()))
    log(out,'smoke_complete',status='passed',weights_discarded=True)
    del params,before,model,optimizer;release()


def run(args):
    root=args.bundle.resolve(strict=True)
    info,ids,td=verify_bundle(root)
    if args.command=='validate':
        print(json.dumps(dict(status='bundle_verified',approved_images=187,split=info['split_counts'],unknown_mask=info['unknown_cells'],model_executed=False,test_labels_parsed=False),ensure_ascii=False));return
    if args.command=='download':
        model_dir=get_model_dir(root,args.model_dir,args.cache_dir)
        print(json.dumps(dict(status='download_verified',model_dir=str(model_dir),revision=REVISION)));return
    out=args.run_dir.resolve()
    if out.is_relative_to(root/'data') or out.is_relative_to(root/'references') or out==root:
        raise ValueError('Use a separate run directory')
    config=dict(command=args.command,precision=args.precision,backbone_lrs=args.backbone_lrs,max_epochs=args.max_epochs,patience=args.patience,
                effective_batch=args.effective_batch,microbatch=args.microbatch,repeats=args.repeats,model_id=MODEL_ID,revision=REVISION,
                seed=SEED,head_lr=.001,weight_decay=.0001,pos_weight='none',image448=True,max_soft_tokens=280,
                script_hashes={p.name:sha(p) for p in (HERE/'benchlib.py',HERE/'models.py',HERE/'run_benchmark.py')},bundle_manifest_sha256=sha(root/'bundle_manifest.json'))
    args.signature=digest(config)
    existed=out.exists()
    if existed and not args.resume:raise FileExistsError('Run exists; use --resume for the identical protocol, or a new --run-dir')
    out.mkdir(parents=True,exist_ok=True)
    import fcntl
    lock=(root/'.gpu_owner.lock').open('a+')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if existed:
            if read(out/'protocol.json')['signature']!=args.signature:raise ValueError('Cannot resume with changed code/config/input')
        else:save(out/'protocol.json',dict(signature=args.signature,config=config,created_at_utc=datetime.now(timezone.utc).isoformat(),PaddleOCR_modified=False,previously_exposed_test=True))
        args.active_run=True
        env=cuda_environment(args)
        if (out/'environment.json').exists():
            if read(out/'environment.json')!=env:raise ValueError('GPU/runtime changed; use a new run directory')
        else:save(out/'environment.json',env)
        log(out,'runtime_ready',GPU=env['GPU'],precision=args.precision,resume=args.resume)
        model_dir=get_model_dir(root,args.model_dir,args.cache_dir)
        log(out,'base_model_verified',revision=REVISION,model_dir=str(model_dir))
        if args.command=='smoke':smoke(root,out,args,model_dir,td);return
        selection_path=out/'selection_frozen.json'
        if selection_path.exists():
            selection=read(selection_path)
            if selection['protocol_signature']!=args.signature:raise ValueError('Frozen selection protocol changed')
            if read(out/'selection_commit.json')['sha256']!=sha(selection_path):raise ValueError('Frozen selection hash changed')
        else:
            if args.command=='train':selection=train_eg2(root,out,args,model_dir,td)
            else:
                es=read(root/'references/eg2_selection.json')
                selection=dict(kind='existing_MPS_selected_checkpoint',selected=es,candidates=[],test_used_for_selection=False,previously_exposed_test=True)
            selection['protocol_signature']=args.signature
            save(selection_path,selection)
            save(out/'selection_commit.json',dict(sha256=sha(selection_path),protocol_signature=args.signature))
            log(out,'selection_frozen_before_test',selection_sha256=sha(selection_path))
        evaluate(root,out,args,model_dir,selection,sha(selection_path))
        # Verify every immutable payload after model work as well.
        verify_bundle(root)
        save(out/'status.json',dict(status='complete',protocol_signature=args.signature,CUDA_executed=True,PaddleOCR_modified=False))
    finally:
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['validate','download','smoke','evaluate','train'])
    parser.add_argument('--bundle',type=Path,default=Path(os.environ.get('POSTER_BUNDLE_DIR',str(HERE/'assets'))))
    parser.add_argument('--run-dir',type=Path,default=Path(os.environ.get('POSTER_RUN_DIR',str(HERE/'runs')))/'cuda-fp32')
    parser.add_argument('--cache-dir',type=Path,default=Path(os.environ.get('HF_HOME','/workspace/.cache/huggingface')))
    parser.add_argument('--model-dir',type=Path,help='Already downloaded pinned snapshot; never changed in place')
    parser.add_argument('--precision',choices=['fp32','bf16'],default='fp32')
    parser.add_argument('--backbone-lrs',type=float,nargs='+',default=[1e-6,3e-6])
    parser.add_argument('--max-epochs',type=int,default=4)
    parser.add_argument('--patience',type=int,default=2)
    parser.add_argument('--effective-batch',type=int,default=8)
    parser.add_argument('--microbatch',type=int,choices=[1,2,4,8],default=1)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    args.active_run=False
    if args.max_epochs<1 or args.patience<1 or args.effective_batch<args.microbatch or args.repeats<1 or any(not math.isfinite(x) or x<=0 for x in args.backbone_lrs):parser.error('Positive settings and effective batch >= microbatch required')
    if len({format(x,'.0e') for x in args.backbone_lrs})!=len(args.backbone_lrs):parser.error('Learning rates must have distinct scientific IDs')
    try:run(args)
    except Exception as exc:
        if args.active_run and args.command in ('train','evaluate','smoke') and (args.run_dir/'protocol.json').is_file():
            save(args.run_dir/'failure.json',dict(status='failed',reason=repr(exc),traceback=traceback.format_exc(),safe_to_resume_at_epoch_boundary=True))
        print(str(exc),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
