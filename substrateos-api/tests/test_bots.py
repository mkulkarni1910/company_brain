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
    assert strip_at_mention("<at>SubStrateOS</at> what is PTO?") == "what is PTO?"


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


def test_manifest_zip_icons_are_valid_png():
    from app.bots.manifest import build_manifest_zip
    _PNG_SIG = b"\x89PNG\r\n\x1a\n"
    raw = build_manifest_zip("app-id", "host.com")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert zf.read("color.png")[:8] == _PNG_SIG
        assert zf.read("outline.png")[:8] == _PNG_SIG
