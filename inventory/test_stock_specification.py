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
            self.assertIn('ERP 코드 / 사양',html)
            self.assertIn('DC24V<br>IP67 &lt;시험&gt;',html)
            self.assertIn('AC220V 기본 사양',html)
            self.assertNotIn('위치 모델명',html)
            self.assertNotIn('기본 모델명',html)
            self.assertNotIn('ERP 코드 / 모델',html)

    def test_missing_spec_is_explicit_and_search_still_matches_spec(self):
        item=Item.objects.create(code='NO-SPEC00001',name='미등록 모델')
        Stock.objects.create(item=item,location='C01',quantity=1)
        self.assertContains(self.client.get('/',{'q':item.code}),'사양 미등록')
        response=self.client.get('/',{'q':'IP67'})
        self.assertEqual(response.context['page_obj'].paginator.count,1)
        self.assertEqual(response.context['page_obj'][0].location,'A01')
