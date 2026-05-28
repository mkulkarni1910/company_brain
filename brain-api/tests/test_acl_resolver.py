from app.ingest.acl_resolver import resolve_synthetic_acl


def test_uploaded_doc_default_acl_is_tenant_everyone() -> None:
    acls = resolve_synthetic_acl(source="uploaded", source_id="abc", overrides=None)
    assert "t-test:everyone" in acls


def test_overrides_take_precedence() -> None:
    acls = resolve_synthetic_acl(
        source="uploaded", source_id="abc", overrides=["g-sales", "u-100"]
    )
    assert set(acls) == {"g-sales", "u-100"}
