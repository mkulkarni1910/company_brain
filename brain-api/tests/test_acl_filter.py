from app.acl.enforcement import build_acl_filter
from app.domain.identity import User


def test_filter_includes_tenant_and_principals() -> None:
    u = User(
        user_id="u-1",
        tenant_id="t-test",
        email="a@b",
        display_name="A",
        group_ids={"g-sales", "g-central"},
    )
    f = build_acl_filter(u)
    assert "tenant_id eq 't-test'" in f
    assert "search.in(acl_principals" in f
    # all principals present (any order)
    for p in {"u-1", "g-sales", "g-central"}:
        assert p in f


def test_filter_escapes_single_quotes_in_ids() -> None:
    u = User(
        user_id="u'1",
        tenant_id="t'test",
        email="a@b",
        display_name="A",
        group_ids=set(),
    )
    f = build_acl_filter(u)
    # OData escapes single quotes by doubling them
    assert "t''test" in f
    assert "u''1" in f
