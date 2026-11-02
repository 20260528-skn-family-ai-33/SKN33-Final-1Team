"""CPU-only SSH packet checks: integrity, safe extraction, and Git exclusions."""
from pathlib import Path
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import assets


class Assets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spec = {'archive_prefix': 'packet'}
        self.content = b'approved train/dev packet'
        self.payload = {'files': {'data/train_dev.json': {'bytes': len(self.content), 'sha256': hashlib.sha256(self.content).hexdigest()}}}

    def archive(self, members):
        file = self.root / 'packet.tar'
        with tarfile.open(file, 'w') as stream:
            for name, data, kind in members:
                info = tarfile.TarInfo(name)
                info.type = kind
                if kind == tarfile.REGTYPE:
                    info.size = len(data)
                    stream.addfile(info, io.BytesIO(data))
                else:
                    info.linkname = '/tmp/outside'
                    stream.addfile(info)
        return file

    def member(self, path='packet/data/train_dev.json', data=None, kind=tarfile.REGTYPE):
        return path, self.content if data is None else data, kind

    def extract(self, members, manifest=None):
        destination = self.root / 'installed'
        with patch.object(assets, 'verify_bundle') as validate:
            assets.extract_payload(self.archive(members), destination, self.spec, manifest or self.payload)
            validate.assert_called_once()
        return destination

    def rejected(self, members, manifest=None):
        with self.assertRaises((ValueError, FileExistsError)):
            self.extract(members, manifest)
        self.assertFalse((self.root / 'installed').exists())
        self.assertEqual(list(self.root.glob('.poster-assets-*')), [])

    def test_archive_hash_and_size_are_required(self):
        file = self.archive([self.member()])
        spec = {'bytes': file.stat().st_size, 'sha256': assets.sha(file)}
        assets.check_archive(file, spec)
        with self.assertRaises(ValueError): assets.check_archive(file, dict(spec, bytes=1))
        with self.assertRaises(ValueError): assets.check_archive(file, dict(spec, sha256='0' * 64))

    def test_only_payload_is_installed_code_and_env_are_skipped(self):
        result = self.extract([self.member(), self.member('packet/run_benchmark.py', b'old code'), self.member('packet/.env', b'not a real credential')])
        self.assertEqual((result / 'data/train_dev.json').read_bytes(), self.content)
        self.assertFalse((result / 'run_benchmark.py').exists())
        self.assertFalse((result / '.env').exists())
        self.assertEqual(json.loads((result / 'bundle_manifest.json').read_text()), self.payload)

    def test_parent_traversal_cannot_escape_staging(self):
        self.rejected([self.member(), self.member('packet/../../outside')])
        self.assertFalse((self.root / 'outside').exists())

    def test_absolute_archive_paths_rejected(self):
        self.rejected([self.member('/tmp/absolute')])

    def test_symlinks_rejected_even_when_not_in_manifest(self):
        self.rejected([self.member(), self.member('packet/link', kind=tarfile.SYMTYPE)])

    def test_duplicate_required_file_rejected(self):
        self.rejected([self.member(), self.member()])

    def test_missing_required_file_rejected(self):
        self.rejected([self.member('packet/old_code.py')])

    def test_payload_digest_mismatch_rejected(self):
        self.rejected([self.member(data=b'X' * len(self.content))])

    def test_payload_size_mismatch_rejected(self):
        self.rejected([self.member(data=b'short')])

    def test_unsafe_manifest_paths_rejected(self):
        bad = {'files': {'../outside': self.payload['files']['data/train_dev.json']}}
        self.rejected([self.member()], bad)

    def test_existing_destination_is_never_overwritten(self):
        destination = self.root / 'installed'
        destination.mkdir()
        sentinel = destination / 'keep';sentinel.write_text('unchanged')
        with self.assertRaises(FileExistsError):
            assets.extract_payload(self.archive([self.member()]), destination, self.spec, self.payload)
        self.assertEqual(sentinel.read_text(), 'unchanged')

    def test_missing_ssh_upload_fails_before_extraction(self):
        with patch('sys.argv', ['assets.py', 'fetch', '--archive', str(self.root / 'missing.tar'), '--bundle-dir', str(self.root / 'bundle')]), patch.object(assets, 'extract_payload') as extract:
            with self.assertRaisesRegex(FileNotFoundError, 'Upload the archive via SSH first'):
                assets.main()
            extract.assert_not_called()

    def test_existing_bundle_must_match_git_manifest(self):
        destination = self.root / 'bundle';destination.mkdir()
        (destination / 'bundle_manifest.json').write_text('{}')
        with patch('sys.argv', ['assets.py', 'verify', '--bundle-dir', str(destination)]), patch.object(assets, 'verify_bundle') as validate:
            with self.assertRaisesRegex(ValueError, 'differs from Git'):
                assets.main()
            validate.assert_not_called()

    def test_env_and_binary_outputs_ignored_but_template_visible(self):
        relative = ['experiments/poster_benchmark/' + name for name in ['.env', '.env.runpod', '.env_copy', 'private.raw.env', 'assets/references/model.pt', 'runs/a/latest.pt', '.venv/bin/python']]
        template = 'experiments/poster_benchmark/.env.example'
        result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin'], cwd=ROOT.parents[1], input='\n'.join(relative + [template]) + '\n', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), set(relative))


if __name__ == '__main__':
    unittest.main()
