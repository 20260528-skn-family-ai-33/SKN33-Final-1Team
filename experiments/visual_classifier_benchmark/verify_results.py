"""Verify a downloaded export using only Python's standard library."""
from pathlib import Path
import argparse
import json

from resultlib import extract_archive, require, save, verify_results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Export .tar.gz or an extracted evidence directory')
    parser.add_argument('--extract-to', type=Path)
    parser.add_argument('--out', type=Path, help='Write verification JSON outside the evidence directory')
    args = parser.parse_args()
    if args.input.is_file():
        require(args.extract_to is not None, 'Archives require --extract-to NEW_DIRECTORY')
        root = extract_archive(args.input, args.extract_to)
    else:
        require(args.extract_to is None, '--extract-to is only valid for an archive')
        root = args.input
    result = verify_results(root)
    if args.out:
        require(not args.out.resolve().is_relative_to(root.resolve()), 'Verification output must be outside immutable evidence')
        save(args.out, result)
    print(json.dumps(dict(status=result['status'], evidence=str(root), models=len(result['models']),
                          selected_source=result['selected']['source'], omitted_checkpoints=result['omitted_checkpoints'],
                          parameter_updates_independently_verified=False), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
