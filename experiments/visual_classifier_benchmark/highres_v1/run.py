"""Offline SigLIP2 partial tuning for an immutable high-resolution poster packet.

All output goes to new cache/run directories. Test labels are scored only after
selection.json is frozen. No dependency installation, download, or deployment.
"""
from pathlib import Path
import argparse, hashlib, inspect, json, math, os, random, shutil, sys, time

LABELS=['person','food_drink','animal','plant_flower','vehicle','building_structure','musical_instrument','fireworks']
SEED=20261027

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()

def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def save(path,obj):
    path=Path(path); tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8');tmp.replace(path)
def require(cond,message):
    if not cond: raise ValueError(message)
def inside(root,relative):
    p=(root/relative).resolve();require(p.is_relative_to(root.resolve()),'Path escapes root: '+str(relative));return p

def check_packet(root):
    packet=read(root/'packet.json');rows=packet['rows']
    require(packet['schema']=='highres-poster-review-v1' and packet['labels']==LABELS,'Packet schema mismatch')
    require(len(rows)==187 and len({r['image_id'] for r in rows})==187,'Packet must contain 187 unique IDs')
    groups={}; active=[]
    for r in rows:
        require(r['split'] in ('train','dev','test','legacy_dev','legacy_test'),'Invalid split')
        require(r['use'] in ('active','duplicate_excluded'),'Invalid use')
        require(list(r['labels'])==LABELS and all(type(v) in (int,type(None)) and v in (0,1,None) for v in r['labels'].values()),'Invalid labels')
        require(groups.setdefault(r['group_id'],r['split'])==r['split'],'Cross-split event group: '+r['group_id'])
        path=inside(root,r['image']);require(path.is_file() and sha(path)==r['sha256'],'Image hash mismatch: '+r['image_id'])
        if r['use']=='active':active.append(r)
    counts={s:sum(r['split']==s for r in active) for s in ('train','dev','test','legacy_dev','legacy_test')}
    require(counts=={'train':130,'dev':26,'test':26,'legacy_dev':3,'legacy_test':1},'Split counts changed')
    by_id={r['image_id']:r for r in rows}
    require(by_id['NEW017']['use']=='duplicate_excluded' and by_id['NEW017']['duplicate_of']=='NEW001','Duplicate handling changed')
    require(by_id['NEW018']['split']=='train' and by_id['NEW029']['split']=='train','Old training duplicate escaped train')
    for r in rows:
        if r['image_id'].startswith('P'):
            expected=('legacy_test' if r['image_id']=='P130' else 'legacy_dev' if r['image_id'] in ('P001','P018','P066') else 'train')
            require(r['split']==expected,'Legacy split changed: '+r['image_id'])
    return packet,active,counts

def model_identity(model_dir,reference):
    selection_path=reference/'selection_frozen.json';selected=read(selection_path)
    protocol=read(reference/'protocol.json'); status=read(reference/'status.json');commit=read(reference/'selection_commit.json')
    digest=hashlib.sha256(json.dumps(protocol['config'],sort_keys=True,allow_nan=False).encode()).hexdigest()
    require(status['status']=='complete' and status['protocol_signature']==selected['protocol_signature']==protocol['signature']==digest,'Reference protocol/status mismatch')
    require(commit['sha256']==sha(selection_path),'Reference selection commit mismatch')
    checkpoint=inside(reference,selected['checkpoint'])
    require(checkpoint.is_file() and selected['artifact_hashes'].get(checkpoint.name)==sha(checkpoint),'Reference checkpoint missing or mismatched')
    require('tail4-lr1e-06' in str(checkpoint),'Reference is not the preserved tail4 checkpoint')
    recorded=protocol['config']['model_packet']['files']
    for name in ('config.json','model.safetensors','preprocessor_config.json'):
        path=model_dir/name; require(path.is_file() and path.stat().st_size==recorded[name]['bytes'] and sha(path)==recorded[name]['sha256'],'Base model mismatch: '+name)
    config=read(model_dir/'config.json');require(config.get('model_type')=='siglip' and config['vision_config']['image_size']==384,'Expected fixed SigLIP 384 base')
    thresholds=selected['thresholds'];require(len(thresholds)==8 and all(type(x) in (int,float) and math.isfinite(x) and 0<=x<=1 for x in thresholds),'Invalid thresholds')
    return dict(checkpoint=str(checkpoint),checkpoint_sha256=sha(checkpoint),selection_sha256=sha(selection_path),
                model_files={n:recorded[n]['sha256'] for n in ('config.json','model.safetensors','preprocessor_config.json')},thresholds=thresholds)

