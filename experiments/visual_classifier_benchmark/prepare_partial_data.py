"""Human review and immutable training packets. No model inference or installation."""
from pathlib import Path
from html import escape
import argparse
import csv
import hashlib
import json
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'poster_benchmark'))
from benchlib import LABELS, contained, digest, read, save, sha, verify_bundle

NEW_FIELDS = ['image_path', 'group_id', 'source_id', 'split', *LABELS, 'approval', 'reviewer', 'reason']
CORRECTION_FIELDS = ['image_id', 'group_id', 'image', *['original_' + k for k in LABELS],
                     *['new_' + k for k in LABELS], 'approval', 'reviewer', 'reason']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def base_data(bundle):
    bundle = Path(bundle)
    require(read(bundle / 'bundle_manifest.json') == read(HERE.parent / 'poster_benchmark/bundle_manifest.json'),
            'Use the unchanged approved base bundle')
    return verify_bundle(bundle)


def csv_write(path, fields, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def csv_read(path, fields):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == fields, 'CSV header/order differs: ' + str(path))
        rows = list(reader)
    require(all(None not in r and all(isinstance(v, str) for v in r.values()) for r in rows), 'Unexpected/missing CSV columns')
    return rows


def approved(row):
    decision = row['approval'].strip()
    require(decision in ('', 'UNREVIEWED', 'REJECTED', 'APPROVED'), 'Unknown approval value')
    if decision == 'APPROVED':
        require(bool(row['reviewer'].strip()) and bool(row['reason'].strip()), 'APPROVED needs reviewer and reason')
        return True
    return False


def cell(value, allow_keep=False):
    value = value.strip()
    if value in ('0', '1'):
        return int(value)
    if value == '?':
        return None
    if value == '' and not allow_keep:
        return None
    raise ValueError('Label must be 0, 1, ?, or blank')


def corrections(rows, originals):
    mapping = {r['image_id']: r for r in originals if r['split'] == 'train'}
    result, audit, seen = {}, [], set()
    for row in rows:
        image_id = row['image_id'].strip()
        require(image_id in mapping and image_id not in seen, 'Correction must reference one unique TRAIN image: ' + image_id)
        seen.add(image_id); original = mapping[image_id]
        require(row['group_id'] == original['group_id'] and row['image'] == original['image'], 'Original identity changed')
        for key in LABELS:
            expected = '?' if original['labels'][key] is None else str(original['labels'][key])
            require(row['original_' + key] == expected, 'Original label changed in review CSV')
        updates = {k: cell(row['new_' + k], True) for k in LABELS if row['new_' + k].strip()}
        accepted = approved(row)
        require(not updates or accepted or row['approval'].strip() == 'REJECTED', 'Unapproved correction cannot enter training')
        if not accepted:
            continue
        labels = dict(original['labels']); labels.update(updates)
        require(any(v is not None for v in labels.values()), 'A training image must have a known label')
        changed = {k: dict(before=original['labels'][k], after=labels[k]) for k in LABELS if labels[k] != original['labels'][k]}
        result[image_id] = labels
        audit.append(dict(image_id=image_id, reviewer=row['reviewer'], reason=row['reason'], changes=changed))
    return result, audit


def fingerprint(path):
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        rgb = ImageOps.exif_transpose(source).convert('RGB')
        require(rgb.width * rgb.height <= 40_000_000, 'Image exceeds 40M pixels')
        h = hashlib.sha256(f'{rgb.width}x{rgb.height}:RGB:'.encode() + rgb.tobytes()).hexdigest()
        gray = list(rgb.resize((9, 8), Image.Resampling.LANCZOS).convert('L').tobytes())
        dhash = sum(int(gray[y * 9 + x] > gray[y * 9 + x + 1]) << (y * 8 + x) for y in range(8) for x in range(8))
        thumb = tuple(rgb.resize((16, 16), Image.Resampling.BILINEAR).tobytes())
    return dict(file_sha256=sha(path), rgb_sha256=h, dhash=dhash, thumbnail=thumb)


