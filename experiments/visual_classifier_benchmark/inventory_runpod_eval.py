"""Read-only metadata inventory for RunPod requirement and image datasets.

This is not a model evaluation, a label approval, an integrity proof, or an
independent test set. The only writes are new inventory reports in --out.
"""
import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

LABELS = ('person', 'food_drink', 'animal', 'plant_flower', 'vehicle',
          'building_structure', 'musical_instrument', 'fireworks')
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff'}
FIELDS = ('dataset', 'kind', 'source', 'source_sha256', 'split', 'rows', 'known_cells', 'unknown_cells',
          'unreviewed_rows', 'image_paths_present', 'image_paths_missing_or_unsafe',
          'approval', 'evidence_status', 'notes')
MAX_METADATA_BYTES = 64 * 1024 * 1024


def regular(path):
    return path.is_file() and not path.is_symlink()


def read_json(path):
    if not regular(path):
        raise ValueError('missing_or_nonregular')
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError('metadata_size_limit')
    with path.open(encoding='utf-8-sig') as handle:
        return json.load(handle)


def record(dataset, kind, source, **kwargs):
    row = {name: '' for name in FIELDS}
    row.update(dataset=dataset, kind=kind, source=str(source), **kwargs)
    return row


def metadata_sha(path):
    """Hash bounded metadata only; never decode or hash image/model payloads."""
    try:
        if path.suffix.lower() not in ('.json', '.csv') or not regular(path) or path.stat().st_size > MAX_METADATA_BYTES:
            return ''
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ''


def safe_image(root, value):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return False
    candidate = root / value
    try:
        if not candidate.resolve().is_relative_to(root.resolve()):
            return False
        # A symlink at any point makes identity and ownership ambiguous.
        current = root
        for part in Path(value).parts:
            if part in ('..', '.'):
                return False
            current = current / part
            if current.is_symlink():
                return False
        return regular(candidate)
    except (OSError, RuntimeError):
        return False


def label_counts(rows, unreviewed_key=None):
    counts = Counter()
    for row in rows:
        counts['rows'] += 1
        status = row.get(unreviewed_key) if unreviewed_key else None
        masked = bool(unreviewed_key and status not in ('APPROVED', 'AGENT_APPROVED'))
        counts['unreviewed_rows'] += int(masked)
        labels = row.get('labels')
        for label in LABELS:
            value = labels.get(label) if isinstance(labels, dict) else None
            if not masked and type(value) is int and value in (0, 1):
                counts['known_cells'] += 1
            else:
                counts['unknown_cells'] += 1
    return counts


def summarize_labeled(records, dataset, root, rows, source, approval, notes):
    if not isinstance(rows, list):
        records.append(record(dataset, 'image_labels', source, evidence_status='invalid_format', notes=notes))
        return
    by_split = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        split = str(row.get('split', 'unknown'))
        by_split.setdefault(split, []).append(row)
    for split, subset in sorted(by_split.items()):
        counts = label_counts(subset)
        present = sum(safe_image(root, row.get('image')) for row in subset)
        records.append(record(dataset, 'image_labels', source, split=split,
            rows=counts['rows'], known_cells=counts['known_cells'], unknown_cells=counts['unknown_cells'],
            unreviewed_rows='', image_paths_present=present,
            image_paths_missing_or_unsafe=len(subset)-present, approval=approval,
            evidence_status='metadata_read_not_verified', notes=notes))


def read_visual(records, bundle, packets):
    for name, split_default in (('train_dev.json', None), ('test.json', 'test')):
        source = bundle / 'data' / name
        try:
            rows = read_json(source)
            summarize_labeled(records, 'original_bundle', bundle, rows, source,
                'bundle_metadata_claim; no independent approval check',
                'Original split; repeated evaluation exposure. Unknown labels excluded. '
                'Images are path-existence checks only; no content decoded or hashed.')
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            records.append(record('original_bundle', 'image_labels', source, split=split_default or '',
                evidence_status='unavailable', notes=type(exc).__name__))
    if not packets.is_dir() or packets.is_symlink():
        records.append(record('training_packets', 'packet_inventory', packets, evidence_status='unavailable'))
        return
    with os.scandir(packets) as entries:
        roots = sorted((Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)), key=str)
    for root in roots[:500]:
        manifest_path, source = root / 'packet_manifest.json', root / 'train_dev.json'
        if not regular(manifest_path):
            records.append(record(root.name, 'training_packet', manifest_path, evidence_status='manifest_missing'))
            continue
        try:
            manifest = read_json(manifest_path)
            rows = read_json(source)
            approval = 'packet_claim; approval lineage and checksums not verified'
            notes = ('schema=' + str(manifest.get('schema', 'unknown')) +
                     '; added_images=' + str(manifest.get('added_images', 'unknown')) +
                     '; changed_train_cells=' + str(manifest.get('changed_train_cells', 'unknown')) +
                     '; test_in_packet=' + str(manifest.get('test_in_packet', 'unknown')) +
                     '; packet versions overlap; do not sum as independent images')
            summarize_labeled(records, root.name, root, rows, source, approval, notes)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            records.append(record(root.name, 'training_packet', source, evidence_status='unavailable',
                                  notes=type(exc).__name__))
    if len(roots) > 500:
        records.append(record('training_packets', 'scan_limit', packets, rows=len(roots),
                              evidence_status='partial', notes='Only first 500 packet directories visited'))


