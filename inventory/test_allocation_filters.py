from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import Item,Stock,Allocation

class HeaderFilterTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('manager');self.client.force_login(self.user)
        i=Item.objects.create(code='DEMO');s=Stock.objects.create(item=i,location='A01',quantity=200,reserved=150)
        self.a=Allocation.objects.create(stock=s,project='최근',legacy_seq='2026110602',quantity=3)
        Allocation.objects.bulk_create([Allocation(stock=s,project='오래된',legacy_seq='2026100101',quantity=1) for _ in range(55)])
        Allocation.objects.create(stock=s,project='최근',legacy_seq='2026110602',quantity=4)
        t=Stock.objects.create(item=i,location='B02',status='expected',quantity=5,reserved=2);Allocation.objects.create(stock=t,project='예정',quantity=2)
    def test_whole_query_filter_totals_and_options_past_page_50(self):
        r=self.client.get('/allocations/',{'f_project':['오래된'],'f_location':['A01']});self.assertEqual(r.context['total'],55);self.assertEqual(r.context['filtered_quantity'],55)
        r=self.client.get('/allocations/options/',{'field':'project'});self.assertEqual({v['value'] for v in r.json()['values']},{'오래된','최근','예정'})
        r=self.client.get('/allocations/',{'f_project':['최근','예정'],'min_quantity':'3','max_quantity':'4'});self.assertEqual(r.context['total'],2);self.assertEqual(r.context['filtered_quantity'],7)
    def test_signed_filter_cannot_be_reused_after_change(self):
        r=self.client.get('/allocations/',{'f_project':'최근'});token=r.context['filter_token']
        r=self.client.post('/allocations/?f_project=오래된',{'scope':'filtered','filter_token':token,'action':'cancel','confirmed':'on'})
        self.assertContains(r,'필터가 변경');self.assertEqual(Allocation.objects.filter(state='reserved').count(),58)
    def test_filtered_cancel_exact_matches_only(self):
        r=self.client.get('/allocations/',{'f_project':'최근','f_quantity':'3'})
        r=self.client.post('/allocations/?f_project=최근&f_quantity=3',{'scope':'filtered','filter_token':r.context['filter_token'],'action':'cancel','confirmed':'on'})
        self.assertEqual(r.status_code,302);self.a.refresh_from_db();self.assertEqual(self.a.state,'cancelled');self.assertEqual(Allocation.objects.filter(state='reserved').count(),57)
    def test_stale_scope_and_invalid_ranges_block(self):
        r=self.client.get('/allocations/',{'f_project':'최근'});token=r.context['filter_token']
        Allocation.objects.create(stock=self.a.stock,project='최근',quantity=1)
        r=self.client.post('/allocations/?f_project=최근',{'scope':'filtered','filter_token':token,'action':'cancel','confirmed':'on'});self.assertContains(r,'필터 결과가 변경')
        self.assertEqual(self.client.get('/allocations/',{'min_quantity':'4','max_quantity':'2'}).status_code,400)
        self.assertEqual(self.client.get('/allocations/options/',{'field':'__password'}).status_code,400)
    def test_status_filter_and_permission(self):
        r=self.client.get('/allocations/',{'f_status':'expected'});self.assertEqual(r.context['total'],1);self.assertEqual(r.context['filtered_quantity'],2)
        self.client.logout();self.assertEqual(self.client.get('/allocations/options/',{'field':'project'}).status_code,302)
