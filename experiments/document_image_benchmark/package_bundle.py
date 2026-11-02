"""Package prepared local inputs and code; never execute model runtimes."""
import argparse
import json
from pathlib import Path
import shutil
import tarfile

from core import sha, save, validate_packet
from runtime import SOURCE_HASHES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    repo = source.parents[1]
    root = args.prepared.resolve()
    packet = validate_packet(root / 'packet')
    if args.archive.exists() or (root / 'code').exists() or (root / 'bundle_manifest.json').exists():
        raise ValueError('기존 실행 묶음/압축파일은 덮어쓰지 않습니다.')
    code = root / 'code'
    code.mkdir()
    for path in source.glob('*.py'):
        shutil.copyfile(path, code / path.name)
    shutil.copytree(source / 'schemas', code / 'schemas', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(source / 'tests', code / 'tests', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(source / 'README.md', root / 'README.md')
    for relative, digest in SOURCE_HASHES.items():
        path = repo / relative
        if sha(path) != digest:
            raise ValueError('검토한 의존 코드가 변경되었습니다: ' + relative)
        target = code / 'dependencies' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    shutil.copyfile(repo / 'scripts/__init__.py', code / 'dependencies/scripts/__init__.py')
    files = {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob('*')) if path.is_file()}
    save(root / 'bundle_manifest.json', {'schema': 'document-image-bundle-v1', 'files': files,
        'cases': len(packet['cases']), 'conditions': sum(len(c['conditions']) for c in packet['cases']),
        'model_execution_during_preparation': False})
    def metadata(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        info.mode = 0o755 if info.isdir() else 0o644
        return info
    with tarfile.open(args.archive, 'x:gz', format=tarfile.PAX_FORMAT) as archive:
        archive.add(root, arcname=root.name, filter=metadata)
    digest = sha(args.archive)
    with Path(str(args.archive) + '.sha256').open('x', encoding='utf-8') as stream:
        stream.write(digest + '  ' + args.archive.name + '\n')
    # Confirm the actual gzip archive is complete and content hashes survive packaging.
    import hashlib
    with tarfile.open(args.archive, 'r:gz') as archive:
        for relative, expected in files.items():
            stream = archive.extractfile(root.name + '/' + relative)
            if stream is None or hashlib.sha256(stream.read()).hexdigest() != expected:
                raise ValueError('압축파일 검증 실패: ' + relative)
    print(json.dumps({'archive': str(args.archive), 'bytes': args.archive.stat().st_size,
                      'sha256': digest, 'verified_files': len(files)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
