"""Read existing Qwen outputs and score eight visual requirement fields; no model execution."""
import argparse
import json
from pathlib import Path

from requirements_compact import (PROFILE, FIXED_PROFILE, TARGET_PROFILE, BALANCED_PROFILE,
                                  SEMANTIC_PROFILE, STRUCTURE_PROFILE, interpret)
from requirements_json_contract import evaluate, parse
from resultlib import LABELS, digest, inside, read, require, save, sha, write_csv

MODES = {'compact_assembled': PROFILE, 'compact_fixed_assembled': FIXED_PROFILE,
         'compact_target_assembled': TARGET_PROFILE, 'compact_balanced_assembled': BALANCED_PROFILE,
         'compact_semantic_assembled': SEMANTIC_PROFILE, 'compact_structure_assembled': STRUCTURE_PROFILE}


def safe_ratio(n, d):
    return n / d if d else None


def check_evidence(run):
    status_path = run / 'status.json'
    status = read(status_path)
    protocol = read(run / 'protocol.json')
    require(status['status'] == 'complete' and status['protocol_signature'] == protocol['signature'] ==
            digest(protocol['config']), 'Incomplete run or protocol signature mismatch: ' + str(run))
    is_retry = protocol['config'].get('schema') == 'requirements-validation-retry-v1'
    names = ('protocol.json', 'predictions.json', 'comparison_summary.json') if is_retry else (
        'protocol.json', '4b/predictions.json', '4b/metrics.json')
    checks = {str(status_path): sha(status_path)}
    for name in names:
        path = inside(run, name)
        require(name in status['artifact_hashes'] and sha(path) == status['artifact_hashes'][name],
                'Evidence hash mismatch: ' + str(path))
        checks[str(path)] = status['artifact_hashes'][name]
    records = read(inside(run, names[1]))
    require(records and len({r['case_id'] for r in records}) == len(records), 'Empty or duplicate case IDs')
    return protocol, is_retry, records, checks


def replay(raw, text, mode):
    if mode in MODES:
        return interpret(raw, text, MODES[mode])['parsed']
    require(mode == 'full_json', 'Unknown saved output mode: ' + str(mode))
    return parse(raw, text)


def valid(parsed):
    return all(parsed[k] for k in ('json_valid', 'schema_valid', 'source_valid'))


def variant_rows(run, records, is_retry):
    variants = [('first_pass', []), ('final', [])] if is_retry else [('saved', [])]
    for row in records:
        case = dict(expected=row['expected_draft'])
        if is_retry:
            first = row['first']
            first_parsed = replay(first['raw'], row['text'], first['output_mode'])
            require(first_parsed == first['parsed'] and evaluate(case, first_parsed) == row['first_score'],
                    'Saved first response differs: ' + row['case_id'])
            selected = row['retry'] if row['selected'] == 'retry' else first
            require(selected is not None and not selected.get('skipped', False), 'Invalid saved selection')
            final_parsed = replay(selected['raw'], row['text'], selected['output_mode'])
            require(final_parsed == selected['parsed'] and evaluate(case, final_parsed) == row['final_score'],
                    'Saved final response differs: ' + row['case_id'])
            variants[0][1].append((row, first_parsed))
            variants[1][1].append((row, final_parsed))
        else:
            parsed = replay(row['raw'], row['text'], row.get('output_mode', 'full_json'))
            require(parsed == row['parsed'] and evaluate(case, parsed) == row['result'],
                    'Saved response differs: ' + row['case_id'])
            variants[0][1].append((row, parsed))
    return variants


def project_case(row, parsed):
    expected = row['expected_draft']
    gold_positive = {i['field'] for i in expected['items'] if i['field'] in LABELS}
    # Human-review/ambiguous gold entries do not establish that other labels are absent.
    gold_uncertain = any(r['reason'] != 'non_requirement' for r in expected['needs_review'])
    ok = valid(parsed)
    obj = parsed['value'] if ok else None
    predicted_positive = {i['field'] for i in obj['items'] if i['field'] in LABELS} if obj else set()
    prediction_uncertain = not ok or any(r['reason'] != 'non_requirement' for r in obj['needs_review'])
    cells = []
    for label in LABELS:
        gold = 1 if label in gold_positive else None if gold_uncertain else 0
        prediction = 1 if label in predicted_positive else None if prediction_uncertain else 0
        cells.append(dict(case_id=row['case_id'], label=label, truth=gold, prediction=prediction,
            gold_unknown=gold is None, prediction_unanswered=prediction is None,
            known_correct=(gold == prediction) if gold is not None and prediction is not None else False,
            parsed_valid=ok))
    return cells


