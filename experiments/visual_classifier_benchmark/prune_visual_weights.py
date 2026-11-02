"""Explicit plan/apply for completed visual-experiment weights only; preserve data and reports."""
from pathlib import Path
from contextlib import ExitStack, contextmanager
import argparse
import fcntl
import json
import math

from resultlib import inside, read, require, save, sha
from qwen_zero_shot_report import reference

HERE = Path(__file__).resolve().parent
RUNS = HERE / 'runs'
MODELS = Path('/workspace/models')
KEEP_RUNS = ('siglip-ai-reviewed-v1-fp32', 'siglip-poster-v2-mix-fp32')
UNUSED_DOWNLOADS = ('dinov2-small', 'dinov2-base', 'convnextv2-tiny-384', 'dinov3-b16')


@contextmanager
def locks():
    with ExitStack() as stack:
        for root in sorted(RUNS.iterdir()):
            path = root / '.lock'
            if path.is_file():
                # Linux NFS implements LOCK_EX using a write lock: a read-only fd gets EBADF.
                # r+ keeps the existing lock file intact and never creates/truncates it.
                stream = stack.enter_context(path.open('r+'))
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def identity():
    expected_accuracy = 0.8439716312056738
    original = inside(RUNS, KEEP_RUNS[0])
    require(read(original / 'status.json')['status'] == 'complete', 'Original84.40 run must be complete')
    chosen = read(original / 'selection_frozen.json')
    require(read(original / 'selection_commit.json')['sha256'] == sha(original / 'selection_frozen.json'),
            'Original selected model metadata changed')
    best = inside(original, chosen['checkpoint'])
    require(best.is_file() and sha(best) == chosen['artifact_hashes'][best.name], 'Original84.40 checkpoint changed')
    score = read(original / 'siglip2_partial/test_metrics.json')['label_wise_accuracy']
    require(math.isclose(score, expected_accuracy, abs_tol=1e-12), 'Not the84.40 corrected partial model')
    info = reference(inside(RUNS, KEEP_RUNS[1]))
    require(info['model'] == 'siglip2_previous_partial', 'Latest reference is not the preserved partial model')
    score = read(inside(RUNS, KEEP_RUNS[1]) / info['model'] / 'test_metrics.json')['label_wise_accuracy']
    require(math.isclose(score, expected_accuracy, abs_tol=1e-12), 'Latest reference accuracy differs')
    for name in ('siglip2-base-384', 'qwen3.5-9b'):
        require((MODELS / name).is_dir(), 'Protected source model directory missing: ' + name)
    return dict(original_checkpoint=str(best), original_checkpoint_sha256=sha(best),
                original_selection_sha256=sha(original / 'selection_frozen.json'),
                latest_reference=info, preserved_run_directories=list(KEEP_RUNS),
                preserved_model_directories=['siglip2-base-384', 'qwen3.5-9b'])


def metadata_files():
    # .npy/.npz, heads, JSON, CSV, logs and all other non-weight artifacts remain byte-for-byte.
    return {p.relative_to(RUNS).as_posix(): sha(inside(RUNS, p.relative_to(RUNS).as_posix()))
            for p in RUNS.rglob('*') if p.is_file() and p.name != '.lock' and
            not p.name.endswith(('.safetensors', '.safetensors.tmp'))}


def eligible(path):
    path = Path(path)
    if path.is_relative_to(RUNS):
        relative = path.relative_to(RUNS)
        require(len(relative.parts) > 1 and relative.parts[0] not in KEEP_RUNS and
                relative.parts[0].startswith(('siglip-', 'dinov2-')) and path.name.endswith('.safetensors'),
                'Not an authorized experiment weight: ' + str(path))
        root = RUNS / relative.parts[0]
        require(read(root / 'status.json')['status'] == 'complete', 'Never prune an incomplete/running run')
        return inside(RUNS, relative.as_posix())
    require(path.is_relative_to(MODELS), 'Path outside cleanup scope')
    relative = path.relative_to(MODELS)
    require(len(relative.parts) > 1 and relative.parts[0] in UNUSED_DOWNLOADS and
            (path.name.endswith('.safetensors') or path.name.startswith('pytorch_model') and path.suffix == '.bin'),
            'Not an authorized unused downloaded weight')
    return inside(MODELS, relative.as_posix())


