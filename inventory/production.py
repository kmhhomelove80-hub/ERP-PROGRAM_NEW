"""Production workbook reader with explicit header mapping and bounded input."""
from datetime import date, datetime
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string
from .production_mapping import KEYS, LABELS, catalog, resolve, validate_mapping, MappingError

MAX_UPLOAD = 10 * 1024 * 1024

def open_workbook(content):
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError('10MB 이하의 엑셀 파일을 선택해 주세요.')
    try:
        with ZipFile(BytesIO(content)) as z:
            if sum(f.file_size for f in z.infolist()) > 80 * 1024 * 1024 or len(z.infolist()) > 5000:
                raise ValueError('압축 해제 크기가 너무 큰 파일입니다.')
        return load_workbook(BytesIO(content), data_only=True, keep_links=False)
    except (BadZipFile, KeyError, OSError) as exc:
        raise ValueError('읽을 수 있는 XLSX/XLSM 파일이 아닙니다.') from exc

def settings_for(workbook, config):
    sheet=config.get('sheet') or ('선박등기구' if '선박등기구' in workbook.sheetnames else workbook.sheetnames[0])
    if sheet not in workbook.sheetnames:
        raise ValueError('시트가 변경되었습니다. 시트를 다시 선택해 주세요.')
    ws=workbook[sheet]
    if ws.max_row > 20000 or ws.max_column > 256 or ws.max_row * ws.max_column > 2000000:
        raise ValueError('지원 범위는 20,000행, 256열, 총 2백만 셀 이하입니다.')
    try:
        first,last,start=(int(config.get(k,d)) for k,d in [('first',2),('last',3),('start',4)])
    except (TypeError,ValueError) as exc:
        raise ValueError('헤더와 데이터 시작 행은 정수로 입력해 주세요.') from exc
    headers=catalog(ws,first,last,start)
    return ws,headers,{'sheet':sheet,'first':first,'last':last,'start':start}

def text(value):
    if value is None: return ''
    if isinstance(value,(datetime,date)): return value.strftime('%Y-%m-%d')
    return str(value).strip()

def extract(workbook, config, mapping):
    ws,headers,settings=settings_for(workbook,config)
    validate_mapping(mapping,ws.max_column)
    start=settings['start']; schedule_col=column_index_from_string(mapping['A'])
    fill={}
    for area in ws.merged_cells.ranges:
        if area.min_col == area.max_col == schedule_col and area.max_row >= start:
            for r in range(max(start,area.min_row),area.max_row+1):
                fill[r]=text(ws.cell(area.min_row,schedule_col).value)
    rows=[]
    for r in range(start,ws.max_row+1):
        values=[text(ws[f'{mapping[k]}{r}'].value) for k in KEYS]
        if not any(values): continue
        values[0]=fill.get(r,values[0])
        for index in (0,7): values[index]=' '.join(values[index].split())
        rows.append({'row':r,'values':values[:9],'parts':values[9:]})
    if not rows: raise ValueError('선택한 시트와 데이터 시작 행에 조회할 자료가 없습니다.')
    # Empty header columns can be chosen manually, but require confirmation again next time.
    settings['selectors']={k:headers[mapping[k]] for k in KEYS}
    settings['mapping']=mapping
    return rows,settings
