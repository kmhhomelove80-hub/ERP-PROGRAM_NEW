import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Item, Stock, MovementRequest


class LiveMovementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('live-user')
        self.other = get_user_model().objects.create_user('other-user')
        stock = Stock.objects.create(item=Item.objects.create(code='LIVE-001', name='조명'), location='A01', quantity=10)
        for user, destination, state in [(self.user, 'B01', 'completed'), (self.user, 'C01', 'pending'), (self.other, 'SECRET', 'completed')]:
            MovementRequest.objects.create(id=uuid.uuid4(), source=stock, requested_by=user, quantity=1, destination=destination, state=state)
        self.client.force_login(self.user)

    def test_partial_search_preserves_state_and_permissions(self):
        response = self.client.get('/movements/', {'partial':'1', 'status':'completed', 'q':'B01'})
        self.assertContains(response, 'B01')
        self.assertNotContains(response, 'C01')
        self.assertNotContains(response, 'SECRET')
        self.assertNotContains(response, '<html')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        response = self.client.get('/movements/', {'partial':'1', 'status':'completed', 'q':'SECRET'})
        self.assertEqual(response.context['page_obj'].paginator.count, 0)

    def test_normal_page_and_empty_query_and_login(self):
        self.assertContains(self.client.get('/movements/'), 'data-live-filter')
        response = self.client.get('/movements/', {'partial':'1', 'status':'', 'q':''})
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        self.client.logout()
        self.assertEqual(self.client.get('/movements/', {'partial':'1'}).status_code, 302)