def supports_interpolation(module):
    # Transformers 4.x exposes SiglipVisionTransformer; 5.x uses SiglipVisionModel.
    for name in ('SiglipVisionTransformer','SiglipVisionModel'):
        cls=getattr(module,name,None)
        if cls is not None and 'interpolate_pos_encoding' in inspect.signature(cls.forward).parameters:
            return True
    return False

def preflight(args):
    packet,rows,counts=check_packet(args.packet)
    import torch, transformers, safetensors, PIL, numpy
    if 768 in args.sizes:
        from transformers.models.siglip import modeling_siglip
        require(supports_interpolation(modeling_siglip),'Installed Transformers does not expose position interpolation; stop or use --sizes 384')
    require(torch.cuda.is_available(),'CUDA unavailable; no installation attempted')
    identity=model_identity(args.model_dir,args.reference)
    return dict(counts=counts,packet_sha256=sha(args.packet/'packet.json'),runner_sha256=sha(Path(__file__)),model=identity,device=torch.cuda.get_device_name(0),
                versions=dict(torch=torch.__version__,transformers=transformers.__version__,safetensors=safetensors.__version__,Pillow=PIL.__version__,numpy=numpy.__version__))

def separate_output(destination, protected):
    dest=destination.resolve()
    for root in protected:
        if root is None: continue
        root=root.resolve()
        require(not dest.is_relative_to(root) and not root.is_relative_to(dest),'Output overlaps a protected input: '+str(root))

