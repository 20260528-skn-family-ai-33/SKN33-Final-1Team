"""Audit saved SigLIP2 probabilities and conservatively replay dev-only thresholds.

Standard library only. No model imports, learning, inference or installation.
"""
from pathlib import Path
from html import escape
import argparse
import fcntl
import math
import shutil
import sys
import traceback

import resultlib as results
from prepare_partial_data import base_data
from benchlib import (LABELS, contained, digest, load_test, log, read, save, sha)

HERE = Path(__file__).resolve().parent
DEV_OUTPUTS = ('dev_threshold_diagnosis.csv', 'dev_threshold_sweep.csv', 'dev_errors.csv',
               'ranking_conflicts.csv', 'dev_review.html', 'dev_summary.json')
TEST_OUTPUTS = ('comparison.csv', 'per_label_comparison.csv', 'error_changes.json', 'comparison_summary.json')
require = results.require


def counts(rows, probabilities, j, cutoff):
    values = [(row['labels'][LABELS[j]], p[j]) for row, p in zip(rows, probabilities)
              if row['labels'][LABELS[j]] is not None]
    tp = sum(y == 1 and p >= cutoff for y, p in values)
    fp = sum(y == 0 and p >= cutoff for y, p in values)
    fn = sum(y == 1 and p < cutoff for y, p in values)
    tn = sum(y == 0 and p < cutoff for y, p in values)
    n = len(values)
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, errors=fp + fn,
        accuracy=(tp + tn) / n if n else 0.,
        precision=tp / (tp + fp) if tp + fp else 0., recall=tp / (tp + fn) if tp + fn else 0.,
        f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.)


def allowed(candidate, baseline, args):
    return (candidate['f1'] + 1e-12 >= baseline['f1'] - args.max_f1_drop
            and candidate['recall'] + 1e-12 >= baseline['recall'] - args.max_recall_drop)


def threshold_choice(rows, probabilities, j, original, args):
    baseline = counts(rows, probabilities, j, original)
    known = {r['labels'][LABELS[j]] for r in rows if r['labels'][LABELS[j]] is not None}
    if known != {0, 1}:
        return original
    grid = sorted({original, *[i / 20 for i in range(21) if abs(i / 20 - original) <= args.max_move + 1e-12]})
    candidates = [(t, counts(rows, probabilities, j, t)) for t in grid]
    candidates = [(t, c) for t, c in candidates if allowed(c, baseline, args)]
    return max(candidates, key=lambda item: (item[1]['accuracy'], item[1]['f1'],
        -abs(item[0] - original), item[0] == original, -item[0]))[0]


def input_data(args):
    _, _, rows = base_data(args.bundle)
    dev = [row for row in rows if row['split'] == 'dev']
    protocol, status = read(args.prior_run / 'protocol.json'), read(args.prior_run / 'status.json')
    selected_path = args.prior_run / 'selection_frozen.json'
    selected = read(selected_path)
    require(status['status'] == 'complete' and protocol['signature'] == digest(protocol['config'])
            and status['protocol_signature'] == selected['protocol_signature'] == protocol['signature'],
            'Completed unchanged SigLIP2 full fine-tuning run required')
    require(read(args.prior_run / 'selection_commit.json')['sha256'] == sha(selected_path), 'Original selection changed')
    require(protocol['config']['bundle_sha256'] == sha(args.bundle / 'bundle_manifest.json'), 'Original dataset changed')
    for name, checksum in protocol['config']['script_hashes'].items():
        require(sha(contained(HERE, name)) == checksum, 'Original experiment source changed: ' + name)
    folder = contained(args.prior_run, selected['checkpoint']).parent
    for name, checksum in selected['artifact_hashes'].items():
        require(sha(contained(folder, name)) == checksum, 'Original selected artifact changed: ' + name)
    probabilities = results.load_npy(folder / 'dev_probabilities.npy')
    require(len(dev) == len(probabilities) == 37, 'Expected saved probabilities for the original37 dev images')
    cutoffs = selected['thresholds']
    scored, _ = results.score_rows(results.rows_with_probabilities(dev, probabilities, cutoffs), cutoffs)
    results.compare_metrics(selected['dev_metrics'], scored, 'reproduced_dev')
    return dev, probabilities, selected