def label_draft_cases(cases, compact=False):
    rows = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        if compact:
            fields = {x[0] for x in case.get('items', []) if isinstance(x, list) and x}
            uncertain = bool(case.get('review'))
        else:
            expected = case.get('expected', {})
            fields = {item.get('field') for item in expected.get('items', []) if isinstance(item, dict)}
            uncertain = any(isinstance(r, dict) and r.get('reason') != 'non_requirement'
                            for r in expected.get('needs_review', []))
        labels = {label: 1 if label in fields else None if uncertain else 0 for label in LABELS}
        rows.append(dict(labels=labels))
    return rows


def read_text(records, visual, workspace):
    paths = [('qwen_original_40', visual / 'requirements_pilot_v1.json', 'draft'),
             ('qwen_structure_dev_28', visual / 'requirements_structure_dev_v1.json', 'compact'),
             ('qwen_reviewed_72', workspace / 'qwen-eval-agent-70-pending-2.json', 'review')]
    for name, path, kind in paths:
        try:
            packet = read_json(path)
            cases = packet['cases']
            if not isinstance(cases, list):
                raise ValueError('invalid_cases')
            if kind == 'review':
                counts = label_counts(cases, 'review_status')
                statuses = Counter(str(c.get('review_status', 'unknown')) for c in cases if isinstance(c, dict))
                approval = 'states=' + json.dumps(dict(statuses), sort_keys=True)
            else:
                counts = label_counts(label_draft_cases(cases, compact=kind == 'compact'))
                approval = 'synthetic draft; human_verified=false'
            records.append(record(name, 'requirement_text_labels', path, split='development/exposed',
                rows=counts['rows'], known_cells=counts['known_cells'], unknown_cells=counts['unknown_cells'],
                unreviewed_rows=counts['unreviewed_rows'], approval=approval,
                evidence_status='metadata_read_not_verified',
                notes='No input text or answers printed; known labels are provisional, not independent accuracy.'))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            records.append(record(name, 'requirement_text_labels', path, evidence_status='unavailable',
                                  notes=type(exc).__name__))


def csv_inventory(records, datasets, max_entries, max_depth):
    if not datasets.is_dir() or datasets.is_symlink():
        records.append(record('workspace_datasets', 'file_inventory', datasets, evidence_status='unavailable'))
        return
    stack = [(datasets, 0)]
    seen = 0
    image_count = 0
    image_roots = Counter()
    csv_files = []
    partial = False
    while stack:
        root, depth = stack.pop()
        try:
            with os.scandir(root) as scan:
                for entry in scan:
                    seen += 1
                    if seen > max_entries:
                        partial = True
                        break
                    if entry.name.startswith('.') or entry.is_symlink():
                        continue
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if depth < max_depth:
                            stack.append((path, depth + 1))
                        else:
                            partial = True
                    elif entry.is_file(follow_symlinks=False):
                        if path.suffix.lower() in IMAGE_EXTS:
                            image_count += 1
                            image_roots[str(root)] += 1
                        elif path.suffix.lower() == '.csv':
                            csv_files.append(path)
        except OSError:
            partial = True
        if seen > max_entries:
            break
    records.append(record('workspace_datasets', 'image_file_scan', datasets, rows=image_count,
        evidence_status='partial' if partial else 'scanned',
        notes='Image files only; duplicates not identified; visited entries=' + str(seen) +
              '; excludes hidden paths and symlinks; depth limit=' + str(max_depth)))
    for root, count in sorted(image_roots.items()):
        records.append(record('workspace_datasets', 'image_root', root, rows=count,
            evidence_status='partial' if partial else 'scanned', notes='File count only; not approved images.'))
    for path in sorted(csv_files)[:500]:
        if not regular(path):
            continue
        if path.stat().st_size > MAX_METADATA_BYTES:
            records.append(record('workspace_datasets', 'csv_metadata', path,
                evidence_status='size_limit', notes='Not read'))
            continue
        try:
            with path.open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                statuses = Counter()
                rows = 0
                for row in reader:
                    rows += 1
                    if rows > 100000:
                        break
                    if 'approval' in row:
                        value = row['approval'].strip().upper()
                        statuses[value if value in ('APPROVED', 'REJECTED', 'UNREVIEWED', '') else 'OTHER'] += 1
            records.append(record('workspace_datasets', 'csv_metadata', path, rows=min(rows, 100000),
                approval=json.dumps(dict(statuses), sort_keys=True),
                evidence_status='partial' if rows > 100000 else 'metadata_read_not_verified',
                notes='No CSV cell values printed except recognized approval states.'))
        except (OSError, UnicodeError, csv.Error):
            records.append(record('workspace_datasets', 'csv_metadata', path, evidence_status='unavailable'))


