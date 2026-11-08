import io
import uuid
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from openpyxl import load_workbook, Workbook
from .models import Item, Stock, ReconciliationRow, ErpSnapshot
from .reconciliation import inspect_erp_exports, inspect_erp_file, create_snapshot, money
from .valuation import estimate, summarize


class ValuationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('money-fixture');self.client.force_login(self.user)
        self.item=Item.objects.create(code='VALUE-A', name='가상 제품')
        self.a=Stock.objects.create(item=self.item, location='A01', quantity=2, reserved=1)
        self.b=Stock.objects.create(item=self.item, location='B01', quantity=1)
        Stock.objects.create(item=self.item, location='예정', status='expected', quantity=5)
        self.missing=Stock.objects.create(item=Item.objects.create(code='NO-AMOUNT'), location='C01', quantity=1)
        self.snap=self.snapshot('100',3)

    def snapshot(self, amount, quantity):
        p={'digest':str(uuid.uuid4()),'source_name':'synthetic.xls','rows':[{'warehouse':'Y09','code':'VALUE-A','name':'가상','quantity':quantity,'shipping_pending':0,'inventory_amount':amount}]}
        return create_snapshot(request_id=uuid.uuid4(),label='가상 기준',erp_as_of=timezone.now(),plan=p,actor=self.user)

    def test_amount_reference_rounding_received_reserved_and_filter_export(self):
        response=self.client.get('/')
        self.assertEqual(response.context['valuation']['total'],100)
        self.assertEqual((response.context['valuation']['missing'],response.context['valuation']['excluded']),(1,1))
        self.assertContains(response,'입고 예정 제외');self.assertContains(response,'금액 자료 없음')
        response=self.client.get('/',{'f_location':['A01']})
        self.assertEqual(response.context['valuation']['total'],67)
        # Allocated one unit is included in the two-unit physical quantity.
        book=load_workbook(io.BytesIO(self.client.get('/stock/export/',{'f_location':['A01']}).content));rows=list(book.active.values)
        self.assertEqual(rows[1][12],67);self.assertIsNone(rows[1][13]);self.assertTrue(rows[1][14]);book.close()
        self.a.refresh_from_db();self.assertEqual((self.a.quantity,self.a.reserved),(2,1))

    def test_latest_missing_does_not_fall_back_and_zero_is_valid(self):
        self.snapshot(None,3)
        self.assertEqual(self.client.get('/').context['valuation']['valued'],0)
        self.snapshot('0',3)
        context=self.client.get('/').context['valuation'];self.assertEqual((context['total'],context['valued']),(0,2))
        self.snapshot('200',0)
        self.assertEqual(self.client.get('/').context['valuation']['valued'],0)
        self.snapshot('-20',3)
        self.assertEqual(self.client.get('/').context['valuation']['valued'],0)

    def test_exact_decimals_invalid_values_and_large_totals(self):
        self.assertEqual(money('1,234.5000'),Decimal('1234.5000'))
        for bad in ['NaN','Infinity','1.00001',True,'=1+1','1e25']:
            with self.assertRaises(ValidationError):money(bad)
        for ref in [{'erp_quantity':0,'inventory_amount':Decimal('10')},{'erp_quantity':-1,'inventory_amount':Decimal('-10')},None]:
            self.assertIsNone(estimate(ref,2)[0])
        from unittest.mock import patch
        with patch('inventory.valuation.latest_reference', return_value=({'VALUE-A': {'erp_quantity': 1, 'inventory_amount': Decimal('99999999999999999999')}}, self.snap)):
            result=summarize(Stock.objects.filter(pk__in=[self.a.pk,self.b.pk]))[0]
        self.assertEqual(result['total'],Decimal('299999999999999999997'))

    def test_cp949_amount_optional_blank_and_snapshot_persisted(self):
        text='품목코드\t품목명\t현재고\t재고금액\t출하대기\nVALUE-A\t가상\t3\t1,234.5000\t0\nBLANK\t가상2\t1\t\t0'
        plan=inspect_erp_exports({'Y09':SimpleUploadedFile('sample.xls',text.encode('cp949'))})
        self.assertEqual(plan['rows'][0]['inventory_amount'],'1234.5000');self.assertIsNone(plan['rows'][1]['inventory_amount'])
        snap=create_snapshot(request_id=uuid.uuid4(),label='가상 원본',erp_as_of=timezone.now(),plan=plan,actor=self.user)
        self.assertEqual(snap.rows.get(code='VALUE-A').inventory_amount,Decimal('1234.5000'))
        old=inspect_erp_exports({'Y09':SimpleUploadedFile('old.xls','품목코드\t품목명\t현재고\t출하대기\nOLD\t가상\t3\t0'.encode('cp949'))})
        self.assertIsNone(old['rows'][0]['inventory_amount'])

    def test_standard_six_column_and_old_five_columns(self):
        for six in (True,False):
            book=Workbook();sheet=book.active;sheet.title='ERP재고';sheet.append(['창고','ERP 코드','현재고','출하대기','모델명']+(['재고금액'] if six else []));sheet.append(['Y09','SAMPLE',3,0,'가상']+([10.5] if six else []));out=io.BytesIO();book.save(out)
            plan=inspect_erp_file(SimpleUploadedFile('sample.xlsx',out.getvalue()),'standard')
            self.assertEqual(plan['rows'][0]['inventory_amount'],'10.5' if six else None)

    def test_global_value_sum_past_fifty_rows(self):
        Stock.objects.bulk_create([Stock(item=self.item,location=f'G{i}',quantity=1) for i in range(55)])
        r=self.client.get('/',{'x_location':'G'})
        self.assertEqual(r.context['page_obj'].paginator.count,55);self.assertEqual(r.context['valuation']['total'],1815)
