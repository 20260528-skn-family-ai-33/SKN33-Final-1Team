"""Build a separate, hash-pinned dataset; never edit the source images or labels."""
from pathlib import Path
import argparse, csv, hashlib, json, random, shutil

LABELS=['person','food_drink','animal','plant_flower','vehicle','building_structure','musical_instrument','fireworks']
HERE=Path(__file__).resolve().parent

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()

def n(*ids): return ['NEW%03d'%x for x in ids]
# Event/year variants and visual templates are grouped before random splitting.
GROUPS=[n(1,17),n(11,50),n(15,80),n(18,29),n(19,75,136),n(20,38,49),
        n(25,157),n(26,144),n(32,97),n(37,105),n(45,107,118),n(48,112),
        n(57,64),n(61,62),n(68,82,141),n(70,111),n(74,156),n(79,132),
        n(86,137),n(91,117),n(148,149,150,151,152,153)]
FORCED={'NEW018':'train','NEW030':'train','NEW045':'dev','NEW074':'test','NEW077':'train'}

def grouped_splits(rows):
    ids={r['image_id'] for r in rows}
    groups={r['image_id']:r['image_id'] for r in rows}
    for items in GROUPS:
        if not set(items)<=ids: raise ValueError('Missing group image: '+str(items))
        for name in items: groups[name]=items[0]
    candidates=[r for r in rows if r['image_id'].startswith('NEW') and r['image_id']!='NEW017']
    members={}
    for r in candidates: members.setdefault(groups[r['image_id']],[]).append(r)
    forced={groups[k]:v for k,v in FORCED.items()}
    total=[sum(r['labels'][key]==1 for r in candidates) for key in LABELS]
    ratios={'train':.7,'dev':.15,'test':.15}
    keys=sorted(members)
    rng=random.Random(20261027)
    best=None
    # Lock groups before fitting; stratification uses labels only, never model predictions.
    for _ in range(5000):
        assignment={g:forced.get(g) or rng.choices(['train','dev','test'],[.70,.15,.15])[0] for g in keys}
        counts={s:0 for s in ratios}; pos={s:[0]*len(LABELS) for s in ratios}
        for g in keys:
            s=assignment[g];counts[s]+=len(members[g])
            for r in members[g]:
                for i,key in enumerate(LABELS): pos[s][i]+=r['labels'][key]==1
        score=sum(((counts[s]-len(candidates)*ratios[s])/len(candidates))**2*20 for s in ratios)
        score+=sum(((pos[s][i]-total[i]*ratios[s])/max(total[i],1))**2 for s in ratios for i in range(len(LABELS)))
        score+=sum(2 for s in ('dev','test') for i in range(len(LABELS)) if total[i]>=3 and pos[s][i]==0)
        if best is None or score<best[0]: best=(score,assignment)
    assignment=best[1]
    splits={r['image_id']:assignment[groups[r['image_id']]] for r in candidates}
    splits['NEW017']=splits['NEW001']
    return groups,splits

def build(source, destination, notes):
    source=source.resolve();destination=destination.resolve()
    if destination.exists(): raise FileExistsError('Destination exists; choose a fresh folder: '+str(destination))
    if destination.is_relative_to(source) or source.is_relative_to(destination): raise ValueError('Source and output must be separate')
    manifest=list(csv.DictReader((source/'manifest.csv').open(encoding='utf-8-sig',newline='')))
    parsed={}
    for line in notes.read_text(encoding='utf-8').splitlines():
        image_id,bits,evidence=line.split('\t',2)
        if image_id in parsed or len(bits)!=8 or any(x not in '01?' for x in bits): raise ValueError('Bad review row: '+image_id)
        parsed[image_id]=(bits,evidence)
    if len(manifest)!=187 or len(parsed)!=187 or {x['image_id'] for x in manifest}!=set(parsed): raise ValueError('Expected exactly 187 reviewed rows')
    rows=[]
    for r in manifest:
        path=(source/r['image']).resolve()
        if not path.is_relative_to(source) or not path.is_file() or sha(path)!=r['sha256']: raise ValueError('Source hash/path mismatch: '+r['image_id'])
        bits,evidence=parsed[r['image_id']]
        labels={name:(None if bit=='?' else int(bit)) for name,bit in zip(LABELS,bits)}
        rows.append(dict(image_id=r['image_id'],image=r['image'],sha256=r['sha256'],width=int(r['width']),height=int(r['height']),
                         kind=r['kind'],title=r['title'],source_page=r['source_page'],license=r['license'],labels=labels,
                         visual_evidence=evidence,review_status='AI_REVIEWED_NOT_HUMAN_APPROVED'))
    groups,new_splits=grouped_splits(rows)
    old_splits={'기존_train':'train','기존_dev':'legacy_dev','기존_test':'legacy_test'}
    src_by_id={r['image_id']:r for r in manifest}
    for r in rows:
        raw=src_by_id[r['image_id']]
        r['group_id']=groups[r['image_id']]
        r['split']=new_splits[r['image_id']] if r['image_id'].startswith('NEW') else old_splits[raw['split']]
        r['use']= 'duplicate_excluded' if r['image_id']=='NEW017' else 'active'
        r['duplicate_of']='NEW001' if r['image_id']=='NEW017' else ''
        r['known_old_duplicate']='P112/train' if r['image_id']=='NEW018' else (r['image_id']+'/'+raw['split'] if r['image_id'].startswith('P') else '')
    destination.mkdir(parents=True)
    try:
        (destination/'images').mkdir()
        for r in rows: shutil.copy2(source/r['image'],destination/r['image'])
        for r in rows:
            if sha(destination/r['image'])!=r['sha256']: raise ValueError('Copy mismatch: '+r['image_id'])
        packet=dict(schema='highres-poster-review-v1',seed=20261027,labels=LABELS,reviewer='Codex AI visual review',
                    review_qualification='Not independently human-approved; unknown values are masked',
                    independence='Unverified relative to all historical data; provisional grouped holdout',
                    license_status='UNVERIFIED',near_duplicate_method='pHash and dHash screening + manual event groups; not proof of independence',
                    rows=rows)
        (destination/'packet.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        with (destination/'labels.csv').open('w',encoding='utf-8-sig',newline='') as f:
            fields=['image_id','image','width','height','kind','title','source_page','license','group_id','split','use','duplicate_of','known_old_duplicate',*LABELS,'visual_evidence','review_status','sha256']
            w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
            for r in rows:
                w.writerow({**r,**{k:'?' if v is None else v for k,v in r['labels'].items()}})
        shutil.copy2(source/'manifest.csv',destination/'source_manifest.csv')
        shutil.copy2(source/'README.md',destination/'source_README.md')
    except Exception:
        # Keep partial output for inspection; it will not be accepted as a complete packet.
        raise
    counts={s:sum(r['split']==s and r['use']=='active' for r in rows) for s in ('train','dev','test','legacy_dev','legacy_test')}
    support={s:{k:sum(r['split']==s and r['use']=='active' and r['labels'][k]==1 for r in rows) for k in LABELS} for s in counts}
    summary=dict(images=len(rows),active=sum(counts.values()),counts=counts,positive_support=support,unknown=sum(v is None for r in rows for v in r['labels'].values()),
                 duplicate_excluded=['NEW017'],known_existing_train=['NEW018/P112'])
    (destination/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--notes',type=Path,default=HERE/'review_notes.tsv')
    a=p.parse_args();build(a.source,a.output,a.notes)
