"""Export completed evidence; never installs packages, trains or contacts RunPod."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import shutil
import tarfile
import tempfile

from resultlib import RUNS, LABELS, SCHEMA, inside, read, require, save, sha, verify_results

HERE = Path(__file__).resolve().parent
SOURCE_FILES = ('visual_classifier_benchmark/encoders.py', 'visual_classifier_benchmark/run_experiment.py',
                'visual_classifier_benchmark/finetune_siglip.py', 'visual_classifier_benchmark/finetune_siglip_focused.py',
                'poster_benchmark/benchlib.py', 'visual_classifier_benchmark/resultlib.py',
                'visual_classifier_benchmark/export_results.py', 'visual_classifier_benchmark/verify_results.py',
                'visual_classifier_benchmark/build_result_report.py', 'visual_classifier_benchmark/result_report.css')


def export_results(runs_root, bundle, output, checkpoints='none', include_images=True, sources=None):
    runs_root, bundle, output = (Path(p).resolve() for p in (runs_root, bundle, output))
    sources = Path(sources or HERE.parent).resolve()
    require(checkpoints in ('none', 'selected', 'all'), 'Unknown checkpoint mode')
    require(output.name.endswith('.tar.gz'), 'Output must end in .tar.gz')
    require(not output.exists() and not output.with_name(output.name + '.sha256').exists(), 'Export already exists; use a new filename')
    require(not any(output.is_relative_to(p) for p in (runs_root, bundle, sources / 'visual_classifier_benchmark')),
            'Export output must be outside input/code directories')
    # This gate runs before creating output folders or touching any result files.
    status_hashes = {}
    for run in RUNS:
        folder = runs_root / run
        require(read(folder / 'status.json')['status'] == 'complete', 'Run is still running or incomplete: ' + run)
        status_hashes[run] = sha(folder / 'status.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.visual-export-', dir=output.parent) as temporary:
        staging = Path(temporary) / 'evidence'; staging.mkdir()
        files, source_hashes, omitted = {}, {}, {}
        def copy(source, name):
            source = Path(source)
            require(source.is_file() and not source.is_symlink(), 'Missing or symlink input: ' + str(source))
            before = sha(source)
            target = inside(staging, name); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            require(sha(source) == sha(target) == before, 'Source changed while copying: ' + str(source))
            files[name] = dict(bytes=target.stat().st_size, sha256=before)
            source_hashes[source] = before
        for run in RUNS:
            folder = runs_root / run
            selected = read(folder / 'selection_frozen.json')
            selected_path = folder / selected['checkpoint'] if 'checkpoint' in selected else None
            for p in sorted(folder.rglob('*')):
                if p.is_dir():
                    require(not p.is_symlink(), 'Symlink directory in results')
                    continue
                relative = p.relative_to(folder).as_posix()
                name = 'runs/' + run + '/' + relative
                if p.suffix == '.safetensors':
                    if checkpoints == 'all' or checkpoints == 'selected' and p == selected_path:
                        copy(p, name)
                    else:
                        omitted[name] = sha(p)
                elif p.suffix in ('.json', '.jsonl', '.csv', '.npy', '.log'):
                    copy(p, name)
        copy(bundle / 'bundle_manifest.json', 'bundle/bundle_manifest.json')
        for name in ('dataset_info.json', 'split_ids.json', 'train_dev.json', 'test.json', 'taxonomy.json'):
            copy(bundle / 'data' / name, 'bundle/data/' + name)
        if include_images:
            for row in read(bundle / 'data/test.json'):
                copy(inside(bundle, row['image']), 'bundle/' + row['image'])
        for name in SOURCE_FILES:
            copy(inside(sources, name), 'sources/' + name)
        manifest = dict(schema=SCHEMA, created_at_utc=datetime.now(timezone.utc).isoformat(),
            runs=list(RUNS), labels=LABELS, files=files, omitted_checkpoints=omitted,
            checkpoint_mode=checkpoints, images_included=include_images,
            source_runs_root=str(runs_root), source_bundle=str(bundle))
        save(staging / 'export_manifest.json', manifest)
        # Catch existing corrupt or inconsistent evidence before creating an archive.
        verify_results(staging)
        require(all(sha(p) == checksum for p, checksum in source_hashes.items()), 'Source changed during export')
        require(all(read(runs_root / run / 'status.json')['status'] == 'complete'
                    and sha(runs_root / run / 'status.json') == checksum for run, checksum in status_hashes.items()),
                'Completion status changed during export')
        archive = Path(temporary) / 'results.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            for name in sorted(files) + ['export_manifest.json']:
                stream.add(inside(staging, name), arcname=name, recursive=False)
        checksum = sha(archive)
        archive.rename(output)
        output.with_name(output.name + '.sha256').write_text(checksum + '  ' + output.name + '\n', encoding='utf-8')
    return dict(archive=str(output), sha256=checksum, files=len(files), omitted_checkpoints=len(omitted))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs-root', type=Path, default=HERE / 'runs')
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--checkpoints', choices=('none', 'selected', 'all'), default='none')
    parser.add_argument('--no-images', action='store_true')
    args = parser.parse_args()
    import json
    print(json.dumps(export_results(args.runs_root, args.bundle, args.out, args.checkpoints, not args.no_images), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