def diagnose(args, dev, probabilities, prior):
    original = prior['thresholds']
    proposed, diagnosis, sweep, conflicts, errors = [], [], [], [], []
    groups = sorted({row['group_id'] for row in dev})
    require(len(groups) >= 2, 'Multiple development groups required for deletion sensitivity')
    for j, label in enumerate(LABELS):
        baseline = counts(dev, probabilities, j, original[j])
        full_cut = threshold_choice(dev, probabilities, j, original[j], args)
        full = counts(dev, probabilities, j, full_cut)
        fold_cuts, held_probabilities = [], [None] * len(dev)
        # This is sensitivity analysis, not independent cross-validation: the model
        # and the original cutoffs were already selected with the whole dev set.
        for group in groups:
            fit_indices = [i for i, row in enumerate(dev) if row['group_id'] != group]
            fit_rows, fit_p = [dev[i] for i in fit_indices], [probabilities[i] for i in fit_indices]
            cutoff = threshold_choice(fit_rows, fit_p, j, original[j], args)
            fold_cuts.append(cutoff)
            for i, row in enumerate(dev):
                if row['group_id'] == group:
                    held_probabilities[i] = [float(probabilities[i][j] >= cutoff)] * len(LABELS)
        require(all(p is not None for p in held_probabilities), 'Missing held-group decision')
        sensitivity = counts(dev, held_probabilities, j, .5)
        stable_fraction = sum(abs(t - full_cut) <= .05 + 1e-12 for t in fold_cuts) / len(fold_cuts)
        accepted = (full_cut != original[j] and full['errors'] < baseline['errors']
                    and sensitivity['errors'] < baseline['errors']
                    and allowed(full, baseline, args) and allowed(sensitivity, baseline, args)
                    and stable_fraction >= args.min_stability)
        proposed.append(full_cut if accepted else original[j])
        positive = [(row, p[j]) for row, p in zip(dev, probabilities) if row['labels'][label] == 1]
        negative = [(row, p[j]) for row, p in zip(dev, probabilities) if row['labels'][label] == 0]
        require(positive and negative, 'Both known dev classes required: ' + label)
        inverted = [(neg_p - pos_p, pos_row, pos_p, neg_row, neg_p)
                    for pos_row, pos_p in positive for neg_row, neg_p in negative if neg_p >= pos_p]
        inverted.sort(key=lambda v: (-v[0], v[1]['image_id'], v[3]['image_id']))
        for margin, pos_row, pos_p, neg_row, neg_p in inverted[:10]:
            conflicts.append(dict(label=label, positive_image_id=pos_row['image_id'], positive_probability=pos_p,
                negative_image_id=neg_row['image_id'], negative_probability=neg_p, probability_margin=margin,
                positive_image=pos_row['image'], negative_image=neg_row['image']))
        # Optimistic empirical bound for a scalar cutoff in [0,1], not a proposed cutoff.
        exact_grid = {0., 1., *[p[j] for p in probabilities]}
        exact_grid.update(math.nextafter(p[j], math.inf) for p in probabilities if p[j] < 1.)
        best_exact = min(counts(dev, probabilities, j, t)['errors'] for t in exact_grid)
        for t in sorted({original[j], *[i / 20 for i in range(21)]}):
            sweep.append(dict(label=label, threshold=t, **counts(dev, probabilities, j, t)))
        diagnosis.append(dict(label=label, dev_AP=prior['dev_metrics']['per_label'][label]['average_precision'],
            original_threshold=original[j], original_fp=baseline['fp'], original_fn=baseline['fn'],
            full_dev_accuracy_candidate=full_cut, candidate_fp=full['fp'], candidate_fn=full['fn'],
            applied_threshold=proposed[-1], applied_change=accepted,
            deletion_sensitivity_fp=sensitivity['fp'], deletion_sensitivity_fn=sensitivity['fn'],
            stable_fraction=stable_fraction, fold_threshold_min=min(fold_cuts), fold_threshold_max=max(fold_cuts),
            optimistic_min_dev_errors=best_exact, nonseparable_positive_negative_pairs=len(inverted),
            known_positive=len(positive), known_negative=len(negative)))
        for row, p in zip(dev, probabilities):
            y = row['labels'][label]
            if y is not None and int(p[j] >= original[j]) != y:
                errors.append(dict(image_id=row['image_id'], label=label, truth=y, probability=p[j],
                    threshold=original[j], error='FN' if y else 'FP', image=row['image'], group_id=row['group_id']))
    for name, rows in (('dev_threshold_diagnosis.csv', diagnosis), ('dev_threshold_sweep.csv', sweep),
                       ('ranking_conflicts.csv', conflicts), ('dev_errors.csv', errors)):
        fields = list(rows[0]) if rows else (['label', 'positive_image_id', 'positive_probability', 'negative_image_id',
            'negative_probability', 'probability_margin', 'positive_image', 'negative_image']
            if name == 'ranking_conflicts.csv' else ['image_id', 'label', 'truth', 'probability', 'threshold', 'error', 'image', 'group_id'])
        results.write_csv(args.out / name, rows, fields)
    make_gallery(args, dev, errors, conflicts)
    prior_metrics, _ = results.score_rows(results.rows_with_probabilities(dev, probabilities, original), original)
    new_metrics, _ = results.score_rows(results.rows_with_probabilities(dev, probabilities, proposed), proposed)
    summary = dict(original_dev_metrics=prior_metrics, proposed_dev_metrics=new_metrics,
        changed_labels=[label for j, label in enumerate(LABELS) if proposed[j] != original[j]],
        optimistic_scalar_threshold_min_dev_errors=sum(r['optimistic_min_dev_errors'] for r in diagnosis),
        sensitivity_is_independent_validation=False, test_used_for_selection=False,
        note='Development-only descriptive diagnostics; both the model and original thresholds already used this dev set. '
             'Ranking pairs cannot both be classified correctly by one scalar threshold. The empirical bound is optimistic.')
    save(args.out / 'dev_summary.json', summary)
    return proposed, summary


