"""Native-resolution 768-pixel poster tiles with poster-level labels.

Inputs and existing checkpoints are read-only. The entire original image is
covered, with no resize of tile content. A 384 global view is combined with the
max per-label tile logit. During fitting the only supervision is the whole
poster label; labels are never assigned to individual tiles. Inference uses all
tiles; an exact two-pass max recomputes at most eight winning tiles with grads.
"""
from pathlib import Path
import argparse, hashlib, json, math, os, random, shutil, time
import numpy as np
import torch
from PIL import Image, ImageOps
try:
    import run_v4 as base
except ModuleNotFoundError as exc:
    if exc.name != 'run_v4':
        raise
    # In the repository the identical v4 code is stored as highres_v1/run.py.
    import importlib.util
    import sys
    runner=Path(__file__).resolve().parent.parent/'highres_v1'/'run.py'
    if not runner.is_file():
        raise RuntimeError('Place the verified run_v4.py next to this script') from exc
    spec=importlib.util.spec_from_file_location('run_v4',runner)
    base=importlib.util.module_from_spec(spec);sys.modules['run_v4']=base;spec.loader.exec_module(base)

TILE=768
ALPHA=.25
EFFECTIVE_BATCH=4
BASE_RUNNER_SHA='fdf034601b43c5a632df25c79875e0334e870f3c9ffafd7bba63d452ac9a8d24'
Image.MAX_IMAGE_PIXELS=150_000_000

def origins(length,tile=TILE):
    base.require(length>0 and tile>0,'Invalid image or tile size')
    if length<=tile:return [0]
    values=list(range(0,length-tile+1,tile))
    if values[-1] != length-tile:values.append(length-tile)
    return values

def boxes(width,height,tile=TILE):
    return [(x,y,min(x+tile,width),min(y+tile,height)) for y in origins(height,tile) for x in origins(width,tile)]

