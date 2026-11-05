"""Build only whitelisted source/runbook files, never runtime data or secrets."""
import argparse
import hashlib
from pathlib import Path
import zipfile


def build(output):
    root = Path(__file__).resolve().parents[1]
    guide = root / 'deploy/windows/actas-registration'
    files = {'actas_register.py': (root / 'scripts/actas_register.py').read_bytes(),
             'actas_export_check.py': (root / 'scripts/actas_export_check.py').read_bytes()}
    for name in ('Register-Existing.cmd', 'Collect-And-Register.cmd', 'README-FIRST.txt'):
        files[name] = (guide / name).read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
    files['SHA256SUMS.txt'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                                      for name, data in files.items()).encode('utf-8')
    output = Path(output);output.mkdir(parents=True, exist_ok=True)
    path = output / 'ACTAS-register-v1-20261105.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():archive.writestr('actas-register-v1/' + name, data)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        for name, data in files.items():assert archive.read('actas-register-v1/' + name) == data
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix('.zip.sha256').write_text(digest + '  ' + path.name + '\n')
    print(path, path.stat().st_size, digest)
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Actas 대조 등록 패키지 생성')
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
