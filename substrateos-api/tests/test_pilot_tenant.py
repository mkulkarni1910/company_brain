"""Single-org pilot tenant mapping in resolve_user (_apply_pilot_tenant)."""
from types import SimpleNamespace

from app.api import _auth_resolve
from app.domain.identity import User


def _user() -> User:
    return User(
        user_id="oid-1",
        tenant_id="f3bddc3c-aad-guid",
        email="u@x",
        display_name="Alex",
        group_ids={"g-real"},
    )


def test_pilot_off_leaves_user_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        _auth_resolve, "get_settings",
        lambda: SimpleNamespace(pilot_single_tenant=False, brain_tenant_id="t-eval"),
    )
    u = _auth_resolve._apply_pilot_tenant(_user())
    assert u.tenant_id == "f3bddc3c-aad-guid"
    assert u.group_ids == {"g-real"}


def test_pilot_on_remaps_tenant_and_grants_everyone(monkeypatch) -> None:
    monkeypatch.setattr(
        _auth_resolve, "get_settings",
        lambda: SimpleNamespace(pilot_single_tenant=True, brain_tenant_id="t-eval"),
    )
    u = _auth_resolve._apply_pilot_tenant(_user())
    assert u.tenant_id == "t-eval"
    assert "t-eval:everyone" in u.group_ids
    assert "g-real" in u.group_ids  # real groups preserved
    assert "oid-1" in u.principals()  # identity preserved for personalization
