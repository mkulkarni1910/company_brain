from app.connectors.oauth import admin_consent_url, token_url


def test_admin_consent_url():
    u = admin_consent_url(client_id="cid", redirect_uri="https://x/cb", state="s1")
    assert u.startswith("https://login.microsoftonline.com/organizations/v2.0/adminconsent?")
    assert "client_id=cid" in u and "state=s1" in u
    assert "redirect_uri=https%3A%2F%2Fx%2Fcb" in u
    assert "scope=https%3A%2F%2Fgraph.microsoft.com%2F.default" in u


def test_token_url():
    assert token_url("tenant-123") == "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token"
