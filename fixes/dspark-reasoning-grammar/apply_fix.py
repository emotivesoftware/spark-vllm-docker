#!/usr/bin/env python3
"""Build-time backport of vLLM #44297 + #44993; default is read-only."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

PATCH_FILE = Path(__file__).with_name('patch.json')


def patch_source(source, entry):
    if hashlib.sha256(source.encode()).hexdigest() != entry['before_sha256']:
        raise ValueError(f"Unsupported or already patched source: {entry['path']}")
    for replacement in entry['replacements']:
        if source.count(replacement['old']) != 1:
            raise ValueError(f"Missing or ambiguous anchor: {entry['path']}")
        source = source.replace(replacement['old'], replacement['new'], 1)
    if hashlib.sha256(source.encode()).hexdigest() != entry['after_sha256']:
        raise ValueError(f"Patched hash mismatch: {entry['path']}")
    compile(source, entry['path'], 'exec')
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='vllm package directory')
    parser.add_argument('--apply', action='store_true', help='Write verified changes during an image build only')
    args = parser.parse_args()
    root = args.root
    if root is None:
        spec = importlib.util.find_spec('vllm')
        if spec is None or spec.origin is None:
            raise SystemExit('vllm package not found')
        root = Path(spec.origin).parent
    manifest = json.loads(PATCH_FILE.read_text())
    changes = []
    # Validate all three files and compile every result before writing anything.
    for entry in manifest['files']:
        path = root / entry['path']
        source = path.read_bytes().decode('utf-8')
        changes.append((path, patch_source(source, entry)))
    if args.apply:
        for path, source in changes:
            path.write_bytes(source.encode('utf-8'))
    print(('Applied' if args.apply else 'Validated (read-only)') + ' vLLM #44297 + #44993 backport')


if __name__ == '__main__':
    main()
