import uuid
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from .models import CompanyPrinter,CompanyPrintJob,PickingOrder,Item,Stock,Allocation
from .company_print import preview,enqueue,claim,finish

class CompanyPrintTests(TestCase):
    def setUp(self):
        U=get_user_model();self.user=U.objects.create_superuser('manager');self.worker=U.objects.create_user('worker')
        self.order=PickingOrder.objects.create(project='가상 프로젝트',actor=self.user)
        i=Item.objects.create(code='PRINT',specification='가상 규격');s=Stock.objects.create(item=i,location='A01',quantity=15,reserved=2)
        self.allocation=Allocation.objects.create(stock=s,order=self.order,project='가상',quantity=2)
        self.printer=CompanyPrinter.objects.create(name='가상 프린터',enabled=True)
    def submit(self,token=None,copies=1):
        if token is None:_,token=preview(self.order,self.user)
        return enqueue(order_id=self.order.pk,token=token,copies=copies,actor=self.user)
    def test_queue_never_changes_stock_and_repeat_is_idempotent(self):
        _,token=preview(self.order,self.user);job,created=self.submit(token);self.assertTrue(created)
        again,created=self.submit(token);self.assertFalse(created);self.assertEqual(again.pk,job.pk);self.assertEqual(CompanyPrintJob.objects.count(),1)
        self.assertEqual(Stock.objects.get().quantity,15)
    def test_claim_once_and_unknown_never_auto_requeues(self):
        job,_=self.submit();one=claim();self.assertEqual(one.pk,job.pk);self.assertIsNone(claim())
        finish(job_id=job.pk,claim_id=one.claim_id,success=False,result='가상 오류');self.assertIsNone(claim());job.refresh_from_db();self.assertEqual(job.state,'unknown')
    def test_result_claim_guard_and_spooler_not_paper_complete(self):
        self.submit();job=claim()
        with self.assertRaises(ValidationError):finish(job_id=job.pk,claim_id=uuid.uuid4(),success=True,result='')
        finish(job_id=job.pk,claim_id=job.claim_id,success=True,result='전달');job.refresh_from_db();self.assertEqual(job.state,'submitted');self.assertIsNotNone(job.submitted_at)
    def test_stale_preview_and_cross_user_tokens_rejected(self):
        _,token=preview(self.order,self.user);self.allocation.quantity=3;self.allocation.save()
        with self.assertRaises(ValidationError):self.submit(token)
        with self.assertRaises(ValidationError):enqueue(order_id=self.order.pk,token=token,copies=1,actor=self.worker)
    def test_printer_disabled_and_copy_bounds(self):
        self.printer.enabled=False;self.printer.save()
        with self.assertRaises(ValidationError):self.submit()
        self.printer.enabled=True;self.printer.save()
        for count in (0,6,-1,'bad'):
            with self.assertRaises(ValidationError):self.submit(copies=count)
    def test_web_permissions_and_cancel_only_before_claim(self):
        self.client.force_login(self.worker);self.assertEqual(self.client.get(f'/picking/{self.order.pk}/company-print/').status_code,403)
        self.assertEqual(self.client.get('/company-print/settings/').status_code,403)
        self.client.force_login(self.user);job,_=self.submit()
        self.client.post('/company-print/',{'job':job.pk});job.refresh_from_db();self.assertEqual(job.state,'cancelled');self.assertIsNone(claim())
        job,_=self.submit();claim();self.client.post('/company-print/',{'job':job.pk});job.refresh_from_db();self.assertEqual(job.state,'sending')

    def test_disabling_or_switching_printer_holds_existing_queue(self):
        job,_=self.submit();self.printer.enabled=False;self.printer.save();self.assertIsNone(claim())
        self.printer.enabled=True;self.printer.name='다른 프린터';self.printer.save();self.assertIsNone(claim())
        job.refresh_from_db();self.assertEqual(job.state,'queued');self.assertEqual(job.printer,'가상 프린터')
