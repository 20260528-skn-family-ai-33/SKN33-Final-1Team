"""Evidence export/validation/report tests run with standard-library Python."""
from pathlib import Path
from unittest import mock
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import resultlib as lib
from export_results import export_results
from build_result_report import build_report
from result_fixture import fixture


class ResultToolTests(unittest.TestCase):
    def export(self, root, improved=True, checkpoints='none', images=True):
        runs, bundle = fixture(root, improved)
        archive = Path(root) / 'exports/results.tar.gz'
        export_results(runs, bundle, archive, checkpoints, images)
        evidence = lib.extract_archive(archive, Path(root) / 'downloaded')
        return runs, bundle, archive, evidence

    def test_roundtrip_recomputes_metrics_and_selects_trained_model(self):
        with tempfile.TemporaryDirectory() as td:
            runs, bundle, archive, evidence = self.export(td)
            verified = lib.verify_results(evidence)
            self.assertEqual(len(verified['models']), 6)
            self.assertEqual(verified['selected']['source'], 'trained')
            self.assertFalse(verified['checkpoint_bytes_verified'])
            self.assertFalse(verified['parameter_updates_independently_verified'])
            self.assertEqual(verified['models'][-1]['metrics']['known_evaluated_cells'], 282)
            self.assertTrue(verified['models'][-1]['goal_met'])  # Median36ms is not an extra goal.
            self.assertEqual(len(verified['error_changes']), 1)
            self.assertTrue(archive.with_name(archive.name + '.sha256').is_file())

    def test_existing_model_fallback_and_lightweight_report_without_images(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, _, evidence = self.export(td, improved=False, images=False)
            result = lib.verify_results(evidence)
            self.assertEqual(result['selected']['source'], 'prior')
            report = build_report(evidence, Path(td) / 'report', make_figures=False)
            html = report.read_text()
            self.assertIn('기존 가중치 유지', html)
            self.assertIn('합성 데이터 검증용', html)
            self.assertEqual(html.count('<section id='), 9)
            self.assertIn('중앙값30ms 추가 조건', html)
            self.assertTrue((report.parent / 'verification.json').is_file())
            with self.assertRaisesRegex(ValueError, 'already exists'):
                build_report(evidence, report.parent, make_figures=False)

    def test_export_refuses_running_run_before_creating_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); runs, bundle = fixture(root)
            status = runs / lib.RUNS[2] / 'status.json'
            lib.save(status, dict(status='running'))
            before = {p: lib.sha(p) for p in runs.rglob('*') if p.is_file()}
            out = root / 'exports/not-ready.tar.gz'
            with self.assertRaisesRegex(ValueError, 'still running'):
                export_results(runs, bundle, out)
            self.assertFalse(out.parent.exists())
            self.assertEqual(before, {p: lib.sha(p) for p in runs.rglob('*') if p.is_file()})

    def test_export_does_not_mutate_sources_and_selected_checkpoints_are_optional(self):
        with tempfile.TemporaryDirectory() as td:
            runs, bundle = fixture(Path(td))
            before = {p: lib.sha(p) for p in runs.rglob('*') if p.is_file()}
            archive = Path(td) / 'exports/selected.tar.gz'
            export_results(runs, bundle, archive, checkpoints='selected')
            evidence = lib.extract_archive(archive, Path(td) / 'evidence')
            self.assertTrue(lib.verify_results(evidence)['checkpoint_bytes_verified'])
            self.assertEqual(before, {p: lib.sha(p) for p in runs.rglob('*') if p.is_file()})

    def test_modified_source_during_copy_does_not_publish_archive(self):
        import export_results as module
        with tempfile.TemporaryDirectory() as td:
            runs, bundle = fixture(Path(td))
            original = module.shutil.copyfile
            def change(source, destination):
                result = original(source, destination)
                if Path(source).name == 'environment.json':
                    Path(source).write_text('{}')
                return result
            out = Path(td) / 'exports/race.tar.gz'
            with mock.patch.object(module.shutil, 'copyfile', side_effect=change):
                with self.assertRaisesRegex(ValueError, 'changed while copying'):
                    export_results(runs, bundle, out)
            self.assertFalse(out.exists())

    def test_corrupted_evidence_fails_before_report_creation(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, _, evidence = self.export(td)
            p = evidence / 'runs' / lib.RUNS[2] / 'siglip2_focused/test_metrics.json'
            lib.save(p, {})
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                build_report(evidence, Path(td) / 'report', make_figures=False)
            self.assertFalse((Path(td) / 'report').exists())

    def test_semantically_invalid_but_rehashed_timing_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, _, evidence = self.export(td)
            p = evidence / 'runs' / lib.RUNS[2] / 'siglip2_focused/timing_raw.csv'
            p.write_text(p.read_text(encoding='utf-8-sig').replace('36.0', '37.0'), encoding='utf-8-sig')
            manifest = lib.read(evidence / 'export_manifest.json')
            manifest['files'][p.relative_to(evidence).as_posix()] = dict(bytes=p.stat().st_size, sha256=lib.sha(p))
            lib.save(evidence / 'export_manifest.json', manifest)
            with self.assertRaisesRegex(ValueError, 'artifact hash differs'):
                lib.verify_results(evidence)

    def test_archive_checksum_traversal_links_duplicates_and_size_limits(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, kind in (('../outside', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                               ('safe/link', tarfile.SYMTYPE), ('safe\\evil', tarfile.REGTYPE)):
                archive = root / 'unsafe.tar.gz'
                with tarfile.open(archive, 'w:gz') as stream:
                    member = tarfile.TarInfo(name); member.type = kind; member.size = 1 if kind == tarfile.REGTYPE else 0
                    stream.addfile(member, io.BytesIO(b'x'))
                with self.assertRaises(ValueError):
                    lib.extract_archive(archive, root / 'output')
                self.assertFalse((root / 'output').exists())
            with tarfile.open(root / 'duplicate.tar.gz', 'w:gz') as stream:
                for _ in range(2):
                    member = tarfile.TarInfo('same'); member.size = 1; stream.addfile(member, io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                lib.extract_archive(root / 'duplicate.tar.gz', root / 'output')
            with self.assertRaisesRegex(ValueError, 'limits'):
                lib.extract_archive(root / 'duplicate.tar.gz', root / 'output', max_bytes=0)

    def test_standard_library_cli_imports_no_training_libraries(self):
        folder = Path(__file__).resolve().parents[1]
        script = "import sys; import export_results, verify_results, build_result_report; assert not set(('torch','transformers','numpy','timm')) & set(sys.modules); print('stdlib-only')"
        result = subprocess.run([sys.executable, '-B', '-c', script], cwd=folder, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('stdlib-only', result.stdout)

    def test_average_precision_handles_ties_and_unknown_labels(self):
        self.assertAlmostEqual(lib.average_precision([1, 0, 1], [.9, .9, .1]), 7 / 12)
        self.assertIsNone(lib.average_precision([1, 1], [.9, .1]))
        probabilities = [[.75] * 8, [.25] * 8]
        labels = [dict(image_id=str(i), labels={k: (1 if i == 0 else None) for k in lib.LABELS}) for i in range(2)]
        score, counts = lib.score_rows(lib.rows_with_probabilities(labels, probabilities, [.5] * 8), [.5] * 8)
        self.assertEqual(score['label_wise_accuracy'], 1.)
        self.assertEqual(score['known_evaluated_cells'], 8)
        self.assertEqual(counts['animal']['tn'], 0)


if __name__ == '__main__':
    unittest.main()
