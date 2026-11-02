"""Verify and unpack SSH-uploaded poster assets. Standard library only."""
from pathlib import Path, PurePosixPath
import argparse
import contextlib
import hashlib
import json
import os
import shutil
import tarfile
import tempfile

from benchlib import contained, read, sha, verify_bundle

HERE = Path(__file__).resolve().parent
CHUNK = 2 ** 20


def check_archive(path, spec):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != spec['bytes'] or sha(path) != spec['sha256']:
        raise ValueError('Archive size/SHA256 differs from the committed manifest')


def extract_payload(archive, destination, spec, payload):
    """Extract only manifest payloads. Never extract code, credentials or links."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Bundle destination already exists; verify it or use another directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.poster-assets-', dir=destination.parent))
    try:
        expected = payload['files']
        for relative in expected:
            p = PurePosixPath(relative)
            if p.is_absolute() or '..' in p.parts or p.parts[0] not in ('data', 'references'):
                raise ValueError('Invalid path in payload manifest')
        seen = set()
        with tarfile.open(archive, 'r:') as stream:
            for member in stream:
                name = PurePosixPath(member.name)
                if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != spec['archive_prefix']:
                    raise ValueError('Unsafe archive member path')
                if not member.isfile():
                    raise ValueError('Only regular files are allowed in the uploaded archive')
                relative = '/'.join(name.parts[1:])
                if relative not in expected:
                    continue  # The original archive also contains old scripts; Git supplies all code.
                if relative in seen or member.size != expected[relative]['bytes']:
                    raise ValueError('Duplicate member or payload size mismatch')
                seen.add(relative)
                target = contained(stage, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with contextlib.closing(stream.extractfile(member)) as source, target.open('xb') as output:
                    while chunk := source.read(CHUNK):
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != expected[relative]['sha256']:
                    raise ValueError('Extracted payload SHA256 mismatch')
        if seen != set(expected):
            raise ValueError('Archive is missing required payload files')
        (stage / 'bundle_manifest.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        verify_bundle(stage)
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['fetch', 'verify'])
    parser.add_argument('--bundle-dir', type=Path, default=Path(os.environ.get('POSTER_BUNDLE_DIR', str(HERE / 'assets'))))
    parser.add_argument('--archive', type=Path, default=Path(os.environ.get('POSTER_BUNDLE_ARCHIVE', '/workspace/runpod-poster-benchmark-2026-10-22.tar')), help='Original archive uploaded via SSH; never downloaded by this script')
    args = parser.parse_args()
    spec, payload = read(HERE / 'asset_manifest.json'), read(HERE / 'bundle_manifest.json')
    destination = args.bundle_dir.expanduser().resolve()
    if destination.exists() or args.command == 'verify':
        if read(destination / 'bundle_manifest.json') != payload:
            raise ValueError('Bundle manifest differs from Git; use the pinned asset packet')
        verify_bundle(destination)
    else:
        archive = args.archive.expanduser().resolve()
        if not archive.is_file():
            raise FileNotFoundError('Upload the archive via SSH first: ' + str(archive) + '; or set POSTER_BUNDLE_ARCHIVE / POSTER_BUNDLE_DIR')
        check_archive(archive, spec)
        extract_payload(archive, destination, spec, payload)
    print(json.dumps({'status': 'assets_verified', 'bundle': str(destination), 'payload_files': len(payload['files']), 'images': 187}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, tarfile.TarError) as exc:
        raise SystemExit(str(exc)) from None