def is_duplicate(a, b):
    if a['file_sha256'] == b['file_sha256'] or a['rgb_sha256'] == b['rgb_sha256']:
        return True
    return (a['dhash'] ^ b['dhash']).bit_count() <= 2 and sum(abs(x - y) for x, y in zip(a['thumbnail'], b['thumbnail'])) / len(a['thumbnail']) <= 6


def make_review(bundle, output, prior_run=None, loader=base_data):
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    require(not output.exists() and not output.is_relative_to(bundle), 'Choose a new review directory outside the base bundle')
    info, ids, rows = loader(bundle)
    probabilities, cutoffs = None, None
    if prior_run is not None:
        from resultlib import load_npy
        prior_run = Path(prior_run).resolve()
        require(not output.is_relative_to(prior_run), 'Review output must be outside completed runs')
        selected = read(prior_run / 'selection_frozen.json')
        status = read(prior_run / 'status.json'); protocol = read(prior_run / 'protocol.json')
        require(status['status'] == 'complete' and status['protocol_signature'] == protocol['signature'] == selected['protocol_signature'] == digest(protocol['config']), 'Previous run must be complete and unchanged')
        require(protocol['config']['bundle_sha256'] == sha(bundle / 'bundle_manifest.json'), 'Previous run used different data')
        require(read(prior_run / 'selection_commit.json')['sha256'] == sha(prior_run / 'selection_frozen.json'), 'Previous selection changed')
        folder = contained(prior_run, selected['checkpoint']).parent
        require(sha(folder / 'dev_probabilities.npy') == selected['artifact_hashes']['dev_probabilities.npy'], 'Previous dev probabilities changed')
        probabilities, cutoffs = load_npy(folder / 'dev_probabilities.npy'), selected['thresholds']
        require(len(cutoffs) == len(LABELS) and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in cutoffs), 'Invalid dev thresholds')
    output.mkdir(parents=True)
    review_rows = []
    for row in rows:
        if row['split'] == 'train':
            review_rows.append(dict(image_id=row['image_id'], group_id=row['group_id'], image=row['image'],
                **{'original_' + k: '?' if row['labels'][k] is None else row['labels'][k] for k in LABELS},
                **{'new_' + k: '' for k in LABELS}, approval='UNREVIEWED', reviewer='', reason=''))
    csv_write(output / 'train_corrections.csv', CORRECTION_FIELDS, review_rows)
    csv_write(output / 'additional_labels.csv', NEW_FIELDS, [])
    from PIL import Image, ImageOps
    dev_index, cards = 0, {'train': [], 'dev': []}
    diagnostic = []
    for row in rows:
        image_id = row['image_id']
        name = digest(image_id)[:20] + '.jpg'
        target = output / 'previews' / name; target.parent.mkdir(exist_ok=True)
        with Image.open(contained(bundle, row['image'])) as source:
            im = ImageOps.exif_transpose(source).convert('RGB'); im.thumbnail((480, 640)); im.save(target, quality=88)
        errors = []
        if row['split'] == 'dev' and probabilities is not None:
            require(dev_index < len(probabilities), 'Dev probability population differs')
            for j, key in enumerate(LABELS):
                y = row['labels'][key]; p = probabilities[dev_index][j]
                if y is not None and int(p >= cutoffs[j]) != y:
                    error = 'FN' if y else 'FP'; errors.append(key + ':' + error)
                    diagnostic.append(dict(image_id=image_id, label=key, truth=y, probability=p, threshold=cutoffs[j], error=error, split='dev'))
            dev_index += 1
        text = ', '.join(k + '=' + ('?' if v is None else str(v)) for k, v in row['labels'].items())
        cards[row['split']].append('<article><h2>' + escape(image_id) + '</h2><img src="previews/' + name + '" alt="' + escape(image_id) + '"><p>' + escape(text) + '</p><p>' + escape(' / '.join(errors)) + '</p></article>')
    if probabilities is not None:
        require(dev_index == len(probabilities), 'Dev probability population differs')
    css = 'body{font:15px/1.65 system-ui;margin:24px;color:#203d34;background:#f4f3ee}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}article{background:white;padding:16px;border:1px solid #dbded3;border-radius:12px}img{width:100%;height:340px;object-fit:contain}p{overflow-wrap:anywhere;font-size:12px}'
    for split in ('train', 'dev'):
        message = '학습 이미지 검토 · 승인 수정은 train_corrections.csv에 기록' if split == 'train' else '개발 진단 전용 · 학습용 라벨 수정/추가 대상이 아님'
        note = '<p>저장된 개발 예측이 없어 현재 정답 라벨만 표시합니다. 오류 여부는 판정하지 않았습니다.</p>' if split == 'dev' and probabilities is None else ''
        html = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + message + '</title><style>' + css + '</style><h1>' + message + '</h1><p>0=없음, 1=있음, ?=모름. 모델의 오답 표시는 검토 단서이며 정답 라벨을 자동 변경하지 않습니다.</p>' + note + '<div class="grid">' + ''.join(cards[split]) + '</div></html>'
        (output / (split + '_review.html')).write_text(html, encoding='utf-8')
    csv_write(output / 'dev_diagnostics.csv', ('image_id', 'label', 'truth', 'probability', 'threshold', 'error', 'split'), diagnostic)
    save(output / 'review_context.json', dict(base_manifest_sha256=sha(bundle / 'bundle_manifest.json'),
        labels=LABELS, train_images=len(review_rows), dev_images=sum(r['split'] == 'dev' for r in rows), test_images_displayed=0,
        dev_predictions_loaded=probabilities is not None, base_label_qualification=info.get('approval_qualification', 'Existing approved labels retained'),
        counts={split: {k: {str(value): sum(r['split'] == split and r['labels'][k] == value for r in rows) for value in (0, 1, None)} for k in LABELS} for split in ('train', 'dev')}))
    return dict(review_dir=str(output), train_corrections=str(output / 'train_corrections.csv'), additional_labels=str(output / 'additional_labels.csv'))


