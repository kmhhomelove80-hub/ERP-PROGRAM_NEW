from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from .forms import AllocationActionForm
from .models import Item,Stock,Allocation

class CompactUiTests(TestCase):
 def test_current_time_default_and_automatic_submit_time(self):
  now=timezone.now();old=now-timedelta(hours=3)
  with patch('inventory.forms.timezone.now',return_value=now):
   form=AllocationActionForm({'action':'release','confirmed':'on','shipped_at_auto':'True','shipped_at':timezone.localtime(old).strftime('%Y-%m-%dT%H:%M')})
   self.assertTrue(form.is_valid(),form.errors);self.assertEqual(form.cleaned_data['shipped_at'],now)
   blank=AllocationActionForm({'action':'release','confirmed':'on'});self.assertTrue(blank.is_valid(),blank.errors);self.assertEqual(blank.cleaned_data['shipped_at'],now)
   manual=AllocationActionForm({'action':'release','confirmed':'on','shipped_at_auto':'','shipped_at':timezone.localtime(old).isoformat()});self.assertTrue(manual.is_valid(),manual.errors);self.assertEqual(manual.cleaned_data['shipped_at'],old)
 def test_toolbar_precedes_table_and_old_uuid_hidden(self):
  user=get_user_model().objects.create_user('compact-owner',is_staff=True);self.client.force_login(user)
  stock=Stock.objects.create(item=Item.objects.create(code='COMPACT'),location='A',quantity=5,reserved=1);Allocation.objects.create(stock=stock,project='COMPACT',quantity=1)
  response=self.client.get('/allocations/?q=COMPACT');body=response.content.decode();self.assertLess(body.index('dispatch-toolbar'),body.index('allocation-table'));self.assertContains(response,'name="shipped_at_auto"');self.assertTrue(response.context['form']['shipped_at'].value())
 def test_real_release_without_manual_time(self):
  user=get_user_model().objects.create_user('compact-release',is_staff=True);self.client.force_login(user)
  stock=Stock.objects.create(item=Item.objects.create(code='NOW'),location='A',quantity=5,reserved=1);a=Allocation.objects.create(stock=stock,project='NOW',quantity=1)
  response=self.client.get('/allocations/?q=NOW');before=timezone.now();post=self.client.post('/allocations/?q=NOW',{'action':'release','scope':'filtered','filter_token':response.context['filter_token'],'confirmed':'on','shipped_at_auto':'True'})
  self.assertEqual(post.status_code,302);a.refresh_from_db();self.assertEqual(a.state,'released');self.assertGreaterEqual(a.shipped_at,before);self.assertLessEqual(a.shipped_at,timezone.now())

 def test_substitution_time_also_defaults_to_now(self):
  from .forms import SubstitutionDispatchForm
  now=timezone.now()
  with patch('inventory.forms.timezone.now',return_value=now):
   form=SubstitutionDispatchForm({'product_confirmed':'on'})
   self.assertTrue(form.is_valid(),form.errors);self.assertEqual(form.cleaned_data['shipped_at'],now)
