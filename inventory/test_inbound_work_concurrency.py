import json,uuid
from unittest import skipUnless
from django.test import TransactionTestCase
from django.db import connection
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from .models import EmployeeMenuAccess,Receipt,CompanyPrintJob,CompanyPrinter,PickingOrder,Item,Stock,Allocation
from .inbound_work import create_work,update_rows,confirm_work
from .company_print import preview,enqueue,claim
from .test_concurrency import PostgreSQLConcurrencyTests

@skipUnless(connection.vendor=='postgresql','실제 PostgreSQL 동시성 검사')
class SharedWorkConcurrencyTests(TransactionTestCase):
    parallel=PostgreSQLConcurrencyTests.parallel
    def setUp(self):
        self.manager=get_user_model().objects.create_superuser('manager')
        self.other=get_user_model().objects.create_superuser('other')
        self.work=create_work(data={'title':'가상 목록','document_date':'2026-11-06','inbound_nonce':signing.dumps({'actor':self.manager.pk,'id':str(uuid.uuid4())},salt='inbound-receipt-page'),'inbound_revision':'1','inbound_rows':json.dumps([['C','가상','1W',2,''],['C','가상','1W',3,'']])},actor=self.manager)
    def change(self,row,location):
        try:update_rows(work_id=self.work.pk,changes=[{'id':row.pk,'version':1,'location':location,'placed':True}],actor=self.manager);return 'saved'
        except ValidationError:return 'conflict'
    def test_same_row_only_one_writer_wins(self):
        row=self.work.rows.first()
        self.assertCountEqual(self.parallel([lambda:self.change(row,'A'),lambda:self.change(row,'B')]),['saved','conflict'])
        row.refresh_from_db();self.assertEqual(row.version,2)
    def test_different_rows_preserve_both_workers(self):
        first,last=list(self.work.rows.all())
        self.assertEqual(self.parallel([lambda:self.change(first,'A'),lambda:self.change(last,'B')]),['saved','saved'])
        self.assertEqual(list(self.work.rows.values_list('location',flat=True)),['A','B'])
    def test_two_managers_confirm_once(self):
        for row in self.work.rows.all():self.change(row,'A')
        self.work.refresh_from_db();revision=self.work.revision
        def confirm(user):return confirm_work(work_id=self.work.pk,revision=revision,actor=user)[1]
        self.assertCountEqual(self.parallel([lambda:confirm(self.manager),lambda:confirm(self.other)]),[True,False])
        self.assertEqual(Receipt.objects.count(),2);self.assertEqual(Stock.objects.get(item__code='C').quantity,5)
    def test_two_print_workers_claim_once(self):
        order=PickingOrder.objects.create(project='가상',actor=self.manager);item=Item.objects.create(code='P');stock=Stock.objects.create(item=item,location='A',quantity=2,reserved=1);Allocation.objects.create(order=order,stock=stock,project='가상',quantity=1)
        CompanyPrinter.objects.create(name='가상 프린터',enabled=True);_,token=preview(order,self.manager);job,_=enqueue(order_id=order.pk,token=token,copies=1,actor=self.manager)
        def take():
            result=claim();return str(result.pk) if result else None
        self.assertCountEqual(self.parallel([take,take]),[str(job.pk),None]);self.assertEqual(CompanyPrintJob.objects.get().state,'sending')
