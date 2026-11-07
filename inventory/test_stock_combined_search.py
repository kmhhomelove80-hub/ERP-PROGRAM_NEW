from io import BytesIO
from urllib.parse import parse_qs, urlparse
from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook
from .models import Item, Stock


class CombinedStockSearchTests(TestCase):
    def setUp(self):
        self.worker = get_user_model().objects.create_user('combined-worker')
        self.client.force_login(self.worker)
        self.match = Stock.objects.create(item=Item.objects.create(code='MATCH', name='ACS lamp', specification='IP56 24V'),location='A',quantity=10,reserved=3)
        Stock.objects.create(item=Item.objects.create(code='WRONG-SPEC', name='ACS lamp', specification='IP65'),location='B',quantity=20)
        Stock.objects.create(item=Item.objects.create(code='WRONG-NAME', name='OTHER lamp', specification='IP56'),location='C',quantity=30)

    def test_both_conditions_required_case_insensitive_and_read_only(self):
        response=self.client.get('/',{'model':' acs ','spec':' ip56 '})
        self.assertEqual(list(response.context['page_obj']),[self.match])
        self.assertEqual(response.context['totals'],{'quantity':10,'reserved':3,'available':7})
        self.assertContains(response,'제품명·사양 조건 검색')
        self.assertContains(response,'name="model" value="acs"')
        self.assertNotContains(response,'WRONG-SPEC')
        self.assertNotContains(response,'WRONG-NAME')
        self.match.refresh_from_db();self.assertEqual((self.match.quantity,self.match.reserved),(10,3))

    def test_individual_filters_and_existing_query_and_status_combine(self):
        self.assertEqual(self.client.get('/',{'model':'acs'}).context['page_obj'].paginator.count,2)
        self.assertEqual(self.client.get('/',{'spec':'ip56'}).context['page_obj'].paginator.count,2)
        self.assertEqual(self.client.get('/',{'model':'acs','spec':'ip56','q':'A'}).context['page_obj'].paginator.count,1)
        self.assertEqual(self.client.get('/',{'model':'acs','spec':'ip56','status':'expected'}).context['page_obj'].paginator.count,0)

    def test_same_row_and_displayed_legacy_values(self):
        shared=Item.objects.create(code='SHARED',name='ACS canonical',specification='IP56 canonical')
        Stock.objects.create(item=shared,location='D',quantity=4,legacy_name='OTHER',legacy_specification='IP56')
        Stock.objects.create(item=shared,location='E',quantity=4,legacy_name='ACS',legacy_specification='IP65')
        override=Stock.objects.create(item=Item.objects.create(code='OVERRIDE',name='OTHER',specification='IP65'),location='F',quantity=2,legacy_name='acs special',legacy_specification='IP56 special')
        results=self.client.get('/',{'model':'ACS','spec':'IP56'}).context['page_obj']
        self.assertEqual({s.pk for s in results},{self.match.pk,override.pk})

    def test_sort_pagination_partial_and_export_keep_filters(self):
        for i in range(51):
            Stock.objects.create(item=self.match.item,location=f'X{i:03}',quantity=1)
        response=self.client.get('/',{'model':'acs','spec':'ip56','sort':'available','direction':'desc'})
        self.assertEqual(response.context['page_obj'].paginator.count,52)
        for header in response.context['sort_headers']:
            params=parse_qs(urlparse(header['url']).query)
            self.assertEqual((params['model'],params['spec']),(['acs'],['ip56']))
        self.assertContains(response,'model=acs&amp;spec=ip56')
        self.assertContains(response,'page=2')
        partial=self.client.get('/',{'model':'acs','spec':'ip56','page':2,'partial':'1'})
        self.assertEqual(len(partial.context['page_obj']),2)
        self.assertNotContains(partial,'<html')
        exported=self.client.get('/stock/export/',{'model':'acs','spec':'ip56'})
        rows=list(load_workbook(BytesIO(exported.content)).active.iter_rows(min_row=2,values_only=True))
        self.assertEqual(len(rows),52)
        self.assertEqual({row[0] for row in rows},{'MATCH'})

    def test_exact_scan_is_preserved_and_special_characters_literal(self):
        response=self.client.get('/',{'q':'MATCH','scan':'1','model':'acs','spec':'ip56'})
        self.assertEqual(list(response.context['page_obj']),[self.match])
        self.assertEqual(response.context['scan_mode'],'1')
        item=Item.objects.create(code='LITERAL',name='ACS%_ lamp',specification='IP56*')
        stock=Stock.objects.create(item=item,location='Z',quantity=1)
        response=self.client.get('/',{'model':'%_','spec':'56*'})
        self.assertEqual(list(response.context['page_obj']),[stock])
