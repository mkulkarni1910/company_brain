from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib


def _make_png(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Generate a solid-colour PNG without Pillow."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = bytes([0]) + bytes([r, g, b] * width)
    idat = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# SubStrateOS amber (#C35A13) — 192×192 colour icon and 32×32 outline icon
_COLOR_PNG = _make_png(192, 192, 195, 90, 19)
_OUTLINE_PNG = _make_png(32, 32, 195, 90, 19)


def build_manifest_zip(app_id: str, api_host: str) -> bytes:
    """Return bytes of a Teams app package (manifest.json + icons)."""
    manifest = {
        "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
        "manifestVersion": "1.17",
        "version": "1.0.0",
        "id": app_id,
        "developer": {
            "name": "SubStrateOS",
            "websiteUrl": f"https://{api_host}",
            "privacyUrl": f"https://{api_host}",
            "termsOfUseUrl": f"https://{api_host}",
        },
        "name": {"short": "SubStrateOS", "full": "SubStrateOS Intelligence Layer"},
        "description": {
            "short": "Ask your company knowledge base",
            "full": (
                "SubStrateOS is your company intelligence layer. @-mention it in any channel "
                "or chat to get grounded answers drawn from SharePoint, Teams, and connected "
                "sources — scoped to what you can see."
            ),
        },
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": "#C35A13",
        "bots": [{
            "botId": app_id,
            "scopes": ["personal", "team", "groupChat"],
            "isNotificationOnly": False,
        }],
        "permissions": ["identity", "messageTeamMembers"],
        "validDomains": [api_host],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("color.png", _COLOR_PNG)
        zf.writestr("outline.png", _OUTLINE_PNG)
    return buf.getvalue()
