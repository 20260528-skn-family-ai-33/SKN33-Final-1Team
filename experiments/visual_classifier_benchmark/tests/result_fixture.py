"""Synthetic completed runs; these are never presented as measured GPU results."""
from pathlib import Path
import shutil
import struct
import zlib

from resultlib import (LABELS, RUNS, digest, save, sha, score_rows, dev_thresholds,
                       rows_with_probabilities, error_changes, write_csv)


def npy(path, probabilities):
    header = repr(dict(descr='<f4', fortran_order=False, shape=(len(probabilities), 8)))
    padding = 64 - ((10 + len(header) + 1) % 64)
    data = (header + ' ' * padding + '\n').encode('latin1')
    with path.open('wb') as stream:
        stream.write(b'\x93NUMPY\x01\x00' + struct.pack('<H', len(data)) + data)
        for row in probabilities:
            stream.write(struct.pack('<8f', *row))


def fixture(root, improved=True):
    root = Path(root); runs = root / 'remote/runs'; bundle = root / 'remote/bundle'
    bundle.mkdir(parents=True)
    groups = {}; ids = []
    for split, n, unknown in (('train', 113, 43), ('dev', 37, 13), ('test', 37, 14)):
        rows = []
        for i in range(n):
            image_id = f'{split}{i:03}'
            labels = {k: (i + j) % 2 for j, k in enumerate(LABELS)}
            rows.append(dict(image_id=image_id, group_id=image_id, image=f'data/images/{image_id}.png', split=split, labels=labels))
            ids.append(dict(image_id=image_id, group_id=image_id, split=split, pixel_sha256=digest(image_id)))
        for i in range(unknown):
            rows[i % min(10, n)]['labels'][LABELS[(i // 10 + i) % 8]] = None
        # Guarantee the exact unknown-cell population, independent of duplicate positions.
        missing = sum(v is None for r in rows for v in r['labels'].values())
        for row in rows:
            for key in LABELS:
                if missing < unknown and row['labels'][key] is not None:
                    row['labels'][key] = None; missing += 1
        groups[split] = rows
    save(bundle / 'data/train_dev.json', groups['train'] + groups['dev'])
    save(bundle / 'data/test.json', groups['test']); save(bundle / 'data/split_ids.json', ids)
    save(bundle / 'data/taxonomy.json', dict(labels=LABELS, synthetic_test_fixture=True))
    save(bundle / 'data/dataset_info.json', dict(labels=LABELS, approved_images=187, approval_method='explicit_user_chat_approval',
         split_counts=dict(train=113, dev=37, test=37), known_cells=dict(train=861, dev=283, test=282),
         unknown_cells=dict(train=43, dev=13, test=14), previously_exposed_test=True, synthetic_test_fixture=True,
         approval_qualification='합성 테스트 데이터 · 실제 GPU 성능 아님'))
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 24, 32, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress((b'\x00' + bytes((169, 185, 152)) * 24) * 32)) + chunk(b'IEND', b''))
    for row in groups['test']:
        path = bundle / row['image']; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(png)
    save(bundle / 'bundle_manifest.json', dict(schema='runpod-poster-bundle-v1', files={p.relative_to(bundle).as_posix(): dict(bytes=p.stat().st_size, sha256=sha(p)) for p in bundle.rglob('*') if p.is_file()}))
    environment = dict(GPU='SYNTHETIC CPU FIXTURE', precision='fp32', versions={})
    sources = Path(__file__).resolve().parents[2]
    code = {name: sha(sources / 'visual_classifier_benchmark' / name) for name in ('encoders.py', 'run_experiment.py', 'finetune_siglip.py', 'finetune_siglip_focused.py')}
    code['poster_benchmark/benchlib.py'] = sha(sources / 'poster_benchmark/benchlib.py')
    config = dict(environment=environment, precision='fp32', repeats=2, warmups=1, script_hashes=code,
                  bundle_manifest_sha256=sha(bundle / 'bundle_manifest.json'))
    for run in RUNS:
        (runs / run).mkdir(parents=True)
        save(runs / run / 'environment.json', environment)
    def probabilities(rows):
        # Exactly representable values so the npy and JSON probabilities agree.
        return [[.75 if r['labels'][k] == 1 else .25 for k in LABELS] for r in rows]
    dev_p = probabilities(groups['dev'])
    if improved:
        pos = next(i for i, r in enumerate(groups['dev']) if r['labels']['musical_instrument'] == 1)
        neg = next(i for i, r in enumerate(groups['dev']) if r['labels']['musical_instrument'] == 0)
        dev_p[pos][6] = .375; dev_p[neg][6] = .4375
    cuts = dev_thresholds(groups['dev'], dev_p)
    old_spec = dict(id='lr1e-05-none', backbone_lr=1e-5, positive_weight='none')
    new_spec = dict(id='lr1e-06-mild', backbone_lr=1e-6, positive_boost=2., negative_boost=2.)
    def candidate(run, spec, p, source=None):
        folder = runs / run / 'candidates' / spec['id']; folder.mkdir(parents=True)
        (folder / 'best.safetensors').write_bytes(b'SYNTHETIC CHECKPOINT - NOT A REAL MODEL')
        npy(folder / 'dev_probabilities.npy', p)
        thresholds = dev_thresholds(groups['dev'], p)
        metrics, counts = score_rows(rows_with_probabilities(groups['dev'], p, thresholds), thresholds)
        history = [dict(epoch=1, train_loss=.5, known_cells=861, optimizer_steps=15, dev_macro_AP=metrics['macro_average_precision'], elapsed_seconds=1.)]
        save(folder / 'history.json', history)
        result = dict(**spec, best_epoch=1, trained_epochs=1, thresholds=thresholds, dev_metrics=metrics,
                      parameter_groups_changed={'synthetic': True}, gradient_audit={'synthetic': 1.},
                      checkpoint='candidates/' + spec['id'] + '/best.safetensors',
                      artifact_hashes={n: sha(folder / n) for n in ('best.safetensors', 'history.json', 'dev_probabilities.npy')})
        if source:
            result['source'] = source
            variants = []
            for policy, focused in (('dev_F1', False), ('dev_target_Fbeta', True)):
                ts = dev_thresholds(groups['dev'], p, focused)
                score, confusion = score_rows(rows_with_probabilities(groups['dev'], p, ts), ts)
                variants.append(dict(**{k: v for k, v in result.items() if k not in ('thresholds', 'dev_metrics')},
                                     threshold_policy=policy, thresholds=ts, dev_metrics=score, dev_confusion=confusion))
            result['variants'] = variants
        save(folder / 'candidate_complete.json', result)
        return result
    def freeze(run, configuration, selection):
        signature = digest(configuration)
        save(runs / run / 'protocol.json', dict(config=configuration, signature=signature))
        selection = dict(**selection, protocol_signature=signature, test_used_for_selection=False)
        save(runs / run / 'selection_frozen.json', selection)
        save(runs / run / 'selection_commit.json', dict(sha256=sha(runs / run / 'selection_frozen.json')))
        return selection
    base_models = {}
    for name in ('siglip2', 'convnextv2'):
        folder = runs / RUNS[0] / name; folder.mkdir()
        save(folder / 'head_selection.json', dict(thresholds=[.5] * 8))
        base_models[name] = dict(thresholds=[.5] * 8, artifact_hashes={'head_selection.json': sha(folder / 'head_selection.json')})
    freeze(RUNS[0], config, dict(models=base_models))
    full_config = dict(config, candidates=[old_spec], baseline_protocol_sha256=sha(runs / RUNS[0] / 'protocol.json'),
                       baseline_selection_sha256=sha(runs / RUNS[0] / 'selection_frozen.json'), bundle_sha256=sha(bundle / 'bundle_manifest.json'))
    full = candidate(RUNS[1], old_spec, dev_p)
    full = freeze(RUNS[1], full_config, full)
    reference_folder = runs / RUNS[2] / 'candidates/prior'; reference_folder.mkdir(parents=True)
    for name in ('best.safetensors', 'dev_probabilities.npy'):
        shutil.copyfile(runs / RUNS[1] / full['checkpoint'] if name == 'best.safetensors' else (runs / RUNS[1] / full['checkpoint']).parent / name, reference_folder / name)
    score, counts = score_rows(rows_with_probabilities(groups['dev'], dev_p, cuts), cuts)
    reference = dict(id='prior', source='prior', best_epoch=1, backbone_lr=0., threshold_policy='original', thresholds=cuts,
                     dev_metrics=score, dev_confusion=counts, checkpoint='candidates/prior/best.safetensors',
                     artifact_hashes={n: sha(reference_folder / n) for n in ('best.safetensors', 'dev_probabilities.npy')})
    save(reference_folder / 'candidate_complete.json', reference)
    new = candidate(RUNS[2], new_spec, probabilities(groups['dev']), 'trained')
    chosen = new['variants'][0] if improved else reference
    focused_config = dict(config, candidates=[new_spec], baseline_protocol_sha256=sha(runs / RUNS[0] / 'protocol.json'),
                          prior_protocol_sha256=sha(runs / RUNS[1] / 'protocol.json'), prior_selection_sha256=sha(runs / RUNS[1] / 'selection_frozen.json'), bundle_sha256=sha(bundle / 'bundle_manifest.json'))
    focused = freeze(RUNS[2], focused_config, chosen)
    old_test = probabilities(groups['test'])
    if improved:
        idx = next(i for i, r in enumerate(groups['test']) if r['labels']['musical_instrument'] == 1)
        old_test[idx][6] = .125
    results = {}
    for run, specs in ((RUNS[0], [('siglip2', [.5] * 8), ('convnextv2', [.5] * 8)]),
                       (RUNS[1], [('siglip2_frozen', [.5] * 8), ('siglip2_full_finetune', cuts)]),
                       (RUNS[2], [('siglip2_full_finetune', cuts), ('siglip2_focused', chosen['thresholds'])])):
        results[run] = {}
        for name, ts in specs:
            folder = runs / run / name; folder.mkdir(exist_ok=True)
            p = probabilities(groups['test']) if improved and name == 'siglip2_focused' else old_test
            rows = rows_with_probabilities(groups['test'], p, ts)
            metric, _ = score_rows(rows, ts)
            timing = {stage: dict(n=74, mean_ms=value, median_ms=value, p95_ms=value) for stage, value in (
                ('decode_preprocess_ms', 12.), ('transfer_model_head_ms', 24.), ('total_ms', 36.))}
            save(folder / 'test_predictions.json', rows); save(folder / 'test_metrics.json', metric); save(folder / 'timing_summary.json', timing)
            write_csv(folder / 'timing_raw.csv', [dict(image_id=r['image_id'], repeat=n, decode_preprocess_ms=12., transfer_model_head_ms=24., total_ms=36.) for n in range(2) for r in rows])
            result = dict(metrics=metric, timing=timing, goal=dict(all_met=False))
            save(folder / ('evaluation_complete.json' if run == RUNS[0] else 'complete.json'), dict(result=result,
                 selection_sha256=sha(runs / run / 'selection_frozen.json'), artifact_hashes={n: sha(folder / n) for n in ('test_predictions.json', 'test_metrics.json', 'timing_summary.json', 'timing_raw.csv')}))
            results[run][name] = result
        save(runs / run / 'comparison_summary.json', dict(results=results[run], selected_source=focused['source'], threshold_policy=focused['threshold_policy'], goal_met=True))
        save(runs / run / 'status.json', dict(status='complete', protocol_signature=read_signature(runs / run / 'protocol.json')))
    a = rows_with_probabilities(groups['test'], old_test, cuts)
    b = rows_with_probabilities(groups['test'], probabilities(groups['test']) if improved else old_test, chosen['thresholds'])
    save(runs / RUNS[2] / 'error_changes.json', error_changes(a, b))
    return runs, bundle


def read_signature(path):
    from resultlib import read
    return read(path)['signature']