def build_packet(bundle, review_dir, output, loader=base_data):
    bundle, review_dir, output = (Path(p).resolve() for p in (bundle, review_dir, output))
    require(not output.exists() and not output.is_relative_to(bundle) and not output.is_relative_to(review_dir), 'Choose a separate new packet directory')
    _, ids, originals = loader(bundle)
    base_hash = sha(bundle / 'bundle_manifest.json')
    require(read(review_dir / 'review_context.json')['base_manifest_sha256'] == base_hash, 'Review belongs to a different base bundle')
    reviewed, audit = corrections(csv_read(review_dir / 'train_corrections.csv', CORRECTION_FIELDS), originals)
    new_rows = csv_read(review_dir / 'additional_labels.csv', NEW_FIELDS)
    mapping = {r['image_id']: r for r in ids}
    protected_groups = {r['group_id'].casefold() for r in ids if r['split'] != 'train'}
    manifest = read(bundle / 'bundle_manifest.json')
    # Held-out files are used only for duplicate fingerprints, never labels/features.
    fingerprints = []
    for relative in manifest['files']:
        if relative.startswith('data/images/'):
            image_id = Path(relative).stem
            require(image_id in mapping, 'Image filename does not match split metadata')
            fingerprints.append((image_id, fingerprint(contained(bundle, relative))))
    require(len(fingerprints) == len(ids), 'Duplicate-check image population differs')
    additions, added_audit, source_groups = [], [], {}
    input_hashes = {name: sha(review_dir / name) for name in ('train_corrections.csv', 'additional_labels.csv', 'review_context.json')}
    for number, row in enumerate(new_rows, 1):
        if not any(v.strip() for v in row.values()):
            continue
        if row['approval'].strip() == 'REJECTED':
            continue
        require(approved(row), f'Additional row {number} needs explicit APPROVED review')
        require(row['split'].strip() == 'train', 'Additional images may enter TRAIN only')
        group, source_id = row['group_id'].strip(), row['source_id'].strip()
        require(group and source_id and group.casefold() not in protected_groups, 'Missing source/group or protected dev/test group')
        require(source_id not in source_groups, 'Duplicate source_id; use distinct original posters')
        source_groups[source_id] = group
        path = Path(row['image_path']).expanduser()
        path = path if path.is_absolute() else review_dir / path
        require(path.is_file() and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'), 'Missing/unsupported additional image')
        labels = {k: cell(row[k]) for k in LABELS}
        require(any(v is not None for v in labels.values()), 'Additional image needs a known label')
        fp = fingerprint(path)
        duplicate = next((image_id for image_id, old in fingerprints if is_duplicate(fp, old)), None)
        require(duplicate is None, 'Exact/near duplicate of protected or existing image: ' + str(duplicate))
        image_id = f'A{number:05d}'
        fingerprints.append((image_id, fp))
        additions.append(dict(image_id=image_id, group_id=group, split='train', labels=labels, image=f'images/added/{image_id}{path.suffix.lower()}', _source=path))
        added_audit.append(dict(image_id=image_id, source_id=source_id, reviewer=row['reviewer'], reason=row['reason'], group_id=group,
                                labels=labels, review_image_path=row['image_path'], file_sha256=fp['file_sha256'], rgb_sha256=fp['rgb_sha256']))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.partial-packet-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'packet'; stage.mkdir()
        rows = []
        for original in originals:
            name = 'images/base/' + Path(original['image']).name
            target = stage / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(contained(bundle, original['image']), target)
            require(sha(target) == manifest['files'][original['image']]['sha256'], 'Base image changed during copy')
            rows.append(dict(**{k: v for k, v in original.items() if k not in ('image', 'labels')}, image=name,
                             labels=reviewed.get(original['image_id'], original['labels'])))
        for row in additions:
            source = row['_source']; target = stage / row['image']; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            require(sha(target) == next(a['file_sha256'] for a in added_audit if a['image_id'] == row['image_id']), 'Additional image changed during copy')
            rows.append({k: v for k, v in row.items() if k != '_source'})
        save(stage / 'train_dev.json', rows)
        save(stage / 'approvals.json', dict(corrections=audit, additions=added_audit))
        for name in input_hashes:
            shutil.copyfile(review_dir / name, stage / name)
            require(sha(stage / name) == input_hashes[name] == sha(review_dir / name), 'Review changed during packet creation')
        changed = sum(len(a['changes']) for a in audit)
        save(stage / 'packet_manifest.json', dict(schema='approved-partial-training-v1', base_manifest_sha256=base_hash,
            labels=LABELS, added_images=len(additions), changed_train_cells=changed, test_in_packet=False,
            known_cells={s: sum(v is not None for r in rows if r['split'] == s for v in r['labels'].values()) for s in ('train', 'dev')},
            files={p.relative_to(stage).as_posix(): dict(bytes=p.stat().st_size, sha256=sha(p)) for p in stage.rglob('*') if p.is_file()}))
        validate_packet(stage, bundle, loader)
        stage.rename(output)
    return dict(packet=str(output), added_images=len(additions), changed_train_cells=changed, training_started=False)


def validate_packet(packet, bundle, loader=base_data):
    packet, bundle = Path(packet), Path(bundle)
    _, ids, original = loader(bundle)
    manifest = read(packet / 'packet_manifest.json')
    require(manifest['schema'] == 'approved-partial-training-v1' and manifest['labels'] == LABELS and manifest['test_in_packet'] is False,
            'Unknown packet schema')
    require(manifest['base_manifest_sha256'] == sha(bundle / 'bundle_manifest.json'), 'Base bundle differs')
    require(not any(p.is_symlink() for p in packet.rglob('*')), 'Packet symlinks are not allowed')
    files = {p.relative_to(packet).as_posix() for p in packet.rglob('*') if p.is_file()}
    require(files == set(manifest['files']) | {'packet_manifest.json'}, 'Missing/unexpected packet files')
    for name, expected in manifest['files'].items():
        p = contained(packet, name)
        require(not p.is_symlink() and p.stat().st_size == expected['bytes'] and sha(p) == expected['sha256'], 'Packet artifact changed: ' + name)
    rows = read(packet / 'train_dev.json')
    base = {r['image_id']: r for r in original}
    require(len({r['image_id'] for r in rows}) == len(rows), 'Duplicate training IDs')
    require({r['image_id'] for r in rows if r['image_id'] in base} == set(base), 'Base row population changed')
    protected = {r['group_id'].casefold() for r in ids if r['split'] != 'train'}
    reviewed, audit = corrections(csv_read(packet / 'train_corrections.csv', CORRECTION_FIELDS), original)
    require(read(packet / 'approvals.json')['corrections'] == audit, 'Correction approval records differ')
    approved_extra = {a['image_id']: a for a in read(packet / 'approvals.json')['additions']}
    csv_extra = {}
    for number, row in enumerate(csv_read(packet / 'additional_labels.csv', NEW_FIELDS), 1):
        if not any(v.strip() for v in row.values()) or row['approval'].strip() == 'REJECTED':
            continue
        require(approved(row) and row['split'].strip() == 'train', 'Unapproved additional CSV entry')
        csv_extra[f'A{number:05d}'] = dict(labels={k: cell(row[k]) for k in LABELS}, source_id=row['source_id'].strip(),
                                          group_id=row['group_id'].strip(), reviewer=row['reviewer'], reason=row['reason'], review_image_path=row['image_path'])
    require(set(csv_extra) == set(approved_extra), 'Additional approval population differs')
    require(all(all(approved_extra[k][field] == value for field, value in expected.items()) for k, expected in csv_extra.items()), 'Additional approval metadata differs')
    require(sum(len(a['changes']) for a in audit) == manifest['changed_train_cells'], 'Correction count differs')
    for row in rows:
        require(list(row['labels']) == LABELS and any(v is not None for v in row['labels'].values()) and
                all(type(v) in (int, type(None)) and v in (0, 1, None) for v in row['labels'].values()), 'Invalid labels')
        require(contained(packet, row['image']).is_file(), 'Training image missing')
        if row['image_id'] in base:
            before = base[row['image_id']]
            require(row['split'] == before['split'] and row['group_id'] == before['group_id'], 'Base group/split changed')
            require(row['labels'] == reviewed.get(row['image_id'], before['labels']), 'Unapproved base label change')
            require(sha(contained(packet, row['image'])) == sha(contained(bundle, before['image'])), 'Base image content changed')
        else:
            require(row['split'] == 'train' and row['group_id'].casefold() not in protected and row['image_id'] in approved_extra, 'Unapproved/protected additional row')
            require(row['labels'] == approved_extra[row['image_id']]['labels'] and row['group_id'] == approved_extra[row['image_id']]['group_id'], 'Unapproved additional label/group change')
            require(sha(contained(packet, row['image'])) == approved_extra[row['image_id']]['file_sha256'], 'Additional image content changed')
    require(sum(r['image_id'] not in base for r in rows) == manifest['added_images'], 'Additional image count differs')
    require({s: sum(v is not None for r in rows if r['split'] == s for v in r['labels'].values()) for s in ('train', 'dev')} == manifest['known_cells'], 'Known-cell population differs')
    return manifest, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('review', 'build-data'))
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--prior-run', type=Path, default=HERE / 'runs/siglip-full-fp32')
    parser.add_argument('--review-dir', type=Path, default=HERE / 'reviews/partial-v1')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'review':
        result = make_review(args.bundle, args.out or args.review_dir, args.prior_run)
    else:
        require(args.out is not None, 'build-data requires --out NEW_PACKET_DIRECTORY')
        result = build_packet(args.bundle, args.review_dir, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