def crop_native(image,box,tile=TILE):
    crop=image.crop(box).convert('RGB')
    result=Image.new('RGB',(tile,tile),(128,128,128))
    result.paste(crop,((tile-crop.width)//2,(tile-crop.height)//2));crop.close()
    return result

def normalizer(model_dir):
    config=base.read(model_dir/'preprocessor_config.json')
    mean=torch.tensor(config['image_mean'],dtype=torch.float32).view(1,3,1,1)
    std=torch.tensor(config['image_std'],dtype=torch.float32).view(1,3,1,1)
    base.require(mean.shape==(1,3,1,1) and std.shape==(1,3,1,1) and bool((std>0).all()),'Invalid normalization')
    return mean,std

def tile_input(image,box,mean,std,device):
    with crop_native(image,box) as tile:
        a=np.asarray(tile,dtype=np.float32).copy()/255.
    t=torch.from_numpy(a).permute(2,0,1).unsqueeze(0).contiguous()
    return ((t-mean)/std).to(device)

def bag_logits(global_logits,tile_count,tile_forward,alpha=ALPHA,training=False):
    """Two-pass training is exact for max-pooling, provided forwards are deterministic."""
    base.require(tile_count>0 and global_logits.shape==(1,len(base.LABELS)),'Invalid bag')
    with torch.no_grad():
        all_scores=torch.cat([tile_forward(i) for i in range(tile_count)],dim=0)
        base.require(all_scores.shape==(tile_count,len(base.LABELS)) and bool(torch.isfinite(all_scores).all()),'Invalid tile scores')
        if training:
            winners=sorted(set(torch.argmax(all_scores,dim=0).cpu().tolist()))
        else:
            maximum=all_scores.max(dim=0,keepdim=True).values
    if training:
        # Last-layer and head dropout are disabled in eval mode: both passes agree.
        selected=torch.cat([tile_forward(i) for i in winners],dim=0)
        maximum=selected.max(dim=0,keepdim=True).values
    return (1-alpha)*global_logits + alpha*maximum

def open_original(args,row):
    with Image.open(base.inside(args.packet,row['image'])) as src:
        image=ImageOps.exif_transpose(src).convert('RGB')
    base.require(image.size==(row['width'],row['height']),'Original image dimensions changed: '+row['image_id'])
    return image

def poster_logits(args,row,body,head,mean,std,device,training):
    global_x=base.images(args,[row],384,device)
    global_z=base.logits(body,head,global_x,384)
    with open_original(args,row) as image:
        regions=boxes(image.width,image.height)
        def forward(i):
            x=tile_input(image,regions[i],mean,std,device)
            return base.logits(body,head,x,TILE)
        logits=bag_logits(global_z,len(regions),forward,training=training)
    return logits,len(regions)

def inventory(args):
    _,rows,counts=base.check_packet(args.packet)
    totals={}
    for split in counts:
        subset=[r for r in rows if r['split']==split]
        n=[len(boxes(r['width'],r['height'])) for r in subset]
        totals[split]=dict(images=len(n),tiles=sum(n),max_tiles=max(n) if n else 0)
    print(json.dumps(dict(packet_sha256=base.sha(args.packet/'packet.json'),tile_size=TILE,
        native_pixels=True,entire_image_covered=True,edge_tiles_may_overlap=True,by_split=totals),ensure_ascii=False,indent=2))

def inputs(args):
    base.require(base.sha(Path(base.__file__))==BASE_RUNNER_SHA,'run_v4.py changed; use the verified runner')
    args.sizes=[384,768]
    env=base.preflight(args)
    _,rows,_=base.check_packet(args.packet);base.check_cache(args,rows)
    return env,rows

def predict_tiles(args,body,head,rows,mean,std,device='cuda'):
    body.eval();head.eval();preds=[];loss=known=tile_count=0
    with torch.inference_mode():
        for i,row in enumerate(rows,1):
            z,n_tiles=poster_logits(args,row,body,head,mean,std,device,False)
            numerator,n=base.masked_loss(z,base.target_tensor(row,device))
            loss+=float(numerator.item());known+=n;tile_count+=n_tiles
            preds.append(torch.sigmoid(z).cpu().tolist()[0])
            if i%10==0 or i==len(rows):print(f'평가 진행: {i}/{len(rows)} 포스터, {tile_count}조각',flush=True)
    return loss/known,preds,tile_count

def smoke(args):
    env,rows=inputs(args)
    row=max((r for r in rows if r['split']=='train'),key=lambda r:len(boxes(r['width'],r['height'])))
    body,head=base.load_models(args,Path(env['model']['checkpoint']));base.freeze_tail(body,head)
    mean,std=normalizer(args.model_dir);body.eval();head.eval()
    try:
        body.zero_grad(set_to_none=True);head.zero_grad(set_to_none=True);torch.cuda.reset_peak_memory_stats()
        print('점검 포스터:',row['image_id'],'조각:',len(boxes(row['width'],row['height'])),flush=True)
        z,n_tiles=poster_logits(args,row,body,head,mean,std,'cuda',True)
        loss,known=base.masked_loss(z,base.target_tensor(row,'cuda'));(loss/known).backward();torch.cuda.synchronize()
        params=[p for p in list(body.parameters())+list(head.parameters()) if p.requires_grad]
        base.require(any(p.grad is not None for p in params) and all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in params),'Invalid gradients')
        base.require(all(p.grad is None for p in body.parameters() if not p.requires_grad),'Frozen parameter received gradient')
        print(json.dumps(dict(image_id=row['image_id'],tiles=n_tiles,known_cells=known,
                              loss=float(loss.item()/known),peak_cuda_bytes=torch.cuda.max_memory_allocated(),optimizer_steps=0),ensure_ascii=False))
        print('SMOKE OK: no weights updated')
    finally:
        del body,head;torch.cuda.empty_cache()

def train_epoch(args,body,head,rows,optimizer,mean,std,epoch,device='cuda'):
    body.eval();head.eval()  # eval disables dropout, not gradients; two-pass max must match
    base.require(all(r['split']=='train' and r['use']=='active' for r in rows),'Non-training poster in optimization')
    order=list(rows);random.Random(base.SEED+epoch).shuffle(order)
    params=[p for p in list(body.parameters())+list(head.parameters()) if p.requires_grad]
    total=known_seen=tile_seen=0
    for offset in range(0,len(order),EFFECTIVE_BATCH):
        group=order[offset:offset+EFFECTIVE_BATCH]
        known=sum(v is not None for r in group for v in r['labels'].values());base.require(known>0,'Empty labels')
        optimizer.zero_grad(set_to_none=True)
        for r in group:
            z,n_tiles=poster_logits(args,r,body,head,mean,std,device,True)
            numerator,n=base.masked_loss(z,base.target_tensor(r,device))
            base.require(bool(torch.isfinite(numerator)),'Nonfinite loss')
            (numerator/known).backward();total+=float(numerator.detach().cpu());known_seen+=n;tile_seen+=n_tiles
        base.require(any(p.grad is not None for p in params) and all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in params),'Nonfinite gradients')
        base.require(all(p.grad is None for p in body.parameters() if not p.requires_grad),'Frozen parameter received gradient')
        torch.nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True);optimizer.step()
        if (offset+len(group))%20==0 or offset+len(group)==len(order):
            print(f'학습 {epoch}회: {offset+len(group)}/{len(order)} 포스터, {tile_seen}조각',flush=True)
    return dict(train_loss=total/known_seen,known_cells=known_seen,tiles=tile_seen)

