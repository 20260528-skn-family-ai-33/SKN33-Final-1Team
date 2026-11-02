"""Read saved requirement responses; write supplemental diagnostics without inference."""
import argparse
from collections import Counter
import json
from pathlib import Path

from requirements_json_contract import COUNTS, aggregate, evaluate, normalize, parse
from requirements_compact import PROFILE, FIXED_PROFILE, TARGET_PROFILE, BALANCED_PROFILE, SEMANTIC_PROFILE, STRUCTURE_PROFILE, CONDITION_PROFILE, interpret as compact_interpret
from resultlib import digest, inside, read, require, save, sha, write_csv


def token(value):
    # Distinguish JSON integer 2 from string "2" and bool true from integer 1.
    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True)


def same(a, b, fields):
    return all(k in b and token(a[k]) == token(b[k]) for k in fields)


def align(expected, actual, text):
    """Exact item+source first, then unique identical source; never guess by index."""
    remaining_gold, remaining_actual = set(range(len(expected))), set(range(len(actual)))
    pairs = []
    fields = ('type', 'field', 'target', 'operator', 'value', 'source')
    for gi, gold in enumerate(expected):
        for ai in sorted(remaining_actual):
            if same(gold, actual[ai], fields) and gold['source'] in text:
                pairs.append((gi, ai))
                remaining_gold.remove(gi)
                remaining_actual.remove(ai)
                break
    for gi in sorted(remaining_gold.copy()):
        source = expected[gi]['source']
        gold_ids = [i for i in remaining_gold if expected[i]['source'] == source]
        actual_ids = [i for i in remaining_actual if actual[i].get('source') == source]
        if source in text and len(gold_ids) == len(actual_ids) == 1:
            ai = actual_ids[0]
            pairs.append((gi, ai))
            remaining_gold.remove(gi)
            remaining_actual.remove(ai)
    return sorted(pairs), sorted(remaining_gold), sorted(remaining_actual)


