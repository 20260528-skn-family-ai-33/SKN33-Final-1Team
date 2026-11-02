"""Prepare an additive review directory from an existing packet; no training or inference."""
import argparse
import csv
import json
import shutil
import tempfile
from pathlib import Path

import prepare_partial_data as partial
from resultlib import LABELS, read, require, sha, write_csv

HERE=Path(__file__).resolve().parent


def load_inputs(args):
    # Validate every prior packet artifact, original corrections and additions.
    manifest, rows=partial.validate_packet(args.prior_packet,args.bundle)
    require(manifest['added_images']==349 and manifest['changed_train_cells']==32,
            'Expected the existing 349 additions and 32 corrections; choose the correct prior packet')
    old=partial.csv_read(args.prior_packet/'additional_labels.csv',partial.NEW_FIELDS)
    old_by_image={r['image_id']:r for r in rows if r['image_id'].startswith('A')}
    approved={a['image_id']:a for a in read(args.prior_packet/'approvals.json')['additions']}
    require(len(approved)==349 and set(approved)==set(old_by_image),'Existing addition evidence differs')
    reconstructed=[]
    for number,entry in enumerate(old,1):
        if entry['approval'].strip()!='APPROVED':
            reconstructed.append(entry);continue
        image_id=f'A{number:05d}'
        require(image_id in approved and image_id in old_by_image,'Previous addition identity changed')
        file=partial.contained(args.prior_packet,old_by_image[image_id]['image'])
        require(sha(file)==approved[image_id]['file_sha256'],'Previous addition image changed')
        copy=dict(entry);copy['image_path']=str(file.resolve());reconstructed.append(copy)
    audit=read(args.overlap_report)
    require(audit.get('status')=='lineage_inventory_complete' and not audit.get('incomplete') and
            audit.get('candidates')==10 and audit.get('exact_match_candidates')==0 and
            audit.get('near_review_candidates')==0 and not audit.get('issues'),
            'RunPod candidate overlap report is incomplete or has matches')
    candidate_manifest=read(args.candidates/'candidate_manifest.json')
    entries=candidate_manifest['records']
    require(len(entries)==10 and len({r['id'] for r in entries})==10 and
            {r['image_id'] for r in audit['details']}=={r['id'] for r in entries},
            'Candidate identities differ from overlap report')
    # Repeat the candidate-to-holdout check on actual current files. This is only a
    # duplicate screen; it does not read gold labels or prove event independence.
    protected=[]
    for folder in args.eval_roots:
        require(folder.is_dir(),'Evaluation candidate directory missing: '+str(folder))
        for image in sorted(folder.iterdir()):
            if image.is_file() and not image.is_symlink() and image.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
                protected.append((str(image),partial.fingerprint(image)))
    require(len(protected)>=38,'Expected both evaluation candidate folders and their 38 images')
    generated=[]; candidate_ids=set(); existing_ids={r['source_id'] for r in reconstructed}
    for r in entries:
        require(r.get('status')=='downloaded_for_review' and r.get('review_status')=='AI_DRAFT',
                'Candidate review state changed')
        image=partial.contained(args.candidates,r['image'])
        require(image.is_file() and sha(image)==r['sha256'],'Candidate image changed')
        fp=partial.fingerprint(image)
        match=next((name for name,ref in protected if partial.is_duplicate(fp,ref)),None)
        require(match is None,'Candidate duplicates an evaluation candidate: '+str(match))
        values=r['labels']
        require(list(values)==LABELS and all(str(values[k]) in ('0','1','?') for k in LABELS),
                'Invalid candidate labels')
        source_id='ai-poster-candidate-20261027:'+r['id']
        group='ai-poster-candidate-20261027-'+r['id']
        require(source_id not in existing_ids and source_id not in candidate_ids,'Source identifier collision')
        candidate_ids.add(source_id)
        generated.append(dict(image_path=str(image.resolve()),group_id=group,source_id=source_id,split='train',
            **{k:str(values[k]) for k in LABELS},approval='APPROVED',reviewer='Codex AI (user delegated label review)',
            reason=r['evidence']))
    return dict(manifest=manifest,rows=rows,old=reconstructed,new=generated,protected_count=len(protected),
                prior_sha=sha(args.prior_packet/'packet_manifest.json'),
                candidate_sha=sha(args.candidates/'candidate_manifest.json'),
                audit_sha=sha(args.overlap_report))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('preflight','prepare'))
    p.add_argument('--prior-packet',type=Path,default=HERE/'training_packets/siglip-poster-v2')
    p.add_argument('--bundle',type=Path,default=HERE.parent/'poster_benchmark/assets')
    p.add_argument('--candidates',type=Path,default=Path('/workspace/siglip-train-candidates-r1-2026-10-27'))
    p.add_argument('--overlap-report',type=Path,default=HERE/'reviews/train-candidates-overlap-v2/report.json')
    p.add_argument('--eval-roots',nargs='+',type=Path,default=[
        Path('/workspace/poster-eval-ai-reviewed-v1-2026-10-27/images'),
        Path('/workspace/poster-eval-round3-screened-2026-10-27/images')])
    p.add_argument('--out',type=Path,default=HERE/'reviews/siglip-poster-plus10-v1')
    args=p.parse_args()
    for name in ('prior_packet','bundle','candidates','overlap_report','out'):
        setattr(args,name,getattr(args,name).expanduser().resolve())
    args.eval_roots=[x.expanduser().resolve() for x in args.eval_roots]
    require(all(not args.out.is_relative_to(p) and not p.is_relative_to(args.out)
            for p in (args.prior_packet,args.bundle,args.candidates,*args.eval_roots)),
            'Output must be separate from all source inputs')
    info=load_inputs(args)
    summary=dict(status='preflight_ready' if args.command=='preflight' else 'review_prepared',
        previous_added_images=info['manifest']['added_images'],previous_changed_train_cells=info['manifest']['changed_train_cells'],
        previous_train_images=sum(r['split']=='train' for r in info['rows']),
        previous_dev_images=sum(r['split']=='dev' for r in info['rows']),
        new_candidate_images=len(info['new']),new_candidates_approval='AI_APPROVED_LABELS_ONLY',
        evaluated_candidate_files_checked=info['protected_count'],previous_packet_manifest_sha256=info['prior_sha'],
        candidates_manifest_sha256=info['candidate_sha'],overlap_report_sha256=info['audit_sha'],
        model_inference_executed=False,training_executed=False,installation_executed=False,
        image_rights_verified=False,independent_holdout_verified=False,
        note='This creates only an additive review directory. New labels are AI-approved at user request; '
             'no model training or output packet is created. AI review is not human review; '
             'image reuse rights and event/design family overlap remain unverified.')
    if args.command=='prepare':
        require(not args.out.exists(),'Use a new review path; no existing data will be overwritten')
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.poster-review-',dir=args.out.parent) as tmp:
            stage=Path(tmp)/'review';stage.mkdir()
            for name in ('train_corrections.csv','review_context.json'):
                shutil.copyfile(args.prior_packet/name,stage/name)
                require(sha(stage/name)==sha(args.prior_packet/name),'Prior review file changed')
            partial.csv_write(stage/'additional_labels.csv',partial.NEW_FIELDS,info['old']+info['new'])
            (stage/'preservation_report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            stage.rename(args.out)
        summary['review_dir']=str(args.out)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
