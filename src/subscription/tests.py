# subscription/tests.py

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest import mock
import json
from datetime import timedelta
from django.utils import timezone

from .models import PlanTier, UserSubscription, PaymentTransaction
# Schemas are not typically imported into tests unless you want to validate raw response against them,
# which is more advanced. Usually, you check specific fields in the response.json().
from .services import ZARINPAL_STARTPAY_URL_TEMPLATE

User = get_user_model()


class SubscriptionModelTests(TestCase):

    def setUp(self):
        self.user_data_raw_model = {
            "email": "modeltestuser@example.com", "username": "modeltestuser_sub",
            "name": "ModelTest", "family_name": "UserSub", "password": "SecurePassword123!"
        }
        self.user_data_raw_expired = {
            "email": "expiredmodel@example.com", "username": "expiredmodel_sub",
            "name": "Expired", "family_name": "Model", "password": "SecurePassword123!"
        }

    def test_create_plan_tier(self):
        plan = PlanTier.objects.create(
            name="Pro Monthly", price=100000, currency="IRR",
            duration_days=30, max_requests=100, is_active=True
        )
        self.assertEqual(plan.name, "Pro Monthly")
        self.assertTrue(plan.is_active)

    def test_user_subscription_is_active(self):
        user = User.objects.create_user(**self.user_data_raw_model)
        plan = PlanTier.objects.create(name="Test Plan", price=100, duration_days=30, max_requests=10, is_active=True)
        active_sub = UserSubscription.objects.create(
            user=user, plan_tier=plan, status=UserSubscription.SubscriptionStatus.ACTIVE,
            start_date=timezone.now() - timedelta(days=1), expire_date=timezone.now() + timedelta(days=29)
        )
        self.assertTrue(active_sub.is_active)
        expired_user_obj = User.objects.create_user(**self.user_data_raw_expired)
        expired_sub = UserSubscription.objects.create(
            user=expired_user_obj, plan_tier=plan, status=UserSubscription.SubscriptionStatus.ACTIVE,
            start_date=timezone.now() - timedelta(days=31), expire_date=timezone.now() - timedelta(days=1)
        )
        self.assertFalse(expired_sub.is_active)
        expired_sub.update_status()
        reloaded_expired_sub = UserSubscription.objects.get(id=expired_sub.id)
        self.assertEqual(reloaded_expired_sub.status, UserSubscription.SubscriptionStatus.EXPIRED)