def plan(out):
    with locks():
        guard = identity()
        candidates = []
        for root in sorted(RUNS.iterdir()):
            if root.name in KEEP_RUNS or not root.is_dir() or not root.name.startswith(('siglip-', 'dinov2-')):
                continue
            status = root / 'status.json'
            if not status.exists() or read(status).get('status') != 'complete':
                continue
            candidates.extend(root.rglob('*.safetensors'))
        for name in UNUSED_DOWNLOADS:
            root = MODELS / name
            if root.is_dir():
                candidates.extend(p for p in root.rglob('*') if p.is_file() and
                                  (p.name.endswith('.safetensors') or p.name.startswith('pytorch_model') and p.suffix == '.bin'))
        files = []
        for candidate in sorted(set(candidates)):
            path = eligible(candidate)
            files.append(dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path)))
        require(not out.exists(), 'Keep the old audit plan; choose another --plan path')
        require(not out.is_relative_to(RUNS) and not out.is_relative_to(MODELS), 'Keep cleanup audit outside runs/models')
        save(out, dict(schema='visual-weight-prune-v1', cleanup_script_sha256=sha(Path(__file__)),
            guard=guard, metadata_sha256=metadata_files(), files=files,
            recoverable_from_this_plan=False,
            note='Only filenames/sizes/checksums are recorded, not a backup of removed weights. '
                 'Old full-run replay/hash verification can fail after pruning; CSV/JSON/log/arrays are preserved.'))
        print(json.dumps(dict(plan=str(out), deleting_executed=False, files=len(files),
                              reclaimable_GiB=sum(r['bytes'] for r in files) / 1024**3, preserved=guard),
                         ensure_ascii=False, indent=2))


def apply(out):
    require(not out.is_relative_to(RUNS) and not out.is_relative_to(MODELS), 'Audit must be outside runs/models')
    value = read(out)
    require(value['schema'] == 'visual-weight-prune-v1' and value['cleanup_script_sha256'] == sha(Path(__file__)),
            'Plan/script changed; inspect and create a new plan')
    receipt = out.with_name(out.stem + '.receipt.json')
    with locks():
        require(identity() == value['guard'], 'Protected84.40/source reference changed; nothing else removed')
        require(all(inside(RUNS, n).is_file() and sha(inside(RUNS, n)) == h for n, h in value['metadata_sha256'].items()),
                'Existing comparison material changed since plan')
        state = read(receipt) if receipt.exists() else None
        require(state is None or state['plan_sha256'] == sha(out), 'Existing receipt belongs to another plan')
        paths = []
        for item in value['files']:
            path = eligible(Path(item['path']))
            if path.exists():
                require(path.stat().st_size == item['bytes'] and sha(path) == item['sha256'], 'Weight changed: ' + str(path))
            else:
                require(state is not None, 'Weight disappeared before first apply; create a new plan')
            paths.append(path)
        if state is None:
            state = dict(status='applying', plan_sha256=sha(out), removed=[],
                         note='A missing listed file after this receipt was started is treated as a resumed deletion.')
            save(receipt, state)
        for path in paths:
            if path.exists():
                path.unlink()
            if str(path) not in state['removed']:
                state['removed'].append(str(path))
                save(receipt, state)
        require(identity() == value['guard'] and all(sha(inside(RUNS, n)) == h for n, h in value['metadata_sha256'].items()),
                'Post-cleanup preservation check failed; keep the plan/receipt for investigation')
        state.update(status='complete', removed_files=len(paths),
                     planned_reclaimed_GiB=sum(r['bytes'] for r in value['files']) / 1024**3)
        save(receipt, state)
        print(json.dumps(state, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('plan', 'apply'))
    p.add_argument('--plan', type=Path, default=HERE / 'reviews/weights-cleanup-v1/plan.json')
    args = p.parse_args()
    out = args.plan.expanduser().resolve()
    (plan if args.command == 'plan' else apply)(out)


if __name__ == '__main__':
    main()