def make_cache(args):
    from PIL import Image,ImageOps
    Image.MAX_IMAGE_PIXELS=150_000_000
    _,rows,_=check_packet(args.packet)
    require(not args.cache.exists(),'Cache already exists; use a fresh directory')
    separate_output(args.cache,[args.packet])
    args.cache.mkdir(parents=True);records={}
    for size in (384,768): (args.cache/str(size)).mkdir()
    for i,r in enumerate(rows,1):
        with Image.open(inside(args.packet,r['image'])) as src:
            rgb=ImageOps.exif_transpose(src).convert('RGB')
            for size in (384,768):
                scale=min(size/rgb.width,size/rgb.height)
                shape=(max(1,round(rgb.width*scale)),max(1,round(rgb.height*scale)))
                canvas=Image.new('RGB',(size,size),(128,128,128))
                reduced=rgb.resize(shape,Image.Resampling.BICUBIC)
                canvas.paste(reduced,((size-shape[0])//2,(size-shape[1])//2));reduced.close()
                rel=f'{size}/{r["image_id"]}.png';path=inside(args.cache,rel);canvas.save(path);canvas.close()
                records[rel]=dict(sha256=sha(path),source_sha256=r['sha256'])
            rgb.close()
        if i%20==0 or i==len(rows): print(f'preprocessed {i}/{len(rows)}',flush=True)
    save(args.cache/'cache.json',dict(schema='highres-cache-v1',packet_sha256=sha(args.packet/'packet.json'),
                                     preprocessing='EXIF transpose, RGB, BICUBIC aspect-preserving letterbox on RGB(128,128,128), PNG',files=records))
    print('Cache ready:',args.cache)

def check_cache(args,rows):
    meta=read(args.cache/'cache.json');require(meta['schema']=='highres-cache-v1' and meta['packet_sha256']==sha(args.packet/'packet.json'),'Cache does not match packet')
    require(len(meta['files'])==len(rows)*2,'Incomplete cache')
    for r in rows:
        for size in (384,768):
            name=f'{size}/{r["image_id"]}.png';p=inside(args.cache,name); rec=meta['files'][name]
            require(p.is_file() and rec['source_sha256']==r['sha256'] and sha(p)==rec['sha256'],'Cache mismatch: '+name)
    return meta

def score(rows,probs,thresholds):
    require(len(rows)==len(probs),'Prediction count mismatch')
    per=[];correct=known=0
    for i,key in enumerate(LABELS):
        tp=tn=fp=fn=0
        for r,p in zip(rows,probs):
            y=r['labels'][key]
            if y is None: continue
            require(len(p)==8 and all(math.isfinite(float(x)) and 0<=float(x)<=1 for x in p),'Invalid probability')
            pred=float(p[i])>=thresholds[i]
            tp+=int(y==1 and pred);tn+=int(y==0 and not pred);fp+=int(y==0 and pred);fn+=int(y==1 and not pred)
        n=tp+tn+fp+fn;den=2*tp+fp+fn
        per.append(dict(label=key,known=n,positive=tp+fn,negative=tn+fp,tp=tp,tn=tn,fp=fp,fn=fn,f1=(2*tp/den if den else None)))
        known+=n;correct+=tp+tn
    valid=[x['f1'] for x in per if x['f1'] is not None]
    return dict(known_cells=known,accuracy=correct/known if known else None,macro_f1=sum(valid)/len(valid) if valid else None,per_label=per,
                note='AI-reviewed provisional labels; per-label support is small; None means undefined')

def load_models(args,weights,device='cuda'):
    import torch
    from torch import nn
    from transformers import AutoModel
    from safetensors.torch import load_file
    full,loading=AutoModel.from_pretrained(str(args.model_dir),local_files_only=True,trust_remote_code=False,use_safetensors=True,
                                          dtype=torch.float32,attn_implementation='sdpa',output_loading_info=True)
    for k in ('missing_keys','unexpected_keys','mismatched_keys'):
        require(not loading.get(k),'Model load mismatch: '+k+' '+str(loading.get(k)))
    # Config files may omit default hidden_size. Use the resolved model config,
    # matching the existing benchmark's load_encoder implementation.
    dimension=full.config.vision_config.hidden_size
    require(type(dimension) is int and dimension>0,'Invalid resolved vision dimension')
    backbone=full.vision_model.to(device); del full
    head=nn.Linear(dimension,8).to(device); state=load_file(str(weights),device='cpu')
    backbone.load_state_dict({k[9:]:v for k,v in state.items() if k.startswith('backbone.')},strict=True)
    head.load_state_dict({k[11:]:v for k,v in state.items() if k.startswith('classifier.')},strict=True)
    return backbone,head

def images(args,rows,size,device):
    import numpy as np, torch
    from PIL import Image
    config=read(args.model_dir/'preprocessor_config.json')
    mean=torch.tensor(config['image_mean'],dtype=torch.float32).view(1,3,1,1)
    std=torch.tensor(config['image_std'],dtype=torch.float32).view(1,3,1,1)
    batch=[]
    for r in rows:
        with Image.open(inside(args.cache,f'{size}/{r["image_id"]}.png')) as im:
            a=np.asarray(im.convert('RGB'),dtype=np.float32).copy()/255.0
        batch.append(torch.from_numpy(a).permute(2,0,1).unsqueeze(0))
    return ((torch.cat(batch)-mean)/std).to(device)

def logits(backbone,head,x,size):
    from torch.nn import functional as F
    output=backbone(x,interpolate_pos_encoding=(size!=384)).pooler_output
    return head(F.normalize(output.float(),p=2,dim=-1))

def target_tensor(row,device):
    import torch
    # Fully known labels otherwise infer LongTensor, which BCE cannot accept.
    return torch.tensor([[float('nan') if row['labels'][k] is None else row['labels'][k] for k in LABELS]],
                        device=device,dtype=torch.float32)

def masked_loss(logit,y):
    import torch
    from torch.nn import functional as F
    mask=torch.isfinite(y);safe=torch.nan_to_num(y,nan=0.0)
    require(bool(mask.any()),'No known labels in batch')
    return (F.binary_cross_entropy_with_logits(logit.float(),safe,reduction='none')*mask).sum(),int(mask.sum().item())

def infer(args,backbone,head,rows,size,device='cuda'):
    import torch
    backbone.eval();head.eval();probs=[];total=0.;known=0
    with torch.inference_mode():
        for r in rows:
            x=images(args,[r],size,device); z=logits(backbone,head,x,size)
            y=target_tensor(r,device)
            loss,n=masked_loss(z,y);total+=float(loss.item());known+=n
            probs.append(torch.sigmoid(z).cpu().tolist()[0])
    return total/known,probs

def state(backbone,head):
    return {**{'backbone.'+k:v.detach().cpu().contiguous().clone() for k,v in backbone.state_dict().items()},
            **{'classifier.'+k:v.detach().cpu().contiguous().clone() for k,v in head.state_dict().items()}}

def freeze_tail(backbone,head):
    backbone.requires_grad_(False);layers=list(backbone.encoder.layers)
    require(len(layers)>=4 and hasattr(backbone,'head'),'Unexpected vision tower architecture')
    for layer in layers[-4:]:layer.requires_grad_(True)
    backbone.post_layernorm.requires_grad_(True);backbone.head.requires_grad_(True);head.requires_grad_(True)

def train_epoch(args,backbone,head,rows,size,optimizer,epoch,device='cuda'):
    import torch
    backbone.eval();head.train()
    for layer in list(backbone.encoder.layers)[-4:]:layer.train()
    backbone.post_layernorm.train();backbone.head.train()
    order=list(rows);random.Random(SEED+epoch).shuffle(order);total=0.;count=0
    require(all(r['split']=='train' and r['use']=='active' for r in rows),'Non-training row in optimization')
    params=[p for p in list(backbone.parameters())+list(head.parameters()) if p.requires_grad]
    for start in range(0,len(order),8):
        group=order[start:start+8];known=sum(v is not None for r in group for v in r['labels'].values());require(known>0,'No known labels')
        optimizer.zero_grad(set_to_none=True)
        for r in group:
            x=images(args,[r],size,device)
            y=target_tensor(r,device)
            numerator,n=masked_loss(logits(backbone,head,x,size),y)
            require(bool(torch.isfinite(numerator)),'Non-finite loss')
            (numerator/known).backward();total+=float(numerator.detach().cpu());count+=n
        require(any(p.grad is not None for p in params) and all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in params),'Missing or nonfinite trainable gradients')
        require(all(p.grad is None for p in backbone.parameters() if not p.requires_grad),'Frozen parameter received gradient')
        torch.nn.utils.clip_grad_norm_(params,1.0,error_if_nonfinite=True);optimizer.step()
    return total/count

def smoke(args,device='cuda'):
    """One forward/backward per size, without optimizer steps or file writes."""
    import torch
    env=preflight(args);_,rows,_=check_packet(args.packet);check_cache(args,rows)
    row=next(r for r in rows if r['split']=='train' and r['use']=='active')
    backbone,head=load_models(args,Path(env['model']['checkpoint']),device=device);freeze_tail(backbone,head)
    try:
        for size in args.sizes:
            backbone.eval();head.train()
            for layer in list(backbone.encoder.layers)[-4:]:layer.train()
            backbone.post_layernorm.train();backbone.head.train()
            backbone.zero_grad(set_to_none=True);head.zero_grad(set_to_none=True)
            torch.cuda.reset_peak_memory_stats()
            x=images(args,[row],size,device)
            y=target_tensor(row,device)
            z=logits(backbone,head,x,size)
            require(z.shape==(1,8) and bool(torch.isfinite(z).all()),'Invalid smoke logits')
            loss,n=masked_loss(z,y);(loss/n).backward();torch.cuda.synchronize()
            params=[p for p in list(backbone.parameters())+list(head.parameters()) if p.requires_grad]
            require(any(p.grad is not None for p in params) and all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in params),'Invalid smoke gradients')
            require(all(p.grad is None for p in backbone.parameters() if not p.requires_grad),'Frozen parameter received gradient')
            print(json.dumps(dict(size=size,known_cells=n,loss=float(loss.item()/n),peak_cuda_bytes=torch.cuda.max_memory_allocated(),optimizer_steps=0)),flush=True)
            del x,y,z,loss;torch.cuda.empty_cache()
        print('SMOKE OK: no weights updated')
    finally:
        del backbone,head;torch.cuda.empty_cache()