class SubscriptionAPITests(TestCase):
    # !!! IMPORTANT: Replace this with the URL identified by your accounts/tests.py !!!
    TOKEN_OBTAIN_URL = "/api/token/pair"  # <<< --- !!! REPLACE THIS IF DIFFERENT !!!

    def setUp(self):
        self.client = Client()
        self.user_data_for_api_tests = {
            "email": "apiuser_subscription@example.com", "username": "apiuser_sub_test",
            "name": "ApiSubTest", "family_name": "UserMain", "password": "SecurePassword123!"
        }
        self.user = User.objects.create_user(**self.user_data_for_api_tests)
        self.plan1 = PlanTier.objects.create(name="Basic", price=50000, currency="IRR", duration_days=30,
                                             max_requests=20, is_active=True)
        self.plan2 = PlanTier.objects.create(name="Premium", price=150000, currency="IRR", duration_days=30,
                                             max_requests=100, is_active=True)

        login_payload = {
            "email": self.user_data_for_api_tests["email"],
            "password": self.user_data_for_api_tests["password"]
        }
        print(
            f"\n[SubscriptionTest DEBUG] In setUp, attempting token from: {self.TOKEN_OBTAIN_URL} with payload: {login_payload}")
        response = self.client.post(
            self.TOKEN_OBTAIN_URL, data=json.dumps(login_payload), content_type='application/json'
        )
        self.assertEqual(response.status_code, 200,
                         f"Failed to get token from {self.TOKEN_OBTAIN_URL}: {response.content.decode()}")
        tokens = response.json()
        self.access_token = tokens.get('access')
        self.assertIsNotNone(self.access_token, "Access token was not found in the response.")
        self.auth_headers = {'HTTP_AUTHORIZATION': f'Bearer {self.access_token}'}
        print("[SubscriptionTest DEBUG] Token acquired successfully in setUp.")

    def _get_api_url(self, view_name_suffix: str, default_path: str) -> str:
        """Helper to try reversing URL or fall back to direct path."""
        try:
            # Assumes your main API instance has namespace 'api-1.0.0'
            # and controller path is used as a suffix for ninja-extra.
            # e.g., 'api-1.0.0:list_tiers-subscription'
            return reverse(f'api-1.0.0:{view_name_suffix}')
        except Exception:
            # print(f"Reversing '{view_name_suffix}' failed. Using direct path: {default_path}")
            return default_path

    def test_list_tiers_authenticated(self):
        url = self._get_api_url('list_tiers-subscription', "/api/subscription/tiers")
        print(f"[SubscriptionTest DEBUG] Testing list_tiers (authenticated) URL: {url}")
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, 200, response.content.decode())
        response_data = response.json()
        self.assertEqual(len(response_data), 2)  # plan1, plan2
        self.assertEqual(response_data[0]['name'], self.plan1.name)

    def test_list_tiers_fails_if_unauthenticated(self):
        url = self._get_api_url('list_tiers-subscription', "/api/subscription/tiers")
        print(f"[SubscriptionTest DEBUG] Testing list_tiers (unauthenticated access failure) URL: {url}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403, response.content.decode())  # Expect 403 due to IsAuthenticated
        self.assertIn("permission", response.json().get("detail", "").lower())

    @mock.patch('subscription.services.initiate_zarinpal_payment')
    def test_initiate_payment_authenticated(self, mock_initiate_zarinpal):
        mock_authority = "TESTAUTH123_SUB_INIT"
        mock_payment_url = ZARINPAL_STARTPAY_URL_TEMPLATE.format(mock_authority)
        mock_initiate_zarinpal.return_value = {
            "payment_url": mock_payment_url, "authority": mock_authority, "transaction_db_id": 1
        }
        url = self._get_api_url('initiate_payment-subscription', "/api/subscription/initiate-payment")
        print(f"[SubscriptionTest DEBUG] Testing initiate_payment URL: {url}")
        payload = {"plan_tier_id": self.plan1.id}
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, 200, response.content.decode())
        response_data = response.json()
        self.assertEqual(response_data['authority'], mock_authority)
        mock_initiate_zarinpal.assert_called_once_with(self.user, self.plan1.id)

    def test_get_subscription_status_authenticated_no_subscription(self):
        url = self._get_api_url('get_subscription_status-subscription', "/api/subscription/status")
        print(f"[SubscriptionTest DEBUG] Testing get_subscription_status (no sub) URL: {url}")
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, 200, response.content.decode())
        response_data = response.json()
        self.assertEqual(response_data['status'], 'none', f"Response was: {response_data}")
        self.assertFalse(response_data['is_active'])

    def test_get_subscription_status_authenticated_with_subscription(self):
        UserSubscription.objects.create(
            user=self.user, plan_tier=self.plan1, status=UserSubscription.SubscriptionStatus.ACTIVE,
            start_date=timezone.now(), expire_date=timezone.now() + timedelta(days=30),
            latest_payment_transaction_id="TXSUB123_STATUS"
        )
        url = self._get_api_url('get_subscription_status-subscription', "/api/subscription/status")
        print(f"[SubscriptionTest DEBUG] Testing get_subscription_status (with sub) URL: {url}")
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, 200, response.content.decode())
        response_data = response.json()
        self.assertEqual(response_data['plan_tier']['name'], self.plan1.name)
        self.assertTrue(response_data['is_active'])

    @mock.patch('subscription.services.verify_zarinpal_payment')
    def test_payment_callback_success(self, mock_verify_payment):
        mock_ref_id = "REF_SUB_CALLBACK_OK"
        mock_expire_date = timezone.now() + timedelta(days=30)
        mock_verify_payment.return_value = {
            "success": True, "message": "Payment verified.", "ref_id": mock_ref_id,
            "transaction_status": PaymentTransaction.TransactionStatus.VERIFIED,
            "subscription_active_until": mock_expire_date
        }
        authority = "AUTH_CALLBACK_OK"
        PaymentTransaction.objects.create(
            user=self.user, plan_tier_purchased=self.plan1, gateway_transaction_id=authority,
            amount=self.plan1.price, currency=self.plan1.currency
        )
        url = self._get_api_url('payment_callback-payment', "/api/payment/callback")
        query_params = f"?Authority={authority}&Status=OK"
        print(f"[SubscriptionTest DEBUG] Testing payment_callback (success) URL: {url + query_params}")
        response = self.client.get(url + query_params)
        self.assertEqual(response.status_code, 200, response.content.decode())
        response_data = response.json()
        self.assertEqual(response_data['message'], "Payment verified.")
        self.assertEqual(response_data['ref_id'], mock_ref_id)
        mock_verify_payment.assert_called_once_with(authority, "OK")

    @mock.patch('subscription.services.verify_zarinpal_payment')
    def test_payment_callback_failure_from_gateway_status(self, mock_verify_payment):
        authority = "AUTH_CALLBACK_NOK"
        mock_verify_payment.return_value = {
            "success": False, "message": "Gateway reported failure.",
            "transaction_status": PaymentTransaction.TransactionStatus.FAILED
        }
        PaymentTransaction.objects.create(
            user=self.user, plan_tier_purchased=self.plan1, gateway_transaction_id=authority,
            amount=self.plan1.price, currency=self.plan1.currency
        )
        url = self._get_api_url('payment_callback-payment', "/api/payment/callback")
        query_params = f"?Authority={authority}&Status=NOK"
        print(f"[SubscriptionTest DEBUG] Testing payment_callback (gateway NOK) URL: {url + query_params}")
        response = self.client.get(url + query_params)
        self.assertEqual(response.status_code, 400, response.content.decode())  # Asserting 400 as expected by API
        response_data = response.json()
        self.assertIn("Gateway reported failure.", response_data.get('detail', ""))
        mock_verify_payment.assert_called_once_with(authority, "NOK")

    @mock.patch('subscription.services.verify_zarinpal_payment')
    def test_payment_callback_verification_service_fails(self, mock_verify_payment):
        authority = "AUTH_CALLBACK_SVC_FAIL"
        mock_verify_payment.return_value = {
            "success": False, "message": "Internal verification error.",
            "transaction_status": PaymentTransaction.TransactionStatus.FAILED
        }
        PaymentTransaction.objects.create(
            user=self.user, plan_tier_purchased=self.plan1, gateway_transaction_id=authority,
            amount=self.plan1.price, currency=self.plan1.currency
        )
        url = self._get_api_url('payment_callback-payment', "/api/payment/callback")
        query_params = f"?Authority={authority}&Status=OK"  # Gateway says OK, but our service fails
        print(f"[SubscriptionTest DEBUG] Testing payment_callback (service fail) URL: {url + query_params}")
        response = self.client.get(url + query_params)
        self.assertEqual(response.status_code, 400, response.content.decode())  # Asserting 400
        response_data = response.json()
        self.assertIn("Internal verification error.", response_data.get('detail', ""))
        mock_verify_payment.assert_called_once_with(authority, "OK")