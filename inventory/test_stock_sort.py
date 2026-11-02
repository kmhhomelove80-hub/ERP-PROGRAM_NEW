from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Item, Stock, Allocation


class StockSortTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('sort-user'))
        self.a = Stock.objects.create(item=Item.objects.create(code='AAA',name='共通'),location='Z01',quantity=12,reserved=2)
        self.b = Stock.objects.create(item=Item.objects.create(code='BBB',name='共通'),location='A01',quantity=5,reserved=3)
        Allocation.objects.create(stock=self.a,project='Zulu',quantity=1)
        Allocation.objects.create(stock=self.a,project='Alpha',quantity=1)
        Allocation.objects.create(stock=self.b,project='Beta',quantity=3)
        Allocation.objects.create(stock=self.b,project='AAA cancelled',quantity=1,state='cancelled')

    def test_all_columns_toggle_both_directions(self):
        for key, ascending in [('code',[self.a,self.b]),('available',[self.b,self.a]),('location',[self.b,self.a]),('allocated',[self.a,self.b]),('hull',[self.a,self.b])]:
            for direction, expected in [('asc',ascending),('desc',ascending[::-1])]:
                response = self.client.get('/', {'sort':key,'direction':direction,'partial':'1'})
                self.assertEqual(list(response.context['page_obj']), expected, (key,direction))
                self.assertContains(response, f'data-sort="{key}"')
                active = next(h for h in response.context['sort_headers'] if h['active'])
                self.assertIn('direction='+('desc' if direction=='asc' else 'asc'), active['url'])
                self.assertNotContains(response, '<html')

    def test_global_numeric_sort_and_pagination_preserve_filters(self):
        for i in range(51):
            Stock.objects.create(item=self.a.item,location=f'N{i:02}',quantity=100+i)
        response = self.client.get('/', {'sort':'available','direction':'desc','q':'AAA'})
        self.assertEqual(response.context['page_obj'][0].quantity,150)
        self.assertEqual(response.context['page_obj'].paginator.count,52)
        self.assertContains(response,'sort=available&amp;direction=desc&amp;page=2')
        response = self.client.get('/', {'sort':'available','direction':'desc','q':'AAA','page':2})
        self.assertEqual(response.context['page_obj'][-1],self.a)

    def test_invalid_sort_is_safe_and_hull_join_does_not_duplicate_totals(self):
        response = self.client.get('/', {'sort':'hull'})
        self.assertEqual(response.context['totals'], {'quantity':17,'reserved':5,'available':12})
        self.assertEqual(response.context['page_obj'].paginator.count,2)
        response = self.client.get('/', {'sort':'item__code;DROP TABLE','direction':'invalid'})
        self.assertEqual(response.context['sort'],'code')
        self.assertEqual(response.context['direction'],'asc')
        self.a.refresh_from_db()
        self.assertEqual(self.a.quantity,12)