def train(args):
    import torch
    from safetensors.torch import save_file
    env=preflight(args);_,rows,_=check_packet(args.packet);check_cache(args,rows)
    require(not args.output.exists(),'Output exists; use a fresh run directory')
    separate_output(args.output,[args.packet,args.cache,args.model_dir,args.reference])
    require(args.epochs>0 and args.patience>0,'Epochs and patience must be positive')
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False;torch.backends.cudnn.benchmark=False
    args.output.mkdir(parents=True)
    save(args.output/'environment.json',env)
    train_rows=[r for r in rows if r['split']=='train'];dev=[r for r in rows if r['split']=='dev']
    identity=env['model'];base=Path(identity['checkpoint']);results=[]
    try:
        for size in args.sizes:
            random.seed(SEED);torch.manual_seed(SEED);torch.cuda.manual_seed_all(SEED)
            backbone,head=load_models(args,base);freeze_tail(backbone,head)
            body=[p for p in backbone.parameters() if p.requires_grad]
            optimizer=torch.optim.AdamW([{'params':body,'lr':1e-6},{'params':head.parameters(),'lr':1e-4}],weight_decay=1e-4,foreach=False)
            best,probs=infer(args,backbone,head,dev,size)
            candidate=dict(size=size,epoch=0,dev_loss=best,checkpoint=str(base),trained=False,dev_metrics=score(dev,probs,identity['thresholds']))
            history=[dict(epoch=0,dev_loss=best)];stale=0
            for epoch in range(1,args.epochs+1):
                started=time.monotonic();loss=train_epoch(args,backbone,head,train_rows,size,optimizer,epoch)
                val,probs=infer(args,backbone,head,dev,size)
                history.append(dict(epoch=epoch,train_loss=loss,dev_loss=val,seconds=round(time.monotonic()-started,3)))
                print(json.dumps(dict(size=size,**history[-1]),ensure_ascii=False),flush=True)
                if val<best-1e-7:
                    best=val;stale=0;dest=args.output/f'candidate-{size}.safetensors'
                    temp=dest.with_suffix('.tmp');save_file(state(backbone,head),str(temp));temp.replace(dest)
                    candidate=dict(size=size,epoch=epoch,dev_loss=val,checkpoint=str(dest),trained=True,dev_metrics=score(dev,probs,identity['thresholds']))
                else: stale+=1
                save(args.output/f'history-{size}.json',history)
                if stale>=args.patience:break
            candidate['checkpoint_sha256']=sha(candidate['checkpoint']);results.append(candidate)
            del backbone,head,optimizer;torch.cuda.empty_cache()
        require(model_identity(args.model_dir,args.reference)==identity,'Reference or base model changed during training')
        # Fixed thresholds, lowest development BCE. Epoch 0 allows unchanged weights.
        chosen=min(results,key=lambda c:(c['dev_loss'],c['size']))
        selected_path=args.output/'selected.safetensors';shutil.copyfile(chosen['checkpoint'],selected_path)
        require(sha(selected_path)==chosen['checkpoint_sha256'],'Selected copy hash mismatch')
        chosen={**chosen,'checkpoint':'selected.safetensors'}
        selection=dict(schema='highres-siglip-selection-v1',packet_sha256=env['packet_sha256'],runner_sha256=env['runner_sha256'],cache_sha256=sha(args.cache/'cache.json'),
                       base=identity,selection_rule='minimum dev masked BCE; lower input size breaks ties; thresholds frozen from baseline',
                       test_used_for_selection=False,selected=chosen,candidates=results)
        save(args.output/'selection.json',selection)
        save(args.output/'selection_commit.json',dict(sha256=sha(args.output/'selection.json')))
        save(args.output/'status.json',dict(status='selection_complete',test_scored=False,model_deployed=False))
        print('Selection frozen:',args.output/'selection.json')
    except Exception as e:
        save(args.output/'status.json',dict(status='failed',error=str(e),model_deployed=False));raise

