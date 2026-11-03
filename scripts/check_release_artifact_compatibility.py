"""Validate against the actual previous ZIP overlay, not an ideal Git snapshot."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parent.parent
RAW_MD='docs/manual/SUNIL_ERP_신입사원_사용매뉴얼.md'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--release',type=Path,required=True)
    parser.add_argument('--previous-zip',type=Path,required=True)
    args=parser.parse_args()
    release=args.release.resolve()
    spec=importlib.util.spec_from_file_location('artifact_update_check',release/'release_updater.py')
    u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
    manifest=json.loads((release/'manifest.json').read_text(encoding='utf-8'))
    assert RAW_MD not in {r['path'] for r in manifest['files']}
    with tempfile.TemporaryDirectory(prefix='sunil-artifact-compat-') as directory:
        root=Path(directory);app=root/'app';app.mkdir()
        data=subprocess.check_output(['git','archive','d425d71'],cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:archive.extractall(app,filter='data')
        with zipfile.ZipFile(args.previous_zip) as old:
            name=next(n for n in old.namelist() if n.endswith('/manifest.json'))
            original=json.loads(old.read(name));prefix=name.removesuffix('manifest.json')+'app/'
            assert RAW_MD not in {r['path'] for r in original['files']}
            for row in original['files']:
                content=old.read(prefix+row['path'])
                assert hashlib.sha256(content).hexdigest()==row['sha256']
                target=app/row['path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        # This is the precise old-base + actual Nov 3 ZIP state of the server.
        assert u.digest(app/RAW_MD)=='9fa084ac3718321e21d9db86c4db8e375d35c9180110d1a7e13ae92816092bec'
        u.validate_payload(release,app,manifest)
        # Even a privately edited manuscript stays intact; runtime edits still fail.
        (app/RAW_MD).write_text('private source manuscript, preserve verbatim',encoding='utf-8')
        preserved=(app/RAW_MD).read_bytes()
        run=root/'run';run.mkdir();journal={'rollback_ready':False}
        u.install_files(release,app,run,manifest,journal)
        assert (app/RAW_MD).read_bytes()==preserved
        u.restore_files(app,run,manifest)
        assert (app/RAW_MD).read_bytes()==preserved
        runtime=app/'inventory/views.py';runtime.write_bytes(runtime.read_bytes()+b'\n# separate runtime edit\n')
        try:u.validate_payload(release,app,manifest)
        except RuntimeError:pass
        else:raise AssertionError('Separate runtime edit was not blocked')
    print('PASS: actual d425d71 + published November 3 ZIP passes current release preflight; existing manuscript preserved through apply/rollback; runtime custom-edit protection remains enforced.')


if __name__=='__main__':main()
