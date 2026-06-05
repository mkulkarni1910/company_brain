from __future__ import annotations

import base64
import hashlib
import hmac
import io
import struct
import time
import zipfile

import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat,
)
from jose import jwt


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_rsa_keypair():
    """Return (private_pem_bytes, jwks_dict) using a fresh RSA-2048 key."""
    priv = rsa.generate_private_key(65537, 2048, default_backend())
    pub_nums = priv.public_key().public_numbers()
    n_bytes = pub_nums.n.to_bytes((pub_nums.n.bit_length() + 7) // 8, "big")
    e_bytes = pub_nums.e.to_bytes((pub_nums.e.bit_length() + 7) // 8, "big")
    jwks = {
        "keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "k1",
            "n": base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode(),
            "e": base64.urlsafe_b64encode(e_bytes).rstrip(b"=").decode(),
        }]
    }
    pem = priv.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    return pem, jwks


def _make_token(pem: bytes, app_id: str, *, issuer: str = "https://api.botframework.com") -> str:
    return jwt.encode(
        {"aud": app_id, "iss": issuer, "exp": int(time.time()) + 3600},
        pem, algorithm="RS256", headers={"kid": "k1"},
    )


# ── strip_at_mention ──────────────────────────────────────────────────────────

def test_strip_at_mention_basic():
    from app.bots.teams import strip_at_mention
    assert strip_at_mention("<at>SubstrateOS</at> what is PTO?") == "what is PTO?"


def test_strip_at_mention_newline():
    from app.bots.teams import strip_at_mention
    assert strip_at_mention("<at>Bot</at>\nhello world") == "hello world"


def test_strip_at_mention_no_mention():
    from app.bots.teams import strip_at_mention
    assert strip_at_mention("just a message") == "just a message"


# ── verify_teams_jwt ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_teams_jwt_valid(monkeypatch):
    import app.bots.teams as m
    pem, jwks = _make_rsa_keypair()
    monkeypatch.setattr(m, "_jwks_cache", jwks)
    monkeypatch.setattr(m, "_jwks_cache_ts", time.time())
    token = _make_token(pem, "my-app")
    assert await m.verify_teams_jwt(token, "my-app") is True


@pytest.mark.asyncio
async def test_verify_teams_jwt_wrong_audience(monkeypatch):
    import app.bots.teams as m
    pem, jwks = _make_rsa_keypair()
    monkeypatch.setattr(m, "_jwks_cache", jwks)
    monkeypatch.setattr(m, "_jwks_cache_ts", time.time())
    token = _make_token(pem, "other-app")
    assert await m.verify_teams_jwt(token, "my-app") is False


@pytest.mark.asyncio
async def test_verify_teams_jwt_wrong_issuer(monkeypatch):
    import app.bots.teams as m
    pem, jwks = _make_rsa_keypair()
    monkeypatch.setattr(m, "_jwks_cache", jwks)
    monkeypatch.setattr(m, "_jwks_cache_ts", time.time())
    token = _make_token(pem, "my-app", issuer="https://evil.com")
    assert await m.verify_teams_jwt(token, "my-app") is False


@pytest.mark.asyncio
async def test_verify_teams_jwt_empty_token(monkeypatch):
    import app.bots.teams as m
    pem, jwks = _make_rsa_keypair()
    monkeypatch.setattr(m, "_jwks_cache", jwks)
    monkeypatch.setattr(m, "_jwks_cache_ts", time.time())
    assert await m.verify_teams_jwt("", "my-app") is False


# ── build_teams_reply ────────────────────────────────────────────────────────