def evaluate(args):
    import torch
    env=preflight(args);_,rows,_=check_packet(args.packet);check_cache(args,rows)
    selection_path=args.output/'selection.json';selection=read(selection_path);commit=read(args.output/'selection_commit.json')
    require(commit['sha256']==sha(selection_path) and selection['test_used_for_selection'] is False,'Selection is not frozen')
    require(selection['packet_sha256']==env['packet_sha256'] and selection['cache_sha256']==sha(args.cache/'cache.json'),'Inputs changed since selection')
    require(selection['base']==env['model'] and selection['runner_sha256']==env['runner_sha256'],'Base model or runner changed')
    chosen=selection['selected'];require(chosen['size'] in args.sizes,'Selected input size not enabled for evaluation')
    selected=inside(args.output,chosen['checkpoint'])
    require(sha(selected)==chosen['checkpoint_sha256'],'Selected checkpoint changed')
    result_path=args.output/'evaluation.json';require(not result_path.exists(),'Evaluation already exists; preserve previous result')
    results={};predictions={}
    for name,weights,size in [('baseline',Path(env['model']['checkpoint']),384),('selected',selected,chosen['size'])]:
        backbone,head=load_models(args,weights);results[name]={};predictions[name]={}
        for split in ('test','legacy_dev','legacy_test'):
            subset=[r for r in rows if r['split']==split]
            loss,probs=infer(args,backbone,head,subset,size)
            results[name][split]=dict(loss=loss,**score(subset,probs,selection['base']['thresholds']))
            predictions[name][split]=[dict(image_id=r['image_id'],probabilities=p,labels=r['labels']) for r,p in zip(subset,probs)]
        del backbone,head;torch.cuda.empty_cache()
    save(args.output/'predictions.json',predictions)
    save(result_path,dict(selection_sha256=sha(selection_path),input_resolution={'baseline':384,'selected':chosen['size']},
                          results=results,qualification='AI labels; new 26 test posters are provisional and may be historically exposed; old holdouts exposed'))
    save(args.output/'status.json',dict(status='complete',test_scored=True,model_deployed=False))
    print('Evaluation saved:',result_path)

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['verify','preflight','cache','smoke','train','evaluate'])
    p.add_argument('--packet',required=True,type=Path);p.add_argument('--cache',type=Path)
    p.add_argument('--model-dir',type=Path);p.add_argument('--reference',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--epochs',type=int,default=8);p.add_argument('--patience',type=int,default=3)
    p.add_argument('--sizes',type=int,nargs='+',choices=(384,768),default=[384,768])
    args=p.parse_args();require(len(set(args.sizes))==len(args.sizes),'Duplicate input size')
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['TOKENIZERS_PARALLELISM']='false'
    if args.command=='verify':
        _,_,counts=check_packet(args.packet);print(json.dumps(dict(counts=counts,packet_sha256=sha(args.packet/'packet.json')),ensure_ascii=False,indent=2));return
    if args.command=='cache': require(args.cache is not None,'--cache required');make_cache(args);return
    require(args.model_dir is not None and args.reference is not None,'--model-dir and --reference required')
    if args.command=='preflight':print(json.dumps(preflight(args),ensure_ascii=False,indent=2));return
    if args.command=='smoke':
        require(args.cache is not None,'--cache required');smoke(args);return
    require(args.cache is not None and args.output is not None,'--cache and --output required')
    if args.command=='train':train(args)
    else:evaluate(args)

if __name__=='__main__':main()
