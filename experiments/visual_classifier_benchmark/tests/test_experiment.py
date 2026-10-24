"""CPU tests use synthetic labels/images/random weights; never claim CUDA scores."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import json
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
import torch
from torch import nn

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import encoders
import run_experiment as runner
from benchlib import LABELS, read, save, sha, targets

torch.set_num_threads(1)


class MeanBackbone(nn.Module):
    def forward(self, x):
        return x.mean(dim=(-1, -2))


def tiny_encoder(kind, folder, device, precision, size):
    return encoders.Encoder(MeanBackbone(), 3, [.5] * 3, [.5] * 3, 32,
                            device, precision, 'test')


class ExperimentTests(unittest.TestCase):
    def test_smoke_reads_only_two_training_images_and_never_fits(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            Image.new('RGB', (20, 40), 'white').save(root / 'image.png')
            rows = [dict(image_id=str(i), image='image.png', split='train',
                         labels={key: i % 2 for key in LABELS}) for i in range(3)]
            args = SimpleNamespace(command='smoke', models=['siglip2'], models_root=root,
                                   bundle=root, run_dir=root, precision='fp32')
            with mock.patch.object(runner, 'fit_heads', side_effect=AssertionError('no training')), \
                 mock.patch.object(runner, 'load_test', side_effect=AssertionError('no test access')):
                result = runner.execute(args, {'device': 'cpu'}, 'smoke', rows, tiny_encoder)
            self.assertFalse(result['test_accessed'])
            self.assertFalse(result['training_executed'])

    def test_main_refuses_accidental_restart_and_protocol_drift(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {}):
            root = Path(td)
            bundle = root / 'bundle'
            bundle.mkdir()
            save(bundle / 'bundle_manifest.json', {})
            base = ['run_experiment.py', 'run', '--bundle', str(bundle),
                    '--models-root', str(root), '--models', 'siglip2', '--run-dir', str(root / 'run')]
            with mock.patch.object(runner, 'bundle_data', return_value=({}, [], [])), \
                 mock.patch.object(runner, 'model_packet', return_value={'files': {}}), \
                 mock.patch.object(runner, 'cuda_environment', return_value={'device': 'cpu', 'GPU': 'test'}), \
                 mock.patch.object(runner, 'execute', return_value={'status': 'complete'}):
                with mock.patch.object(sys, 'argv', base):
                    self.assertEqual(runner.main(), 0)
                with mock.patch.object(sys, 'argv', base):
                    with self.assertRaisesRegex(ValueError, 'already exists'):
                        runner.main()
                with mock.patch.object(sys, 'argv', base + ['--resume', '--repeats', '11']):
                    with self.assertRaisesRegex(ValueError, 'settings changed'):
                        runner.main()
                with mock.patch.object(sys, 'argv', base + ['--resume']):
                    self.assertEqual(runner.main(), 0)

    def test_unknown_labels_do_not_influence_head_fit(self):
        rng = np.random.default_rng(4)
        x = rng.normal(size=(40, 5)).astype(np.float32)
        y = np.tile((np.arange(40) % 2)[:, None], (1, 8)).astype(np.float32)
        y[0, :] = np.nan
        vx, vy = x[10:20], y[10:20]
        a, _ = runner.fit_heads(x, y, vx, vy, [.1])
        changed = x.copy()
        changed[0] = 100000
        b, _ = runner.fit_heads(changed, y, vx, vy, [.1])
        np.testing.assert_allclose(a['coef'], b['coef'], rtol=0, atol=0)

    def test_untrainable_label_fails(self):
        with self.assertRaisesRegex(ValueError, 'positive/negative'):
            runner.fit_heads(np.ones((4, 2)), np.zeros((4, 8)),
                             np.ones((4, 2)), np.zeros((4, 8)), [.1])

    def test_no_partial_model_selection_before_test(self):
        with self.assertRaisesRegex(ValueError, 'All requested'):
            runner.selection_bundle(['siglip2', 'convnextv2'], {'siglip2': {}}, 'x')

    def test_letterbox_preserves_edge_and_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'image.png'
            im = Image.new('RGB', (20, 40), 'red')
            im.save(path)
            with encoders.letterbox(path, 40) as box:
                self.assertEqual(box.size, (40, 40))
                self.assertEqual(box.getpixel((0, 20)), (128, 128, 128))
                self.assertEqual(box.getpixel((10, 0)), (255, 0, 0))
                self.assertEqual(box.getpixel((29, 39)), (255, 0, 0))

    def test_local_model_fingerprint_detects_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'config.json').write_text('{}')
            (root / 'model.safetensors').write_bytes(b'first')
            first = runner.model_packet(root, 'convnextv2')
            (root / 'model.safetensors').write_bytes(b'other')
            self.assertNotEqual(first, runner.model_packet(root, 'convnextv2'))

    def test_end_to_end_selection_resume_timing_and_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / 'data'
            data.mkdir()
            rows = []
            rng = np.random.default_rng(22)
            for i in range(24):
                im = Image.fromarray(rng.integers(0, 256, size=(32, 24, 3), dtype=np.uint8))
                im.save(data / f'{i}.png')
                labels = {label: (i + j) % 2 for j, label in enumerate(LABELS)}
                if i == 0:
                    labels['person'] = None
                rows.append(dict(image_id=f'T{i}', image=f'data/{i}.png',
                                 split='train' if i < 12 else 'dev' if i < 20 else 'test', labels=labels))
            out = root / 'run'
            out.mkdir()
            args = SimpleNamespace(command='run', models=['siglip2', 'convnextv2'], models_root=root,
                                   bundle=root, run_dir=out, precision='fp32', cs=[.1], repeats=2, warmups=1)
            access_count = []
            def guarded_test(bundle, selection, checksum):
                frozen = read(selection)
                self.assertEqual(set(frozen['models']), set(args.models))
                self.assertEqual(checksum, sha(selection))
                self.assertTrue((out / 'selection_commit.json').is_file())
                access_count.append(1)
                return rows[20:]
            with mock.patch.object(runner, 'load_test', side_effect=guarded_test):
                result = runner.execute(args, {'device': 'cpu'}, 'synthetic', rows[:20], tiny_encoder)
                self.assertEqual(result['status'], 'complete')
                self.assertEqual(len(access_count), 1)
                for kind in args.models:
                    self.assertEqual(result['results'][kind]['timing']['total_ms']['n'], 8)
                    self.assertEqual(len(read(out / kind / 'test_predictions.json')), 4)
                with mock.patch.object(runner, 'fit_heads', side_effect=AssertionError('must resume')):
                    resumed = runner.execute(args, {'device': 'cpu'}, 'synthetic', rows[:20], tiny_encoder)
                self.assertEqual(result['results'], resumed['results'])
                self.assertTrue(result['training_executed'])
                self.assertFalse(resumed['training_executed'])
                path = out / 'siglip2/head_selection.json'
                path.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'artifact changed'):
                    runner.execute(args, {'device': 'cpu'}, 'synthetic', rows[:20], tiny_encoder)

    def test_offline_siglip_loader_uses_only_vision_tower(self):
        from transformers import SiglipConfig, SiglipModel
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = SiglipConfig(
                vision_config=dict(hidden_size=16, intermediate_size=32, num_hidden_layers=1,
                                   num_attention_heads=2, image_size=32, patch_size=16),
                text_config=dict(hidden_size=16, intermediate_size=32, num_hidden_layers=1,
                                 num_attention_heads=2, vocab_size=32, bos_token_id=0, eos_token_id=1))
            model = SiglipModel(config).eval()
            model.save_pretrained(root)
            save(root / 'preprocessor_config.json', {'image_mean': [.5] * 3, 'image_std': [.5] * 3})
            with mock.patch('huggingface_hub.hf_hub_download', side_effect=AssertionError('offline only')):
                loaded = encoders.load_encoder('siglip2', root, 'cpu', size=32)
            x = torch.rand(1, 3, 32, 32)
            with torch.inference_mode():
                expected = torch.nn.functional.normalize(model.vision_model(x).pooler_output, dim=1)
                actual = loaded(x)
            torch.testing.assert_close(actual, expected)
            self.assertFalse(any(p.requires_grad for p in loaded.parameters()))
            self.assertFalse(any('text_model' in name for name, _ in loaded.named_parameters()))

    def test_offline_convnext_loader_and_nonmatching_weights(self):
        import timm
        from safetensors.torch import save_file
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model = timm.create_model('convnextv2_tiny', pretrained=False).eval()
            save_file(model.state_dict(), str(root / 'model.safetensors'))
            save(root / 'config.json', dict(architecture='convnextv2_tiny', num_classes=1000,
                                           num_features=768, pretrained_cfg={'mean': [.5] * 3, 'std': [.5] * 3}))
            with mock.patch('huggingface_hub.hf_hub_download', side_effect=AssertionError('offline only')):
                loaded = encoders.load_encoder('convnextv2', root, 'cpu', size=32)
            model.reset_classifier(0)
            x = torch.rand(1, 3, 32, 32)
            with torch.inference_mode():
                expected = torch.nn.functional.normalize(model(x), dim=1)
                actual = loaded(x)
            torch.testing.assert_close(actual, expected)
            self.assertEqual(actual.shape, (1, 768))
            save_file({'wrong': torch.ones(1)}, str(root / 'model.safetensors'))
            with self.assertRaises(RuntimeError):
                encoders.load_encoder('convnextv2', root, 'cpu', size=32)


if __name__ == '__main__':
    unittest.main()
