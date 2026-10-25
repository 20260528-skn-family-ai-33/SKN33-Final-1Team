"""Real tiny SigLIP vision blocks on CPU; no downloads or production training."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
import torch
from torch import nn
from transformers import SiglipVisionConfig, SiglipVisionModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import finetune_siglip_partial as partial
from encoders import Encoder
from benchlib import LABELS, read, save, sha, targets

torch.set_num_threads(1)


def models(folder, device, selection):
    config = SiglipVisionConfig(hidden_size=16, intermediate_size=32, num_hidden_layers=4,
                               num_attention_heads=4, image_size=32, patch_size=16)
    encoder = Encoder(SiglipVisionModel(config), 16, [.5] * 3, [.5] * 3, 32, device, 'fp32', 'siglip2')
    return encoder, nn.Linear(16, 8)


def fixture(root):
    rows = []
    for i in range(14):
        Image.new('RGB', (24, 32), (20 + i * 12, 40, 200 - i * 10)).save(root / f'{i}.png')
        rows.append(dict(image_id=f'S{i}', image=f'{i}.png', split='train' if i < 10 else 'dev', labels={k: (i + j) % 2 for j, k in enumerate(LABELS)}))
    rows[0]['labels']['person'] = None
    folder = root / 'run/candidates/prior'; folder.mkdir(parents=True)
    encoder, head = models(root, 'cpu', {})
    partial.ft.save_weights(folder / 'best.safetensors', partial.ft.state(encoder, head))
    p = partial.ft.predict_dev(encoder, head, root, rows[10:])
    reference = dict(id='prior', source='prior', backbone_lr=0., best_epoch=1, threshold_policy='original', thresholds=[.5] * 8,
                     dev_metrics=partial.metrics(targets(rows[10:]), p, [.5] * 8), dev_confusion=partial.focused.confusion(targets(rows[10:]), p, [.5] * 8),
                     checkpoint='candidates/prior/best.safetensors', artifact_hashes={'best.safetensors': sha(folder / 'best.safetensors')})
    args = SimpleNamespace(run_dir=root / 'run', bundle=root, packet=root, prior_run=root / 'old', model_dir=root,
                           device='cpu', epochs=2, patience=2, microbatch=2, warmups=1, repeats=2)
    return args, rows, reference


class PartialFineTuneTests(unittest.TestCase):
    def test_only_selected_tail_blocks_pooler_norm_and_head_are_trainable(self):
        encoder, head = models(None, 'cpu', {})
        wrapped = partial.PartialEncoder(encoder, head, 2)
        for name, p in wrapped.backbone.named_parameters():
            expected = name.startswith(('encoder.layers.2.', 'encoder.layers.3.', 'post_layernorm.', 'head.'))
            self.assertEqual(p.requires_grad, expected, name)
        wrapped.train()
        self.assertFalse(wrapped.backbone.embeddings.training)
        self.assertFalse(wrapped.backbone.encoder.layers[0].training)
        self.assertTrue(wrapped.backbone.encoder.layers[3].training)
        wrapped.eval(); self.assertFalse(wrapped.backbone.encoder.layers[3].training)
        self.assertTrue(all(p.requires_grad for p in head.parameters()))

    def test_head_only_keeps_all_backbone_groups_bit_identical(self):
        with tempfile.TemporaryDirectory() as td:
            args, rows, ref = fixture(Path(td))
            spec = dict(id='head-only', tail_blocks=0, backbone_lr=0.)
            result = partial.candidate_train(args, spec, {}, ref, rows, models)
            self.assertEqual(result['trainable_backbone_parameters'], 0)
            self.assertEqual(result['trainable_groups'], ['classifier'])
            self.assertTrue(result['parameter_groups_changed']['classifier'])
            self.assertTrue(all(not v for k, v in result['parameter_groups_changed'].items() if k != 'classifier'))

    def test_partial_training_freezes_early_blocks_and_restores_exact_checkpoint(self):
        from safetensors.torch import load_file
        with tempfile.TemporaryDirectory() as td:
            args, rows, ref = fixture(Path(td))
            spec = dict(id='tail2-test', tail_blocks=2, backbone_lr=.001)
            with mock.patch.object(partial, 'load_test', side_effect=AssertionError('no test during training')):
                result = partial.candidate_train(args, spec, {}, ref, rows, models)
            changed = result['parameter_groups_changed']
            self.assertFalse(changed['embeddings']); self.assertFalse(changed['encoder.layers.0']); self.assertFalse(changed['encoder.layers.1'])
            self.assertTrue(changed['encoder.layers.2']); self.assertTrue(changed['encoder.layers.3'])
            self.assertTrue(all(v > 0 for v in result['gradient_audit'].values()))
            self.assertEqual(read(args.run_dir / 'candidates/tail2-test/history.json')[0]['known_cells'], 79)
            encoder, head = models(None, 'cpu', {})
            partial.ft.load_state(encoder, head, load_file(str(args.run_dir / result['checkpoint'])))
            actual = partial.ft.predict_dev(encoder, head, args.packet, rows[10:])
            np.testing.assert_array_equal(actual, np.load(args.run_dir / 'candidates/tail2-test/dev_probabilities.npy'))

    def test_training_rejects_dev_or_test_rows(self):
        with self.assertRaisesRegex(ValueError, 'Only TRAIN'):
            partial.training_epoch(None, None, None, None, [dict(split='test')], 2, 1)

    def test_no_approved_change_blocks_training_before_cuda_initialization(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            args = SimpleNamespace(**{key: root / key for key in ('bundle', 'baseline', 'prior_run', 'model_dir', 'packet', 'run_dir')}, allow_unchanged_data=False)
            with mock.patch.object(partial.common, 'bundle_data', return_value=({}, [], [])), \
                 mock.patch.object(partial, 'validate_packet', return_value=({'added_images': 0, 'changed_train_cells': 0}, [])), \
                 mock.patch.object(partial.common, 'cuda_environment', side_effect=AssertionError('no GPU work')) as cuda:
                with self.assertRaisesRegex(ValueError, 'No approved additions'):
                    partial.run(args)
                cuda.assert_not_called()

    def test_selection_is_frozen_before_evaluation_and_resume_skips_training(self):
        with tempfile.TemporaryDirectory() as td:
            args, rows, ref = fixture(Path(td)); args.candidates = [dict(id='head-only', tail_blocks=0, backbone_lr=0.)]
            def evaluator(a, selected, reference, head):
                self.assertEqual(read(a.run_dir / 'selection_commit.json')['sha256'], sha(a.run_dir / 'selection_frozen.json'))
                self.assertFalse(selected['test_used_for_selection'])
                return dict(status='evaluated')
            def trainer(a, s, h, r, data):
                return partial.candidate_train(a, s, h, r, data, models)
            with mock.patch.object(partial.focused, 'prepare_reference', return_value=ref):
                partial.execute(args, rows, rows, {}, ref, 'signature', trainer, evaluator)
                partial.execute(args, rows, rows, {}, ref, 'signature', mock.Mock(side_effect=AssertionError('no duplicate training')), evaluator)
                save(args.run_dir / 'selection_commit.json', dict(sha256='tampered'))
                with self.assertRaisesRegex(ValueError, 'Frozen selection changed'):
                    partial.execute(args, rows, rows, {}, ref, 'signature', trainer, evaluator)

    def test_cpu_end_to_end_evaluation_and_cached_resume(self):
        from safetensors.torch import load_file
        import encoders
        with tempfile.TemporaryDirectory() as td:
            args, rows, ref = fixture(Path(td)); args.candidates = [dict(id='head-only', tail_blocks=0, backbone_lr=0.)]
            test_rows = [dict(r, split='test') for r in rows[10:]]
            encoder, head = models(None, 'cpu', {})
            partial.ft.load_state(encoder, head, load_file(str(args.run_dir / ref['checkpoint'])))
            old = args.prior_run / 'siglip2_full_finetune'; old.mkdir(parents=True)
            partial.common.benchmark(encoder, head, args.bundle, test_rows, np.asarray(ref['thresholds']), old, 1, 1)
            def load_test(bundle, selection, checksum):
                self.assertEqual(sha(selection), checksum)
                self.assertEqual(read(args.run_dir / 'selection_commit.json')['sha256'], checksum)
                return test_rows
            def trainer(a, spec, h, r, dataset):
                return partial.candidate_train(a, spec, h, r, dataset, models)
            with mock.patch.object(partial.focused, 'prepare_reference', return_value=ref), \
                 mock.patch.object(encoders, 'load_encoder', side_effect=lambda *a: models(None, 'cpu', {})[0]), \
                 mock.patch.object(encoders, 'make_head', side_effect=lambda *a: nn.Linear(16, 8)), \
                 mock.patch.object(partial, 'load_test', side_effect=load_test):
                summary = partial.execute(args, rows, rows, {}, ref, 'signature', trainer)
                self.assertFalse(summary['model_deployed'])
                self.assertTrue((args.run_dir / 'per_label_comparison.csv').is_file())
                with mock.patch.object(partial.common, 'benchmark', side_effect=AssertionError('no duplicate evaluation')):
                    resumed = partial.execute(args, rows, rows, {}, ref, 'signature', mock.Mock(side_effect=AssertionError('no duplicate training')))
                self.assertEqual(summary, resumed)


if __name__ == '__main__':
    unittest.main()
