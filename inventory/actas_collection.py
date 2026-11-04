"""Two-file Actas collection. Only existing reference snapshots may be written.

The Windows adapter is injected: no UI/DB actions occur when importing this file.
Original exports and a journal stay in a private, unique local run directory.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

MAX_BYTES = 10 * 1024 * 1024
WAREHOUSES = ('Y02', 'Y09')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_record(path, data):
    fd, name = tempfile.mkstemp(prefix='.actas-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def collection_lock(root):
    """Serialize the desktop UI across processes; never delete another run."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    stream = (root / 'collector.lock').open('a+b')
    # A Windows byte lock denies even a read of byte zero by a second opener.
    # Inspect file length without reading that locked byte.
    if os.fstat(stream.fileno()).st_size == 0:
        stream.write(b'0')
        stream.flush()
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError('다른 Actas 수집 작업이 진행 중입니다.') from None
    try:
        yield
    finally:
        stream.close()  # OS releases the lock even if the process exits.


def closed_file_bytes(path):
    """On Windows require the exporter to have closed all writer handles."""
    if os.name != 'nt':
        with path.open('rb') as stream:
            return stream.read(MAX_BYTES + 1)
    import ctypes
    from ctypes import wintypes
    import msvcrt
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        api.CloseHandle(handle)
        raise
    with os.fdopen(fd, 'rb') as stream:
        return stream.read(MAX_BYTES + 1)


def wait_for_export(path, *, timeout=90, quiet=1.5):
    deadline = time.monotonic() + timeout
    previous, since = None, time.monotonic()
    while time.monotonic() < deadline:
        if path.is_symlink():
            raise RuntimeError('연결된 파일은 수집하지 않습니다.')
        try:
            stat = path.stat()
            if stat.st_size > MAX_BYTES:
                raise RuntimeError('Actas 추출 파일이 10MB를 초과합니다.')
            current = (stat.st_size, stat.st_mtime_ns)
            if current != previous:
                previous, since = current, time.monotonic()
            if stat.st_size and time.monotonic() - since >= quiet:
                data = closed_file_bytes(path)
                after = path.stat()
                if len(data) == stat.st_size and (after.st_size, after.st_mtime_ns) == current:
                    return data
        except (FileNotFoundError, PermissionError):
            pass
        except OSError as error:
            if getattr(error, 'winerror', None) not in (32, 33):
                raise
        time.sleep(.1)
    raise RuntimeError('파일 저장 완료를 확인하지 못했습니다. 이전 자료는 등록하지 않습니다.')


def inspect_pair(folder, record):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from .reconciliation import inspect_erp_exports
    if set(record.get('files', {})) != set(WAREHOUSES):
        raise RuntimeError('Y02와 Y09 파일이 모두 필요합니다.')
    uploads = {}
    for warehouse in WAREHOUSES:
        entry = record['files'][warehouse]
        if entry['name'] != warehouse + '.xls':
            raise RuntimeError('창고별 파일 경로가 올바르지 않습니다.')
        path = folder / entry['name']
        if path.is_symlink():
            raise RuntimeError('연결된 파일은 등록하지 않습니다.')
        data = closed_file_bytes(path)
        if not data or len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise RuntimeError('수집 후 파일이 변경됐습니다. 등록을 중단합니다.')
        uploads[warehouse] = SimpleUploadedFile(entry['name'], data)
    plan = inspect_erp_exports(uploads)
    counts = {w: sum(r['warehouse'] == w for r in plan['rows']) for w in WAREHOUSES}
    if not all(counts.values()):
        raise RuntimeError('빈 창고 자료는 자동 등록하지 않습니다. 원본을 직접 확인하세요.')
    return plan, counts


def collect(root, exporter, *, actor, progress=lambda text: None):
    from .menu_permissions import require_menu_write
    require_menu_write(actor, 'management')  # Before touching the Actas UI.
    with collection_lock(root):
        run = Path(root) / uuid.uuid4().hex
        run.mkdir()
        record = {'schema': 1, 'request_id': str(uuid.uuid4()), 'actor_id': actor.pk,
                  'phase': 'collecting', 'started_at': utc_now(), 'files': {}}
        write_record(run / 'run.json', record)
        try:
            for warehouse in WAREHOUSES:
                target = run / (warehouse + '.xls')
                if target.exists():
                    raise RuntimeError('새 수집 경로가 비어 있지 않습니다.')
                progress(warehouse + ' 재고 조회 및 파일 저장 중')
                receipt = exporter.export(warehouse, target)
                if receipt.get('warehouse') != warehouse or receipt.get('query_complete') is not True:
                    raise RuntimeError('Actas 창고 선택·조회 완료를 확인하지 못했습니다.')
                data = wait_for_export(target)
                record['files'][warehouse] = {'name': target.name, 'sha256': hashlib.sha256(data).hexdigest(),
                                              'saved_at': utc_now(), 'receipt': receipt}
                write_record(run / 'run.json', record)
            plan, counts = inspect_pair(run, record)
            record.update(phase='validated', validated_at=utc_now(), digest=plan['digest'], counts=counts)
            write_record(run / 'run.json', record)
            progress('두 창고 자료 검증 완료. ERP 대조 자료 등록 중')
            return register_collected(run, actor=actor, locked=True)
        except Exception as error:
            # Preserve validated/registering state for idempotent recovery if
            # the transaction committed but the final result could not be saved.
            current = json.loads((run / 'run.json').read_text(encoding='utf-8'))
            current['failure_type'] = type(error).__name__
            if current['phase'] == 'collecting':
                current.update(phase='collection_failed', failed_at=utc_now())
            write_record(run / 'run.json', current)
            progress('중단. 기록 폴더: ' + str(run))
            raise


def register_collected(folder, *, actor, locked=False):
    """Retry the SAME request; never re-query Actas or create a second snapshot."""
    from .menu_permissions import require_menu_write
    from .reconciliation import create_snapshot
    require_menu_write(actor, 'management')
    folder = Path(folder).resolve()
    if not locked:
        with collection_lock(folder.parent):
            return register_collected(folder, actor=actor, locked=True)
    record = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
    if record.get('schema') != 1 or record.get('phase') not in ('validated', 'registering', 'complete') or record.get('actor_id') != actor.pk:
        raise RuntimeError('같은 담당자의 검증 완료 수집 기록만 등록할 수 있습니다.')
    from .models import ErpSnapshot
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(record['started_at'])).total_seconds()
    if (age < 0 or age > 15 * 60) and not ErpSnapshot.objects.filter(pk=record['request_id']).exists():
        record['phase'] = 'expired'
        write_record(folder / 'run.json', record)
        raise RuntimeError('수집 후 15분이 지난 미등록 자료입니다. 새로 수집해야 합니다.')
    plan, counts = inspect_pair(folder, record)
    if record['digest'] != plan['digest'] or record['counts'] != counts:
        raise RuntimeError('검증 결과가 기존 수집 기록과 다릅니다.')
    record['phase'] = 'registering'
    write_record(folder / 'run.json', record)
    snapshot = create_snapshot(request_id=uuid.UUID(record['request_id']),
                               label='Actas Y02·Y09 자동 수집 ' + record['started_at'],
                               erp_as_of=datetime.fromisoformat(record['started_at']), plan=plan, actor=actor)
    record.update(phase='complete', snapshot_id=str(snapshot.pk), completed_at=utc_now())
    write_record(folder / 'run.json', record)
    return {'snapshot_id': str(snapshot.pk), 'counts': counts, 'run': str(folder),
            'started_at': record['started_at'], 'completed_at': record['completed_at']}
