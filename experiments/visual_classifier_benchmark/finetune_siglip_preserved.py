"""Run targeted v2 using the retained partial checkpoint after prior-weight cleanup.

Only the specifically identified missing legacy checkpoint may be absent. The
legacy metadata and its other selected artifacts, plus the complete partial
reference, remain subject to their original integrity checks.
"""
from pathlib import Path

import finetune_siglip_targeted_v2 as v2
import finetune_siglip_focused as focused
import finetune_siglip as ft
from benchlib import contained, digest, read, sha

HERE = Path(__file__).resolve().parent
MISSING_RELATIVE = 'candidates/lr3e-05-train_balanced/best.safetensors'
MISSING_SHA256 = '7da5d6e81d71ca5dcc92ef84f42a4f8c3f98b79e0255879b655afff0c31516fa'
original_input_protocol = focused.input_protocol
original_sources = v2.sources


def input_protocol(args):
    selection = read(args.prior_run / 'selection_frozen.json')
    checkpoint = contained(args.prior_run, selection['checkpoint'])
    if checkpoint.is_file():
        return original_input_protocol(args)

    require = v2.require
    require(selection['checkpoint'] == MISSING_RELATIVE, 'Unexpected missing prior checkpoint; restore it first')
    artifacts = selection['artifact_hashes']
    require(artifacts.get(checkpoint.name) == MISSING_SHA256, 'Unexpected prior checkpoint identity')
    baseline, head = ft.assert_baseline(args.baseline)
    protocol = read(args.prior_run / 'protocol.json')
    status = read(args.prior_run / 'status.json')
    require(status['status'] == 'complete' and protocol['signature'] == digest(protocol['config'])
            == status['protocol_signature'] == selection['protocol_signature'], 'Prior protocol mismatch')
    require(read(args.prior_run / 'selection_commit.json')['sha256'] == sha(args.prior_run / 'selection_frozen.json'),
            'Prior selection changed')
    for name, expected in artifacts.items():
        artifact = contained(checkpoint.parent, name)
        if artifact == checkpoint:
            continue
        require(artifact.is_file() and sha(artifact) == expected, 'Prior selected artifact missing or changed: ' + name)
    for name, expected in protocol['config']['script_hashes'].items():
        require(sha(HERE / name) == expected, 'Prior source changed: ' + name)
    config = protocol['config']
    require(config['baseline_protocol_sha256'] == sha(args.baseline / 'protocol.json')
            and config['baseline_selection_sha256'] == sha(args.baseline / 'selection_frozen.json')
            and config['model_packet'] == baseline['config']['models']['siglip2'], 'Prior baseline chain changed')
    print('Prior full checkpoint missing: identity recorded but file cannot be verified; '
          'the retained partial checkpoint is independently validated and used for training.', flush=True)
    return baseline, head, selection


def sources():
    hashes = original_sources()
    for name in ('finetune_siglip_preserved.py', 'run_preserved_finetune.sh'):
        hashes[name] = sha(HERE / name)
    return hashes


if __name__ == '__main__':
    focused.input_protocol = input_protocol
    v2.sources = sources
    v2.main()
