"""Download an instrument review pool from Open Images using only the standard library.

No packages, models, training packets, existing labels, or experiment runs are changed.
Image provenance and box annotations are retained; no label is automatically approved.
"""
import argparse
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError
from urllib.request import Request, urlopen


CLASSES_URL = 'https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions-boxable.csv'
BOXES_URL = 'https://storage.googleapis.com/openimages/v6/oidv6-train-annotations-bbox.csv'
METADATA_URL = 'https://storage.googleapis.com/openimages/2018_04/train/train-images-boxable-with-rotation.csv'
IMAGE_ROOT = 'https://open-images-dataset.s3.amazonaws.com/train/'
DEFAULT_CLASSES = ['Guitar', 'Piano', 'Drum', 'Violin', 'Cello', 'Flute', 'Saxophone', 'Trumpet']
INSTRUMENT_CLASSES = [*DEFAULT_CLASSES, 'Accordion', 'Harp', 'Harpsichord',
                      'Musical keyboard', 'Organ (Musical Instrument)', 'Trombone', 'Musical instrument']
LABELS = ['person', 'food_drink', 'animal', 'plant_flower', 'vehicle',
          'building_structure', 'musical_instrument', 'fireworks']
NEW_FIELDS = ['image_path', 'group_id', 'source_id', 'split', *LABELS,
              'approval', 'reviewer', 'reason']
