"""Explicit root initialization; never part of web startup or DB migration."""
import argparse
import json
from .local import LocalStorage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('command', choices=('init', 'check'))
    args = parser.parse_args()
    if args.command == 'init': LocalStorage.initialize(args.root)
    storage = LocalStorage(args.root)
    try:
        storage.start()
        print(json.dumps(storage.capabilities(), sort_keys=True))
    finally: storage.close()


if __name__ == '__main__': main()
