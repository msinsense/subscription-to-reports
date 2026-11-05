"""Shared fakes/fixtures for the Subscription to Reports test suite.

Nothing here talks to a real Sisense server - every external dependency
(pysisense's SisenseClient/AccessManagement) is stood in for by a small
fake with just enough behavior for main.py to drive.
"""
from __future__ import annotations

import pytest


class FakeResponse:
    """Stand-in for requests.Response, as returned by SisenseClient.get/post."""

    def __init__(self, *, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json_data = json_data
        self.text = text

    def json(self):
        return self._json_data


class FakeApiClient:
    """Stand-in for pysisense.SisenseClient.

    GET and POST responses are both queued per-endpoint (FIFO).
    """

    def __init__(self, *, base_url="https://sisense.example.com", is_ssl=True,
                 operating_system="linux", config=None):
        self.base_url = base_url
        self.is_ssl = is_ssl
        self.operating_system = operating_system
        self.config = config if config is not None else {}
        self._get_responses: dict[str, list] = {}
        self._post_responses: dict[str, list] = {}
        self.get_calls: list = []
        self.post_calls: list = []

    def queue_get(self, endpoint, responses):
        self._get_responses.setdefault(endpoint, [])
        self._get_responses[endpoint].extend(responses)

    def queue_post(self, endpoint, responses):
        self._post_responses.setdefault(endpoint, [])
        self._post_responses[endpoint].extend(responses)

    def get(self, endpoint, params=None, extra_headers=None):
        self.get_calls.append((endpoint, params))
        queue = self._get_responses.get(endpoint)
        if not queue:
            raise AssertionError(f"No queued GET response left for {endpoint!r}")
        return queue.pop(0)

    def post(self, endpoint, data=None, extra_headers=None):
        self.post_calls.append((endpoint, data))
        queue = self._post_responses.get(endpoint)
        if not queue:
            raise AssertionError(f"No queued POST response left for {endpoint!r}")
        return queue.pop(0)


class FakeAccessManagement:
    """Stand-in for pysisense.AccessManagement."""

    def __init__(self, *, my_user=None, users=None):
        self._my_user = my_user if my_user is not None else {"_id": "user1", "email": "admin@example.com"}
        self._users = users if users is not None else []

    def get_my_user(self):
        return self._my_user

    def get_users_all(self):
        return self._users


def make_share(*, subscribe=True, share_id="user1", share_type="user"):
    return {"subscribe": subscribe, "shareId": share_id, "type": share_type}


def make_subscription(**overrides):
    base = {
        "active": True,
        "schedule": "0 0 8 * * 1",
        "tzName": "America/Los_Angeles",
        "startAt": "2025-12-05",
        "context": {},
        "reportType": {},
        "emailSettings": {},
    }
    base.update(overrides)
    return base


def make_dashboard(**overrides):
    base = {
        "oid": "dash1",
        "title": "My Dashboard",
        "owner": "user1",
        "subscription": make_subscription(),
        "shares": [make_share()],
    }
    base.update(overrides)
    return base


@pytest.fixture
def fake_response():
    return FakeResponse


@pytest.fixture
def fake_api_client():
    return FakeApiClient


@pytest.fixture
def fake_access_management():
    return FakeAccessManagement


@pytest.fixture
def dashboard_factory():
    return make_dashboard


@pytest.fixture
def subscription_factory():
    return make_subscription


@pytest.fixture
def share_factory():
    return make_share
