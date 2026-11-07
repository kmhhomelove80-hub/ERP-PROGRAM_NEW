from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Item, Stock


class StockSpecificationTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('spec-viewer'))
        self.item=Item.objects.create(code='SPEC-ITEM001',name='기본 모델명',specification='AC220V 기본 사양')
        Stock.objects.create(item=self.item,location='A01',quantity=2,legacy_name='위치 모델명',legacy_specification='DC24V\nIP67 <시험>')
        Stock.objects.create(item=self.item,location='B01',quantity=3)

    def test_pc_lists_code_and_location_specification_with_base_fallback(self):
        for params in [{},{'partial':'1'},{'partial':'1','sort':'location','direction':'desc'}]:
            response=self.client.get('/',params)
            html=response.content.decode().split('<div class="mobile-stock-cards">')[0]
            self.assertIn('data-field="code"', html)
            self.assertIn('data-field="specification"', html)
            self.assertIn('DC24V IP67 &lt;시험&gt;',html)
            self.assertIn('AC220V 기본 사양',html)
            self.assertNotIn('위치 모델명',html)
            self.assertNotIn('기본 모델명',html)
            self.assertNotIn('ERP 코드 / 모델',html)
            self.assertNotIn('<th>창고</th>',html)
            self.assertNotIn('<td>Y09</td>',html)
            self.assertIn('완료',html)
            self.assertNotIn('>입고 완료</span>',html)

    def test_missing_spec_is_explicit_and_search_still_matches_spec(self):
        item=Item.objects.create(code='NO-SPEC00001',name='미등록 모델')
        Stock.objects.create(item=item,location='C01',quantity=1)
        self.assertContains(self.client.get('/',{'q':item.code}),'사양 미등록')
        response=self.client.get('/',{'q':'IP67'})
        self.assertEqual(response.context['page_obj'].paginator.count,1)
        self.assertEqual(response.context['page_obj'][0].location,'A01')

    def test_writer_sees_short_movement_label_and_expected_status(self):
        user=get_user_model().objects.create_user('spec-full',is_staff=True)
        self.client.force_login(user)
        Stock.objects.create(item=self.item,location='PRE',quantity=1,status='expected')
        html=self.client.get('/').content.decode().split('<div class="mobile-stock-cards">')[0]
        self.assertIn('>이동</a>',html)
        self.assertNotIn('>이동 요청</a>',html)
        self.assertIn('>예정</span>',html)