def diagnose(row):
    text, gold = row['text'], row['expected_draft']
    mode = row.get('output_mode', 'full_json')
    compact_modes = {'compact_assembled': PROFILE, 'compact_fixed_assembled': FIXED_PROFILE,
                     'compact_target_assembled': TARGET_PROFILE,
                     'compact_balanced_assembled': BALANCED_PROFILE,
                     'compact_semantic_assembled': SEMANTIC_PROFILE,
                     'compact_structure_assembled': STRUCTURE_PROFILE,
                     'compact_conditions_assembled': CONDITION_PROFILE}
    require(mode == 'full_json' or mode in compact_modes, 'Unknown output mode')
    if mode in compact_modes:
        profile = compact_modes[mode]
        assembled = compact_interpret(row['raw'], text, profile)
        require(all(row.get(k) == v for k, v in assembled.items()),
                'Compact assembly differs from saved evidence: ' + row['case_id'])
        parsed = assembled['parsed']
    else:
        parsed = parse(row['raw'], text)
    require(parsed == row['parsed'], 'Parser behavior differs from saved evidence: ' + row['case_id'])
    strict = evaluate(dict(expected=gold), parsed)
    require(strict == row['result'], 'Strict scoring differs from saved evidence: ' + row['case_id'])
    obj = parsed['value'] if isinstance(parsed['value'], dict) else {}
    raw_items = obj.get('items', [])
    raw_items = raw_items if isinstance(raw_items, list) else []
    actual = [v for v in raw_items if isinstance(v, dict)]
    pairs, unmatched_gold, unmatched_actual = align(gold['items'], actual, text)
    item_details = []
    counts = Counter()
    counts['gold_items'] = len(gold['items'])
    counts['actual_dict_items'] = len(actual)
    counts['malformed_items'] = len(raw_items) - len(actual)
    counts['aligned_items'] = len(pairs)
    counts['unpaired_gold_items'] = len(unmatched_gold)
    counts['unpaired_actual_items'] = len(unmatched_actual)
    counts['forbidden_gold'] = sum(i['operator'] == 'forbidden' for i in gold['items'])
    counts['count_gold'] = sum(i['operator'] in COUNTS for i in gold['items'])
    for gi, ai in pairs:
        g, a = gold['items'][gi], actual[ai]
        single = parse(json.dumps(dict(items=[a], needs_review=[]), ensure_ascii=False), text)
        field = same(g, a, ('type', 'field'))
        operator = same(g, a, ('operator',))
        value = same(g, a, ('value',))
        target = same(g, a, ('target',))
        differences = [k for k in ('type', 'field', 'target', 'operator', 'value')
                       if not same(g, a, (k,))]
        counts['field_matches'] += field
        counts['operator_matches'] += operator
        counts['value_matches'] += value
        counts['target_matches'] += target
        counts['target_only_mismatches'] += differences == ['target']
        counts['aligned_schema_invalid'] += not single['schema_valid']
        counts['aligned_source_invalid'] += single['schema_valid'] and not single['source_valid']
        if g['operator'] == 'forbidden':
            counts['forbidden_aligned'] += 1
            counts['forbidden_operator_matches'] += operator
            counts['forbidden_field_operator_matches'] += field and operator
        if g['operator'] in COUNTS:
            counts['count_aligned'] += 1
            counts['count_operator_value_matches'] += operator and value
            counts['count_field_operator_value_matches'] += field and operator and value
        item_details.append(dict(gold_index=gi, actual_dict_index=ai, expected=g, actual=a,
            different_fields=differences, item_schema_valid=single['schema_valid'],
            item_source_valid=single['source_valid'], validation_error=single['error'],
            source_errors=single['source_errors']))
    # Inspect every raw dict item independently: a bad top-level document may
    # otherwise stop parse() before reporting quote/field errors in later items.
    validations = []
    for ai, item in enumerate(actual):
        p = parse(json.dumps(dict(items=[item], needs_review=[]), ensure_ascii=False), text)
        validations.append(dict(actual_dict_index=ai, item=item, schema_valid=p['schema_valid'],
            source_valid=p['source_valid'], error=p['error'], source_errors=p['source_errors']))
    return dict(case_id=row['case_id'], text=text, output_mode=mode, strict=strict, counts=dict(counts),
        document_error=parsed['error'], document_source_errors=parsed['source_errors'],
        aligned_items=item_details, raw_item_validation=validations,
        unpaired_expected=[gold['items'][i] for i in unmatched_gold],
        unpaired_actual=[actual[i] for i in unmatched_actual],
        expected_review=gold['needs_review'], actual_review=obj.get('needs_review'),
        raw=row['raw'], compact=row.get('compact'), assembled=row.get('assembled'),
        source_segments=row.get('source_segments'), assembly_trace=row.get('assembly_trace'))


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def summarize(details):
    c = Counter()
    for row in details:
        c.update(row['counts'])
    result = dict(cases=len(details), gold_items=c['gold_items'],
        aligned_items=c['aligned_items'],
        alignment_coverage=ratio(c['aligned_items'], c['gold_items']),
        unpaired_gold_items=c['unpaired_gold_items'],
        unpaired_actual_items=c['unpaired_actual_items'],
        target_only_mismatches=c['target_only_mismatches'],
        aligned_schema_invalid=c['aligned_schema_invalid'],
        aligned_source_invalid=c['aligned_source_invalid'])
    for name in ('field', 'operator', 'value', 'target'):
        result[name + '_matches'] = c[name + '_matches']
        result[name + '_agreement_on_aligned'] = ratio(c[name + '_matches'], c['aligned_items'])
    for name in ('forbidden_gold', 'forbidden_aligned', 'forbidden_operator_matches',
                 'forbidden_field_operator_matches', 'count_gold', 'count_aligned',
                 'count_operator_value_matches', 'count_field_operator_value_matches'):
        result[name] = c[name]
    # Unpaired gold constraints remain in these denominators.
    result['forbidden_operator_recall_diagnostic'] = ratio(c['forbidden_operator_matches'], c['forbidden_gold'])
    result['forbidden_field_operator_recall_diagnostic'] = ratio(c['forbidden_field_operator_matches'], c['forbidden_gold'])
    result['count_operator_value_recall_diagnostic'] = ratio(c['count_operator_value_matches'], c['count_gold'])
    result['count_field_operator_value_recall_diagnostic'] = ratio(c['count_field_operator_value_matches'], c['count_gold'])
    return result


