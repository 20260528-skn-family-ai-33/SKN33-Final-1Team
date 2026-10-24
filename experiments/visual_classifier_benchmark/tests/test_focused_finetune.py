"""Synthetic CPU checks; no downloads, CUDA training, or production data writes."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import copy
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import finetune_siglip_focused as focused
from encoders import Encoder
from benchlib import LABELS, read, save, sha, targets

torch.set_num_threads(1)


class Backbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.projection = nn.Linear(3, 5)

    def forward(self, x):
        return self.projection(x.mean(dim=(-1, -2)))


def tiny_models(folder, device, selection):
    return Encoder(Backbone(), 5, [.5] * 3, [.5] * 3, 32, device, 'fp32', 'tiny'), nn.Linear(5, 8)


def fixture(root):
    rows = []
    for i in range(14):
        Image.new('RGB', (24, 32), (20 + i * 12, 40, 200 - i * 10)).save(root / f'{i}.png')
        rows.append(dict(image_id=f'S{i}', image=f'{i}.png', split='train' if i < 10 else 'dev',
                         labels={key: (i + j) % 2 for j, key in enumerate(LABELS)}))
    rows[0]['labels']['food_drink'] = None
    args = SimpleNamespace(run_dir=root / 'run', model_dir=root, bundle=root, device='cpu',
                           prior_run=root / 'old', epochs=2, patience=2, microbatch=2)
    prior_folder = args.run_dir / 'candidates/prior'
    prior_folder.mkdir(parents=True)
    encoder, head = tiny_models(root, 'cpu', {})
    focused.ft.save_weights(prior_folder / 'best.safetensors', focused.ft.state(encoder, head))
    prob = focused.ft.predict_dev(encoder, head, root, rows[10:])
    focused.common.save_array(prior_folder / 'dev_probabilities.npy', prob)
    prior = dict(id='prior', source='prior', best_epoch=1, backbone_lr=0., threshold_policy='original',
                 thresholds=[.5] * 8, checkpoint='candidates/prior/best.safetensors',
                 dev_metrics=focused.metrics(targets(rows[10:]), prob, [.5] * 8),
                 dev_confusion=focused.confusion(targets(rows[10:]), prob, [.5] * 8),
                 artifact_hashes={n: sha(prior_folder / n) for n in ('best.safetensors', 'dev_probabilities.npy')})
    return args, rows, prior


class FocusedTests(unittest.TestCase):
    def test_hard_instrument_positive_and_target_negatives_have_larger_gradients(self):
        x = torch.tensor([[2., 2., 2., 2., 2., 2., -2., 2.]], requires_grad=True)
        y = torch.tensor([[0., 0., 0., 0., 0., 0., 1., 0.]])
        loss, known = focused.focused_loss(x, y, 2., 2.)
        loss.backward()
        plain_gradient = torch.sigmoid(x.detach()) - y
        for j in focused.FP_INDICES + (focused.INSTRUMENT,):
            self.assertGreater(abs(float(x.grad[0, j])), abs(float(plain_gradient[0, j])))
        for j in (0, 4, 5, 7):
            self.assertEqual(float(x.grad[0, j]), float(plain_gradient[0, j]))
        self.assertEqual(known, 8)

    def test_unknown_target_has_no_loss_or_gradient_and_boost_is_bounded(self):
        x = torch.tensor([[0., 4., -4., 2., 0., 0., -4., 0.]], requires_grad=True)
        y = torch.tensor([[1., float('nan'), 0., 1., 0., 0., 1., 0.]])
        loss, known = focused.focused_loss(x, y, 4., 4.)
        loss.backward()
        self.assertEqual(known, 7)
        self.assertEqual(float(x.grad[0, 1]), 0.)
        mask = torch.isfinite(y)
        plain = (torch.sigmoid(x.detach()) - torch.nan_to_num(y)).abs()
        self.assertTrue(torch.all(x.grad.abs()[mask] <= plain[mask] * 5 + 1e-7))
        with self.assertRaisesRegex(ValueError, 'no known'):
            focused.focused_loss(x, torch.full_like(y, float('nan')), 2., 2.)

    def test_zero_boost_is_masked_bce_and_invalid_boost_rejected(self):
        x = torch.randn(3, 8, requires_grad=True)
        y = torch.randint(0, 2, (3, 8)).float(); y[0, 6] = float('nan')
        a, n = focused.focused_loss(x, y, 0., 0.)
        b, nn = focused.ft.masked_loss(x, y, torch.ones(8))
        torch.testing.assert_close(a, b)
        self.assertEqual(n, nn)
        for value in (-1., float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                focused.focused_loss(x, y, value, 1.)

    def test_accumulated_weighted_loss_matches_full_batch_with_unknowns(self):
        torch.manual_seed(4)
        a, b = nn.Linear(3, 8), nn.Linear(3, 8)
        b.load_state_dict(a.state_dict())
        x = torch.randn(5, 3)
        y = torch.randint(0, 2, (5, 8)).float(); y[0, :3] = float('nan'); y[4, 6] = float('nan')
        known = int(torch.isfinite(y).sum())
        numerator, _ = focused.focused_loss(a(x), y, 2., 4.)
        (numerator / known).backward()
        for start in range(0, 5, 2):
            numerator, _ = focused.focused_loss(b(x[start:start+2]), y[start:start+2], 2., 4.)
            (numerator / known).backward()
        torch.testing.assert_close(a.weight.grad, b.weight.grad)
        torch.testing.assert_close(a.bias.grad, b.bias.grad)

    def test_fbeta_thresholds_prioritize_requested_directions_and_mask_unknowns(self):
        y = np.tile(np.array([1., 1., 0., 0., np.nan])[:, None], (1, 8))
        p = np.tile(np.array([.9, .4, .45, .1, .99])[:, None], (1, 8))
        cuts = focused.focused_thresholds(y, p)
        self.assertLessEqual(cuts[focused.INSTRUMENT], .4)
        for j in focused.FP_INDICES:
            self.assertGreater(cuts[j], .45)
        changed = p.copy(); changed[4] = .01
        self.assertEqual(cuts, focused.focused_thresholds(y, changed))
        with self.assertRaises(ValueError):
            focused.focused_thresholds(np.zeros_like(y), p)

    def test_dev_guard_falls_back_despite_favorable_test_accuracy(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, prior = fixture(Path(td))
            bad = copy.deepcopy(prior)
            bad.update(id='bad', source='trained', backbone_lr=1e-6, test_accuracy=1.)
            bad['dev_metrics']['macro_f1'] -= .01
            self.assertEqual(focused.choose([bad], prior)['id'], 'prior')
            bad = copy.deepcopy(prior); bad['id'] = 'more_target_errors'
            bad['dev_metrics']['label_wise_accuracy'] = 1.
            bad['dev_confusion']['musical_instrument']['fn'] += 1
            self.assertEqual(focused.choose([bad], prior)['id'], 'prior')
            good = copy.deepcopy(prior); good.update(id='good', source='trained', backbone_lr=1e-6)
            good['dev_metrics']['label_wise_accuracy'] += .01
            self.assertEqual(focused.choose([good], prior)['id'], 'good')

    def test_training_and_review_reject_dev_test_rows(self):
        with tempfile.TemporaryDirectory() as td:
            args, rows, _ = fixture(Path(td))
            with self.assertRaisesRegex(ValueError, 'Only training'):
                focused.training_epoch(None, None, None, args.bundle, rows[10:], {}, 2, 1)
            with self.assertRaisesRegex(ValueError, 'only be listed from train'):
                focused.train_review(rows[10:], np.ones((4, 8)), [.5] * 8, Path(td) / 'review.csv')
            leaked = copy.deepcopy(rows); leaked[-1]['split'] = 'test'
            with self.assertRaisesRegex(ValueError, 'Train/dev only'):
                focused.validate_rows(leaked)
            with self.assertRaisesRegex(ValueError, 'Train/dev only'):
                focused.candidate_train(args, {}, {}, {}, leaked, tiny_models)

    def test_real_cpu_training_updates_backbone_and_restores_exact_checkpoint(self):
        from safetensors.torch import load_file
        with tempfile.TemporaryDirectory() as td:
            args, rows, prior = fixture(Path(td))
            candidate = dict(id='tiny', backbone_lr=.001, positive_boost=2., negative_boost=2.)
            with mock.patch.object(focused, 'load_test', side_effect=AssertionError('test must stay closed')):
                result = focused.candidate_train(args, candidate, {}, prior, rows, tiny_models)
            self.assertTrue(all(result['parameter_groups_changed'].values()))
            self.assertGreater(result['gradient_audit']['classifier.weight'], 0.)
            self.assertEqual(result['trained_epochs'], 2)
            history = read(args.run_dir / 'candidates/tiny/history.json')
            self.assertEqual(history[0]['known_cells'], 79)
            self.assertEqual(history[0]['optimizer_steps'], 2)  # Partial accumulation group.
            encoder, head = tiny_models(args.model_dir, 'cpu', {})
            focused.ft.load_state(encoder, head, load_file(str(args.run_dir / result['checkpoint'])))
            actual = focused.ft.predict_dev(encoder, head, args.bundle, rows[10:])
            expected = np.load(args.run_dir / 'candidates/tiny/dev_probabilities.npy')
            np.testing.assert_array_equal(actual, expected)
            focused.verify_artifacts(args.run_dir, result)
            (args.run_dir / 'candidates/tiny/history.json').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'artifact changed'):
                focused.verify_artifacts(args.run_dir, result)

    def test_selection_freezes_before_evaluation_and_resume_skips_training(self):
        with tempfile.TemporaryDirectory() as td:
            args, rows, prior = fixture(Path(td))
            args.candidates = [dict(id='fake')]
            save(args.run_dir / 'candidates/prior/candidate_complete.json', prior)
            candidate = copy.deepcopy(prior)
            candidate.update(id='fake', source='trained', backbone_lr=1e-6)
            candidate['dev_metrics']['label_wise_accuracy'] += .01
            def trainer(*unused):
                path = args.run_dir / 'candidates/fake/candidate_complete.json'
                path.parent.mkdir()
                # Keep a self-contained candidate artifact in its own directory.
                weight = path.parent / 'best.safetensors'
                weight.write_bytes((args.run_dir / prior['checkpoint']).read_bytes())
                candidate.update(checkpoint='candidates/fake/best.safetensors', artifact_hashes={'best.safetensors': sha(weight)})
                result = {**candidate, 'variants': [candidate]}
                save(path, result)
                return result
            def evaluator(a, selected, reference, head):
                self.assertEqual(selected['id'], 'fake')
                self.assertEqual(read(a.run_dir / 'selection_commit.json')['sha256'], sha(a.run_dir / 'selection_frozen.json'))
                self.assertFalse(selected['test_used_for_selection'])
                return 'evaluated'
            with mock.patch.object(focused, 'load_test', side_effect=AssertionError('no training test access')):
                self.assertEqual(focused.execute(args, rows, {}, prior, 'sig', trainer, evaluator), 'evaluated')
                self.assertEqual(focused.execute(args, rows, {}, prior, 'sig', mock.Mock(side_effect=AssertionError('no retraining')), evaluator), 'evaluated')
            save(args.run_dir / 'selection_commit.json', {'sha256': 'tampered'})
            with self.assertRaisesRegex(ValueError, 'Frozen selection changed'):
                focused.execute(args, rows, {}, prior, 'sig', trainer, evaluator)

    def test_output_path_cannot_overwrite_previous_runs_or_inputs(self):
        args = SimpleNamespace(baseline=Path('/work/heads'), prior_run=Path('/work/prior'),
                               bundle=Path('/data/bundle'), model_dir=Path('/models/siglip'))
        for path in ('/work/prior', '/work/prior/sub', '/work', '/data/bundle/run', '/'):
            args.run_dir = Path(path)
            with self.assertRaisesRegex(ValueError, 'separate new run'):
                focused.validate_output(args)
        args.run_dir = Path('/work/focused')
        focused.validate_output(args)

    def test_cpu_pipeline_trains_compares_and_reuses_completed_evaluation(self):
        from safetensors.torch import load_file
        import encoders
        with tempfile.TemporaryDirectory() as td:
            args, rows, prior = fixture(Path(td))
            save(args.run_dir / 'candidates/prior/candidate_complete.json', prior)
            args.candidates = [dict(id='pipeline', backbone_lr=.001, positive_boost=2., negative_boost=2.)]
            args.warmups, args.repeats = 1, 2
            tests = [dict(r, split='test') for r in rows[10:]]
            encoder, head = tiny_models(args.model_dir, 'cpu', {})
            focused.ft.load_state(encoder, head, load_file(str(args.run_dir / prior['checkpoint'])))
            prior_eval = args.prior_run / 'siglip2_full_finetune'
            prior_eval.mkdir(parents=True)
            focused.common.benchmark(encoder, head, args.bundle, tests, np.asarray(prior['thresholds']), prior_eval, 1, 1)
            def loader(*unused):
                return tiny_models(args.model_dir, 'cpu', {})[0]
            def classifier(*unused):
                return nn.Linear(5, 8)
            def trainer(a, c, h, p, data):
                return focused.candidate_train(a, c, h, p, data, tiny_models)
            def gated_test(bundle, selection_path, checksum):
                self.assertEqual(sha(selection_path), checksum)
                self.assertEqual(read(args.run_dir / 'selection_commit.json')['sha256'], checksum)
                return tests
            with mock.patch.object(encoders, 'load_encoder', side_effect=loader), \
                 mock.patch.object(encoders, 'make_head', side_effect=classifier), \
                 mock.patch.object(focused, 'load_test', side_effect=gated_test):
                result = focused.execute(args, rows, {}, prior, 'pipeline_signature', trainer)
                self.assertIn(result['selected_source'], ('prior', 'trained'))
                self.assertFalse(result['test_used_for_selection'])
                self.assertTrue((args.run_dir / 'per_label_comparison.csv').is_file())
                self.assertTrue((args.run_dir / 'siglip2_focused/timing_raw.csv').is_file())
                with mock.patch.object(focused.common, 'benchmark', side_effect=AssertionError('no duplicate evaluation')):
                    resumed = focused.execute(args, rows, {}, prior, 'pipeline_signature', mock.Mock(side_effect=AssertionError('no duplicate training')))
                self.assertEqual(result, resumed)

    def test_confusion_and_comparison_exclude_unknown_and_report_regressions(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            old = [dict(image_id='1', labels={k: 0 for k in LABELS}, probabilities=[.1] * 8, predictions=[0] * 8)]
            old[0]['labels']['food_drink'] = None
            new = copy.deepcopy(old)
            new[0]['predictions'][1] = 1  # Unknown excluded.
            new[0]['predictions'][2] = 1  # Animal regression.
            for name, rows in (('siglip2_full_finetune', old), ('siglip2_focused', new)):
                (out / name).mkdir(); save(out / name / 'test_predictions.json', rows)
            focused.comparison_details(out)
            changes = read(out / 'error_changes.json')
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0]['label'], 'animal')
            self.assertEqual(changes[0]['change'], 'regressed')


if __name__ == '__main__':
    unittest.main()