def train(args):
    from safetensors.torch import save_file
    env,rows=inputs(args);base.require(not args.output.exists(),'Output exists; choose a new directory')
    base.separate_output(args.output,[args.packet,args.cache,args.model_dir,args.reference])
    base.require(1<=args.epochs<=8 and 1<=args.patience<=8,'Invalid epochs or patience')
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False;torch.backends.cudnn.benchmark=False
    random.seed(base.SEED);torch.manual_seed(base.SEED);torch.cuda.manual_seed_all(base.SEED)
    args.output.mkdir(parents=True);base.save(args.output/'environment.json',env)
    source=Path(env['model']['checkpoint']);thresholds=env['model']['thresholds']
    train_rows=[r for r in rows if r['split']=='train'];dev=[r for r in rows if r['split']=='dev']
    mean,std=normalizer(args.model_dir)
    try:
        body,head=base.load_models(args,source);base.freeze_tail(body,head)
        global_loss,global_prob=base.infer(args,body,head,dev,384)
        global_metrics=base.score(dev,global_prob,thresholds)
        best=dict(kind='baseline',epoch=0,dev_loss=global_loss,dev_metrics=global_metrics,
                  checkpoint=str(source),checkpoint_sha256=base.sha(source))
        best_key=(global_metrics['macro_f1'],-global_loss)
        tile_loss,tile_prob,n_tiles=predict_tiles(args,body,head,dev,mean,std)
        tile_metrics=base.score(dev,tile_prob,thresholds)
        history=[dict(epoch=0,dev_loss=tile_loss,dev_metrics=tile_metrics,dev_tiles=n_tiles)]
        key=(tile_metrics['macro_f1'],-tile_loss)
        if key>best_key:
            best=dict(kind='tile',epoch=0,dev_loss=tile_loss,dev_metrics=tile_metrics,
                      checkpoint=str(source),checkpoint_sha256=base.sha(source));best_key=key
        optimizer=torch.optim.AdamW([{'params':[p for p in body.parameters() if p.requires_grad],'lr':1e-6},
                                    {'params':head.parameters(),'lr':1e-4}],weight_decay=1e-4,foreach=False)
        stale=0
        for epoch in range(1,args.epochs+1):
            started=time.monotonic()
            trained=train_epoch(args,body,head,train_rows,optimizer,mean,std,epoch)
            loss,prob,n_tiles=predict_tiles(args,body,head,dev,mean,std)
            metrics=base.score(dev,prob,thresholds);key=(metrics['macro_f1'],-loss)
            history.append(dict(epoch=epoch,**trained,dev_loss=loss,dev_metrics=metrics,dev_tiles=n_tiles,
                                seconds=round(time.monotonic()-started,3)))
            print(json.dumps(dict(epoch=epoch,train_loss=trained['train_loss'],dev_loss=loss,
                                  dev_macro_f1=metrics['macro_f1'],seconds=history[-1]['seconds']),ensure_ascii=False),flush=True)
            if key>best_key:
                dest=args.output/'best_tile.safetensors';tmp=dest.with_suffix('.tmp')
                save_file(base.state(body,head),str(tmp));tmp.replace(dest)
                best=dict(kind='tile',epoch=epoch,dev_loss=loss,dev_metrics=metrics,
                          checkpoint=str(dest),checkpoint_sha256=base.sha(dest))
                best_key=key;stale=0
            else:stale+=1
            base.save(args.output/'history.json',history)
            if stale>=args.patience:break
        base.require(base.model_identity(args.model_dir,args.reference)==env['model'],'Reference model changed')
        selected=args.output/'selected.safetensors';shutil.copyfile(best['checkpoint'],selected)
        base.require(base.sha(selected)==best['checkpoint_sha256'],'Selected copy mismatch')
        selected_record={**best,'checkpoint':'selected.safetensors'}
        selection=dict(schema='poster-native-tiles-v1',packet_sha256=env['packet_sha256'],base_runner_sha256=BASE_RUNNER_SHA,
                       runner_sha256=base.sha(Path(__file__)),cache_sha256=base.sha(args.cache/'cache.json'),base=env['model'],
                       policy=dict(tile_size=768,native_pixels=True,coverage='all pixels; end tiles may overlap',global_size=384,
                                   tile_logit_weight=ALPHA,labels='poster-level only',two_pass_max=True),
                       selection_rule='dev macro F1 at existing thresholds; tie: lower dev masked BCE; baseline included',
                       test_used_for_selection=False,selected=selected_record,baseline_dev=dict(loss=global_loss,metrics=global_metrics))
        base.save(args.output/'selection.json',selection)
        base.save(args.output/'selection_commit.json',dict(sha256=base.sha(args.output/'selection.json')))
        base.save(args.output/'status.json',dict(status='selection_complete',test_scored=False,model_deployed=False))
        print('Selection frozen:',args.output/'selection.json')
    except Exception as exc:
        base.save(args.output/'status.json',dict(status='failed',error=str(exc),model_deployed=False));raise
    finally:
        torch.cuda.empty_cache()