def load_evidence(run, model):
    status_path, protocol_path = run / 'status.json', run / 'protocol.json'
    status, protocol = read(status_path), read(protocol_path)
    require(status['status'] == 'complete', 'Run must be complete: ' + str(run))
    require(status['protocol_signature'] == protocol['signature'] == digest(protocol['config']),
            'Protocol signature mismatch')
    hashes = status['artifact_hashes']
    names = ('protocol.json', model + '/predictions.json', model + '/metrics.json')
    before = {str(status_path): sha(status_path)}
    for name in names:
        path = inside(run, name)
        require(name in hashes and sha(path) == hashes[name], 'Evidence hash mismatch: ' + name)
        before[str(path)] = hashes[name]
    rows, metrics = read(inside(run, model + '/predictions.json')), read(inside(run, model + '/metrics.json'))
    require(rows and len({r['case_id'] for r in rows}) == len(rows), 'Empty/duplicate case IDs')
    require(metrics['model'] == model and metrics['cases'] == len(rows), 'Wrong model/case count')
    require(metrics['synthetic'] is True and metrics['human_verified'] is False, 'Unexpected evidence scope')
    return rows, metrics, before


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', nargs='+', type=Path, required=True)
    p.add_argument('--model', choices=('2b', '4b'), default='4b')
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    runs, out = [p.expanduser().resolve() for p in args.run_dir], args.out.expanduser().resolve()
    require(len(runs) == len(set(runs)), 'Duplicate run paths')
    require(all(not out.is_relative_to(r) and not r.is_relative_to(out) for r in runs),
            'Diagnostics output must be outside source runs')
    require(not out.exists(), 'Use a new diagnostics output directory')
    summaries, reports, evidence, reference, reference_strict = [], [], {}, None, None
    for index, run in enumerate(runs):
        rows, metrics, hashes = load_evidence(run, args.model)
        evidence.update(hashes)
        identity = {r['case_id']: dict(text=r['text'], expected=r['expected_draft']) for r in rows}
        if reference is None:
            reference = identity
        require(identity == reference, 'Runs have different cases, texts or draft labels')
        details = [diagnose(row) for row in rows]
        strict = aggregate([d['strict'] for d in details])
        require(all(metrics.get(k) == v for k, v in strict.items()), 'Saved strict aggregate differs')
        if reference_strict is None:
            reference_strict = strict
        summaries.append(dict(run=str(run), model=args.model, output_mode=metrics.get('output_mode', 'full_json'),
            strict_exact_match_rate=strict['draft_exact_match_rate'],
            strict_item_f1=strict['draft_item_f1'],
            strict_schema_valid_rate=strict['schema_valid_rate'],
            strict_source_valid_rate=strict['source_valid_rate'],
            strict_review_tp=strict['review_tp'], strict_review_fp=strict['review_fp'],
            strict_review_fn=strict['review_fn'],
            strict_exact_delta_pp=100 * (strict['draft_exact_match_rate'] - reference_strict['draft_exact_match_rate']),
            historical_p95_ms=metrics['p95_ms'], latency_remeasured=False, **summarize(details)))
        reports.append((str(index + 1).zfill(2), details))
    require(all(sha(Path(path)) == h for path, h in evidence.items()), 'Source evidence changed while reading')
    out.mkdir(parents=True, exist_ok=False)
    for name, details in reports:
        save(out / (name + '-cases.json'), details)
        write_csv(out / (name + '-cases.csv'), [dict(case_id=d['case_id'],
            strict_exact=d['strict']['exact'], schema_valid=d['strict']['schema_valid'],
            source_valid=d['strict']['source_valid'], aligned_items=d['counts']['aligned_items'],
            unpaired_gold_items=d['counts']['unpaired_gold_items'],
            target_only_mismatches=d['counts'].get('target_only_mismatches', 0),
            field_mismatches=d['counts']['aligned_items'] - d['counts'].get('field_matches', 0),
            operator_mismatches=d['counts']['aligned_items'] - d['counts'].get('operator_matches', 0),
            value_mismatches=d['counts']['aligned_items'] - d['counts'].get('value_matches', 0),
            document_error=d['document_error']) for d in details])
    write_csv(out / 'comparison.csv', summaries)
    save(out / 'summary.json', dict(results=summaries, synthetic=True, human_verified=False,
        training_executed=False, inference_executed=False, latency_remeasured=False,
        source_hashes=evidence,
        diagnostic_source_hashes={name: sha(Path(__file__).with_name(name)) for name in
            ('diagnose_requirements_json.py', 'requirements_json_contract.py', 'requirements_compact.py', 'resultlib.py')},
        policy='Strict saved scores preserved. Supplemental raw-item diagnostics include schema-invalid '
               'documents. Align exact item+source first, then unique identical verbatim source. '
               'Ambiguous or changed quotes remain unpaired. Field rates use aligned items only; '
               'forbidden/count recalls use all gold constraints. Target ignored only in explicitly '
               'named diagnostic recalls; these are not semantic accuracy or accepted outputs. '
               'For compact mode, item-level diagnostics describe assembled items, with raw compact '
               'responses and assembly traces retained separately.'))
    print(json.dumps(dict(out=str(out), results=summaries, inference_executed=False), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
