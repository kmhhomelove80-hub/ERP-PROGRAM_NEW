"""Whitelist-only query measurement package; never bundles field diagnostics."""
import argparse
import hashlib
from pathlib import Path
import zipfile


def build(output):
    root = Path(__file__).resolve().parents[1]
    guide = root / 'deploy/windows/actas-query-probe'
    files = {name: (root / 'scripts' / name).read_bytes() for name in ('actas_query_probe.py', 'actas_export_check.py', 'actas_conditions_ocr.ps1')}
    for name in ('Probe-Query.cmd', 'README-FIRST.txt'):
        files[name] = (guide / name).read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
    files['SHA256SUMS.txt'] = ''.join(hashlib.sha256(value).hexdigest() + '  ' + name + '\n'
                                      for name, value in files.items()).encode()
    output = Path(output);output.mkdir(parents=True, exist_ok=True)
    path = output / 'ACTAS-query-probe-v1-20261105.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():archive.writestr('actas-query-probe-v1/' + name, data)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        for name, data in files.items():assert archive.read('actas-query-probe-v1/' + name) == data
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix('.zip.sha256').write_text(digest + '  ' + path.name + '\n')
    print(path.name, path.stat().st_size, digest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Actas 조회 신호 측정 패키지 생성')
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