DEFAULT_OUT = Path('/workspace/datasets/openimages-instruments-v1')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def log(event, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=False), flush=True)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path, value):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def write_csv(path, fields, rows):
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def fetch(url, destination, retries, timeout, max_bytes=None, progress=False, jpeg=False):
    """Reuse checked files, resume partial HTTP transfers, and replace only complete files."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = destination.with_name(destination.name + '.download.json')
    part = destination.with_name(destination.name + '.part')
    part_state = part.with_name(part.name + '.json')
    if destination.exists():
        require(receipt_path.exists(), 'Untracked file exists; choose a new output/cache: ' + str(destination))
        receipt = read_json(receipt_path)
        require(receipt['url'] == url, 'Download source changed: ' + str(destination))
        if destination.stat().st_size == receipt['bytes'] and sha256(destination) == receipt['sha256']:
            with destination.open('rb') as stream:
                if not jpeg or stream.read(3) == b'\xff\xd8\xff':
                    return receipt
    for attempt in range(retries + 1):
        try:
            state = read_json(part_state) if part_state.exists() else {}
            offset = part.stat().st_size if part.exists() and state.get('url') == url else 0
            headers = {'User-Agent': 'CreativeChecker-OpenImages/1.0', 'Accept-Encoding': 'identity'}
            if offset:
                headers['Range'] = 'bytes=' + str(offset) + '-'
                if state.get('validator'):
                    headers['If-Range'] = state['validator']
            with urlopen(Request(url, headers=headers), timeout=timeout) as response:
                if response.status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    require(match and int(match[1]) == offset, 'Unexpected HTTP range: ' + url)
                    total = int(match[3])
                else:
                    require(response.status == 200, 'Unexpected HTTP status: ' + str(response.status))
                    offset = 0
                    total = int(response.headers.get('Content-Length', '0'))
                require(not max_bytes or not total or total <= max_bytes, 'File exceeds size limit: ' + url)
                save_json(part_state, dict(url=url, validator=response.headers.get('ETag') or
                                          response.headers.get('Last-Modified', '')))
                size, next_progress = offset, offset + 64 * 1024 * 1024
                with part.open('ab' if offset else 'wb') as stream:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        require(not max_bytes or size <= max_bytes, 'File exceeds size limit: ' + url)
                        stream.write(block)
                        if progress and size >= next_progress:
                            log('metadata_download', file=destination.name, bytes=size, total_bytes=total)
                            next_progress = size + 64 * 1024 * 1024
                require(size > 0 and (not total or size == total), 'Incomplete download: ' + url)
            if jpeg:
                with part.open('rb') as stream:
                    valid_header = stream.read(3) == b'\xff\xd8\xff'
                if not valid_header:
                    part.unlink(missing_ok=True)
                    part_state.unlink(missing_ok=True)
                    raise ValueError('Expected JPEG image: ' + url)
            receipt = dict(url=url, bytes=size, sha256=sha256(part))
            # Write receipt first so an interruption after rename can recover safely.
            save_json(receipt_path, receipt)
            os.replace(part, destination)
            part_state.unlink(missing_ok=True)
            return receipt
        except (OSError, ValueError) as exc:
            if isinstance(exc, HTTPError) and exc.code == 416:
                part.unlink(missing_ok=True)
                part_state.unlink(missing_ok=True)
            if attempt == retries:
                raise
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError('Download retries exhausted')


def class_names(cache, args):
    path = cache / 'oidv7-class-descriptions-boxable.csv'
    fetch(CLASSES_URL, path, args.retries, args.timeout, max_bytes=2 * 1024 * 1024)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return {name: mid for mid, name in csv.reader(stream)}


def make_selection(box_file, classes, per_class, seed):
    by_mid = {mid: name for name, mid in classes.items()}
    candidates, boxes = {name: set() for name in classes}, {}
    with box_file.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'ImageID', 'LabelName', 'Confidence', 'XMin', 'XMax', 'YMin', 'YMax',
                    'IsDepiction', 'IsOccluded', 'IsTruncated', 'IsGroupOf'}
        require(required <= set(reader.fieldnames or []), 'Open Images box CSV schema differs')
        for count, row in enumerate(reader, 1):
            if count % 1000000 == 0:
                log('annotation_scan', rows=count)
            name = by_mid.get(row['LabelName'])
            if name is None or float(row['Confidence']) != 1 or row['IsGroupOf'] == '1':
                continue
            image_id = row['ImageID']
            require(re.fullmatch('[0-9a-f]{16}', image_id), 'Unexpected ImageID')
            bounds = {key: float(row[key]) for key in ('XMin', 'XMax', 'YMin', 'YMax')}
            require(0 <= bounds['XMin'] < bounds['XMax'] <= 1 and
                    0 <= bounds['YMin'] < bounds['YMax'] <= 1, 'Invalid box: ' + image_id)
            candidates[name].add(image_id)
            boxes.setdefault(image_id, []).append(dict(class_name=name, label_mid=row['LabelName'],
                **bounds, **{key: row[key] for key in ('IsDepiction', 'IsOccluded', 'IsTruncated', 'IsGroupOf')}))
    selected, used = [], set()
    # Rarer classes get first choice; IDs are reproducibly sampled and never selected twice.
    for name in sorted(classes, key=lambda value: (len(candidates[value]), value)):
        ranked = sorted(candidates[name], key=lambda value:
                        hashlib.sha256(f'{seed}/{name}/{value}'.encode()).hexdigest())
        chosen = [value for value in ranked if value not in used][:per_class]
        require(len(chosen) == per_class,
                f'Only {len(chosen)} unique {name} images available; lower --per-class in a new --out')
        for image_id in chosen:
            used.add(image_id)
            selected.append(dict(image_id=image_id, primary_class=name, boxes=boxes[image_id]))
    return selected


def selected_metadata(path, ids):
    result = {}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'ImageID', 'Subset', 'OriginalURL', 'OriginalLandingURL', 'License', 'Author'}
        require(required <= set(reader.fieldnames or []), 'Open Images image metadata schema differs')
        for row in reader:
            if row['ImageID'] in ids:
                require(row['Subset'] == 'train', 'Non-train image in source metadata')
                result[row['ImageID']] = row
                if len(result) == len(ids):
                    break
    require(set(result) == ids, 'Selected image provenance missing; no images were requested')
    return result


def download_image(item, out, args):
    image_id = item['image_id']
    path = out / 'images' / (image_id + '.jpg')
    try:
        receipt = fetch(IMAGE_ROOT + image_id + '.jpg', path, args.retries, args.timeout,
                        max_bytes=args.max_image_mb * 1024 * 1024, jpeg=True)
        with path.open('rb') as stream:
            require(stream.read(3) == b'\xff\xd8\xff', 'Expected JPEG image: ' + image_id)
        return dict(image_id=image_id, ok=True, image_path='images/' + path.name, **receipt)
    except (OSError, ValueError) as exc:
        return dict(image_id=image_id, ok=False, error=str(exc))


def write_review(out, records, args):
    rows, cards, seen, duplicates = [], [], {}, []
    for record in records:
        if not record['ok']:
            continue
        if record['sha256'] in seen:
            record['duplicate_of'] = seen[record['sha256']]
            duplicates.append(record['image_id'])
            continue
        seen[record['sha256']] = record['image_id']
        image_id, metadata = record['image_id'], record['metadata']
        rows.append(dict(image_path=record['image_path'], group_id='openimages-' + image_id,
            source_id='openimages:train/' + image_id, split='train',
            **{key: '1' if key == 'musical_instrument' else '?' for key in LABELS},
            approval='UNREVIEWED', reviewer='',
            reason='Open Images human box annotation; draft instrument positive; review all labels and rights'))
        escape = html.escape
        link = metadata['OriginalLandingURL']
        href = escape(link, quote=True) if link.startswith(('https://', 'http://')) else '#'
        evidence = ', '.join(sorted({box['class_name'] for box in record['boxes']}))
        cards.append('<article><h2>' + image_id + ' · ' + escape(evidence) + '</h2>'
            '<img loading="lazy" src="' + record['image_path'] + '" alt="' + image_id + '">'
            '<p>악기=1 초안 · 나머지 7개=? · 미승인</p>' +
            '<p>Author: ' + escape(metadata['Author']) + '<br>License (source record): ' +
            escape(metadata['License']) + '</p><a href="' + href + '" target="_blank" '
            'rel="noopener noreferrer">원본 출처 확인</a></article>')
    # Never overwrite a CSV that the user may have reviewed between resume calls.
    review = out / 'additional_labels.csv'
    if not review.exists():
        write_csv(review, NEW_FIELDS, rows)
    else:
        log('review_csv_preserved', path=str(review), message='New successful rows go to additional_labels.generated.csv')
        write_csv(out / 'additional_labels.generated.csv', NEW_FIELDS, rows)
    gallery = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    gallery += '<title>Open Images 악기 후보 검토</title><style>body{font:15px/1.6 system-ui;margin:24px;background:#f4f3ee;color:#203d34}'
    gallery += '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}article{padding:16px;background:white}'
    gallery += 'img{width:100%;height:300px;object-fit:contain}h2,p{font-size:13px;overflow-wrap:anywhere}</style>'
    gallery += '<h1>Open Images 악기 후보 · 학습 반영 전 검토</h1><p>외부 객체 사진이며 포스터가 아닙니다. '
    gallery += '악기 존재와 8개 라벨, 원본 이용 조건을 확인하세요. 마이크·스피커·음표만 있으면 악기 양성이 아닙니다. '
    gallery += '현재 CSV 승인 상태를 표시하는 화면은 아닙니다.</p><div class="grid">' + ''.join(cards) + '</div></html>'
    (out / 'review.html').write_text(gallery, encoding='utf-8')
    counts = {name: sum(record['ok'] and 'duplicate_of' not in record and record['primary_class'] == name
                        for record in records) for name in args.classes}
    return counts, duplicates


def run(args):
    out = args.out.resolve()
    if args.command == 'show-results':
        print(json.dumps(read_json(out / 'download_summary.json'), ensure_ascii=False, indent=2))
        return
    cache = (args.cache_dir or out.parent / '.openimages-cache').resolve()
    cache.mkdir(parents=True, exist_ok=True)
    names = class_names(cache, args)
    if args.command == 'classes':
        for name in INSTRUMENT_CLASSES:
            if name in names:
                print(name + '\t' + names[name])
        return
    require(args.per_class > 0 and 1 <= args.workers <= 16, 'Use positive --per-class and --workers between 1 and 16')
    require(args.retries >= 0 and args.timeout > 0 and args.max_image_mb > 0, 'Invalid download limits')
    canonical = {name.casefold(): name for name in INSTRUMENT_CLASSES if name in names}
    require(all(name.casefold() in canonical for name in args.classes),
            'Unknown/non-instrument class; use the classes command for exact names')
    args.classes = [canonical[name.casefold()] for name in args.classes]
    require(len(set(args.classes)) == len(args.classes), 'Duplicate class names')
    config = dict(schema_version=1, split='train', classes=args.classes, per_class=args.per_class,
                  seed=args.seed, class_names_url=CLASSES_URL, boxes_url=BOXES_URL,
                  image_metadata_url=METADATA_URL, image_root=IMAGE_ROOT, exclude_group_boxes=True)
    out.mkdir(parents=True, exist_ok=True)
    selection_file = out / 'selection.json'
    if selection_file.exists():
        require(args.resume, 'Output exists; use --resume with the same settings or choose a new --out')
        selection = read_json(selection_file)
        require(selection['config'] == config, 'Settings differ from existing selection; choose a new --out')
    else:
        require(not (out / 'additional_labels.csv').exists(), 'Review CSV exists without selection; choose a new --out')
        boxes_path = cache / 'oidv6-train-annotations-bbox.csv'
        log('metadata_required', approximate_bytes=2896855311,
            message='V7 links to these V6 boxes/2018 image metadata; cached once. Only selected images are downloaded.')
        boxes_receipt = fetch(BOXES_URL, boxes_path, args.retries, args.timeout, progress=True)
        selected = make_selection(boxes_path, {name: names[name] for name in args.classes},
                                  args.per_class, args.seed)
        metadata_path = cache / 'train-images-boxable-with-rotation.csv'
        metadata_receipt = fetch(METADATA_URL, metadata_path, args.retries, args.timeout, progress=True)
        metadata = selected_metadata(metadata_path, {item['image_id'] for item in selected})
        for item in selected:
            item['metadata'] = metadata[item['image_id']]
        selection = dict(config=config, metadata_downloads=[boxes_receipt, metadata_receipt], images=selected)
        save_json(selection_file, selection)
    images = selection['images']
    write_csv(out / 'selected_boxes.csv', ['image_id', 'class_name', 'label_mid', 'XMin', 'XMax', 'YMin', 'YMax',
              'IsDepiction', 'IsOccluded', 'IsTruncated', 'IsGroupOf'],
              [dict(image_id=item['image_id'], **box) for item in images for box in item['boxes']])
    write_csv(out / 'selected_images.csv', ['image_id', 'primary_class', 'classes', 'split'],
              [dict(image_id=item['image_id'], primary_class=item['primary_class'],
                    classes=';'.join(sorted({box['class_name'] for box in item['boxes']})), split='train') for item in images])
    log('selection_ready', unique_images=len(images), classes=args.classes, requested_per_class=args.per_class)
    outcomes = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(download_image, item, out, args) for item in images]
        for completed, job in enumerate(as_completed(jobs), 1):
            result = job.result()
            outcomes[result['image_id']] = result
            if completed % 10 == 0 or completed == len(jobs):
                log('image_download_progress', completed=completed, total=len(jobs))
    records = [dict(item, **{key: value for key, value in outcomes[item['image_id']].items() if key != 'image_id'})
               for item in images]
    counts, duplicates = write_review(out, records, args)
    save_json(out / 'download_manifest.json', dict(config=config, images=records,
              license_status='source_metadata_recorded_not_individually_verified',
              image_decode_checked=False, labels_approved=False))
    failed = [dict(image_id=row['image_id'], error=row['error']) for row in records if not row['ok']]
    summary = dict(status='complete' if not failed and not duplicates else 'incomplete',
        out=str(out), requested_per_class=args.per_class, unique_review_images=sum(counts.values()),
        primary_class_counts=counts, failed_images=failed, duplicate_image_ids=duplicates,
        review_csv=str(out / 'additional_labels.csv'), review_html=str(out / 'review.html'),
        regenerated_review_csv=str(out / 'additional_labels.generated.csv')
            if (out / 'additional_labels.generated.csv').exists() else None,
        labels_approved=False, training_packet_changed=False, training_executed=False,
        existing_32_corrections_changed=False,
        note='Draft instrument=1 from human boxes; other 7 labels unknown. Review before merging into existing data.')
    save_json(out / 'download_summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary['status'] != 'complete':
        raise SystemExit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['classes', 'download', 'show-results'])
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--classes', nargs='+', default=DEFAULT_CLASSES)
    parser.add_argument('--per-class', type=int, default=40)
    parser.add_argument('--seed', type=int, default=20261026)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--retries', type=int, default=3)
    parser.add_argument('--max-image-mb', type=int, default=50)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError) as exc:
        parser.exit(1, 'Open Images download failed: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
