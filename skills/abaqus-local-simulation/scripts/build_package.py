"""Mechanical packaging: manifest + verified ZIP, no simulations or old file changes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

BASE = Path(__file__).resolve().parents[1]


def selected_files():
    result = []
    for path in sorted(BASE.rglob('*')):
        if not path.is_file() or path.name == 'PACKAGE_MANIFEST.json':
            continue
        if '__pycache__' in path.parts or path.suffix.lower() in ('.pyc', '.pyo', '.odb', '.res', '.stt', '.mdl', '.dll'):
            continue
        result.append(path)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    destination = Path(args.output)
    if not destination.is_absolute() or destination.suffix.lower() != '.zip':
        raise ValueError('Absolute new .zip output path required')
    if destination.exists():
        raise FileExistsError('Refusing to overwrite existing package: ' + str(destination))
    files = selected_files()
    records = [dict(path=str(path.relative_to(BASE)).replace('\\','/'), bytes=path.stat().st_size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in files]
    manifest = dict(name='abaqus-local-simulation', version='1.0.0', files=records,
        solver_binaries_included=False, model_or_odb_included=False,
        interfaces=['Skill','CLI','local stdio MCP'], license='MIT for newly authored bridge only')
    manifest_path = BASE / 'PACKAGE_MANIFEST.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    files.append(manifest_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, arcname=BASE.name + '/' + str(path.relative_to(BASE)).replace('\\','/'))
    with zipfile.ZipFile(destination, 'r') as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC check failed')
        for row in records:
            member = archive.read(BASE.name + '/' + row['path'])
            if hashlib.sha256(member).hexdigest() != row['sha256']:
                raise ValueError('ZIP hash mismatch: ' + row['path'])
    print(json.dumps({'zip':str(destination),'files':len(files),'bytes':destination.stat().st_size,
        'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'crc_and_file_hashes_verified':True},indent=2))


if __name__ == '__main__':
    main()
