from django.test import TestCase
from django.contrib.auth import get_user_model
import json
from datetime import datetime, timezone as dt_timezone

from .models import SexChoices

User = get_user_model()


class AccountAPITests(TestCase):
    SIGNUP_URL = "/api/auth/signup"
    CUSTOM_LOGIN_URL = "/api/auth/login"

    TOKEN_OBTAIN_URL_WITH_SLASH = "/api/token/pair/"
    TOKEN_OBTAIN_URL_WITHOUT_SLASH = "/api/token/pair"
    EXPECTED_TOKEN_OBTAIN_URL = "/api/token/pair"

    PROFILE_URL = "/api/users/profile"

    def setUp(self):
        self.user_data_raw = {
            "email": "testuser@example.com",
            "username": "testuser",
            "name": "Test",
            "family_name": "User",
            "password": "SecurePassword123!"
        }
        self.login_payload = {
            "email": self.user_data_raw["email"],
            "password": self.user_data_raw["password"]
        }

    def test_api_docs_reachable(self):
        response = self.client.get("/api/docs")
        self.assertEqual(response.status_code, 200,
                         f"Failed to reach /api/docs. Content: {response.content.decode()}")

    def test_minimal_token_pair_path_variations(self):
        """Test /api/token/pair with and without trailing slash to find the working one."""
        print(f"\n[DEBUG] Testing with slash: {self.TOKEN_OBTAIN_URL_WITH_SLASH}")
        response_with_slash = self.client.post(
            self.TOKEN_OBTAIN_URL_WITH_SLASH, data=json.dumps({}), content_type="application/json"
        )  # Send empty JSON to trigger 400/401 if path exists
        print(f"[DEBUG] Status with slash: {response_with_slash.status_code}")
        if response_with_slash.status_code == 404:
            print(f"[DEBUG] Content with slash (404): {response_with_slash.content.decode()}")

        print(f"\n[DEBUG] Testing without slash: {self.TOKEN_OBTAIN_URL_WITHOUT_SLASH}")
        response_without_slash = self.client.post(
            self.TOKEN_OBTAIN_URL_WITHOUT_SLASH, data=json.dumps({}), content_type="application/json"
        )
        print(f"[DEBUG] Status without slash: {response_without_slash.status_code}")
        if response_without_slash.status_code == 404:
            print(f"[DEBUG] Content without slash (404): {response_without_slash.content.decode()}")

        found_working_path = False
        if response_with_slash.status_code != 404:
            print(
                f"[DEBUG] Path with slash ({self.TOKEN_OBTAIN_URL_WITH_SLASH}) seems to work (status: {response_with_slash.status_code}).")
            self.assertIn(response_with_slash.status_code, [400, 401], "Path with slash gave unexpected status.")
            found_working_path = True
            # If this works, subsequent tests should use self.TOKEN_OBTAIN_URL_WITH_SLASH
            AccountAPITests.EXPECTED_TOKEN_OBTAIN_URL = self.TOKEN_OBTAIN_URL_WITH_SLASH

        if response_without_slash.status_code != 404:
            print(
                f"[DEBUG] Path without slash ({self.TOKEN_OBTAIN_URL_WITHOUT_SLASH}) seems to work (status: {response_without_slash.status_code}).")
            self.assertIn(response_without_slash.status_code, [400, 401], "Path without slash gave unexpected status.")
            found_working_path = True
            # If this one works (and the one with slash didn't), update the class variable
            if AccountAPITests.EXPECTED_TOKEN_OBTAIN_URL != self.TOKEN_OBTAIN_URL_WITHOUT_SLASH and response_with_slash.status_code == 404:
                AccountAPITests.EXPECTED_TOKEN_OBTAIN_URL = self.TOKEN_OBTAIN_URL_WITHOUT_SLASH

        self.assertTrue(found_working_path, "Neither /api/token/pair/ nor /api/token/pair returned a non-404 status.")
        print(
            f"[INFO] Effective TOKEN_OBTAIN_URL for subsequent tests will be: {AccountAPITests.EXPECTED_TOKEN_OBTAIN_URL}")

    def test_user_signup_success(self):
        response = self.client.post(
            self.SIGNUP_URL,
            data=json.dumps(self.user_data_raw),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 201, response.content.decode())
        response_data = response.json()
        self.assertEqual(response_data["user"]["email"], self.user_data_raw["email"])
        self.assertTrue(isinstance(response_data["user"]["date_joined"], str))
        try:
            datetime.fromisoformat(response_data["user"]["date_joined"].replace('Z', '+00:00'))
        except ValueError:
            self.fail("date_joined is not a valid ISO datetime string in signup response")
        self.assertTrue(User.objects.filter(email=self.user_data_raw["email"]).exists())

    def test_user_signup_duplicate_email(self):
        User.objects.create_user(**self.user_data_raw)
        response = self.client.post(
            self.SIGNUP_URL,
            data=json.dumps(self.user_data_raw),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 400, response.content.decode())
        self.assertIn("Email already registered", response.json()["detail"])

    def test_custom_login_success(self):
        User.objects.create_user(**self.user_data_raw)
        response = self.client.post(
            self.CUSTOM_LOGIN_URL,
            data=json.dumps(self.login_payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content.decode())
        response_data = response.json()
        self.assertEqual(response_data["user"]["email"], self.user_data_raw["email"])
        self.assertIn("access", response_data["tokens"])

    def test_custom_login_invalid_credentials(self):
        response = self.client.post(
            self.CUSTOM_LOGIN_URL,
            data=json.dumps({"email": "wrong@example.com", "password": "badpassword"}),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 401, response.content.decode())
        self.assertIn("Invalid credentials", response.json()["detail"])

    def test_token_obtain_pair_success(self):
        User.objects.create_user(**self.user_data_raw)
        # This test now relies on EXPECTED_TOKEN_OBTAIN_URL determined by test_minimal_token_pair_path_variations
        # For standalone execution, it uses the default.
        # To make this test robust, it should ideally run *after* variations test or use a fixed known good URL.
        # For now, let's assume self.EXPECTED_TOKEN_OBTAIN_URL is correct from default.
        response = self.client.post(
            AccountAPITests.EXPECTED_TOKEN_OBTAIN_URL,  # Use the class variable
            data=json.dumps(self.login_payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200,
                         response.content.decode() + f" (URL tested: {AccountAPITests.EXPECTED_TOKEN_OBTAIN_URL})")
        response_data = response.json()
        self.assertIn("access", response_data)
        self.assertIn("refresh", response_data)

    def test_token_obtain_pair_invalid_credentials(self):
        response = self.client.post(
            AccountAPITests.EXPECTED_TOKEN_OBTAIN_URL,  # Use the class variable
            data=json.dumps({"email": "no@suchuser.com", "password": "bad"}),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 401,
                         response.content.decode() + f" (URL tested: {AccountAPITests.EXPECTED_TOKEN_OBTAIN_URL})")
        self.assertIn("detail", response.json())

    def _get_authenticated_header(self):
        User.objects.create_user(**self.user_data_raw)
        # Uses the potentially updated class variable
        token_obtain_url_to_use = AccountAPITests.EXPECTED_TOKEN_OBTAIN_URL

        token_response = self.client.post(
            token_obtain_url_to_use,
            data=json.dumps(self.login_payload),
            content_type="application/json"
        )
        # This assertion needs to pass for other tests to work
        self.assertEqual(token_response.status_code, 200,
                         f"Token obtain failed in _get_authenticated_header with URL {token_obtain_url_to_use}. "
                         f"Content: {token_response.content.decode()}")
        access_token = token_response.json()["access"]
        return {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}

    def test_get_profile_success(self):
        auth_header = self._get_authenticated_header()
        response = self.client.get(self.PROFILE_URL, **auth_header)
        self.assertEqual(response.status_code, 200, response.content.decode())
        response_data = response.json()
        self.assertEqual(response_data["user"]["email"], self.user_data_raw["email"])
        self.assertIsNone(response_data["profile"]["city"])

    def test_get_profile_unauthenticated(self):
        response = self.client.get(self.PROFILE_URL)
        self.assertEqual(response.status_code, 401, response.content.decode())

    def test_update_profile_success(self):
        auth_header = self._get_authenticated_header()
        update_payload = {
            "city": "Testville",
            "sex": SexChoices.MALE.value,
            "height": 180.5
        }
        response = self.client.put(
            self.PROFILE_URL,
            data=json.dumps(update_payload),
            content_type="application/json",
            **auth_header
        )
        self.assertEqual(response.status_code, 200, response.content.decode())
        response_data = response.json()
        self.assertEqual(response_data["city"], "Testville")
        self.assertEqual(response_data["sex"], SexChoices.MALE.value)
        self.assertEqual(response_data["height"], 180.5)

        user_obj = User.objects.get(email=self.user_data_raw["email"])
        self.assertEqual(user_obj.profile.city, "Testville")
        self.assertEqual(user_obj.profile.sex, SexChoices.MALE.name)