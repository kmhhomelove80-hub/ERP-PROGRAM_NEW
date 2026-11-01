from pathlib import Path
from tempfile import TemporaryDirectory
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


class InAppManualTests(TestCase):
    def setUp(self):
        self.reader=get_user_model().objects.create_user('manual-reader')
        self.writer=get_user_model().objects.create_user('manual-writer',is_staff=True)

    def test_login_required_for_manual_and_pdf(self):
        for path in ['/manual/','/manual/pdf/','/manual/pdf/?download=1']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,302)
            self.assertIn('/login/',response.url)

    def test_both_roles_can_read_all_chapters_and_nav_keeps_work_in_new_tab(self):
        for user in [self.reader,self.writer]:
            self.client.force_login(user)
            response=self.client.get('/manual/')
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(response.context['chapters']),14)
            self.assertContains(response,'신규 품목')
            self.assertContains(response,'미배정 물량 추가 피킹')
            self.assertContains(response,'id="manual-chapter-14"')
            self.assertContains(response,'PDF 보기')
            response=self.client.get('/')
            self.assertContains(response,'href="/manual/" target="_blank" rel="noopener"')

    def test_search_matches_current_text_and_korean_whitespace(self):
        self.client.force_login(self.reader)
        response=self.client.get('/manual/',{'q':'신규품목'})
        self.assertLess(len(response.context['matches']),14)
        self.assertTrue(any(c['number']==6 for c in response.context['matches']))
        self.assertEqual(response.context['matches'][0]['number'],6)
        response=self.client.get('/manual/',{'q':'NO-SUCH-MANUAL-TERM-98765'})
        self.assertContains(response,'관련 안내를 찾지 못했습니다.')
        self.assertEqual(response.context['matches'],[])

    def test_search_html_escaped_and_no_writes(self):
        self.client.force_login(self.reader)
        response=self.client.get('/manual/',{'q':'<script>alert(1)</script>'})
        self.assertNotContains(response,'<script>alert(1)</script>')
        self.assertContains(response,'&lt;script&gt;')
        for path in ['/manual/','/manual/pdf/']:
            self.assertEqual(self.client.post(path,{}).status_code,405)

    def test_pdf_inline_download_and_bytes_match_bundled_manual(self):
        self.client.force_login(self.reader)
        for suffix, disposition in [('', 'inline'),('?download=1','attachment')]:
            response=self.client.get('/manual/pdf/'+suffix)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response['Content-Type'],'application/pdf')
            self.assertIn(disposition,response['Content-Disposition'])
            self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
            response.close()

    def test_missing_release_files_report_not_found(self):
        self.client.force_login(self.reader)
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            self.assertEqual(self.client.get('/manual/').status_code,404)
            self.assertEqual(self.client.get('/manual/pdf/').status_code,404)