def test_build_teams_reply_shape():
    from app.bots.teams import build_teams_reply
    from app.domain.query import Answer, Citation
    answer = Answer(
        text="Here is the answer.",
        citations=[
            Citation(doc_id="d1", chunk_id="c1", source_url="https://x.com/doc",
                     title="Policy Doc", snippet="snippet"),
        ],
        query_id="q1",
    )
    reply = build_teams_reply(answer)
    assert reply["type"] == "message"
    assert reply["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = reply["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert any(b.get("text") == "Here is the answer." for b in card["body"])
    assert card["actions"][0]["type"] == "Action.OpenUrl"
    assert card["actions"][0]["url"] == "https://x.com/doc"


def test_build_teams_reply_caps_citations():
    from app.bots.teams import build_teams_reply
    from app.domain.query import Answer, Citation
    cits = [Citation(doc_id=f"d{i}", chunk_id=f"c{i}", source_url=f"https://x.com/{i}",
                     title=f"Doc {i}", snippet="s") for i in range(10)]
    reply = build_teams_reply(Answer(text="t", citations=cits, query_id="q"))
    assert len(reply["attachments"][0]["content"]["actions"]) == 5


# ── verify_slack_signature ───────────────────────────────────────────────────

def _slack_sig(secret: str, ts: str, body: bytes) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_slack_signature_valid():
    from app.bots.slack import verify_slack_signature
    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    sig = _slack_sig("mysecret", ts, body)
    assert verify_slack_signature("mysecret", ts, body, sig) is True


def test_slack_signature_tampered_body():
    from app.bots.slack import verify_slack_signature
    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    sig = _slack_sig("mysecret", ts, body)
    assert verify_slack_signature("mysecret", ts, b'{"type":"tampered"}', sig) is False


def test_slack_signature_expired_timestamp():
    from app.bots.slack import verify_slack_signature
    ts = str(int(time.time()) - 400)   # 400 s > 300 s threshold
    body = b'{"type":"event_callback"}'
    sig = _slack_sig("mysecret", ts, body)
    assert verify_slack_signature("mysecret", ts, body, sig) is False


def test_slack_signature_wrong_secret():
    from app.bots.slack import verify_slack_signature
    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    sig = _slack_sig("correct", ts, body)
    assert verify_slack_signature("wrong", ts, body, sig) is False


# ── strip_bot_mention ─────────────────────────────────────────────────────────

def test_strip_bot_mention_basic():
    from app.bots.slack import strip_bot_mention
    assert strip_bot_mention("<@U123ABC> what is PTO?") == "what is PTO?"


def test_strip_bot_mention_no_mention():
    from app.bots.slack import strip_bot_mention
    assert strip_bot_mention("hello") == "hello"


def test_strip_bot_mention_extra_spaces():
    from app.bots.slack import strip_bot_mention
    assert strip_bot_mention("<@UABC>   hello world") == "hello world"


# ── post_slack_reply error surfacing ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_slack_reply_returns_error_on_ok_false(respx_mock):
    import httpx
    from app.bots.slack import post_slack_reply
    from app.domain.query import Answer
    respx_mock.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "not_in_channel"})
    )
    err = await post_slack_reply("xoxb-t", "C123", None, Answer(text="hi", citations=[], query_id="q"))
    assert err == "not_in_channel"


@pytest.mark.asyncio
async def test_post_slack_reply_returns_none_on_success(respx_mock):
    import httpx
    from app.bots.slack import post_slack_reply
    from app.domain.query import Answer
    respx_mock.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    err = await post_slack_reply("xoxb-t", "C123", None, Answer(text="hi", citations=[], query_id="q"))
    assert err is None


# ── small talk ───────────────────────────────────────────────────────────────

def test_is_smalltalk_matches_greetings():
    from app.bots.smalltalk import is_smalltalk
    for t in ["Hello", "hi", "Hi!", "hey there", "Good morning", "thanks",
              "Thank you!", "ok", "bye", "help"]:
        assert is_smalltalk(t), t


def test_is_smalltalk_rejects_real_questions():
    from app.bots.smalltalk import is_smalltalk
    for t in ["what is PTO?", "hello world how do I file PTO",
              "RFP status", "good morning meeting notes from yesterday"]:
        assert not is_smalltalk(t), t


# ── build_manifest_zip ───────────────────────────────────────────────────────

def test_manifest_zip_structure():
    from app.bots.manifest import build_manifest_zip
    raw = build_manifest_zip("aaaa-bbbb-cccc", "api.example.com")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
    assert "manifest.json" in names
    assert "color.png" in names
    assert "outline.png" in names


