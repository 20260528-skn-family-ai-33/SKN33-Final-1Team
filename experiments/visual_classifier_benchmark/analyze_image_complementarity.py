"""Analyze saved SigLIP2/Qwen image decisions without model imports or inference."""
import argparse
from collections import Counter, defaultdict
import csv
import fcntl
import json
import math
from pathlib import Path

from qwen_zero_shot_report import metrics, parse_answer
from resultlib import LABELS, digest, inside, read, require, save, sha, write_csv

HERE = Path(__file__).resolve().parent
SIGLIP = 'siglip2_partial'
QWEN = 'qwen3_5_4b_image'
CATEGORIES = ('both_correct', 'siglip_only_correct', 'qwen_only_correct', 'both_wrong')
CELL_FIELDS = ['split','image_id','image','label','truth','category','siglip_prediction',
    'qwen_prediction','siglip_correct','qwen_correct','siglip_probability','siglip_threshold',
    'siglip_distance_to_threshold','qwen_parse_error','siglip_repeat_disagreement',
    'qwen_repeat_disagreement','siglip_first_request','qwen_first_request']


def source_evidence(root):
    status = read(root/'status.json')
    protocol = read(root/'protocol.json')
    require(status['status']=='complete' and status['protocol_signature']==protocol['signature']==
            digest(protocol['config']), 'Source comparison must be complete with a valid protocol')
    require(protocol['config']['schema']=='qwen4b-siglip-image-comparison-v1', 'Unexpected source task')
    hashes = status['artifact_hashes']
    require(all(name in hashes for name in ('protocol.json','predictions.json','comparison.csv','per_label.csv')),
            'Source evidence is incomplete')
    for name, expected in hashes.items():
        require(sha(inside(root,name))==expected, 'Source artifact changed: '+name)
    return protocol, dict(status_sha256=sha(root/'status.json'),
                          artifact_hashes=hashes, protocol_signature=protocol['signature'])


def load_predictions(root, protocol):
    rows = read(root/'predictions.json')
    groups = defaultdict(dict)
    for row in rows:
        model, split, image_id = row['model'], row['split'], row['image_id']
        require(model in (SIGLIP,QWEN) and split in ('dev','test') and
                isinstance(image_id,str) and image_id.isalnum(), 'Invalid saved image identity')
        require(image_id not in groups[(split,model)], 'Duplicate saved image row')
        require(isinstance(row['labels'],dict) and list(row['labels'])==LABELS and
                all(v is None or type(v) is int and v in (0,1) for v in row['labels'].values()),
                'Invalid saved gold labels')
        pred=row['predictions']
        require(isinstance(pred,list) and len(pred)==8 and
                all(v is None or type(v) is int and v in (0,1) for v in pred), 'Invalid predictions')
        request_name='requests/'+model+'/'+split+'/'+image_id+'-r0.json'
        request=read(inside(root,request_name))
        require(request['protocol_signature']==protocol['signature'] and request['model']==model and
                request['split']==split and request['image_id']==image_id and request['repeat']==0 and
                request['record_sha256']==digest({k:v for k,v in request.items() if k!='record_sha256'}) and
                request['predictions']==pred, 'First request does not match saved prediction')
        if model==SIGLIP:
            probabilities=row['probabilities']
            cutoffs=protocol['config']['siglip']['thresholds']
            require(len(probabilities)==len(cutoffs)==8 and all(type(v) in (int,float) and
                    math.isfinite(v) and 0<=v<=1 for v in probabilities) and
                    pred==[int(p>=t) for p,t in zip(probabilities,cutoffs)] and
                    probabilities==request['probabilities'], 'Saved SigLIP scores or threshold decisions differ')
        else:
            actual,error=parse_answer(row['raw_text'])
            require(actual==pred and error==row['parse_error'] and row['raw_text']==request['raw_text'],
                    'Saved Qwen raw response differs from parser or request')
        groups[(split,model)][image_id]=row
    require(groups, 'No saved predictions')
    splits = sorted({split for split,_ in groups})
    for split in splits:
        require(set(groups[(split,SIGLIP)])==set(groups[(split,QWEN)]) and len(groups[(split,SIGLIP)])==37,
                'Paired image population differs or is not original37: '+split)
    with (root/'comparison.csv').open(encoding='utf-8-sig',newline='') as handle:
        comparisons=list(csv.DictReader(handle))
    require(len(comparisons)==len(splits)*2 and
            len({(r['split'],r['model']) for r in comparisons})==len(comparisons), 'Invalid comparison rows')
    for row in comparisons:
        calculated=metrics(list(groups[(row['split'],row['model'])].values()))
        require(int(row['known_cells'])==calculated['known'] and
                math.isclose(float(row['accuracy']),calculated['accuracy'],rel_tol=1e-10,abs_tol=1e-10),
                'Saved comparison accuracy differs from saved predictions')
    return groups,splits