def make_gallery(args, dev, errors, conflicts):
    image_dir = args.out / 'images'
    image_dir.mkdir(exist_ok=True)
    image_map = {}
    for row in dev:
        source = contained(args.bundle, row['image'])
        name = digest(row['image_id'])[:20] + source.suffix.lower()
        shutil.copyfile(source, image_dir / name)
        image_map[row['image_id']] = 'images/' + name
    by_id = {}
    for error in errors:
        by_id.setdefault(error['image_id'], []).append(error)
    cards = []
    for image_id, items in by_id.items():
        text = ' / '.join(f"{r['label']} {r['error']}: p={r['probability']:.4f}, t={r['threshold']:.2f}" for r in items)
        cards.append('<article><h2>' + escape(image_id) + '</h2><img src="' + escape(image_map[image_id], quote=True)
            + '"><p>' + escape(text) + '</p></article>')
    pairs = []
    for row in conflicts:
        cells = []
        for prefix, truth in (('positive', 1), ('negative', 0)):
            image_id = row[prefix + '_image_id']
            cells.append('<td>' + escape(f"{image_id}: truth={truth}, p={row[prefix + '_probability']:.4f}")
                + '<br><img src="' + escape(image_map[image_id], quote=True) + '"></td>')
        pairs.append('<tr><th>' + escape(row['label']) + '</th>' + ''.join(cells) + '</tr>')
    html = ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>SigLIP2 개발 오류·순위 진단</title><style>body{font:15px/1.6 system-ui;margin:24px;background:#f5f6f7;color:#203344}'
        '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}article,td,th{padding:14px;background:white}'
        'img{width:100%;max-height:420px;object-fit:contain}p{overflow-wrap:anywhere}table{width:100%;table-layout:fixed}th{width:15%}</style>'
        '<h1>개발셋 오류와 양성·음성 순위 충돌</h1><p>기존 정답 기준의 오류입니다. 원본 이미지를 복사했으며 라벨을 자동 수정하지 않습니다. '
        '평가 이미지는 포함하지 않습니다. 아래 쌍은 양성 확률≤음성 확률이므로 단일 임계값으로 둘을 동시에 맞힐 수 없습니다.</p>'
        '<div class="grid">' + ''.join(cards) + '</div><h2>라벨별 확률 차이가 큰 충돌 쌍 최대10개</h2><table>'
        + ''.join(pairs) + '</table></html>')
    (args.out / 'dev_review.html').write_text(html, encoding='utf-8')


def verify_selection(args, signature):
    path = args.out / 'selection_frozen.json'
    selected = read(path)
    require(selected['protocol_signature'] == signature
            and read(args.out / 'selection_commit.json')['sha256'] == sha(path), 'Diagnostic selection changed')
    for name, checksum in selected['dev_artifact_hashes'].items():
        require(sha(contained(args.out, name)) == checksum, 'Development diagnostic artifact changed: ' + name)
    return selected