def test_manifest_zip_app_id():
    import json
    from app.bots.manifest import build_manifest_zip
    raw = build_manifest_zip("my-app-id-123", "api.example.com")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["id"] == "my-app-id-123"
    assert manifest["bots"][0]["botId"] == "my-app-id-123"
    assert "api.example.com" in manifest["validDomains"]


@pytest.mark.asyncio
async def test_connector_token_uses_bot_tenant_when_set(respx_mock):
    # Single-tenant bot registrations only accept Connector tokens issued by
    # the bot's own tenant — tokens from the generic botframework.com endpoint
    # get 401 "Authorization has been denied" from smba.trafficmanager.net.
    import httpx
    from app.bots import teams
    teams._token_cache.update({"token": None, "exp": 0.0})
    route = respx_mock.post(
        "https://login.microsoftonline.com/tenant-guid-1/oauth2/v2.0/token"
    ).mock(return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600}))
    tok = await teams._connector_token("app", "pw", tenant_id="tenant-guid-1")
    assert tok == "tok-1"
    assert route.called
    teams._token_cache.update({"token": None, "exp": 0.0})


@pytest.mark.asyncio
async def test_connector_token_defaults_to_botframework_tenant(respx_mock):
    import httpx
    from app.bots import teams
    teams._token_cache.update({"token": None, "exp": 0.0})
    route = respx_mock.post(
        "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
    ).mock(return_value=httpx.Response(200, json={"access_token": "tok-2", "expires_in": 3600}))
    tok = await teams._connector_token("app", "pw", tenant_id=None)
    assert tok == "tok-2"
    assert route.called
    teams._token_cache.update({"token": None, "exp": 0.0})


@pytest.mark.asyncio
async def test_send_teams_activity_posts_to_service_url(monkeypatch):
    # Teams ignores the webhook's HTTP response body; the reply must be POSTed
    # to {serviceUrl}/v3/conversations/{conv}/activities/{replyToId}.
    from app.bots import teams

    calls = []

    class FakeResp:
        status_code = 200
        text = ""

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            calls.append((url, kw))
            return FakeResp()

    monkeypatch.setattr(teams.httpx, "AsyncClient", lambda **kw: FakeClient())
    monkeypatch.setattr(teams, "_connector_token", _async_const("tok-123"))

    incoming = {
        "serviceUrl": "https://smba.trafficmanager.net/in/",
        "conversation": {"id": "19:abc@thread.tacv2"},
        "id": "act1",
        "from": {"id": "user-1"},
        "recipient": {"id": "bot-1"},
    }
    ok = await teams.send_teams_activity(
        incoming=incoming, activity={"type": "message", "text": "hi"},
        app_id="app", app_password="pw",
    )
    assert ok is True
    url, kw = calls[0]
    assert url.startswith("https://smba.trafficmanager.net/in/v3/conversations/")
    assert url.endswith("/activities/act1")
    assert kw["headers"]["Authorization"] == "Bearer tok-123"
    body = kw["json"]
    assert body["from"] == {"id": "bot-1"}          # bot speaks as itself
    assert body["recipient"] == {"id": "user-1"}    # back to the sender
    assert body["replyToId"] == "act1"


def _async_const(value):
    async def _f(*a, **k):
        return value
    return _f


def test_manifest_conforms_to_v117_schema():
    # Teams rejected the package with "Manifest parsing error": the v1.17
    # schema has no packageName and spells the scope groupChat (camelCase).
    import json

    from app.bots.manifest import build_manifest_zip
    raw = build_manifest_zip("my-app-id-123", "api.example.com")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert "packageName" not in manifest
    assert manifest["bots"][0]["scopes"] == ["personal", "team", "groupChat"]


def test_manifest_zip_icons_are_valid_png():
    from app.bots.manifest import build_manifest_zip
    _PNG_SIG = b"\x89PNG\r\n\x1a\n"
    raw = build_manifest_zip("app-id", "host.com")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert zf.read("color.png")[:8] == _PNG_SIG
        assert zf.read("outline.png")[:8] == _PNG_SIG
