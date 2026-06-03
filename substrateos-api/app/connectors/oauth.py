"""Microsoft admin-consent OAuth helpers for the SharePoint connector.

The connector uses the *admin-consent* flow: the connecting org's admin consents
our multi-tenant app for their tenant, then we read their SharePoint app-only via
client-credentials against that tenant's token endpoint.
"""
from __future__ import annotations

from urllib.parse import urlencode

_GRAPH_DEFAULT = "https://graph.microsoft.com/.default"


def admin_consent_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """Microsoft admin-consent URL. After the admin consents, Microsoft redirects to
    redirect_uri with ?admin_consent=True&tenant=<id>&state=<state>."""
    q = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": _GRAPH_DEFAULT,
    })
    return f"https://login.microsoftonline.com/organizations/v2.0/adminconsent?{q}"


def token_url(tenant_id: str) -> str:
    """Client-credentials token endpoint for a specific (connected) tenant."""
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
