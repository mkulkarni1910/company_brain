from app.domain.identity import User


def _escape(s: str) -> str:
    """OData escape: double single-quotes."""
    return s.replace("'", "''")


def build_acl_filter(user: User) -> str:
    """Compose the AI Search $filter expression for index-time ACL trimming.

    Combines tenant scoping and principal-set membership via search.in().
    """
    principals = ",".join(sorted(_escape(p) for p in user.principals()))
    tenant = _escape(user.tenant_id)
    return (
        f"tenant_id eq '{tenant}' and "
        f"search.in(acl_principals, '{principals}', ',')"
    )
