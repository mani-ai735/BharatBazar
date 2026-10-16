"""Integration checks; run in the API container while the Compose stack is up."""
import json
import os
import secrets
import unittest
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import delete, select

from app import AdminSession, SessionLocal, User, ph, tok


BASE_URL = os.getenv("ADMIN_TEST_BASE_URL", "http://web").rstrip("/")
PASSWORD = "Test-only-Strong-Password-938!"


def request(path, method="GET", body=None, cookie=None, bearer=None):
    headers = {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = Request(f"{BASE_URL}/api/v1{path}", data=payload, headers=headers, method=method)
    try:
        with urlopen(req, timeout=8) as response:
            data = json.loads(response.read() or b"{}")
            set_cookie = response.headers.get("Set-Cookie", "")
            return response.status, data, set_cookie
    except HTTPError as error:
        data = json.loads(error.read() or b"{}")
        return error.code, data, error.headers.get("Set-Cookie", "")


def cookie_value(set_cookie):
    return set_cookie.split(";", 1)[0]


class AdminAccessIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suffix = secrets.token_hex(6)
        cls.admin_email = f"admin-check-{suffix}@example.com"
        cls.buyer_email = f"buyer-check-{suffix}@example.com"
        cls.seller_email = f"seller-check-{suffix}@example.com"
        cls.register_email = f"register-check-{suffix}@example.com"
        cls.admin_name = f"Admin Check {suffix}"
        with SessionLocal() as session:
            users = [
                User(email=cls.admin_email, name=cls.admin_name, password_hash=ph.hash(PASSWORD), role="admin"),
                User(email=cls.buyer_email, name="Buyer Check", password_hash=ph.hash(PASSWORD), role="buyer"),
                User(email=cls.seller_email, name="Seller Check", password_hash=ph.hash(PASSWORD), role="seller"),
            ]
            session.add_all(users)
            session.commit()
            cls.admin_id, cls.buyer_id, cls.seller_id = [user.id for user in users]

    @classmethod
    def tearDownClass(cls):
        ids = [cls.admin_id, cls.buyer_id, cls.seller_id]
        with SessionLocal() as session:
            session.execute(delete(AdminSession).where(AdminSession.admin_id.in_(ids)))
            session.execute(delete(User).where(User.id.in_(ids)))
            session.execute(delete(User).where(User.email == cls.register_email))
            session.commit()

    def login(self, endpoint, identifier, password=PASSWORD):
        status, data, header = request(endpoint, "POST", {"identifier": identifier, "password": password})
        return status, data, header

    def test_01_logged_out_and_bearer_admin_are_rejected(self):
        self.assertEqual(request("/admin/overview")[0], 401)
        with SessionLocal() as session:
            admin_user = session.get(User, self.admin_id)
            old_bearer = tok(admin_user)
        self.assertEqual(request("/admin/overview", bearer=old_bearer)[0], 401)

    def test_02_buyer_and_seller_are_forbidden(self):
        for email, expected_role in ((self.buyer_email, "buyer"), (self.seller_email, "seller")):
            status, data, cookie = request("/auth/login", "POST", {"email": email, "password": PASSWORD, "terms_accepted": True, "privacy_accepted": True})
            self.assertEqual(status, 200)
            self.assertEqual(data["user"]["role"], expected_role)
            self.assertNotIn("token", data)
            self.assertEqual(request("/admin/overview", cookie=cookie_value(cookie))[0], 403)

    def test_03_admin_password_and_dedicated_login(self):
        status, _, _ = self.login("/auth/admin/login", self.admin_email, "Incorrect-Password-938!")
        self.assertEqual(status, 401)
        status, data, cookie = self.login("/auth/admin/login", self.admin_name)
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["role"], "admin")
        self.assertNotIn("token", data)
        self.assertIn("httponly", cookie.lower())
        self.assertIn("samesite=lax", cookie.lower())
        self.assertIn("secure", cookie.lower())
        self.assertEqual(request("/admin/overview", cookie=cookie_value(cookie))[0], 200)

    def test_04_public_login_cannot_create_an_admin_session(self):
        status, _, cookie = request("/auth/login", "POST", {"email": self.admin_email, "password": PASSWORD, "terms_accepted": True, "privacy_accepted": True})
        self.assertEqual(status, 403)
        self.assertFalse(cookie)

    def test_05_registration_cannot_choose_admin_role(self):
        status, _, _ = request("/auth/register", "POST", {"email": self.register_email, "password": PASSWORD, "name": "Register Check", "phone": "919900001234", "role": "admin"})
        self.assertEqual(status, 422)
        with SessionLocal() as session:
            self.assertIsNone(session.scalar(select(User).where(User.email == self.register_email)))

    def test_06_public_registration_and_cookie_session_still_work(self):
        status, data, cookie = request("/auth/register", "POST", {"email": self.register_email, "password": PASSWORD, "name": "Register Check", "phone": "919900001234", "terms_accepted": True, "privacy_accepted": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["role"], "buyer")
        self.assertNotIn("token", data)
        self.assertEqual(request("/auth/me", cookie=cookie_value(cookie))[1]["role"], "buyer")

    def test_07_logout_revokes_admin_session(self):
        status, _, cookie = self.login("/auth/admin/login", self.admin_email)
        self.assertEqual(status, 200)
        self.assertEqual(request("/auth/admin/logout", "POST", {}, cookie=cookie_value(cookie))[0], 200)
        self.assertEqual(request("/admin/overview", cookie=cookie_value(cookie))[0], 401)

    def test_08_demoted_admin_loses_access_immediately(self):
        status, _, cookie = self.login("/auth/admin/login", self.admin_email)
        self.assertEqual(status, 200)
        with SessionLocal() as session:
            session.get(User, self.admin_id).role = "buyer"
            session.commit()
        self.assertEqual(request("/admin/overview", cookie=cookie_value(cookie))[0], 403)
        self.assertEqual(request("/admin/overview", cookie=cookie_value(cookie))[0], 401)
        with SessionLocal() as session:
            session.get(User, self.admin_id).role = "admin"
            session.commit()

    def test_09_expired_inactivity_session_is_rejected(self):
        status, _, cookie = self.login("/auth/admin/login", self.admin_email)
        self.assertEqual(status, 200)
        with SessionLocal() as session:
            admin_session = session.scalar(select(AdminSession).where(AdminSession.admin_id == self.admin_id).order_by(AdminSession.last_activity.desc()))
            admin_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        self.assertEqual(request("/admin/overview", cookie=cookie_value(cookie))[0], 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
