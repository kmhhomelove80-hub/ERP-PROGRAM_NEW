import json,uuid
from django.test import TestCase,Client
from django.core import signing
from django.core.exceptions import ValidationError,PermissionDenied
from django.contrib.auth import get_user_model
from .models import EmployeeMenuAccess,InboundWorkList,Stock,Item,Receipt,AuditEvent
from .inbound_work import create_work,update_rows,confirm_work,archive_work

class SharedInboundTests(TestCase):
    def setUp(self):
        U=get_user_model();self.manager=U.objects.create_superuser('manager',password='test')
        self.worker=U.objects.create_user('worker',password='test');EmployeeMenuAccess.objects.create(user=self.worker,inbound_record=True)
        self.reader=U.objects.create_user('reader')
        self.data={'inbound_nonce':signing.dumps({'actor':self.manager.pk,'id':str(uuid.uuid4())},salt='inbound-receipt-page'),'inbound_revision':'1','title':'가상 입고','document_date':'2026-11-06','inbound_rows':json.dumps([['DEMO','가상 모델','25W',32,''],['DEMO','가상 모델','25W',32,'']])}
        self.work=create_work(data=self.data,actor=self.manager)
    def test_save_is_idempotent_and_never_receives(self):
        self.assertEqual(create_work(data=self.data,actor=self.manager).pk,self.work.pk)
        self.assertFalse(Stock.objects.exists());self.assertEqual(self.work.rows.count(),2)
        changed=dict(self.data,title='다른 제목')
        with self.assertRaises(ValidationError):create_work(data=changed,actor=self.manager)
    def change(self,row,actor=None,location='A01',placed=True):
        return update_rows(work_id=self.work.pk,changes=[{'id':row.pk,'version':row.version,'location':location,'placed':placed}],actor=actor or self.worker)
    def test_partial_worker_then_manager_completes_and_receives_once(self):
        rows=list(self.work.rows.all());self.change(rows[0]);self.assertFalse(Receipt.objects.exists())
        self.work.refresh_from_db()
        with self.assertRaises(ValidationError):confirm_work(work_id=self.work.pk,revision=self.work.revision,actor=self.manager)
        self.change(rows[1],self.manager,location='A02');self.work.refresh_from_db()
        work,created=confirm_work(work_id=self.work.pk,revision=self.work.revision,actor=self.manager)
        self.assertTrue(created);self.assertEqual(Receipt.objects.count(),2);self.assertEqual(sum(Stock.objects.values_list('quantity',flat=True)),64)
        _,created=confirm_work(work_id=self.work.pk,revision=1,actor=self.manager);self.assertFalse(created);self.assertEqual(Receipt.objects.count(),2)
        with self.assertRaises(ValidationError):self.change(rows[0])
    def test_worker_has_no_final_receipt_or_create_or_hide_rights(self):
        with self.assertRaises(ValidationError):confirm_work(work_id=self.work.pk,revision=1,actor=self.worker)
        with self.assertRaises(ValidationError):create_work(data=self.data,actor=self.worker)
        with self.assertRaises(ValidationError):archive_work(ids=[self.work.pk],archived=True,actor=self.worker)
        with self.assertRaises(PermissionDenied):self.change(self.work.rows.first(),actor=self.reader)
        c=Client();c.force_login(self.worker)
        response=c.post(f'/inbound/saved/{self.work.pk}/row/',json.dumps([{'id':self.work.rows.first().pk,'version':1,'location':'A01','placed':True}]),content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(c.post('/receipts/bulk/',{'action':'confirm'}).status_code,403)
    def test_conflicting_row_keeps_first_workers_input(self):
        row=self.work.rows.first();self.change(row)
        with self.assertRaises(ValidationError):self.change(row,self.manager,location='B02')
        row.refresh_from_db();self.assertEqual(row.location,'A01');self.assertEqual(row.updated_by,self.worker)
    def test_batch_conflict_rolls_back_all_rows(self):
        rows=list(self.work.rows.all());self.change(rows[1]);self.work.refresh_from_db()
        with self.assertRaises(ValidationError):update_rows(work_id=self.work.pk,actor=self.manager,changes=[{'id':r.pk,'version':1,'location':'B','placed':True} for r in rows])
        rows[0].refresh_from_db();self.assertEqual(rows[0].location,'')
    def test_stale_confirmation_blocks(self):
        for row in self.work.rows.all():self.change(row)
        with self.assertRaises(ValidationError):confirm_work(work_id=self.work.pk,revision=1,actor=self.manager)
        self.assertFalse(Receipt.objects.exists())
    def test_hidden_received_list_keeps_stock_and_can_restore(self):
        for row in self.work.rows.all():self.change(row)
        self.work.refresh_from_db();confirm_work(work_id=self.work.pk,revision=self.work.revision,actor=self.manager)
        archive_work(ids=[self.work.pk],archived=True,actor=self.manager);self.work.refresh_from_db();self.assertTrue(self.work.archived)
        self.assertEqual(sum(Stock.objects.values_list('quantity',flat=True)),64)
        archive_work(ids=[self.work.pk],archived=False,actor=self.manager);self.work.refresh_from_db();self.assertFalse(self.work.archived);self.assertEqual(self.work.state,'received')
    def test_unconfirmed_and_foreign_rows_block(self):
        row=self.work.rows.first()
        with self.assertRaises(ValidationError):self.change(row,location='')
        data=dict(self.data,inbound_revision='2');other=create_work(data=data,actor=self.manager)
        with self.assertRaises(ValidationError):update_rows(work_id=self.work.pk,actor=self.worker,changes=[{'id':other.rows.first().pk,'version':1,'location':'A','placed':True}])
    def test_fraction_can_be_saved_but_cannot_be_received(self):
        data=dict(self.data,inbound_revision='2',inbound_rows=json.dumps([['F','가상','1W','1.5','A']]))
        work=create_work(data=data,actor=self.manager);row=work.rows.first();update_rows(work_id=work.pk,changes=[{'id':row.pk,'version':1,'location':'A','placed':True}],actor=self.worker);work.refresh_from_db()
        with self.assertRaises(ValidationError):confirm_work(work_id=work.pk,revision=work.revision,actor=self.manager)
        self.assertFalse(Receipt.objects.exists())
    def test_csrf_and_read_only_worker(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.worker)
        self.assertEqual(c.post(f'/inbound/saved/{self.work.pk}/row/','[]',content_type='application/json').status_code,403)
        c=Client();c.force_login(self.reader)
        self.assertEqual(c.post(f'/inbound/saved/{self.work.pk}/row/','[]',content_type='application/json').status_code,403)

    def test_workbook_multiline_description_is_normalized_for_five_columns(self):
        data=dict(self.data,inbound_revision='2',inbound_rows=json.dumps([['ML','가상\n모델','25W\n220V',1,'']]))
        work=create_work(data=data,actor=self.manager);row=work.rows.get()
        self.assertEqual(row.name,'가상 모델');self.assertEqual(row.specification,'25W 220V')