def evaluate(args):
    env,rows=inputs(args);selection_path=args.output/'selection.json';selection=base.read(selection_path)
    base.require(base.read(args.output/'selection_commit.json')['sha256']==base.sha(selection_path),'Selection changed')
    base.require(selection['packet_sha256']==env['packet_sha256'] and selection['base']==env['model'] and
                 selection['runner_sha256']==base.sha(Path(__file__)) and selection['base_runner_sha256']==BASE_RUNNER_SHA and
                 selection['cache_sha256']==base.sha(args.cache/'cache.json') and not selection['test_used_for_selection'],'Inputs changed since selection')
    output=args.output/'evaluation.json';base.require(not output.exists(),'Evaluation already exists')
    chosen=selection['selected'];selected=base.inside(args.output,chosen['checkpoint'])
    base.require(base.sha(selected)==chosen['checkpoint_sha256'],'Selected weights changed')
    thresholds=selection['base']['thresholds'];test=[r for r in rows if r['split']=='test'];mean,std=normalizer(args.model_dir)
    results={};predictions={}
    for name,weights,mode in [('baseline',Path(env['model']['checkpoint']),'baseline'),('selected',selected,chosen['kind'])]:
        body,head=base.load_models(args,weights)
        if mode=='baseline':loss,probs=base.infer(args,body,head,test,384);tiles=0
        else:loss,probs,tiles=predict_tiles(args,body,head,test,mean,std)
        results[name]=dict(loss=loss,tiles=tiles,**base.score(test,probs,thresholds))
        predictions[name]=[dict(image_id=r['image_id'],labels=r['labels'],probabilities=p) for r,p in zip(test,probs)]
        del body,head;torch.cuda.empty_cache()
    base.save(args.output/'predictions.json',predictions)
    base.save(output,dict(selection_sha256=base.sha(selection_path),results=results,
                          qualification='Previously exposed test; AI-reviewed labels; not independent validation'))
    base.save(args.output/'status.json',dict(status='complete',test_scored=True,model_deployed=False))
    print('Evaluation saved:',output)

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['inventory','smoke','train','evaluate'])
    p.add_argument('--packet',type=Path,required=True);p.add_argument('--cache',type=Path)
    p.add_argument('--model-dir',type=Path);p.add_argument('--reference',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--epochs',type=int,default=4);p.add_argument('--patience',type=int,default=2)
    args=p.parse_args()
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['TOKENIZERS_PARALLELISM']='false'
    if args.command=='inventory':inventory(args);return
    base.require(args.cache is not None and args.model_dir is not None and args.reference is not None,'cache/model-dir/reference required')
    if args.command=='smoke':smoke(args);return
    base.require(args.output is not None,'output required')
    if args.command=='train':train(args)
    else:evaluate(args)

if __name__=='__main__':main()
