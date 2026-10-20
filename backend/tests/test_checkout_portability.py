"""Frozen migration bytes survive a checkout configured like Windows Git."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_frozen_schema_files_keep_exact_bytes_with_autocrlf(tmp_path):
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    contract_path = 'backend/app/persistence/schema_contract.json'
    contract = json.loads((ROOT / contract_path).read_text(encoding='utf-8'))
    expected = dict(contract['migration_sha256'])
    expected[contract_path] = hashlib.sha256((ROOT / contract_path).read_bytes()).hexdigest()
    attributes = ROOT / '.gitattributes'
    if attributes.exists():
        shutil.copyfile(attributes, checkout / '.gitattributes')
    for relative in expected:
        destination = checkout / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)

    # All Git writes target this disposable fixture, never the project index/config.
    empty_attributes = tmp_path / 'empty-attributes'
    empty_attributes.write_bytes(b'')
    def git(*args):
        result = subprocess.run(
            ['git', '-c', 'core.autocrlf=true', '-c', 'core.safecrlf=false',
             '-c', 'core.attributesFile=' + str(empty_attributes), *args],
            cwd=checkout, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    git('init', '-q')
    git('add', '.')
    for relative in expected:
        (checkout / relative).unlink()
    git('checkout-index', '--all', '--force')
    for relative, digest in expected.items():
        assert hashlib.sha256((checkout / relative).read_bytes()).hexdigest() == digest, relative
