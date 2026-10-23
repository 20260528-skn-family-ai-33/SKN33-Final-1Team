"""CPU harness of the full three-arm evaluator; no downloaded model or GPU needed."""
from contextlib import ExitStack, nullcontext, redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


class PipelineIntegration(unittest.TestCase):
    def test_three_arms_execute_ocr_342_times_without_training_or_test_retuning(self):
        import numpy as np
        import torch
        from PIL import Image
        from benchlib import ARMS, LABELS, read, save
        from pipeline_eval import evaluate
        from run_benchmark import hashlib_image

        class Visual(torch.nn.Module):
            def __init__(self, *args):
                super().__init__()
                self.classifier = lambda x: torch.zeros((1, 8))
            def to(self, *args): return self
            def load_state_dict(self, state): return None
            def set_training(self, enabled):
                if enabled: raise AssertionError('training forbidden')
            def embed(self, *args): return torch.zeros((1, 8))

        class OCR:
            calls = []
            closed = False
            identity = {'device': 'gpu:0'}
            def __init__(self, *args): pass
            def predict(self, path, image_id):
                self.calls.append((path, image_id))
                return dict(image_id=image_id, text_count=1, engine_ms=1., raw={'rec_texts': ['테스트']})
            def close(self): type(self).closed = True

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            bundle, source, out = (root/name for name in ('bundle', 'source', 'out'))
            (bundle/'references').mkdir(parents=True)
            out.mkdir()
            heads = {k: dict(coef=[0.]*8, intercept=0., threshold=.5) for k in LABELS}
            save(bundle/'references/eg2_heads.json', {'heads': heads})
            save(bundle/'references/resnet_selection.json', {'thresholds': [.5]*8})
            image = lambda *args: Image.new('RGB', (4, 4), 'red')
            im = image()
            fingerprint = hashlib_image(im)
            im.close()
            rows = [dict(image_id=f'P{i:03}', image=f'data/{i}.png',
                         labels={k: (i+j) % 2 for j, k in enumerate(LABELS)}) for i in range(37)]
            for i in range(14): rows[i]['labels']['person'] = None
            for arm in ARMS:
                folder = source/'arms'/arm
                folder.mkdir(parents=True)
                np.save(folder/'test_probs.npy', np.full((37, 8), .5))
                save(folder/'shared_input_fingerprints.json', {r['image_id']: fingerprint for r in rows})
            args = SimpleNamespace(ocr_python=Path('/fake/python'), ocr_device='gpu:0',
                 ocr_models=root/'models', ocr_timeout=2, repeats=3, model_dir=None, cache_dir=root/'cache')
            provenance = dict(protocol={'config': {'precision': 'fp32'}}, environment={'test': True},
                 selection_sha256='frozen', checkpoint=str(source/'fresh-selected.pt'),
                 selection={'selected': {'thresholds': [.7]*8}})
            loaded = []
            stack.enter_context(patch('models.ResNet', Visual))
            stack.enter_context(patch('models.EG', Visual))
            stack.enter_context(patch('models.image', image))
            stack.enter_context(patch('models.precision_context', lambda *args: nullcontext()))
            stack.enter_context(patch('run_benchmark.cuda_environment', return_value={'test': True}))
            stack.enter_context(patch('run_benchmark.get_model_dir', return_value=root/'base'))
            stack.enter_context(patch('run_benchmark.release'))
            stack.enter_context(patch('run_benchmark.train_eg2', side_effect=AssertionError('training forbidden')))
            stack.enter_context(patch('torch.optim.AdamW', side_effect=AssertionError('optimizer forbidden')))
            stack.enter_context(patch('torch.load', side_effect=lambda path, **kwargs: loaded.append(path) or {}))
            stack.enter_context(patch('torch.cuda.synchronize'))
            stack.enter_context(patch('pipeline_eval.load_test', return_value=rows))
            stack.enter_context(patch('pipeline_eval.PaddleClient', OCR))
            stack.enter_context(redirect_stdout(io.StringIO()))
            results, table = evaluate(args, bundle, source, provenance, out)
            self.assertEqual(len(OCR.calls), (37*3+3)*3)
            self.assertTrue(OCR.closed)
            self.assertEqual(loaded[-1], str(source/'fresh-selected.pt'))
            self.assertEqual(len(table), 3)
            for arm, row in zip(ARMS, table):
                self.assertEqual(row['known_cells'], 282)
                self.assertEqual(row['unknown_excluded'], 14)
                self.assertEqual(row['samples'], 111)
                self.assertEqual(len(list((out/'arms'/arm/'ocr_raw').glob('*.json'))), 37)
                self.assertEqual(results[arm]['test_metrics']['known_evaluated_cells'], 282)
                self.assertEqual(read(out/'arms'/arm/'test_metrics.json')['masked_unknown_cells'], 14)
                self.assertAlmostEqual(row['total_mean_ms'], row['ocr_mean_ms']+row['model_mean_ms'])
            expected = sum(v == 0 for row in rows for v in row['labels'].values()) / 282
            self.assertEqual(table[-1]['accuracy'], expected)  # Frozen dev .7 threshold was retained.


if __name__ == '__main__':
    unittest.main()
