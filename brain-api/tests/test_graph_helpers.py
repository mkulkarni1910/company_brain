"""Tests for shared Graph helper functions in app/connectors/graph.py."""

from app.connectors.graph import parse_users

# ---------------------------------------------------------------------------
# parse_users
# ---------------------------------------------------------------------------

def test_parse_users_maps_id_mail_upn():
    data = {
        "value": [
            {"id": "u1", "mail": "alice@example.com", "userPrincipalName": "alice@example.com"},
            {"id": "u2", "mail": "bob@example.com", "userPrincipalName": "bob@example.com"},
        ]
    }
    users = parse_users(data)
    assert len(users) == 2
    assert users[0] == {"user_id": "u1", "mail": "alice@example.com", "upn": "alice@example.com"}
    assert users[1] == {"user_id": "u2", "mail": "bob@example.com", "upn": "bob@example.com"}


def test_parse_users_drops_empty_id():
    data = {
        "value": [
            {"id": "", "mail": "ghost@example.com", "userPrincipalName": "ghost@example.com"},
            {"id": "u1", "mail": "real@example.com", "userPrincipalName": "real@example.com"},
        ]
    }
    users = parse_users(data)
    assert len(users) == 1
    assert users[0]["user_id"] == "u1"


def test_parse_users_drops_missing_id():
    data = {
        "value": [
            {"mail": "nobody@example.com"},
            {"id": "u2", "mail": "someone@example.com", "userPrincipalName": "someone@example.com"},
        ]
    }
    users = parse_users(data)
    assert len(users) == 1
    assert users[0]["user_id"] == "u2"


def test_parse_users_falls_back_mail_to_upn():
    """When mail is missing, should fall back to userPrincipalName."""
    data = {
        "value": [
            {"id": "u1", "userPrincipalName": "alice@tenant.onmicrosoft.com"},
        ]
    }
    users = parse_users(data)
    assert len(users) == 1
    assert users[0]["mail"] == "alice@tenant.onmicrosoft.com"
    assert users[0]["upn"] == "alice@tenant.onmicrosoft.com"


def test_parse_users_empty_value():
    assert parse_users({"value": []}) == []


def test_parse_users_no_value_key():
    assert parse_users({}) == []


def test_parse_users_multiple_entries_with_mixed_fields():
    data = {
        "value": [
            {"id": "u1", "mail": "a@x.com", "userPrincipalName": "a@x.com"},
            {"id": "", "mail": "empty@x.com"},   # empty id — dropped
            {"id": "u2", "userPrincipalName": "b@x.com"},  # no mail — uses upn
            {"id": "u3", "mail": "c@x.com"},    # no upn — upn=""
        ]
    }
    users = parse_users(data)
    assert len(users) == 3
    user_ids = [u["user_id"] for u in users]
    assert "u1" in user_ids
    assert "u2" in user_ids
    assert "u3" in user_ids
    # u2 mail falls back to upn
    u2 = next(u for u in users if u["user_id"] == "u2")
    assert u2["mail"] == "b@x.com"
