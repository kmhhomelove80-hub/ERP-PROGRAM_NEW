"""Header-based mappings; physical column positions are never reused implicitly."""
import re
from openpyxl.utils import get_column_letter, column_index_from_string

KEYS = ('A','C','D','L','P','S','X','Z','AB','V','W','Y','AA')
LABELS = ('생산일정','계획일','납기','완료량 (검사)','생산잔량','특이사항','작지번호','ERP 코드','현장명','광원부','반제품','PCB 번호','광원부코드')
ALIASES = {'L': ('완료량검사','완료량','완료'), 'P': ('생산잔량','잔량')}

class MappingError(ValueError):
    pass

def normalize(text):
    return re.sub(r'[^0-9a-z가-힣]', '', str(text).casefold())

def validate_rows(first, last, start):
    if not 1 <= first <= last < start <= 1048576 or last-first > 20:
        raise ValueError('헤더 시작 ≤ 헤더 끝 < 데이터 시작이어야 합니다. 헤더 범위는 최대 21행입니다.')

def catalog(ws, first=2, last=3, start=4):
    validate_rows(first,last,start)
    merged = {}
    for area in ws.merged_cells.ranges:
        if area.min_row <= last and area.max_row >= first:
            value = ws.cell(area.min_row,area.min_col).value
            for r in range(max(first,area.min_row),min(last,area.max_row)+1):
                for c in range(area.min_col,area.max_col+1):
                    merged[r,c] = value
    result = {}
    for c in range(1,ws.max_column+1):
        parts=[]
        for r in range(first,last+1):
            text=' '.join(str(merged.get((r,c),ws.cell(r,c).value) or '').split())
            if text and text not in parts: parts.append(text)
        result[get_column_letter(c)]=' / '.join(parts)
    return result

def resolve(headers, selectors=None):
    mapping={}; issues=[]
    for key,label in zip(KEYS,LABELS):
        saved = (selectors or {}).get(key)
        names = (saved,) if saved else (label,*ALIASES.get(key,()))
        # A leaf header may have a merged group heading above it.
        exact=[col for col,text in headers.items() if normalize(text) in {normalize(n) for n in names}]
        candidates=exact or [col for col,text in headers.items() if normalize(text.split(' / ')[-1]) in {normalize(n) for n in names}]
        if len(candidates)==1: mapping[key]=candidates[0]
        else: issues.append(label + ('：해당 없음' if not candidates else '：같은 이름의 열이 여러 개'))
    if len(set(mapping.values())) != len(mapping): issues.append('같은 열이 여러 항목에 연결되어 있습니다')
    if issues: raise MappingError('열 연결을 확인해 주세요.\n'+'\n'.join(issues))
    return mapping

def validate_mapping(mapping, max_column):
    if set(mapping)!=set(KEYS) or len(set(mapping.values()))!=len(KEYS):
        raise ValueError('13개 항목에 서로 다른 열을 지정해 주세요.')
    for col in mapping.values():
        if not isinstance(col,str) or not re.fullmatch('[A-Z]{1,3}',col) or not 1 <= column_index_from_string(col) <= max_column:
            raise ValueError('선택한 열이 시트에 없습니다.')
