"""Gradient/masking/selection tests. Synthetic CPU data, no real model download."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import finetune_siglip as ft
from encoders import Encoder
from benchlib import LABELS, read


class SmallBackbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.projection = nn.Linear(3, 5)

    def forward(self, x):
        return self.projection(x.mean(dim=(-1, -2)))


def tiny_models(folder, device, selection):
    encoder = Encoder(SmallBackbone(), 5, [.5] * 3, [.5] * 3, 32, device, 'fp32', 'tiny')
    return encoder, nn.Linear(5, 8)


class FineTuneTests(unittest.TestCase):
    def test_unknown_loss_and_gradient_are_masked(self):
        x = torch.tensor([[.4, -.3, 1., .5, .2, .1, -.7, .8]], requires_grad=True)
        y = torch.tensor([[1., float('nan'), 0., 1., 0., 0., 1., 0.]])
        loss, known = ft.masked_loss(x, y, torch.ones(8))
        loss.backward()
        self.assertEqual(known, 7)
        self.assertEqual(x.grad[0, 1], 0)
        expected = torch.nn.functional.binary_cross_entropy_with_logits(x.detach()[torch.isfinite(y)], y[torch.isfinite(y)], reduction='sum')
        torch.testing.assert_close(loss.detach(), expected)

    def test_balancing_uses_only_known_training_cells(self):
        y = np.tile(np.array([1., 0., 0., np.nan])[:, None], (1, 8))
        self.assertEqual(ft.positive_weights(y, True), [2.] * 8)
        self.assertEqual(ft.positive_weights(y, False), [1.] * 8)

    def test_accumulation_equals_one_known_cell_normalized_batch(self):
        torch.manual_seed(3)
        first = nn.Linear(3, 8)
        second = nn.Linear(3, 8)
        second.load_state_dict(first.state_dict())
        x = torch.randn(3, 3)
        y = torch.randint(0, 2, (3, 8)).float()
        y[0, :3] = float('nan')
        y[2, :5] = float('nan')
        known = int(torch.isfinite(y).sum())
        numerator, _ = ft.masked_loss(first(x), y, torch.ones(8))
        (numerator / known).backward()
        for index in range(3):
            numerator, _ = ft.masked_loss(second(x[index:index+1]), y[index:index+1], torch.ones(8))
            (numerator / known).backward()
        torch.testing.assert_close(first.weight.grad, second.weight.grad)
        torch.testing.assert_close(first.bias.grad, second.bias.grad)

    def test_dev_selection_ignores_test_and_uses_fixed_tie_break(self):
        a = dict(id='a', backbone_lr=1e-5, positive_weight='none', dev_metrics=dict(macro_f1=.8, label_wise_accuracy=.9, macro_average_precision=.85), test_accuracy=0.)
        b = dict(id='b', backbone_lr=3e-5, positive_weight='none', dev_metrics=dict(macro_f1=.7, label_wise_accuracy=.95, macro_average_precision=.9), test_accuracy=1.)
        self.assertEqual(ft.choose([a, b])['id'], 'a')

    def test_backbone_and_head_train_and_saved_state_reproduces(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = []
            for i in range(12):
                Image.new('RGB', (24, 32), (20 + i * 15, 40, 180 - i * 10)).save(root / f'{i}.png')
                rows.append(dict(image_id=f'S{i}', image=f'{i}.png', split='train' if i < 8 else 'dev',
                                 labels={key: (i + j) % 2 for j, key in enumerate(LABELS)}))
            rows[0]['labels']['person'] = None
            args = SimpleNamespace(run_dir=root, model_dir=root, bundle=root, device='cpu',
                                   epochs=2, patience=2, microbatch=2)
            candidate = dict(id='tiny', backbone_lr=.01, positive_weight='train_balanced')
            selected = ft.candidate_train(args, candidate, {}, rows, tiny_models)
            self.assertTrue(all(selected['parameter_groups_changed'].values()))
            self.assertGreater(selected['gradient_audit']['classifier.weight'], 0)
            self.assertEqual(selected['trained_epochs'], 2)
            from safetensors.torch import load_file
            encoder, head = tiny_models(root, 'cpu', {})
            ft.load_state(encoder, head, load_file(str(root / selected['checkpoint'])))
            actual = ft.predict_dev(encoder, head, root, rows[8:])
            expected = np.load(root / 'candidates/tiny/dev_probabilities.npy')
            np.testing.assert_allclose(actual, expected, rtol=0, atol=0)


if __name__ == '__main__':
    unittest.main()
