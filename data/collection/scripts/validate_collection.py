#!/usr/bin/env python3
"""Read-only collection checks; writes only the explicitly selected review JSON.

No application, model, training, OCR or API is run. Counts are not performance
measurements. Missing evidence stays pending and human approval is never inferred.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REQUIRED_COLUMNS = {
    'asset_id', 'asset_type', 'origin_kind', 'source_method', 'collected_at',
    'path', 'sha256', 'original_group_id', 'parent_asset_id', 'format',
    'width', 'height', 'bytes', 'record_count', 'use_conditions',
    'ai_review_status', 'human_review_status', 'evidence_path', 'is_new',
}
RISK_PATTERNS = {
    'email': re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    'korean_mobile': re.compile(r'(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)'),
    'resident_id_shape': re.compile(r'(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'token_shape': re.compile(r'\b(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,})\b'),
    'personal_absolute_path': re.compile(r'(?:/Users/[^/\s"<>]+|C:\\Users\\[^\\\s"<>]+)'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(path):
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix == '.jsonl':
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    if isinstance(value, list):
        return value
    for key in ('images', 'records', 'labels', 'documents', 'items', 'observations'):
        if isinstance(value.get(key), list):
            return value[key]
    return [value]


def value(row, *keys):
    return next((row[k] for k in keys if k in row), None)


def validate(root, public_paths=()):
    base = root / 'data' / 'collection'
    checks = []
    metrics = {'actual_business_material_status': 'not_provided_unconfirmed',
               'human_approved_ready': 0, 'privacy_manual_review_status': 'pending',
               'training_run': False, 'performance_evaluation_run': False}

    def relative(path):
        try:
            return str(path.relative_to(root))
        except ValueError:
            return path.name

    def check(name, passed=None, detail=None):
        checks.append({'id': name, 'status': 'pending' if passed is None else ('passed' if passed else 'failed'),
                       'detail': detail})

    spec_path = base / 'case-specs.json'
    try:
        spec = json.loads(spec_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        check('spec.readable', False, type(error).__name__)
        return {'schema_version': '1.0', 'metrics': metrics, 'checks': checks,
                'overall_status': 'failed', 'human_review_status': 'pending'}
    check('spec.synthetic_and_pending', spec.get('source_kind') == 'synthetic' and spec.get('human_review_status') == 'pending')
    cases = spec['cases']
    targets = spec['targets']
    groups = {case['group_id'] for case in cases}

    raw = base / 'raw' / 'synthetic'
    docs = sorted(raw.rglob('*.md')) if raw.exists() else []
    images = sorted(raw.rglob('*.png')) if raw.exists() else []
    request_candidates = sorted(p for p in raw.rglob('*.jsonl') if 'request' in p.name.lower()) if raw.exists() else []
    metrics.update(documents=len(docs), new_images=len(images))
    check('documents.target_count', len(docs) == targets['documents'], {'actual': len(docs), 'target': targets['documents']})
    check('images.target_count', len(images) == targets['new_images'], {'actual': len(images), 'target': targets['new_images']})
    text_sources = []
    doc_texts = {}
    for path in docs:
        try:
            content = path.read_text(encoding='utf-8')
            doc_texts[path] = content
            text_sources.append((path, content))
            check('document.readable.' + path.stem, bool(content.strip()))
            check('document.synthetic_disclosure.' + path.stem, '가상' in content and ('개발' in content or '합성' in content))
        except (OSError, UnicodeError) as error:
            check('document.readable.' + path.stem, False, type(error).__name__)
    check('documents.unique_hashes', len({sha(path) for path in docs}) == len(docs))

    document_log = base / 'evidence' / 'document-production.jsonl'
    if document_log.exists():
        try:
            document_rows = records(document_log)
            document_ids = [row.get('document_id') for row in document_rows]
            check('documents.provenance_count_and_ids', len(document_rows) == targets['documents'] and all(document_ids) and len(set(document_ids)) == len(document_ids))
            case_map = {case['group_id']: case for case in cases}
            kinds = collections.defaultdict(set)
            for row in document_rows:
                ident = str(row.get('document_id'))
                path = root / row.get('path', '')
                content = doc_texts.get(path, '')
                check('documents.provenance.' + ident, path in doc_texts and row.get('sha256') == sha(path) and row.get('bytes') == path.stat().st_size and row.get('case_specs_sha256') == sha(spec_path) and row.get('human_review_status') == 'pending' and row.get('source_kind') == 'synthetic' and row.get('independent_evaluation_eligible') is False and row.get('contains_image_answer_key') is False)
                group = row.get('group_id')
                if group in case_map:
                    case = case_map[group]
                    kinds[group].add(row.get('document_kind'))
                    required = [case['brand'], case['product'], str(case['discount_percent']) + '%', case['required_disclaimer'], case['visual'], group]
                    check('documents.case_conditions.' + ident, all(term in content for term in required))
            check('documents.three_types_per_case', set(kinds) == groups and all(len(items) == 3 for items in kinds.values()))
        except (OSError, ValueError, UnicodeError) as error:
            check('documents.provenance_readable', False, type(error).__name__)
    else:
        check('documents.provenance_available', None)

    request_rows = []
    for path in request_candidates:
        try:
            content = path.read_text(encoding='utf-8')
            text_sources.append((path, content))
            request_rows.extend(records(path))
            check('requests.utf8_and_json.' + path.stem, True)
        except (OSError, UnicodeError, ValueError) as error:
            check('requests.utf8_and_json.' + path.stem, False, type(error).__name__)
    ids = [value(row, 'request_id', 'id', 'asset_id') for row in request_rows]
    sentences = [value(row, 'text', 'request_text', 'sentence', 'utterance') for row in request_rows]
    req_groups = [value(row, 'request_group_id', 'original_group_id', 'group_id', 'scenario_group_id', 'situation_id') for row in request_rows]
    metrics['requests'] = len(request_rows)
    metrics['request_groups'] = len(set(req_groups) - {None, ''})
    check('requests.target_count', len(request_rows) == targets['requests'], {'actual': len(request_rows), 'target': targets['requests']})
    check('requests.unique_ids', len(ids) == len(set(ids)) and all(ids))
    check('requests.unique_nonempty_sentences', all(isinstance(s, str) and s.strip() for s in sentences) and len(sentences) == len(set(sentences)))
    counts = collections.Counter(req_groups)
    check('requests.group_count_and_variants', len(counts) == targets['request_groups'] and all(k and n == 5 for k, n in counts.items()),
          {'groups': len(counts), 'variant_count_distribution': dict(collections.Counter(counts.values()))})
    check('requests.no_embedded_answer', all(not any(k in row for k in ('intent_label', 'intent', 'label', 'gold_intent', 'expected_intent')) for row in request_rows))
    check('requests.synthetic_provenance', all(row.get('synthetic') is True and row.get('source_method') and row.get('created_at') and row.get('case_group_id') in groups for row in request_rows))

    evidence = base / 'evidence'
    labels_paths = sorted(p for p in evidence.rglob('*') if p.is_file() and 'label' in p.name.lower() and p.suffix in ('.json', '.jsonl'))
    label_rows = []
    for path in labels_paths:
        try:
            label_rows.extend(records(path))
        except (OSError, ValueError, UnicodeError) as error:
            check('labels.readable.' + path.stem, False, type(error).__name__)
    labels = {value(row, 'request_id', 'id', 'asset_id'): row for row in label_rows}
    if labels_paths:
        check('labels.exact_join', len(label_rows) == len(labels) == len(ids) and set(labels) == set(ids))
        intents = collections.Counter(value(row, 'intent_label', 'intent', 'label', 'gold_intent', 'expected_intent') for row in label_rows)
        metrics['request_intents'] = dict(intents)
        check('labels.intent_distribution', set(intents) == set(spec['request_intents']) and all(n == 50 for n in intents.values()), dict(intents))
        grouped = collections.defaultdict(set)
        for request in request_rows:
            ident = value(request, 'request_id', 'id', 'asset_id')
            grouped[value(request, 'request_group_id', 'original_group_id', 'group_id', 'scenario_group_id', 'situation_id')].add(value(labels.get(ident, {}), 'intent_label', 'intent', 'label', 'gold_intent', 'expected_intent'))
        check('labels.group_consistency', all(len(items) == 1 and None not in items for items in grouped.values()))
        check('labels.human_pending', all(value(row, 'human_review_status', 'human_review') == 'pending' for row in label_rows))
    else:
        check('labels.available', None, 'Separated label evidence not yet available')

    try:
        from PIL import Image
    except ImportError:
        Image = None
        check('images.decoder_available', None, 'Install/use existing Pillow environment')
    hashes = collections.defaultdict(list)
    image_details = []
    for path in images:
        digest = sha(path)
        hashes[digest].append(path.name)
        info = {'path': relative(path), 'sha256': digest, 'bytes': path.stat().st_size}
        if Image is not None:
            try:
                with Image.open(path) as image:
                    info.update(width=image.width, height=image.height, format=image.format)
                    check('image.valid.' + path.stem, image.format == 'PNG' and image.width > 0 and image.height > 0 and image.width * image.height <= 20_000_000 and info['bytes'] <= 20 * 1024 * 1024)
                    image.verify()
            except (OSError, ValueError) as error:
                check('image.decode.' + path.stem, False, type(error).__name__)
        image_details.append(info)
    check('images.unique_hashes', len(hashes) == len(images), {'duplicate_sets': [names for names in hashes.values() if len(names) > 1]})
    image_groups = collections.Counter(path.stem.rsplit('-', 1)[0].rsplit('-', 1)[0] if path.stem.endswith('-missing-evidence') else path.stem.split('-normal')[0].split('-mismatch')[0].split('-ambiguous')[0].split('-missing_evidence')[0] for path in images)
    expected_names = {case['group_id'] + '-' + scenario for case in cases for scenario in case['scenarios']}
    check('images.scenario_coverage', {path.stem for path in images} == expected_names,
          {'missing': sorted(expected_names - {path.stem for path in images}), 'unexpected': sorted({path.stem for path in images} - expected_names)})
    metrics['new_image_groups'] = len(image_groups)
    check('images.group_count', set(image_groups) == groups and all(n == 4 for n in image_groups.values()))

    production_path = evidence / 'image-production.jsonl'
    if production_path.exists():
        try:
            production = records(production_path)
            by_id = {value(row, 'asset_id', 'id'): row for row in production}
            check('images.provenance_join', len(production) == len(by_id) == len(images) and set(by_id) == expected_names)
            roots = collections.Counter()
            for ident, row in by_id.items():
                operation = row.get('operation')
                group = row.get('original_group_id')
                parent = row.get('parent_asset_id')
                valid = group in groups and operation in ('generate', 'edit') and bool(row.get('tool')) and bool(row.get('generated_at'))
                if operation == 'generate':
                    roots[group] += 1
                    valid = valid and not parent
                else:
                    valid = valid and parent in by_id and by_id[parent].get('original_group_id') == group and by_id[parent].get('operation') == 'generate'
                prompt = row.get('prompt_path')
                valid = valid and bool(prompt) and (root / prompt).is_file()
                check('images.provenance.' + str(ident), valid)
                production_image = root / row.get('path', '')
                check('images.provenance_hash.' + str(ident), production_image.is_file() and row.get('sha256') == sha(production_image) and row.get('bytes') == production_image.stat().st_size and row.get('human_review_status') == 'pending')
            check('images.three_separate_generation_roots', set(roots) == groups and all(n == 1 for n in roots.values()))
        except (OSError, ValueError, UnicodeError) as error:
            check('images.provenance_readable', False, type(error).__name__)
    else:
        check('images.provenance_available', None)

    manifest_path = base / 'inventory.csv'
    manifest = []
    if manifest_path.exists():
        try:
            with manifest_path.open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                check('manifest.required_columns', REQUIRED_COLUMNS <= set(reader.fieldnames or []), {'missing': sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))})
                manifest = list(reader)
            manifest_ids = [row.get('asset_id') for row in manifest]
            check('manifest.unique_ids', all(manifest_ids) and len(manifest_ids) == len(set(manifest_ids)))
            for row in manifest:
                ident = str(row.get('asset_id'))
                path = (root / row.get('path', '')).resolve()
                valid = path.is_file() and path.is_relative_to(root.resolve()) and 'runtime' not in path.parts
                check('manifest.file.' + ident, valid)
                if valid:
                    check('manifest.hash.' + ident, row.get('sha256') == sha(path))
                    check('manifest.bytes.' + ident, row.get('bytes') == str(path.stat().st_size))
                    image_info = next((info for info in image_details if info['path'] == relative(path)), None)
                    if image_info and 'width' in image_info:
                        check('manifest.image_dimensions.' + ident, row.get('width') == str(image_info['width']) and row.get('height') == str(image_info['height']) and row.get('format', '').upper() == image_info['format'])
                    if path in request_candidates:
                        check('manifest.request_record_count.' + ident, row.get('record_count') == str(len(records(path))))
                date_valid = bool(row.get('collected_at'))
                if date_valid:
                    if row['collected_at'] == 'unknown':
                        reason = row.get('collected_at_note') or row.get('collected_at_reason') or row.get('source_method', '')
                        date_valid = any(term in reason.lower() for term in ('unknown', 'unconfirmed', '미확인', '불명'))
                    else:
                        try:
                            # Date-only source records preserve their precision;
                            # no invented clock time is written back to inventory.
                            datetime.fromisoformat(row['collected_at'].replace('Z', '+00:00'))
                        except ValueError:
                            date_valid = False
                check('manifest.collection_provenance.' + ident, date_valid and bool(row.get('source_method')) and bool(row.get('original_group_id')))
                if row.get('origin_kind') == 'synthetic':
                    check('manifest.synthetic_pending.' + ident, row.get('human_review_status') == 'pending' and bool(row.get('use_conditions')))
                if row.get('evidence_path'):
                    check('manifest.evidence.' + ident, (root / row['evidence_path']).is_file())
                else:
                    check('manifest.evidence.' + ident, None)
            metrics['inventory_rows'] = len(manifest)
            metrics['human_approved_ready'] = sum(row.get('human_review_status') == 'approved' for row in manifest)
            check('manifest.no_human_approval_claim', metrics['human_approved_ready'] == 0)
            listed = {row.get('path') for row in manifest}
            check('manifest.raw_coverage', {relative(path) for path in docs + images + request_candidates} <= listed)
        except (OSError, ValueError, UnicodeError) as error:
            check('manifest.readable', False, type(error).__name__)
    else:
        check('manifest.available', None)

    observations_path = evidence / 'image-observations.json'
    if observations_path.exists():
        try:
            observations = records(observations_path)
            observation_ids = [value(row, 'asset_id', 'id') for row in observations]
            check('images.observation_coverage', len(observation_ids) == len(set(observation_ids)) and set(observation_ids) == expected_names)
            check('images.observation_human_pending', all(value(row, 'human_review_status', 'human_review') == 'pending' for row in observations))
            check('images.observed_conditions_recorded', all(isinstance(row.get('conditions'), list) and row['conditions'] for row in observations))
            for row in observations:
                ident = str(value(row, 'asset_id', 'id'))
                path = root / row.get('path', '')
                check('images.observation_hash.' + ident, path.is_file() and row.get('sha256') == sha(path))
                condition_ids = [condition.get('criterion') for condition in row.get('conditions', [])]
                check('images.observation_condition_integrity.' + ident,
                      len(condition_ids) == 4 and set(condition_ids) == {'brand', 'product', 'discount', 'development_disclaimer'} and
                      all(condition.get('status') in {'matches', 'mismatch', 'unclear'} and condition.get('observed') and condition.get('expected') for condition in row.get('conditions', [])) and
                      row.get('automated_inspection_executed') is False and row.get('label_status') == 'draft')
        except (OSError, ValueError, UnicodeError) as error:
            check('images.observations_readable', False, type(error).__name__)
    else:
        check('images.observations_available', None)

    package_path = evidence / 'submission-packages.jsonl'
    if package_path.exists():
        try:
            packages = records(package_path)
            package_images = [row.get('image_asset_id') for row in packages]
            package_ids = [row.get('package_id') for row in packages]
            check('images.packages_exact_join', len(packages) == len(set(package_ids)) == len(images) and all(package_ids) and set(package_images) == expected_names)
            omissions = [row for row in packages if row.get('deliberate_omission') is True]
            check('images.three_controlled_package_omissions', len(omissions) == 3 and all(row.get('scenario') == 'missing_evidence' and row.get('provided_creation_evidence_paths') == [] for row in omissions))
            for row in packages:
                ident = str(row.get('package_id'))
                group = row.get('original_group_id')
                expected_docs = {relative(path) for path in docs if path.stem.startswith(str(group).lower() + '-')}
                provided = row.get('provided_creation_evidence_paths', [])
                valid = group in groups and row.get('image_asset_id') == group + '-' + str(row.get('scenario')) and (root / row.get('image_path', '')).is_file() and row.get('synthetic') is True and row.get('human_review_status') == 'pending'
                valid = valid and set(row.get('rule_document_paths', [])) == expected_docs and len(expected_docs) == 3
                if row.get('scenario') == 'missing_evidence':
                    valid = valid and row.get('deliberate_omission') is True and provided == []
                else:
                    valid = valid and row.get('deliberate_omission') is False and bool(provided) and all((root / path).is_file() for path in provided) and relative(production_path) in provided
                check('images.package.' + ident, valid)
            metrics['submission_packages'] = len(packages)
            metrics['controlled_missing_evidence_packages'] = len(omissions)
        except (OSError, ValueError, UnicodeError) as error:
            check('images.packages_readable', False, type(error).__name__)
    else:
        check('images.submission_packages_available', None)

    # Same-source samples are a separate legacy development group.
    legacy = sorted((root / 'data' / 'samples').glob('sample-*.png'))
    metrics.update(legacy_images=len(legacy), legacy_independent_original_groups=1 if legacy else 0)
    check('legacy.separate_count', len(legacy) == 6)

    risks = []
    for path in public_paths:
        try:
            if path.suffix.lower() in ('.html', '.md', '.txt', '.csv', '.json'):
                text_sources.append((path, path.read_text(encoding='utf-8')))
            elif path.suffix.lower() == '.pdf':
                try:
                    from pypdf import PdfReader
                    pdf = PdfReader(str(path))
                    text_sources.append((path, '\n'.join(page.extract_text() or '' for page in pdf.pages) + str(pdf.metadata or '')))
                    check('public.pdf_no_attachments.' + path.stem, not pdf.attachments)
                except ImportError:
                    check('public.pdf_scan.' + path.stem, None, 'pypdf unavailable')
        except (OSError, ValueError, UnicodeError) as error:
            check('public.readable.' + path.stem, False, type(error).__name__)
    for path, content in text_sources:
        for name, pattern in RISK_PATTERNS.items():
            count = len(pattern.findall(content))
            if count:
                risks.append({'source': relative(path), 'risk': name, 'matches': count})
    metrics['privacy_heuristic_hits'] = sum(row['matches'] for row in risks)
    check('privacy.heuristic_scan', not risks, risks)
    check('privacy.human_review', None, 'Heuristics do not establish permission or absence of sensitive information')
    summary = dict(collections.Counter(item['status'] for item in checks))
    metrics['ai_basic_checks_passed'] = summary.get('passed', 0)
    def category_passed(prefixes):
        selected = [item for item in checks if any(item['id'].startswith(prefix) for prefix in prefixes)]
        return bool(selected) and all(item['status'] == 'passed' for item in selected)
    metrics['ai_structurally_checked_documents'] = len(docs) if category_passed(('document.', 'documents.')) else 0
    metrics['ai_structurally_checked_requests'] = len(request_rows) if category_passed(('requests.', 'labels.')) else 0
    metrics['ai_structurally_checked_new_images'] = len(images) if category_passed(('image.', 'images.')) else 0
    return {'schema_version': '1.0', 'generated_at': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
            'method': 'read_only_file_metadata_and_content_checks',
            'scope': 'Synthetic collection quality checks; no training or independent performance evaluation',
            'overall_status': 'failed' if summary.get('failed') else 'pending' if summary.get('pending') else 'passed',
            'human_review_status': 'pending', 'summary': summary, 'metrics': metrics,
            'checks': checks, 'image_metadata': image_details,
            'limitations': ['No human-approved data is inferred from AI checks.',
                            'Exact hashes detect byte duplicates; perceptual similarity requires visual review.',
                            'Sensitive-data heuristics require separate manual review.',
                            'Business material not provided is unconfirmed, not confirmed zero.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output', type=Path, help='Default: ROOT/data/collection/evidence/quality-review.json')
    parser.add_argument('--public', type=Path, action='append', default=[], help='Public HTML/PDF to scan, repeatable')
    args = parser.parse_args()
    root = args.root.resolve()
    report = validate(root, [p.resolve() for p in args.public])
    output = args.output or root / 'data' / 'collection' / 'evidence' / 'quality-review.json'
    # Preserve independently authored observations only while their exact inputs
    # remain unchanged. A generated artifact cannot approve itself by this route.
    if output.exists():
        try:
            old = json.loads(output.read_text(encoding='utf-8'))
            reviews = []
            for review in old.get('independent_reviews', []):
                review['input_freshness'] = 'current' if all((root / path).is_file() and sha(root / path) == digest for path, digest in review.get('input_sha256', {}).items()) and review.get('input_sha256') else 'stale'
                reviews.append(review)
            if reviews:
                report['independent_reviews'] = reviews
        except (OSError, ValueError, UnicodeError):
            pass
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'overall_status': report['overall_status'], 'summary': report.get('summary'),
                      'metrics': report['metrics']}, ensure_ascii=False))
    return 1 if report['overall_status'] == 'failed' else 0


if __name__ == '__main__':
    sys.exit(main())