def score(cells):
    labels = []
    for label in LABELS:
        known = [c for c in cells if c['label'] == label and c['truth'] is not None]
        tp = sum(c['truth'] == 1 and c['prediction'] == 1 for c in known)
        tn = sum(c['truth'] == 0 and c['prediction'] == 0 for c in known)
        fp = sum(c['truth'] == 0 and c['prediction'] == 1 for c in known)
        fn = sum(c['truth'] == 1 and c['prediction'] == 0 for c in known)
        unpos = sum(c['truth'] == 1 and c['prediction'] is None for c in known)
        unneg = sum(c['truth'] == 0 and c['prediction'] is None for c in known)
        gold_pos = tp + fn + unpos
        # A positive abstention counts as a missed positive for this conservative F1.
        denominator = 2 * tp + fp + fn + unpos
        f1 = 2 * tp / denominator if denominator else 0.0
        labels.append(dict(label=label, known=len(known), gold_positive=gold_pos,
            gold_negative=tn + fp + unneg, gold_unknown=sum(c['truth'] is None for c in cells if c['label'] == label),
            tp=tp, tn=tn, fp=fp, fn=fn, unanswered_positive=unpos, unanswered_negative=unneg,
            coverage=safe_ratio(tp + tn + fp + fn, len(known)),
            label_accuracy=safe_ratio(tp + tn, len(known)),
            covered_accuracy=safe_ratio(tp + tn, tp + tn + fp + fn),
            precision=safe_ratio(tp, tp + fp), recall=safe_ratio(tp, gold_pos), f1=f1,
            has_gold_positive=bool(gold_pos)))
    known = sum(r['known'] for r in labels)
    answered = sum(r['tp'] + r['tn'] + r['fp'] + r['fn'] for r in labels)
    correct = sum(r['tp'] + r['tn'] for r in labels)
    f1_values = [r['f1'] for r in labels if r['has_gold_positive']]
    total = dict(known_cells=known, gold_unknown_cells=sum(r['gold_unknown'] for r in labels),
        correct=correct, unanswered_positive=sum(r['unanswered_positive'] for r in labels),
        unanswered_negative=sum(r['unanswered_negative'] for r in labels),
        label_accuracy=safe_ratio(correct, known), coverage=safe_ratio(answered, known),
        covered_accuracy=safe_ratio(correct, answered),
        macro_f1_all_8=sum(r['f1'] for r in labels) / len(labels),
        macro_f1_supported_labels=sum(f1_values) / len(f1_values) if f1_values else None,
        macro_f1_included_labels=len(f1_values),
        macro_f1_excluded_labels=[r['label'] for r in labels if not r['has_gold_positive']])
    return total, labels


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', nargs='+', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    args = p.parse_args()
    runs = [r.expanduser().resolve() for r in args.run_dir]
    out = args.out.expanduser().resolve()
    require(len(set(runs)) == len(runs) and all(not out.is_relative_to(r) and not r.is_relative_to(out) for r in runs),
            'Run paths must be unique and output outside source runs')
    require(not out.exists(), 'Use a new output directory')
    evidence, summaries, per_label, all_cells, reference = {}, [], [], [], None
    for index, run in enumerate(runs):
        protocol, is_retry, rows, hashes = check_evidence(run)
        evidence.update(hashes)
        identity = {r['case_id']: dict(text=r['text'], expected=r['expected_draft']) for r in rows}
        if reference is None:
            reference = identity
        require(identity == reference, 'Runs have different case IDs, text or draft labels')
        name = run.name
        for variant, variants in variant_rows(run, rows, is_retry):
            cells = [cell for row, parsed in variants for cell in project_case(row, parsed)]
            total, labels = score(cells)
            summaries.append(dict(run=name, variant=variant, cases=len(variants), **total))
            per_label.extend(dict(run=name, variant=variant, **r) for r in labels)
            all_cells.extend(dict(run=name, variant=variant, **c) for c in cells)
    require(all(sha(Path(path)) == h for path, h in evidence.items()), 'Evidence changed while reading')
    out.mkdir(parents=True, exist_ok=False)
    # Lists in summary are written as JSON so their meaning is unambiguous in CSV.
    write_csv(out / 'comparison.csv', [dict(r, macro_f1_excluded_labels=json.dumps(r['macro_f1_excluded_labels']))
                                       for r in summaries])
    write_csv(out / 'per_label.csv', per_label)
    write_csv(out / 'per_case_label.csv', all_cells)
    save(out / 'summary.json', dict(results=summaries, labels=LABELS, source_hashes=evidence,
        synthetic=True, human_verified=False, training_executed=False, inference_executed=False,
        policy=dict(task='Qwen text requirement fields; not image visual presence',
            positive='Any extracted item in the label, including forbidden and optional',
            negative='No such item and no unresolved review in that output',
            review='non_requirement represents no requirement; other review reasons mask non-positive gold labels '
                   'and leave non-positive prediction labels unanswered',
            invalid='Invalid JSON/schema/source means all eight prediction labels are unanswered',
            accuracy='(TP+TN) / known gold cells; unanswered predictions count as incorrect',
            macro_f1='macro_f1_all_8 averages all eight F1s, with zero denominator set to 0. '
                     'macro_f1_supported_labels averages only labels with a known gold positive; '
                     'excluded labels are listed. Positive unanswered counts as FN.',
            covered_accuracy='Correct / answered known cells, shown with coverage',
            limitation='Field presence only: target, value, count, polarity, text, review reason and duplicate items '
                       'are not evaluated. AI-authored draft labels and previously exposed cases are not independent gold.')))
    print(json.dumps(dict(out=str(out), results=summaries, inference_executed=False,
                          training_executed=False), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
