import base64
import json

from app.auth import user_from_easy_auth_header


def _principal(oid: str, tid: str, name: str, groups: list[str]) -> str:
    claims = [
        {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": oid},
        {"typ": "http://schemas.microsoft.com/identity/claims/tenantid", "val": tid},
        {"typ": "name", "val": name},
        {"typ": "preferred_username", "val": "u@x"},
    ]
    claims += [{"typ": "groups", "val": g} for g in groups]
    payload = {"auth_typ": "aad", "claims": claims}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_user_from_easy_auth_header_parses_claims() -> None:
    hdr = _principal("oid-1", "tid-1", "Alex", ["g-sales", "g-central"])
    u = user_from_easy_auth_header(hdr)
    assert u.user_id == "oid-1"
    assert u.tenant_id == "tid-1"
    assert u.display_name == "Alex"
    assert {"g-sales", "g-central"} <= u.group_ids


def test_bad_header_raises() -> None:
    import pytest

    from app.auth import InvalidToken

    with pytest.raises(InvalidToken):
        user_from_easy_auth_header("not-base64-json")