def evaluate_saved(args, selection):
    # First semantic test access in this audit occurs only after dev choices freeze.
    truth = load_test(args.bundle, args.out / 'selection_frozen.json', sha(args.out / 'selection_frozen.json'))
    folder = args.prior_run / 'siglip2_full_finetune'
    done = read(folder / 'complete.json')
    require(done['selection_sha256'] == sha(args.prior_run / 'selection_frozen.json'), 'Original test selection changed')
    for name, checksum in done['artifact_hashes'].items():
        require(sha(contained(folder, name)) == checksum, 'Original evaluation changed: ' + name)
    old = read(folder / 'test_predictions.json')
    require(len(old) == len(truth) and all(a['image_id'] == b['image_id'] and a['labels'] == b['labels']
            for a, b in zip(old, truth)), 'Original test population/labels changed')
    old_metrics, old_counts = results.score_rows(old, selection['original_thresholds'])
    results.compare_metrics(read(folder / 'test_metrics.json'), old_metrics, 'original_test')
    new = results.rows_with_probabilities(truth, [r['probabilities'] for r in old], selection['thresholds'])
    new_metrics, new_counts = results.score_rows(new, selection['thresholds'])
    require(old_metrics['macro_average_precision'] == new_metrics['macro_average_precision'], 'Threshold replay must preserve AP')
    results.write_csv(args.out / 'comparison.csv', [dict(policy=name, accuracy=m['label_wise_accuracy'],
        macro_f1=m['macro_f1'], macro_AP=m['macro_average_precision'], fp=sum(c['fp'] for c in counts_.values()),
        fn=sum(c['fn'] for c in counts_.values()), accuracy_90=m['label_wise_accuracy'] >= .9,
        latency_remeasured=False) for name, m, counts_ in
        (('original_dev_F1', old_metrics, old_counts), ('guarded_dev_accuracy', new_metrics, new_counts))])
    rows, changes = [], []
    for j, label in enumerate(LABELS):
        rows.append(dict(label=label, original_threshold=selection['original_thresholds'][j], threshold=selection['thresholds'][j],
            **{'prior_' + k: v for k, v in old_counts[label].items()},
            **{'candidate_' + k: v for k, v in new_counts[label].items()},
            fp_delta=new_counts[label]['fp'] - old_counts[label]['fp'], fn_delta=new_counts[label]['fn'] - old_counts[label]['fn']))
        for a, b in zip(old, new):
            y = a['labels'][label]
            if y is not None and a['predictions'][j] != b['predictions'][j]:
                changes.append(dict(image_id=a['image_id'], label=label, truth=y,
                    prior_prediction=a['predictions'][j], candidate_prediction=b['predictions'][j],
                    change='recovered' if b['predictions'][j] == y else 'regressed'))
    results.write_csv(args.out / 'per_label_comparison.csv', rows)
    save(args.out / 'error_changes.json', changes)
    summary = dict(original_test_metrics=old_metrics, replay_test_metrics=new_metrics,
        changed_labels=selection['changed_labels'], test_used_for_selection=False, previously_exposed_test=True,
        training_executed=False, model_inference_executed=False, latency_remeasured=False,
        prior_timing_context=dict(source=str(folder / 'timing_summary.json'),
            p95_ms=read(folder / 'timing_summary.json')['total_ms']['p95_ms'], newly_measured=False),
        note='Replays saved probabilities only. AP and backbone are unchanged. No new CUDA or p95 measurement. '
             'Same exposed37 test; not independent generalization. Threshold changes are experimental, not deployed.')
    save(args.out / 'comparison_summary.json', summary)


