"""Append reviewed posters to the exact completed Open Images packet; no training."""
from pathlib import Path
import argparse
import json
import shutil
import tempfile

import prepare_partial_data as data
from benchlib import LABELS, contained, read, save, sha

HERE = Path(__file__).resolve().parent
ASSETS = HERE / 'poster_augment_v2'
require = data.require


def verify_assets():
    manifest = read(ASSETS / 'package_manifest.json')
    require(manifest['schema'] == 'curated-poster-augment-v2' and manifest['labels'] == LABELS,
            'Unexpected poster package schema')
    actual = {p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob('*') if p.is_file()}
    require(actual == set(manifest['files']) | {'package_manifest.json'}, 'Poster assets missing or unexpected')
    for relative, expected in manifest['files'].items():
        p = contained(ASSETS, relative)
        require(not p.is_symlink() and p.stat().st_size == expected['bytes'] and sha(p) == expected['sha256'],
                'Poster asset changed: ' + relative)
    rows = data.csv_read(ASSETS / 'additional_labels.csv', data.NEW_FIELDS)
    require(len(rows) == manifest['new_images'] and all(data.approved(r) and r['split'] == 'train' for r in rows),
            'New posters must be explicitly reviewed TRAIN rows')
    require(sha(ASSETS / 'train_corrections.csv') == manifest['corrections_sha256'], '32-cell approval CSV changed')
    return manifest, rows


def check_preserved(parent, parent_rows, output, bundle, package):
    manifest, rows = data.validate_packet(output, bundle)
    require(manifest['added_images'] == parent['added_images'] + package['new_images'] and
            manifest['changed_train_cells'] == parent['changed_train_cells'] == 32,
            'Existing additions or 32 corrections were lost')
    current = {r['image_id']: r for r in rows}
    for old in parent_rows:
        require(current.get(old['image_id']) == old, 'Existing train/dev record changed: ' + old['image_id'])
        require(sha(output / old['image']) == parent['files'][old['image']]['sha256'], 'An inherited image changed')
    require(sha(output / 'train_corrections.csv') == package['corrections_sha256'], 'Correction bytes not preserved')
    return manifest, rows


def build(args):
    package, new_rows = verify_assets()
    require(args.parent_packet != args.output and not args.output.is_relative_to(args.parent_packet) and
            not args.parent_packet.is_relative_to(args.output), 'Use a separate new packet directory')
    parent, old_rows = data.validate_packet(args.parent_packet, args.bundle)
    require(sha(args.parent_packet / 'packet_manifest.json') == package['parent_manifest_sha256'],
            'Parent differs from the completed siglip-openimages-v1 packet; do not substitute or rebuild it')
    require(parent['added_images'] == package['parent_added_images'] == 327 and
            parent['changed_train_cells'] == 32 and parent['base_manifest_sha256'] == package['base_manifest_sha256'],
            'Expected the completed 327-addition / 32-correction packet')
    require(sha(args.parent_packet / 'train_corrections.csv') == package['corrections_sha256'],
            'The existing 32 approved correction bytes differ')
    lineage_path = args.output.with_name(args.output.name + '.lineage.json')
    identity = dict(parent_manifest_sha256=sha(args.parent_packet / 'packet_manifest.json'),
                    poster_package_sha256=sha(ASSETS / 'package_manifest.json'),
                    corrections_sha256=package['corrections_sha256'])
    if args.output.exists():
        require(lineage_path.is_file(), 'Existing output has no lineage; choose a new output directory')
        lineage = read(lineage_path)
        require(all(lineage.get(k) == value for k, value in identity.items()) and
                lineage['packet_manifest_sha256'] == sha(args.output / 'packet_manifest.json'),
                'Existing output belongs to different inputs')
        manifest, rows = check_preserved(parent, old_rows, args.output, args.bundle, package)
        return dict(packet=str(args.output), added_images=manifest['added_images'], changed_train_cells=32,
                    new_posters=package['new_images'], reused=True, training_started=False)

    prior_csv = data.csv_read(args.parent_packet / 'additional_labels.csv', data.NEW_FIELDS)
    by_id = {r['image_id']: r for r in old_rows}
    merged = []
    for number, source in enumerate(prior_csv, 1):
        row = dict(source)
        if data.approved(row):
            old = by_id['A' + str(number).zfill(5)]
            require(row['split'] == old['split'] == 'train' and
                    {k: data.cell(row[k]) for k in LABELS} == old['labels'], 'Inherited approval differs')
            row['image_path'] = str(contained(args.parent_packet, old['image']))
        merged.append(row)
    for source in new_rows:
        row = dict(source)
        row['image_path'] = str(contained(ASSETS, row['image_path']))
        merged.append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.poster-review-', dir=args.output.parent) as temporary:
        review = Path(temporary)
        shutil.copyfile(args.parent_packet / 'train_corrections.csv', review / 'train_corrections.csv')
        context = read(args.parent_packet / 'review_context.json')
        context.update(poster_augmentation=identity)
        save(review / 'review_context.json', context)
        data.csv_write(review / 'additional_labels.csv', data.NEW_FIELDS, merged)
        # Existing builder checks file/pixel/near duplicates against base train/dev/test and every addition.
        data.build_packet(args.bundle, review, args.output)
    manifest, rows = check_preserved(parent, old_rows, args.output, args.bundle, package)
    lineage = dict(**identity, packet_manifest_sha256=sha(args.output / 'packet_manifest.json'),
                   inherited_added_images=327, new_posters=package['new_images'], changed_train_cells=32,
                   train_images=sum(r['split'] == 'train' for r in rows), dev_images=sum(r['split'] == 'dev' for r in rows),
                   test_images_added_to_training=0, training_started=False)
    save(lineage_path, lineage)
    return dict(packet=str(args.output), added_images=manifest['added_images'], changed_train_cells=32,
                new_posters=package['new_images'], train_images=lineage['train_images'],
                dev_images=lineage['dev_images'], preserved_existing_records=True, training_started=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=HERE.parent / 'poster_benchmark/assets')
    parser.add_argument('--parent-packet', type=Path, default=HERE / 'training_packets/siglip-openimages-v1')
    parser.add_argument('--out', dest='output', type=Path, default=HERE / 'training_packets/siglip-poster-v2')
    args = parser.parse_args()
    for name in ('bundle', 'parent_packet', 'output'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    print(json.dumps(build(args), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
