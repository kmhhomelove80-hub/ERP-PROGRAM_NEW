from io import BytesIO
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from .production import open_workbook, extract, settings_for
from .production_mapping import KEYS, LABELS, resolve, MappingError
from .production_views import resolve_one
from .models import ProductionPlanDraft, ProductionPlanSnapshot, Stock


def fixture(shift=0, reverse=False, renamed=False):
    wb=Workbook();ws=wb.active;ws.title='다른 시트' if renamed else '선박등기구';mapping={}
    for i,key in enumerate(reversed(KEYS) if reverse else KEYS,1+shift):
        letter=get_column_letter(i);mapping[key]=letter
        ws.cell(2,i,LABELS[KEYS.index(key)]);ws.merge_cells(start_row=2,start_column=i,end_row=3,end_column=i)
        ws.cell(4,i,'0' if key=='L' else key+'-4');ws.cell(5,i,key+'-5')
    ws[f'{mapping["A"]}4']='인증절차\n대기건';ws.merge_cells(f'{mapping["A"]}4:{mapping["A"]}5')
    out=BytesIO();wb.save(out);wb.close();return out.getvalue(),mapping

class ProductionTests(TestCase):
    def setUp(self):
        User=get_user_model();self.staff=User.objects.create_user('production-staff',is_staff=True)
        self.reader=User.objects.create_user('production-reader');self.client.force_login(self.staff)
    def upload(self,content):
        return self.client.post('/production/settings/',{'action':'upload','workbook':SimpleUploadedFile('plan.xlsx',content)})
    def apply(self,mapping,**options):
        draft=ProductionPlanDraft.objects.get(user=self.staff)
        response=self.client.get('/production/settings/')
        token=response.context['configuration_token']
        if options.get('sheet') and options['sheet'] != response.context['config'].get('sheet'):
            response=self.client.post('/production/settings/',{'action':'inspect','sheet':options['sheet'],'first':2,'last':3,'start':4})
            token=response.context['configuration_token']
        payload={'configuration_token':token,'action':'apply','revision':str(draft.revision),'sheet':'선박등기구','first':'2','last':'3','start':'4',**{'col_'+k:v for k,v in mapping.items()},**options}
        return self.client.post('/production/settings/',payload)
    def test_column_shift_and_reorder_and_merged_schedule(self):
        for shift,reverse in [(0,False),(1,False),(3,True)]:
            content,expected=fixture(shift,reverse);wb=open_workbook(content)
            try:
                ws,headers,config=settings_for(wb,{})
                detected=resolve(headers);self.assertEqual(detected,expected)
                rows,saved=extract(wb,config,detected)
                self.assertEqual([r['values'][0] for r in rows],['인증절차 대기건']*2)
                self.assertEqual(rows[0]['values'][7],'Z-4')
                self.assertEqual(rows[1]['parts'],['V-5','W-5','Y-5','AA-5'])
            finally: wb.close()
    def test_duplicates_and_missing_headers_do_not_fall_back(self):
        content,_=fixture();wb=open_workbook(content)
        try:
            ws,headers,_=settings_for(wb,{})
            headers['AB']='ERP 코드'
            with self.assertRaises(MappingError): resolve(headers)
            with self.assertRaises(MappingError): resolve_one(headers,'Z',{})
            del headers['AB'];headers['H']='changed'
            with self.assertRaises(MappingError): resolve(headers)
        finally: wb.close()
    def test_publish_and_saved_mapping_follow_moved_columns(self):
        content,mapping=fixture();self.assertEqual(self.upload(content).status_code,302)
        self.assertFalse(ProductionPlanSnapshot.objects.exists())
        self.assertEqual(self.apply(mapping).status_code,302)
        self.assertEqual(Stock.objects.count(),0)
        snapshot=ProductionPlanSnapshot.objects.get(pk=1);self.assertEqual(len(snapshot.rows),2)
        content,new_mapping=fixture(1,True);self.upload(content)
        response=self.client.get('/production/settings/')
        for field in response.context['fields']: self.assertEqual(field['selected'],new_mapping[field['key']])
        self.assertEqual(ProductionPlanSnapshot.objects.get(pk=1).rows,snapshot.rows)
        self.assertEqual(self.apply(new_mapping).status_code,302)
        self.assertEqual(ProductionPlanSnapshot.objects.get(pk=1).config['mapping'],new_mapping)
        self.client.force_login(self.reader);response=self.client.get('/production/')
        self.assertContains(response,'production-data');self.assertEqual(response.context['plan_data'][0]['values'][0],'인증절차 대기건')
        self.assertNotContains(response,'엑셀 열기 · 열 연결 설정')
    def test_invalid_mapping_and_stale_draft_preserve_shared_snapshot(self):
        content,mapping=fixture();self.upload(content);self.apply(mapping)
        previous=ProductionPlanSnapshot.objects.get(pk=1).rows
        response=self.apply({**mapping,'Z':mapping['A']})
        self.assertContains(response,'서로 다른 열');self.assertTrue(response.context['fields'])
        self.assertContains(self.apply(mapping,revision='old'),'다른 창에서 파일이 변경')
        self.assertEqual(ProductionPlanSnapshot.objects.get(pk=1).rows,previous)
    def test_custom_sheet_header_and_saved_selector(self):
        content,mapping=fixture(renamed=True);wb=load_workbook(BytesIO(content));ws=wb.active
        ws['A2']='나의 생산일';out=BytesIO();wb.save(out);wb.close();self.upload(out.getvalue())
        self.assertEqual(self.apply(mapping,sheet='다른 시트').status_code,302)
        saved=ProductionPlanDraft.objects.get(user=self.staff).config
        self.assertEqual(saved['sheet'],'다른 시트');self.assertEqual(saved['selectors']['A'],'나의 생산일')
        response=self.client.get('/production/settings/')
        self.assertEqual(response.context['fields'][0]['selected'],'A')
    def test_roles_and_csrf(self):
        self.client.logout();self.assertEqual(self.client.get('/production/').status_code,302)
        self.client.force_login(self.reader);self.assertEqual(self.client.get('/production/').status_code,200)
        self.assertEqual(self.client.get('/production/settings/').status_code,403)
        for action in ('upload','apply','inspect'):
            self.assertEqual(self.client.post('/production/settings/',{'action':action}).status_code,403)
        client=Client(enforce_csrf_checks=True);client.force_login(self.staff)
        self.assertEqual(client.post('/production/settings/',{'action':'apply'}).status_code,403)
    def test_malformed_upload_and_row_range(self):
        self.assertEqual(self.upload(b'not excel').status_code,200);self.assertFalse(ProductionPlanSnapshot.objects.exists())
        content,mapping=fixture();self.upload(content)
        self.assertContains(self.apply(mapping,first='4',last='2'),'헤더 시작')
        self.assertContains(self.apply(mapping,sheet='없는 시트'),'선택한 시트가 없습니다')
    def test_drafts_belong_to_user(self):
        content,mapping=fixture();self.upload(content)
        other=get_user_model().objects.create_user('other',is_staff=True);self.client.force_login(other)
        self.assertNotContains(self.client.get('/production/settings/'),'plan.xlsx')
        self.assertEqual(ProductionPlanDraft.objects.get(user=other).workbook,b'')

    def test_header_range_change_requires_inspection(self):
        content,mapping=fixture();self.upload(content)
        response=self.apply(mapping,first='1')
        self.assertContains(response,'열 목록을 먼저 확인')
        self.assertFalse(ProductionPlanSnapshot.objects.exists())

    def test_duplicate_original_header_needs_manual_choice(self):
        content,mapping=fixture();wb=load_workbook(BytesIO(content))
        ws=wb.active;ws['N2']='생산잔량';out=BytesIO();wb.save(out);wb.close()
        self.upload(out.getvalue());response=self.client.get('/production/settings/')
        field=next(f for f in response.context['fields'] if f['key']=='P')
        self.assertTrue(field['unresolved'])
        self.assertEqual(self.apply(mapping).status_code,302)

class ProductionValueParityTests(TestCase):
    def test_fast_reader_matches_dates_durations_rich_text_and_merges(self):
        from datetime import datetime,timedelta
        from openpyxl.utils.datetime import CALENDAR_MAC_1904
        for epoch in (None,CALENDAR_MAC_1904):
            book=Workbook()
            if epoch:book.epoch=epoch
            sheet=book.active;sheet['A1']=datetime(2026,11,5,9,30);sheet['B1']=timedelta(hours=27,minutes=30);sheet['B1'].number_format='[h]:mm:ss';sheet['C1']=True;sheet['D1']='001';sheet['E1']='=1+2';sheet['F1']='한글 & <문자>';sheet.merge_cells('A2:A3');sheet['A2']='병합'
            out=BytesIO();book.save(out)
            original=load_workbook(BytesIO(out.getvalue()),data_only=True);fast=open_workbook(out.getvalue())
            for address in ('A1','B1','C1','D1','E1','F1','A2','A3'):self.assertEqual(fast.active[address].value,original.active[address].value,address)
            self.assertEqual(str(fast.active.merged_cells),str(original.active.merged_cells));fast.close();original.close()
