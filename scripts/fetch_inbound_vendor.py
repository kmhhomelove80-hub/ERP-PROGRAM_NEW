"""Reproducible SheetJS mirror download; shipped statically, no runtime CDN."""
import base64,hashlib,io,tarfile,urllib.request
from pathlib import Path
URL='https://registry.npmjs.org/@e965/xlsx/-/xlsx-0.20.3.tgz'
INTEGRITY='703RN/3OdsRD5mtse2HBX7Um7xwaP9tlswEG6svOtjqokXoX7rJdQj7DyabD2I+xk22RgaIIU+R6BHgkpZGB/w=='
SHA='cc015130aa8521e7f088f88898eba949ccdcbfb38df0bd129b44b7273c3a6f41'
def main():
 root=Path(__file__).resolve().parents[1]/'inventory/static/inventory/vendor';root.mkdir(parents=True,exist_ok=True)
 target=root/'xlsx.full.min.js'
 if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==SHA:
  print('SheetJS 0.20.3 확인 완료');return
 data=urllib.request.urlopen(URL,timeout=45).read(10*1024*1024)
 if base64.b64encode(hashlib.sha512(data).digest()).decode()!=INTEGRITY:raise RuntimeError('SheetJS 배포 파일 검증 실패')
 with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
  script=archive.extractfile('package/dist/xlsx.full.min.js').read()
  if hashlib.sha256(script).hexdigest()!=SHA:raise RuntimeError('SheetJS 소스 검증 실패')
  target.write_bytes(script)
 print('SheetJS 0.20.3 준비 완료')
if __name__=='__main__':main()
