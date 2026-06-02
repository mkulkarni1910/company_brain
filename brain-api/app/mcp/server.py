"""Remote MCP server (FastMCP, Streamable HTTP) for the company brain.

Mounted at /mcp on the FastAPI app. Two tools — ask_company_brain and
search_company_brain — resolve to the PAT owner via TokenStore and run through
the same ACL-scoped stack as the browser. Collaborators are bound at lifespan
startup (mcp_bind); per-request auth is handled by AuthMiddleware, which stashes
the resolved User in a ContextVar the tool wrappers read.
"""
from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP

from app.domain.identity import User
from app.domain.query import QueryRequest

logger = logging.getLogger(__name__)

# Serve at the mount root so app.mount("/mcp", ...) yields exactly /mcp.
mcp = FastMCP("substrateos", stateless_http=True, streamable_http_path="/")

_state: dict = {}
_current_user: ContextVar[User | None] = ContextVar("mcp_user", default=None)


def mcp_bind(*, orchestrator, search, token_store) -> None:
    _state["orchestrator"] = orchestrator
    _state["search"] = search
    _state["token_store"] = token_store


async def _ask(query: str, user: User, *, orchestrator) -> str:
    ans = await orchestrator.answer(QueryRequest(query=query, k=5), user=user)
    sources = "\n".join(f"- {c.title} ({c.source_url})" for c in ans.citations)
    return ans.text + (f"\n\nSources:\n{sources}" if sources else "")


async def _search(query: str, user: User, *, search) -> str:
    resp = await search.result(user=user, query=query, top=8)
    if not resp.results:
        return "No results."
    lines = [f"- {h.title}: {h.snippet} ({h.source_url})" for h in resp.results]
    return "\n".join(lines)


@mcp.tool()
async def ask_company_brain(query: str) -> str:
    """Answer a question using the company's grounded knowledge (ACL-scoped to you)."""
    user = _current_user.get()
    if user is None:
        return "error: unauthorized — send Authorization: Bearer <your sbx_live_ token>"
    try:
        return await _ask(query, user, orchestrator=_state["orchestrator"])
    except Exception as e:  # noqa: BLE001 — surface as a tool error, never a 500
        logger.warning("mcp ask failed: %s", e)
        return f"error: {e}"


@mcp.tool()
async def search_company_brain(query: str) -> str:
    """Search company documents (ACL-scoped to you). Returns titles, snippets, URLs."""
    user = _current_user.get()
    if user is None:
        return "error: unauthorized — send Authorization: Bearer <your sbx_live_ token>"
    try:
        return await _search(query, user, search=_state["search"])
    except Exception as e:  # noqa: BLE001
        logger.warning("mcp search failed: %s", e)
        return f"error: {e}"


class AuthMiddleware:
    """ASGI middleware: resolve the PAT bearer to a User and stash it in a
    ContextVar for the duration of the request. Missing/invalid → tools see no
    user and return an auth error (the protocol handshake itself stays open)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        user = None
        store = _state.get("token_store")
        if store is not None:
            for k, v in scope.get("headers", []):
                if k == b"authorization":
                    val = v.decode()
                    if val.lower().startswith("bearer "):
                        try:
                            user = await store.resolve(val.split(" ", 1)[1])
                        except Exception as e:  # noqa: BLE001
                            logger.warning("mcp auth resolve failed: %s", e)
                    break
        if user is not None:
            from app.api._auth_resolve import _apply_pilot_tenant
            user = _apply_pilot_tenant(user)
        token = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(token)


def build_mcp_asgi():
    """The Streamable-HTTP ASGI app wrapped with PAT auth. Mount at /mcp."""
    return AuthMiddleware(mcp.streamable_http_app())


_session_started = False


@contextlib.asynccontextmanager
async def run_session_manager():
    """Run the Streamable-HTTP session manager exactly once.

    The manager can only ``.run()`` once per instance and ``mcp`` is a
    module-level singleton, but the TestClient re-enters the app lifespan for
    every test. So we start it on the first lifespan entry and no-op on any
    later one. Production enters the lifespan exactly once."""
    global _session_started
    if _session_started:
        yield
        return
    _session_started = True
    async with mcp.session_manager.run():
        yield
