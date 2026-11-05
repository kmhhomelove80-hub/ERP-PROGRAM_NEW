"""Read-only XLSX values/merges. Avoid constructing millions of formatting objects."""
import io,posixpath,threading
from collections import OrderedDict
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile,BadZipFile
from openpyxl.styles.numbers import BUILTIN_FORMATS,is_date_format
from openpyxl.utils.cell import coordinate_to_tuple,range_boundaries
from openpyxl.utils.datetime import from_excel,CALENDAR_MAC_1904,CALENDAR_WINDOWS_1900
from openpyxl.worksheet.cell_range import MultiCellRange
NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

class Sheet:
 def __init__(self,book,path):
  self.values={};self.max_row=0;self.max_column=0;self.merged_cells=MultiCellRange()
  with book.archive.open(path) as stream:
   for _,node in ET.iterparse(stream,events=('end',)):
    if node.tag==NS+'c':
     key=node.get('r');r,c=coordinate_to_tuple(key)
     if r>20000 or c>256:raise ValueError('지원 범위는 20,000행·256열입니다.')
     value=node.find(NS+'v');kind=node.get('t');raw=value.text if value is not None else None
     if kind=='s':value=book.strings[int(raw)] if raw is not None else None
     elif kind=='inlineStr':value=''.join(t.text or '' for t in node.iter(NS+'t'))
     elif kind=='b':value=raw=='1' if raw is not None else None
     elif kind in ('str','e'):value=raw
     elif kind=='d':
      from openpyxl.utils.datetime import from_ISO8601
      value=from_ISO8601(raw) if raw else None
     elif raw is None:value=None
     else:
      value=float(raw) if any(x in raw for x in ('.','e','E')) else int(raw)
      if int(node.get('s','0')) in book.dates:value=from_excel(value,book.epoch)
     self.values[(r,c)]=value;self.max_row=max(self.max_row,r);self.max_column=max(self.max_column,c);node.clear()
    elif node.tag==NS+'mergeCell':self.merged_cells.add(node.get('ref'));node.clear()
    elif node.tag==NS+'dimension':
     _,_,cols,rows=range_boundaries(node.get('ref','A1'));self.max_row=max(self.max_row,rows);self.max_column=max(self.max_column,cols);node.clear()
    elif node.tag==NS+'row':node.clear()
  if self.max_row>20000 or self.max_column>256 or self.max_row*self.max_column>2000000:raise ValueError('지원 범위는 20,000행·256열·2백만 셀입니다.')
 def cell(self,row,column):return SimpleNamespace(value=self.values.get((row,column)))
 def __getitem__(self,key):return self.cell(*coordinate_to_tuple(key))

class Book:
 def __init__(self,content):
  self.archive=ZipFile(io.BytesIO(content));self.lock=threading.RLock();self.sheets={};self.strings=[];self.dates=set()
  infos=self.archive.infolist()
  if sum(f.file_size for f in infos)>80*1024*1024 or len(infos)>5000:raise ValueError('압축 해제 크기가 너무 큰 파일입니다.')
  workbook=ET.fromstring(self.archive.read('xl/workbook.xml'));prop=workbook.find(NS+'workbookPr');self.epoch=CALENDAR_MAC_1904 if prop is not None and prop.get('date1904') in ('1','true') else CALENDAR_WINDOWS_1900
  links={n.get('Id'):n.get('Target') for n in ET.fromstring(self.archive.read('xl/_rels/workbook.xml.rels')) if n.get('TargetMode')!='External'}
  self.paths={}
  for sheet in workbook.find(NS+'sheets'):
   target=links.get(sheet.get(REL+'id'),'');path=posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/'+target)
   if not path.startswith('xl/') or path not in self.archive.namelist():raise ValueError('시트 연결을 읽을 수 없습니다.')
   self.paths[sheet.get('name')]=path
  self.sheetnames=list(self.paths)
  if not self.sheetnames:raise ValueError('시트가 없습니다.')
  if 'xl/sharedStrings.xml' in self.archive.namelist():
   with self.archive.open('xl/sharedStrings.xml') as stream:
    for _,node in ET.iterparse(stream,events=('end',)):
     if node.tag==NS+'si':
      # Phonetic annotations are presentation-only, not cell text.
      for child in list(node):
       if child.tag==NS+'rPh':node.remove(child)
      self.strings.append(''.join(t.text or '' for t in node.iter(NS+'t')));node.clear()
  if 'xl/styles.xml' in self.archive.namelist():
   formats={};xfs=[];stack=[]
   with self.archive.open('xl/styles.xml') as stream:
    for event,node in ET.iterparse(stream,events=('start','end')):
     if event=='start':stack.append(node.tag);continue
     if node.tag==NS+'numFmt':formats[int(node.get('numFmtId'))]=node.get('formatCode','')
     if node.tag==NS+'xf' and len(stack)>1 and stack[-2]==NS+'cellXfs':xfs.append(int(node.get('numFmtId','0')))
     node.clear();stack.pop()
   self.dates={i for i,n in enumerate(xfs) if is_date_format(formats.get(n,BUILTIN_FORMATS.get(n,'')))}
 def __getitem__(self,name):
  with self.lock:
   if name not in self.sheets:self.sheets[name]=Sheet(self,self.paths[name])
   return self.sheets[name]
 @property
 def active(self):return self[self.sheetnames[0]]
 def close(self):self.archive.close()
