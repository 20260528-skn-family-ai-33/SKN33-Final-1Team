#!/usr/bin/env python3
"""Rebuild inventory from actual synthetic files; never read private intake files.

Human approval, licenses and model revisions are not inferred from file validity.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

COLUMNS = ['asset_id', 'asset_type', 'origin_kind', 'source_method', 'collected_at',
           'path', 'sha256', 'original_group_id', 'parent_asset_id', 'format',
           'width', 'height', 'bytes', 'record_count', 'use_conditions',
           'ai_review_status', 'human_review_status', 'evidence_path', 'is_new']


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build(root):
    base = root / 'data/collection'
    evidence = base / 'evidence'
    spec = json.loads((base / 'case-specs.json').read_text(encoding='utf-8'))
    now = datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
    rows = []

    def register(asset_id, asset_type, path, group, source, collected, evidence_path,
                 parent='', is_new=True):
        path = root / path
        if not path.is_file():
            raise FileNotFoundError(path)
        row = dict.fromkeys(COLUMNS, '')
        row.update(asset_id=asset_id, asset_type=asset_type, origin_kind='synthetic',
                   source_method=source, collected_at=collected, path=str(path.relative_to(root)),
                   sha256=digest(path), original_group_id=group, parent_asset_id=parent,
                   bytes=path.stat().st_size, record_count=1,
                   use_conditions='초도 내부 개발·시연용; 공개 재배포 권리 및 사람 검토 미완료',
                   ai_review_status='file_integrity_checked; independent_review_in_evidence',
                   human_review_status='pending', evidence_path=evidence_path,
                   is_new=str(is_new).lower())
        if path.suffix.lower() == '.png':
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                row.update(format=image.format, width=image.width, height=image.height)
        elif path.suffix.lower() == '.jsonl':
            row.update(format='JSONL', record_count=len(read_jsonl(path)))
        else:
            path.read_text(encoding='utf-8')
            row['format'] = 'Markdown'
        rows.append(row)
        return row

    document_log = read_jsonl(evidence / 'document-production.jsonl')
    for item in document_log:
        row = register(item['document_id'], 'document', item['path'], item['group_id'],
                       item['authoring_method'], item['generated_at'],
                       'data/collection/evidence/document-production.jsonl')
        if item['sha256'] != row['sha256']:
            raise ValueError('Document provenance hash mismatch: ' + item['document_id'])

    requests_path = base / 'raw/synthetic/requests/requests.jsonl'
    requests = read_jsonl(requests_path)
    request_groups = {item['request_group_id'] for item in requests}
    register('SYN-REQUESTS-01', 'requests', str(requests_path.relative_to(root)),
             f'MULTI-REQUEST-GROUPS-{len(request_groups)}',
             'AI-authored situations and paraphrases; deterministic Python rendering',
             min(item['created_at'] for item in requests),
             'data/collection/evidence/request-production.jsonl')

    image_log = read_jsonl(evidence / 'image-production.jsonl')
    for item in image_log:
        row = register(item['asset_id'], 'image', item['path'], item['original_group_id'],
                       'built-in image_gen ' + item['operation'] + '; exact model revision not exposed',
                       item['generated_at'], 'data/collection/evidence/image-production.jsonl',
                       item.get('parent_asset_id') or '')
        if item['sha256'] != row['sha256']:
            raise ValueError('Image provenance hash mismatch: ' + item['asset_id'])
    for index, path in enumerate(sorted((root / 'data/samples').glob('sample-*.png')), 1):
        register(f'LEGACY-AD-{index:02}', 'image', str(path.relative_to(root)),
                 'LEGACY-MORNING-CUP-01',
                 'README.txt: built-in image_gen, 2026-10-02; exact time and model revision unconfirmed',
                 '2026-10-02', 'data/samples/README.txt',
                 parent='' if index == 1 else 'LEGACY-AD-01', is_new=False)

    with (base / 'inventory.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    new = [row for row in rows if row['is_new'] == 'true']
    images = [row for row in new if row['asset_type'] == 'image']
    docs = [row for row in new if row['asset_type'] == 'document']
    old_images = [row for row in rows if row['asset_type'] == 'image' and row['is_new'] == 'false']
    bytes_by_type = collections.Counter()
    for row in new:
        bytes_by_type[row['asset_type']] += row['bytes']
    summary = {
        'schema_version': '1.0', 'as_of_date': '2026-10-06', 'generated_at': now,
        'submission_date': '2026-10-08', 'team_material_status': 'not_provided_unconfirmed',
        'team_intake_cutoff': '2026-10-07T12:00:00+09:00', 'team_intake_closed': False,
        'new_documents': len(docs), 'new_requests': len(requests), 'request_groups': len(request_groups),
        'new_images': len(images), 'new_image_groups': len({row['original_group_id'] for row in images}),
        'existing_images': len(old_images), 'existing_image_groups': len({row['original_group_id'] for row in old_images}),
        'image_dimensions': ', '.join(f'{w}×{h}' for w, h in sorted({(row['width'], row['height']) for row in images + old_images})),
        'new_raw_files': len(new), 'existing_raw_files': len(rows) - len(new), 'total_raw_files': len(rows),
        'human_approved_count': 0, 'human_approved_high_quality_count': 0,
        'by_type_bytes': dict(bytes_by_type), 'legacy_image_bytes': sum(row['bytes'] for row in old_images),
        'new_image_unique_hashes': len({row['sha256'] for row in images}),
        'legacy_image_unique_hashes': len({row['sha256'] for row in old_images}),
        'all_image_unique_hashes': len({row['sha256'] for row in images + old_images}),
        'requests_normalized_unique': len({' '.join(item['text'].split()).casefold() for item in requests}),
        'targets': spec['targets'], 'new_image_generation_roots': sum(item['operation'] == 'generate' for item in image_log),
        'new_image_edit_derivatives': sum(item['operation'] == 'edit' for item in image_log),
        'inventory_sha256': digest(base / 'inventory.csv'),
        'counting_rules': ['Markdown 원문마다 1건; 파싱본·인쇄본은 파생본이며 신규 문서가 아니다.',
                           'Requests: one JSONL file, 250 utterances, 50 semantic groups.',
                           'Images: files and original groups reported separately; runtime copies excluded.',
                           'Team material not provided does not establish that no team material exists.'],
        'training_executed': False, 'performance_evaluation_executed': False,
        'public_upload_executed': False, 'license_review_status': 'not_reviewed',
    }
    # Grouped labels are evidence only; the raw text dataset has no answer keys.
    label_rows = read_jsonl(evidence / 'request-labels.jsonl')
    summary['request_intent_counts'] = dict(collections.Counter(item['intent_label'] for item in label_rows))
    write_json(base / 'summary.json', summary)
    write_json(evidence / 'intake-status.json', {
        'recorded_at': now, 'actual_status': 'not_provided_unconfirmed',
        'planned_cutoff': summary['team_intake_cutoff'], 'intake_closed': False,
        'fallback_action_completed': 'synthetic introductory corpus produced',
        'private_intake_location': '.runtime/collection-team-input/',
        'human_review_status': 'pending', 'external_sharing_executed': False,
        'next_update': 'After team input is provided, review rights/privacy, register eligible copies and rebuild report.'})
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[3])
    build(parser.parse_args().root.resolve())
