from io import BytesIO
from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook
from .models import Item, Stock


class ScannedProductLookupTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('scan-viewer')
        self.client.force_login(self.user)
        self.code='ABK13W1PGRG0000H'
        self.item=Item.objects.create(code=self.code,name='제품 라벨 시험')
        Stock.objects.create(item=self.item,location='C0408',quantity=12,reserved=2)
        Stock.objects.create(item=self.item,location='D0201',quantity=3)
        other=Item.objects.create(code=self.code+'X',specification=self.code)
        Stock.objects.create(item=other,location='OTHER',quantity=99)

    def test_scan_exact_code_returns_all_locations_and_quantities_not_similar_codes(self):
        response=self.client.get('/',{'q':self.code,'scan':'1','partial':'1'})
        self.assertEqual(response.context['page_obj'].paginator.count,2)
        self.assertEqual(response.context['totals'],{'quantity':15,'reserved':2,'available':13})
        for text in [self.code,'C0408','D0201','인식한 ERP 코드']:
            self.assertContains(response,text)
        self.assertNotContains(response,'OTHER')
        self.assertNotContains(response,self.code+'X')

    def test_literal_special_chars_and_case_are_supported_without_truncating(self):
        code='AB12-CD*00000001'
        item=Item.objects.create(code=code)
        Stock.objects.create(item=item,location='STAR',quantity=5)
        response=self.client.get('/',{'q':code.lower(),'scan':'1'})
        self.assertEqual(response.context['page_obj'].paginator.count,1)
        self.assertEqual(response.context['page_obj'][0].item_id,item.pk)

    def test_manual_search_remains_partial_and_scan_flag_persists_for_sort(self):
        response=self.client.get('/',{'q':self.code})
        self.assertEqual(response.context['page_obj'].paginator.count,3)
        response=self.client.get('/',{'q':self.code,'scan':'1','sort':'available'})
        self.assertEqual(response.context['scan_mode'],'1')
        self.assertIn('scan=1',response.context['sort_headers'][0]['url'])

    def test_missing_or_zero_stock_distinguished_from_decode_failure(self):
        self.assertContains(self.client.get('/',{'q':'UNKNOWN','scan':'1'}),'이 코드로 등록된 품목이 없습니다')
        item=Item.objects.create(code='ZERO-00000000001')
        Stock.objects.create(item=item,location='ZERO',quantity=0)
        self.assertContains(self.client.get('/',{'q':item.code,'scan':'1'}),'등록된 품목이지만 조회할 재고가 없습니다')

    def test_scanned_export_matches_exact_results_and_read_only_lookup_does_not_mutate(self):
        response=self.client.get('/stock/export/',{'q':self.code,'scan':'1'})
        book=load_workbook(BytesIO(response.content))
        rows=list(book.active.iter_rows(min_row=2,values_only=True))
        self.assertEqual(len(rows),2)
        self.assertEqual(sum(row[6] for row in rows),15)
        self.assertEqual(Stock.objects.get(location='C0408').quantity,12)