def local_presence(visual, workspace):
    models = []
    for name in ('qwen3.5-2b', 'qwen3.5-4b', 'qwen3.5-9b', 'siglip2-base-384'):
        root = workspace / 'models' / name
        try:
            weights = [p.name for p in root.iterdir() if regular(p) and p.suffix == '.safetensors'] if root.is_dir() and not root.is_symlink() else []
        except OSError:
            weights = []
        models.append(dict(name=name, directory=str(root), directory_present=root.is_dir() and not root.is_symlink(),
                           safetensors_count=len(weights), integrity_verified=False))
    run = visual / 'runs/siglip-ai-reviewed-v1-fp32'
    selection = run / 'selection_frozen.json'
    selected = dict(run=str(run), selection_present=regular(selection), checkpoint_path=None,
                    checkpoint_present=None, selection_and_checkpoint_hash_verified=False)
    if regular(selection):
        try:
            data = read_json(selection)
            rel = data.get('checkpoint')
            selected['checkpoint_path'] = rel if isinstance(rel, str) else None
            selected['checkpoint_present'] = safe_image(run, rel) if isinstance(rel, str) else False
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            selected['checkpoint_present'] = False
    outputs = []
    runs = visual / 'runs'
    if runs.is_dir() and not runs.is_symlink():
        with os.scandir(runs) as entries:
            roots = sorted((Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)), key=str)
        for root in roots[:500]:
            outputs.append(dict(run=root.name, status_present=regular(root/'status.json'),
                comparison_csv_present=regular(root/'comparison.csv'),
                predictions_json_present=regular(root/'predictions.json'),
                artifact_hashes_verified=False))
    return dict(models=models, siglip_selected_checkpoint=selected, results=outputs,
                note='Presence only; prior pruning removed other experiment weights. Inference readiness not established.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path('/workspace'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-entries', type=int, default=50000)
    parser.add_argument('--max-depth', type=int, default=5)
    args = parser.parse_args()
    workspace, out = args.workspace.expanduser().resolve(), args.out.expanduser().resolve()
    if not workspace.is_dir() or not out.is_relative_to(workspace):
        raise ValueError('Output must be under the existing workspace')
    if out.exists():
        raise ValueError('Use a new output directory; existing reports are never replaced')
    if not 100 <= args.max_entries <= 200000 or not 0 <= args.max_depth <= 8:
        raise ValueError('Invalid inventory limits')
    repo = workspace / 'SKN33-Final-1Team'
    visual = repo / 'experiments/visual_classifier_benchmark'
    bundle = repo / 'experiments/poster_benchmark/assets'
    packets = visual / 'training_packets'
    datasets = workspace / 'datasets'
    for protected in (bundle, packets, datasets, workspace / 'models', visual / 'runs'):
        if out.is_relative_to(protected.resolve()):
            raise ValueError('Choose an output directory outside input datasets, runs and models')
    records = []
    read_visual(records, bundle, packets)
    read_text(records, visual, workspace)
    csv_inventory(records, datasets, args.max_entries, args.max_depth)
    # Show top-level locations without recursively treating unrelated directories
    # as datasets. The caller can choose additional image roots after inspection.
    with os.scandir(workspace) as entries:
        top_dirs = sorted((Path(e.path) for e in entries
                           if not e.name.startswith('.') and e.is_dir(follow_symlinks=False)), key=str)
    for path in top_dirs[:500]:
        records.append(record('workspace_top_level', 'directory', path,
            evidence_status='listed_not_scanned', notes='Directory only; content and labels not examined.'))
    if len(top_dirs) > 500:
        records.append(record('workspace_top_level', 'scan_limit', workspace, rows=len(top_dirs),
            evidence_status='partial', notes='Only first 500 top-level directories listed.'))
    presence = local_presence(visual, workspace)
    hash_cache = {}
    for row in records:
        source = row['source']
        if source not in hash_cache:
            hash_cache[source] = metadata_sha(Path(source))
        row['source_sha256'] = hash_cache[source]
    out.mkdir(parents=True, exist_ok=False)
    with (out / 'datasets.csv').open('x', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    summary = dict(schema='runpod-evaluation-inventory-v1', records=records, presence=presence,
        image_identity_deduplication_executed=False, model_inference_executed=False,
        labels_independently_verified=False, model_hashes_verified=False,
        training_executed=False, installation_executed=False,
        warning='Counts describe separate source versions. Do not sum datasets or treat train/dev as independent test. '
                'Unknown labels are not negatives. AI-approved entries are not human verified.')
    with (out / 'inventory.json').open('x', encoding='utf-8') as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps(dict(status='inventory_complete', output=str(out), sources=len(records),
        csv_report=str(out / 'datasets.csv'), json_report=str(out / 'inventory.json'),
        model_inference_executed=False, training_executed=False, installation_executed=False),
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
