"""Build a source-only automatic collection pilot, never field data/credentials."""
import argparse
import hashlib
from pathlib import Path
import zipfile


def build(output):
    root=Path(__file__).resolve().parents[1]
    names=('actas_auto_collect.py','actas_export_check.py','actas_query_probe.py','actas_conditions_ocr.ps1')
    files={name:(root/'scripts'/name).read_bytes() for name in names}
    guide=root/'deploy/windows/actas-auto-collect'
    for name in ('Run-Auto-Check.cmd','README-FIRST.txt'):
        files[name]=(guide/name).read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
    files['SHA256SUMS.txt']=''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in files.items()).encode()
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    path=output/'ACTAS-auto-collect-v1-20261105.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr('actas-auto-collect-v1/'+name,data)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        for name,data in files.items():assert archive.read('actas-auto-collect-v1/'+name)==data
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix('.zip.sha256').write_text(digest+'  '+path.name+'\n')
    print(path.name,path.stat().st_size,digest)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Actas 자동 수집 검증 패키지 생성')
    parser.add_argument('--output',type=Path,required=True)
    build(parser.parse_args().output)
