from app.domain.identity import User


def _escape(s: str) -> str:
    """OData escape: double single-quotes."""
    return s.replace("'", "''")


def build_acl_filter(user: User) -> str:
    """Compose the AI Search $filter expression for index-time ACL trimming.

    Combines tenant scoping and principal-set membership. Uses an OData
    lambda (`any`) because `acl_principals` is a Collection(Edm.String);
    `search.in` requires a single-value field, so we apply it to each
    element of the collection.
    """
    principals = ",".join(sorted(_escape(p) for p in user.principals()))
    tenant = _escape(user.tenant_id)
    return (
        f"tenant_id eq '{tenant}' and "
        f"acl_principals/any(p: search.in(p, '{principals}', ','))"
    )