def totals(cells):
    counts=Counter(c['category'] for c in cells)
    known=sum(counts[k] for k in CATEGORIES)
    a=counts['both_correct']+counts['siglip_only_correct']
    b=counts['both_correct']+counts['qwen_only_correct']
    oracle=known-counts['both_wrong']
    return dict(known_cells=known,**{k:counts[k] for k in CATEGORIES},
        siglip_correct=a,qwen_correct=b,
        siglip_accuracy=a/known if known else None,qwen_accuracy=b/known if known else None,
        net_qwen_correct_change=b-a,
        disagreement_cells=sum(c['siglip_prediction']!=c['qwen_prediction'] for c in cells),
        qwen_unanswered_cells=sum(c['qwen_prediction'] is None for c in cells),
        oracle_correct_upper_bound=oracle,oracle_accuracy_upper_bound=oracle/known if known else None,
        oracle_gain_over_siglip=counts['qwen_only_correct'],oracle_gain_over_qwen=counts['siglip_only_correct'],
        irreducible_by_choosing_one_of_these_two=counts['both_wrong'])


def analyze(args):
    with (args.source_run/'.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        protocol,evidence=source_evidence(args.source_run)
        groups,splits=load_predictions(args.source_run,protocol)
        require(not args.out.exists(),'Use a new output directory; source results are never overwritten')
        cells,unknown,per_image,summaries,label_summaries=[],[],[],[],[]
        cutoffs=protocol['config']['siglip']['thresholds']
        for split in splits:
            split_cells=[]
            for image_id,a in groups[(split,SIGLIP)].items():
                b=groups[(split,QWEN)][image_id]
                require(a['labels']==b['labels'] and a['image']==b['image'], 'Paired gold/image differs: '+image_id)
                require(isinstance(a['image'],str), 'Missing saved image path')
                request_a='requests/'+SIGLIP+'/'+split+'/'+image_id+'-r0.json'
                request_b='requests/'+QWEN+'/'+split+'/'+image_id+'-r0.json'
                require(read(inside(args.source_run,request_a))['image_sha256']==
                        read(inside(args.source_run,request_b))['image_sha256'], 'Paired image file hash differs')
                image_cells=[]
                for j,label in enumerate(LABELS):
                    y,p,q=a['labels'][label],a['predictions'][j],b['predictions'][j]
                    sc=p==y if y is not None else None
                    qc=q==y if y is not None else None
                    category='both_correct' if sc and qc else 'siglip_only_correct' if sc else 'qwen_only_correct' if qc else 'both_wrong'
                    cell=dict(split=split,image_id=image_id,image=a['image'],label=label,truth=y,
                        category=category if y is not None else 'unknown_gold_excluded',
                        siglip_prediction=p,qwen_prediction=q,siglip_correct=sc,qwen_correct=qc,
                        siglip_probability=a['probabilities'][j],siglip_threshold=cutoffs[j],
                        siglip_distance_to_threshold=abs(a['probabilities'][j]-cutoffs[j]),
                        qwen_parse_error=b['parse_error'],siglip_repeat_disagreement=a['repeat_disagreement'],
                        qwen_repeat_disagreement=b['repeat_disagreement'],
                        siglip_first_request=request_a,qwen_first_request=request_b)
                    if y is None: unknown.append(cell)
                    else: image_cells.append(cell)
                split_cells.extend(image_cells)
                image_totals=totals(image_cells)
                per_image.append(dict(split=split,image_id=image_id,image=a['image'],
                    unknown_gold_cells=8-len(image_cells),**image_totals,
                    siglip_all_known_correct=image_totals['siglip_correct']==len(image_cells),
                    qwen_all_known_correct=image_totals['qwen_correct']==len(image_cells)))
            cells.extend(split_cells)
            summaries.append(dict(split=split,images=len(groups[(split,SIGLIP)]),
                                  unknown_gold_cells=37*8-len(split_cells),**totals(split_cells)))
            for label in LABELS:
                label_summaries.append(dict(split=split,label=label,
                    **totals([c for c in split_cells if c['label']==label])))
        require(source_evidence(args.source_run)[1]==evidence,'Source evidence changed while reading')
        args.out.mkdir(parents=True,exist_ok=False)
        write_csv(args.out/'comparison.csv',summaries)
        write_csv(args.out/'per_label.csv',label_summaries)
        write_csv(args.out/'per_image.csv',per_image)
        write_csv(args.out/'all_known_cells.csv',cells,fields=CELL_FIELDS)
        write_csv(args.out/'unknown_gold_cells.csv',unknown,fields=CELL_FIELDS)
        for category in CATEGORIES:
            write_csv(args.out/(category+'.csv'),[c for c in cells if c['category']==category],fields=CELL_FIELDS)
        save(args.out/'summary.json',dict(results=summaries,source_run=str(args.source_run),
            source_evidence=evidence,labels=LABELS,
            definitions={
                'unit':'one known image-label cell; image counts are reported separately',
                'both_correct':'both predictions equal known truth',
                'siglip_only_correct':'only SigLIP matches truth; Qwen wrong or unanswered',
                'qwen_only_correct':'only Qwen matches truth; SigLIP wrong',
                'both_wrong':'neither prediction matches truth; unanswered is incorrect',
                'unknown':'excluded from all correctness counts; retained in a separate file',
                'oracle':'gold-aware per-cell choice, an unattainable routing upper bound for these saved predictions; '
                         'not measured ensemble performance and not a deployable decision policy'},
            caveats=['Original37 test images were previously exposed; not independent generalization.',
                     'No routing rule, threshold or candidate is selected using these labels.',
                     'Rows combine first-repeat accuracy evidence only. Repeat disagreement flags are retained.',
                     'No new latency measurement, combined p95 estimate or claim of reaching 50ms.',
                     'Input labels are existing approved/unknown records, not new independent annotations.'],
            model_inference_executed=False,training_executed=False,installation_executed=False,
            latency_remeasured=False,operational_combination_evaluated=False))
        save(args.out/'source_manifest.json',dict(**evidence,
            analysis_code_sha256=sha(Path(__file__)),parser_code_sha256=sha(HERE/'qwen_zero_shot_report.py'),
            resultlib_sha256=sha(HERE/'resultlib.py')))
        files=[p for p in args.out.iterdir() if p.is_file()]
        save(args.out/'status.json',dict(status='complete',source_protocol_signature=protocol['signature'],
            artifact_hashes={p.name:sha(p) for p in files},model_inference_executed=False,
            training_executed=False,installation_executed=False))
    print(json.dumps(dict(status='complementarity_analysis_complete',out=str(args.out),
        model_inference_executed=False),ensure_ascii=False,indent=2))
    print((args.out/'comparison.csv').read_text(encoding='utf-8-sig'))
    print((args.out/'per_label.csv').read_text(encoding='utf-8-sig'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('run','show-results'))
    p.add_argument('--source-run',type=Path,default=HERE/'runs/qwen4b-image-compare-v1')
    p.add_argument('--out',type=Path,default=HERE/'reviews/qwen4b-siglip-complementarity-v1')
    args=p.parse_args()
    args.source_run=args.source_run.expanduser().resolve();args.out=args.out.expanduser().resolve()
    require(not args.out.is_relative_to(args.source_run) and not args.source_run.is_relative_to(args.out),
            'Output must be separate from source evidence')
    if args.command=='run': analyze(args)
    else:
        status=read(args.out/'status.json')
        require(status['status']=='complete' and all(sha(inside(args.out,n))==h
                for n,h in status['artifact_hashes'].items()),'Analysis output incomplete or changed')
        print(json.dumps({k:v for k,v in status.items() if k!='artifact_hashes'},ensure_ascii=False,indent=2))
        print((args.out/'comparison.csv').read_text(encoding='utf-8-sig'))
        print((args.out/'per_label.csv').read_text(encoding='utf-8-sig'))


if __name__=='__main__': main()