def execute(args):
    dev, probabilities, prior = input_data(args)
    sources = ('audit_siglip_thresholds.py', 'run_siglip_threshold_audit.sh', 'resultlib.py', 'prepare_partial_data.py')
    config = dict(prior_run=str(args.prior_run), bundle=str(args.bundle),
        prior_protocol_sha256=sha(args.prior_run / 'protocol.json'), prior_selection_sha256=sha(args.prior_run / 'selection_frozen.json'),
        bundle_sha256=sha(args.bundle / 'bundle_manifest.json'), max_move=args.max_move, max_f1_drop=args.max_f1_drop,
        max_recall_drop=args.max_recall_drop, min_stability=args.min_stability,
        policy='dev_accuracy_with_F1_recall_guards_and_leave_group_out_sensitivity',
        sources={name: sha(HERE / name) for name in sources}, benchlib_sha256=sha(HERE.parent / 'poster_benchmark/benchlib.py'))
    signature = digest(config)
    require(not args.out.is_relative_to(args.prior_run) and not args.out.is_relative_to(args.bundle), 'Output must be separate from inputs')
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = args.out / 'protocol.json'
        if protocol_path.exists():
            previous = read(protocol_path)
            require(args.resume and previous['signature'] == signature and digest(previous['config']) == signature,
                    'Existing output requires --resume with unchanged inputs/code/settings; use a new --out for changes')
        else:
            require(not any(p.name != '.lock' for p in args.out.iterdir()), 'Output directory must be empty')
            save(protocol_path, dict(signature=signature, config=config))
        status_path = args.out / 'status.json'
        if status_path.exists() and read(status_path)['status'] == 'complete':
            verify_selection(args, signature)
            for name, checksum in read(status_path)['output_hashes'].items():
                require(sha(args.out / name) == checksum, 'Completed output changed: ' + name)
            print('Already complete: ' + str(args.out))
            return
        save(status_path, dict(status='running', protocol_signature=signature))
        try:
            if not (args.out / 'selection_frozen.json').exists():
                cutoffs, summary = diagnose(args, dev, probabilities, prior)
                hashes = {name: sha(args.out / name) for name in DEV_OUTPUTS}
                hashes.update({str(p.relative_to(args.out)): sha(p) for p in (args.out / 'images').iterdir()})
                save(args.out / 'selection_frozen.json', dict(protocol_signature=signature, thresholds=cutoffs,
                    original_thresholds=prior['thresholds'], changed_labels=summary['changed_labels'],
                    dev_artifact_hashes=hashes, test_used_for_selection=False))
                save(args.out / 'selection_commit.json', dict(sha256=sha(args.out / 'selection_frozen.json')))
                log(args.out, 'thresholds_frozen_before_saved_test_replay', changed_labels=summary['changed_labels'])
            selection = verify_selection(args, signature)
            evaluate_saved(args, selection)
            input_data(args)
            require(config['prior_protocol_sha256'] == sha(args.prior_run / 'protocol.json')
                    and config['prior_selection_sha256'] == sha(args.prior_run / 'selection_frozen.json')
                    and config['bundle_sha256'] == sha(args.bundle / 'bundle_manifest.json')
                    and config['benchlib_sha256'] == sha(HERE.parent / 'poster_benchmark/benchlib.py')
                    and all(sha(HERE / n) == h for n, h in config['sources'].items()),
                    'Input or source changed during audit')
            verify_selection(args, signature)
            save(status_path, dict(status='complete', protocol_signature=signature, training_executed=False,
                model_inference_executed=False, latency_remeasured=False,
                output_hashes={name: sha(args.out / name) for name in TEST_OUTPUTS}))
            log(args.out, 'threshold_audit_complete', output=str(args.out), changed_labels=selection['changed_labels'])
        except Exception as exc:
            save(args.out / 'failure.json', dict(reason=repr(exc), traceback=traceback.format_exc()))
            save(status_path, dict(status='failed', protocol_signature=signature))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'show-results'))
    parser.add_argument('--prior-run', type=Path, default=HERE / 'runs/siglip-full-fp32')
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--out', type=Path, default=HERE / 'reviews/siglip-threshold-audit-v1')
    parser.add_argument('--max-move', type=float, default=.20)
    parser.add_argument('--max-f1-drop', type=float, default=.02)
    parser.add_argument('--max-recall-drop', type=float, default=.05)
    parser.add_argument('--min-stability', type=float, default=.80)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    for name in ('prior_run', 'bundle', 'out'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in
           (args.max_move, args.max_f1_drop, args.max_recall_drop, args.min_stability)):
        parser.error('Policy settings must be finite values in [0,1]')
    if args.command == 'show-results':
        for name in ('status.json', 'dev_threshold_diagnosis.csv', 'comparison.csv', 'per_label_comparison.csv'):
            path = args.out / name
            print(path.read_text(encoding='utf-8-sig') if path.is_file() else 'Not ready: ' + str(path))
    else:
        execute(args)


if __name__ == '__main__':
    main()
