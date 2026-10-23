"""No-GPU checks for immutable source selection, real OCR calls and worker failures."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from benchlib import ARMS, digest, read, save, sha
from pipeline_eval import PaddleClient, main, measure_sample, show_results, source_run, validate_source, validate_bundled


class PipelineContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundle = self.root/'bundle'
        self.bundle.mkdir()
        save(self.bundle/'bundle_manifest.json', {'test': 'unchanged'})
        self.source = self.root/'runs'/'train-finished'
        self.source.mkdir(parents=True)
        config = dict(command='train', bundle_manifest_sha256=sha(self.bundle/'bundle_manifest.json'),
                      script_hashes={name: sha(HERE/name) for name in ('benchlib.py', 'models.py', 'run_benchmark.py')})
        signature = digest(config)
        save(self.source/'protocol.json', dict(config=config, signature=signature))
        save(self.source/'status.json', dict(status='complete', CUDA_executed=True, protocol_signature=signature))
        (self.source/'selected.pt').write_bytes(b'freshly trained model, not bundled historical weights')
        selection = dict(kind='dev_selected_fresh_EG2_training', test_used_for_selection=False,
                         protocol_signature=signature, selected=dict(checkpoint='selected.pt',
                         checkpoint_sha256=sha(self.source/'selected.pt'), thresholds=[.7]*8))
        save(self.source/'selection_frozen.json', selection)
        checksum = sha(self.source/'selection_frozen.json')
        save(self.source/'selection_commit.json', dict(sha256=checksum))
        save(self.source/'environment.json', {'test_gpu': True})
        for arm in ARMS:
            folder = self.source/'arms'/arm
            folder.mkdir(parents=True)
            (folder/'test_probs.npy').write_bytes(b'committed source predictions')
            save(folder/'arm_complete.json', dict(selection_sha256=checksum,
                 file_hashes={'test_probs.npy': sha(folder/'test_probs.npy')}))
            save(folder/'arm_complete_commit.json', dict(sha256=sha(folder/'arm_complete.json')))

    def test_selected_fresh_checkpoint_is_read_only_and_thresholds_preserved(self):
        before = {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        value = validate_source(self.source, self.bundle)
        self.assertEqual(value['checkpoint'], str(self.source/'selected.pt'))
        self.assertEqual(value['selection']['selected']['thresholds'], [.7]*8)
        self.assertEqual(before, {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob('*') if p.is_file()})

    def test_latest_ignores_pipeline_but_never_falls_back_from_incomplete_train(self):
        incomplete = self.source.parent/'train-new-incomplete'
        incomplete.mkdir()
        save(incomplete/'protocol.json', {'config': {'command': 'train'}})
        older = self.source/'protocol.json'
        os.utime(older, ns=(1, 1))
        pipeline = self.source.parent/'pipeline-newest'
        pipeline.mkdir()
        save(pipeline/'protocol.json', {'config': {'command': 'evaluate_pipeline'}})
        self.assertEqual(source_run('latest', self.source.parent), incomplete)
        with self.assertRaises((KeyError, FileNotFoundError)):
            validate_source(incomplete, self.bundle)

    def test_no_train_run_does_not_use_bundled_checkpoint(self):
        with self.assertRaisesRegex(ValueError, 'No existing train run'):
            source_run('latest', self.root/'absent')

    def test_bundled_source_is_explicit_local_and_keeps_original_selection(self):
        refs = self.bundle/'references'
        refs.mkdir()
        (refs/'eg2_full_finetune.pt').write_bytes(b'original local model')
        selected = {'checkpoint_sha256': sha(refs/'eg2_full_finetune.pt'), 'thresholds': [.3]*8}
        save(refs/'eg2_selection.json', selected)
        provenance = validate_bundled(self.bundle)
        self.assertEqual(provenance['source_kind'], 'bundled_local_checkpoint')
        self.assertIsNone(provenance['environment'])
        self.assertEqual(provenance['checkpoint'], str(refs/'eg2_full_finetune.pt'))
        self.assertEqual(provenance['selection']['selected'], selected)
        self.assertIs(provenance['selection']['test_used_for_selection'], False)
        (refs/'eg2_full_finetune.pt').write_bytes(b'modified model')
        with self.assertRaisesRegex(ValueError, 'Bundled EG2 checkpoint differs'):
            validate_bundled(self.bundle)

    def test_bundled_check_source_does_not_require_cuda_train_or_run_inference(self):
        refs = self.bundle/'references'
        refs.mkdir()
        (refs/'eg2_full_finetune.pt').write_bytes(b'original local model')
        save(refs/'eg2_selection.json', {'checkpoint_sha256': sha(refs/'eg2_full_finetune.pt'), 'thresholds': [.3]*8})
        output = io.StringIO()
        with patch('sys.argv', ['pipeline_eval.py', '--bundle', str(self.bundle), '--source-run', 'bundled', '--check-source']), \
             patch('pipeline_eval.verify_bundle'), patch('pipeline_eval.evaluate') as inference, redirect_stdout(output):
            self.assertEqual(main(), 0)
            inference.assert_not_called()
        self.assertIn('bundled_local_checkpoint', output.getvalue())
        self.assertIn('"training_executed": false', output.getvalue())

    def test_corrupt_selected_checkpoint_is_rejected(self):
        (self.source/'selected.pt').write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError, 'checkpoint changed'):
            validate_source(self.source, self.bundle)

    def test_frozen_selection_and_predictions_cannot_be_replaced(self):
        path = self.source/'selection_frozen.json'
        original = path.read_bytes()
        value = read(path)
        value['selected']['thresholds'] = [.1]*8
        save(path, value)
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            validate_source(self.source, self.bundle)
        path.write_bytes(original)
        (self.source/'arms'/ARMS[0]/'test_probs.npy').write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError, 'output changed'):
            validate_source(self.source, self.bundle)

    def test_source_directory_cannot_be_used_as_output(self):
        before = {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        arguments = ['pipeline_eval.py', '--bundle', str(self.bundle), '--source-run', str(self.source),
                     '--run-dir', str(self.source), '--ocr-python', sys.executable]
        with patch('sys.argv', arguments), patch('pipeline_eval.verify_bundle'), \
                patch('pipeline_eval.evaluate') as inference, patch('sys.stderr', io.StringIO()):
            self.assertEqual(main(), 1)
            inference.assert_not_called()
        self.assertEqual(before, {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob('*') if p.is_file()})

    def test_results_require_completion_and_do_not_run_models(self):
        folder = self.source.parent/'pipeline-completed'
        folder.mkdir()
        save(folder/'protocol.json', {'config': {'command': 'evaluate_pipeline'}})
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            show_results(self.source.parent)
        save(folder/'status.json', {'status': 'complete'})
        stages = {k: {'mean_ms': v, 'p95_ms': v+1} for k, v in
                  (('ocr_ms', 10), ('model_ms', 2), ('total_ms', 12))}
        save(folder/'pipeline_summary.json', {'results': {'eg2_full_finetune': {
             'test_metrics': {'label_wise_accuracy': .9}, 'timing': stages}}})
        output = io.StringIO()
        with redirect_stdout(output):
            show_results(self.source.parent)
        self.assertIn('accuracy=90.00%', output.getvalue())
        self.assertIn('OCR=10.00 ms', output.getvalue())
        self.assertIn('total=12.00 ms', output.getvalue())

    def test_every_sample_really_calls_ocr_and_total_is_continuous_wall_time(self):
        ocr = Mock(return_value={'text_count': 2})
        visual = Mock(return_value='classification')
        synchronize = Mock()
        for _ in range(3):
            clock = Mock(side_effect=[10., 10.25, 10.75])
            result, prediction, times = measure_sample(ocr, visual, synchronize, clock)
            self.assertEqual(times, {'ocr_ms': 250., 'model_ms': 500., 'total_ms': 750.})
            self.assertEqual(prediction, 'classification')
        self.assertEqual(ocr.call_count, 3)
        self.assertEqual(visual.call_count, 3)
        self.assertEqual(synchronize.call_count, 6)

    def test_ocr_failure_is_not_skipped_or_scored_as_success(self):
        visual = Mock()
        with self.assertRaisesRegex(RuntimeError, 'OCR failed'):
            measure_sample(Mock(side_effect=RuntimeError('OCR failed')), visual, Mock())
        visual.assert_not_called()

    def worker(self, body, timeout=2):
        script = self.root/'fake_worker.py'
        script.write_text(body)
        return PaddleClient([sys.executable, '-u', str(script)], self.root/'worker.log', timeout)

    def test_persistent_worker_and_response_id_validation(self):
        client = self.worker('''import json,sys
print(json.dumps(dict(ok=True,event='ready',device='cpu')),flush=True)
for line in sys.stdin:
 request=json.loads(line)
 print(json.dumps(dict(ok=True,image_id=request['image_id'],text_count=0)),flush=True)
''')
        try:
            for identity in ('P001', 'P002'):
                self.assertEqual(client.predict('/full-resolution/image.png', identity)['image_id'], identity)
            self.assertIsNone(client.process.poll())
        finally:
            client.close()
        self.assertIsNotNone(client.process.poll())

    def test_worker_timeout_is_fatal_and_process_is_cleaned_up(self):
        with self.assertRaises(TimeoutError):
            self.worker('import time\ntime.sleep(30)\n', timeout=.1)

    def test_worker_error_and_wrong_identity_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'startup problem'):
            self.worker("print('{\"ok\":false,\"error\":\"startup problem\"}',flush=True)\n")
        client = self.worker('''import json,sys
print(json.dumps(dict(ok=True,event='ready')),flush=True)
for line in sys.stdin:
 print(json.dumps(dict(ok=True,image_id='wrong')),flush=True)
''')
        try:
            with self.assertRaisesRegex(ValueError, 'ID differs'):
                client.predict('/image', 'P001')
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()
