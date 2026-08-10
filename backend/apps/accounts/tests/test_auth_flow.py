"""End-to-end tests for the register / login / refresh / logout flows."""
from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import Session, User
from apps.profiles.models import Profile

pytestmark = pytest.mark.django_db

ORIGIN = "http://localhost:5173"
REFRESH_COOKIE = "refresh_token"


@pytest.fixture
def client() -> APIClient:
    # Send an allowed Origin so cookie-based endpoints pass the CSRF check.
    return APIClient(HTTP_ORIGIN=ORIGIN)


def register(client, username="haisum", email="haisum@example.com", password="Str0ng-Pass-9"):
    return client.post(
        reverse("accounts:register"),
        {"username": username, "email": email, "password": password},
        format="json",
    )


# --------------------------------------------------------------------------- #
# Register
# --------------------------------------------------------------------------- #
def test_register_creates_user_profile_session_and_tokens(client):
    resp = register(client)
    assert resp.status_code == 201

    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] > 0
    assert body["user"]["username"] == "haisum"

    # User + profile + one session persisted.
    user = User.objects.get(email="haisum@example.com")
    assert Profile.objects.filter(user=user).exists()
    assert Session.objects.filter(user=user).count() == 1

    # Refresh token delivered as httpOnly cookie, not in the body.
    assert REFRESH_COOKIE in resp.cookies
    assert resp.cookies[REFRESH_COOKIE]["httponly"]
    assert "refresh_token" not in body

    # Only the hash is stored, never the raw token.
    raw = resp.cookies[REFRESH_COOKIE].value
    assert not Session.objects.filter(refresh_token_hash=raw).exists()


def test_register_rejects_duplicate_username_case_insensitive(client):
    register(client)
    resp = register(client, username="HAISUM", email="other@example.com")
    assert resp.status_code == 400
    assert "username" in str(resp.json()["error"]["detail"]).lower()


def test_register_rejects_duplicate_email_case_insensitive(client):
    register(client)
    resp = register(client, username="different", email="HAISUM@example.com")
    assert resp.status_code == 400
    assert "email" in str(resp.json()["error"]["detail"]).lower()


def test_register_rejects_weak_password(client):
    resp = register(client, password="123")
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
def test_login_success_creates_second_session(client):
    register(client)
    resp = client.post(
        reverse("accounts:login"),
        {"email": "haisum@example.com", "password": "Str0ng-Pass-9"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]
    # A new device/login => a new session row (2 total).
    assert Session.objects.filter(user__email="haisum@example.com").count() == 2


def test_login_wrong_password_is_generic_401(client):
    register(client)
    resp = client.post(
        reverse("accounts:login"),
        {"email": "haisum@example.com", "password": "wrong-password"},
        format="json",
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


def test_login_unknown_email_same_generic_401(client):
    resp = client.post(
        reverse("accounts:login"),
        {"email": "nobody@example.com", "password": "whatever-123"},
        format="json",
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


# --------------------------------------------------------------------------- #
# Refresh / rotation
# --------------------------------------------------------------------------- #
def test_refresh_rotates_token_and_returns_new_access(client):
    register(client)
    old_cookie = client.cookies[REFRESH_COOKIE].value

    resp = client.post(reverse("accounts:refresh"), format="json")
    assert resp.status_code == 200
    assert resp.json()["access_token"]

    new_cookie = resp.cookies[REFRESH_COOKIE].value
    assert new_cookie != old_cookie  # rotated


def test_reused_refresh_token_revokes_session(client):
    register(client)
    first_token = client.cookies[REFRESH_COOKIE].value

    # First refresh rotates first_token away.
    client.post(reverse("accounts:refresh"), format="json")

    # Replay the now-stale first_token -> reuse detected, session revoked.
    client.cookies[REFRESH_COOKIE] = first_token
    resp = client.post(reverse("accounts:refresh"), format="json")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "token_reuse_detected"

    session = Session.objects.get(user__email="haisum@example.com")
    assert session.revoked_at is not None


def test_refresh_without_cookie_is_401(client):
    resp = client.post(reverse("accounts:refresh"), format="json")
    assert resp.status_code == 401


def test_refresh_rejected_from_foreign_origin(client):
    register(client)
    bad = APIClient(HTTP_ORIGIN="http://evil.example")
    bad.cookies[REFRESH_COOKIE] = client.cookies[REFRESH_COOKIE].value
    resp = bad.post(reverse("accounts:refresh"), format="json")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Logout / me
# --------------------------------------------------------------------------- #
def test_logout_revokes_session_and_clears_cookie(client):
    register(client)
    resp = client.post(reverse("accounts:logout"), format="json")
    assert resp.status_code == 204
    session = Session.objects.get(user__email="haisum@example.com")
    assert session.revoked_at is not None


def test_me_requires_and_returns_authenticated_user(client):
    reg = register(client)
    access = reg.json()["access_token"]

    # Without token -> 401.
    assert client.get(reverse("accounts:me")).status_code == 401

    # With token -> 200 + user.
    resp = client.get(reverse("accounts:me"), HTTP_AUTHORIZATION=f"Bearer {access}")
    assert resp.status_code == 200
    assert resp.json()["email"] == "haisum@example.com"


def test_logout_invalidates_access_token_on_next_request(client):
    reg = register(client)
    access = reg.json()["access_token"]
    auth = {"HTTP_AUTHORIZATION": f"Bearer {access}"}

    # Works before logout.
    assert client.get(reverse("accounts:me"), **auth).status_code == 200

    # Logout revokes the session behind this access token.
    client.post(reverse("accounts:logout"), format="json")

    # Same (unexpired) access token is now rejected — no 15-min window.
    assert client.get(reverse("accounts:me"), **auth).status_code == 401


def test_logout_all_invalidates_access_token_on_next_request(client):
    reg = register(client)
    access = reg.json()["access_token"]
    auth = {"HTTP_AUTHORIZATION": f"Bearer {access}"}

    client.post(reverse("accounts:logout-all"), format="json", **auth)
    assert client.get(reverse("accounts:me"), **auth).status_code == 401


def test_logout_all_revokes_every_session(client):
    reg = register(client)
    access = reg.json()["access_token"]
    # Second login => second session.
    client.post(
        reverse("accounts:login"),
        {"email": "haisum@example.com", "password": "Str0ng-Pass-9"},
        format="json",
    )
    assert Session.objects.active().count() == 2

    resp = client.post(reverse("accounts:logout-all"), HTTP_AUTHORIZATION=f"Bearer {access}", format="json")
    assert resp.status_code == 200
    assert Session.objects.active().count() == 0
