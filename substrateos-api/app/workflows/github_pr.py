"""The raise-PR playbook (When → Check → Stop → Do → Record), transport-agnostic.

Surface adapters (Slack blocks, Teams Adaptive Cards, the web pending_action
payload) call start/confirm/cancel and render the returned results — the logic
is never forked per surface. PRs are created with the requesting user's own
GitHub token, so attribution is structural."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import get_settings
from app.connectors.github import GithubApiError, GithubAuthError, GithubClient
from app.connectors.github_store import GithubStore
from app.domain.workflow import RefundRun
from app.workflows.github_engine import PrDraftEngine
from app.workflows.store import RunStore

logger = logging.getLogger(__name__)

_NOT_CONFIGURED = (
    "The GitHub tool isn't set up yet — ask your admin to connect a repository "
    "in Admin → Surfaces → GitHub."
)
_DISABLED = "The GitHub tool is disabled — raise-PR requests are refused."
_RECONNECT = "Your GitHub connection expired — reconnect and ask again: "


@dataclass
class StartResult:
    status: Literal["preview", "connect", "clarify", "blocked", "error"]
    run: RefundRun | None = None
    connect_url: str | None = None
    message: str = ""


@dataclass
class ActionResult:
    ok: bool
    status: str = ""
    pr_url: str | None = None
    message: str = ""


class GithubFlow:
    def __init__(self, *, store: RunStore, github: GithubStore, connections,
                 engine: PrDraftEngine, client_factory=GithubClient) -> None:
        self._store = store
        self._github = github
        self._connections = connections
        self._engine = engine
        self._client_factory = client_factory

    async def _tool_enabled(self, tenant: str) -> bool:
        try:
            surfaces = await self._connections.list_surfaces(tenant)
            cfg = next((s for s in surfaces if s.name == "github"), None)
            return cfg.enabled if cfg is not None else True
        except Exception:  # noqa: BLE001 — config-store outage must not silence the tool
            return True

    async def _connect_result(self, tenant: str, email: str, *, expired: bool = False) -> StartResult:
        state = await self._github.mint_connect_state(tenant, email)
        url = f"{get_settings().substrateos_api_base_url}/auth/github/start?s={state}"
        msg = (_RECONNECT if expired else
               "Connect your GitHub account first — the PR will be authored as you: ")
        return StartResult(status="connect", connect_url=url, message=msg + url)

    # ── When + Check + Stop ────────────────────────────────────────────────────

    async def start(self, text: str, *, requester_name: str, requester_email: str | None,
                    surface: str, channel: str | None = None,
                    thread_ts: str | None = None) -> StartResult:
        s = get_settings()
        tenant = s.substrateos_tenant_id
        if not await self._tool_enabled(tenant):
            return StartResult(status="blocked", message=_DISABLED)
        cfg = await self._github.get_config(tenant)
        if cfg is None or not s.github_client_id or not s.github_client_secret:
            return StartResult(status="error", message=_NOT_CONFIGURED)
        if not requester_email:
            return StartResult(status="error",
                               message="I couldn't resolve your email on this surface, "
                                       "so I can't link a GitHub login to you.")
        token = await self._github.get_user_token(tenant, requester_email)
        if not token:
            return await self._connect_result(tenant, requester_email)

        try:
            draft, clarify = await self._engine.draft(
                text, client=self._client_factory(token), config=cfg)
        except GithubAuthError:
            return await self._connect_result(tenant, requester_email, expired=True)
        except GithubApiError as e:
            logger.warning("raise-pr draft failed: %s", e)
            return StartResult(status="error",
                               message=f"I couldn't read {cfg.owner}/{cfg.repo} — "
                                       "check the repo configuration with your admin.")
        if draft is None:
            return StartResult(status="clarify", message=clarify or "")

        run = await self._store.create(
            requester_name=requester_name, requester_slack_id=None,
            channel=channel, thread_ts=thread_ts, kind="github_pr", request_text=text,
        )
        run.status = "pending_confirm"
        run.surface = surface
        run.requester_email = requester_email
        run.pr_draft = draft
        await self._store.save(run)
        await self._store.add_event(run.id, step="Request received",
                                    detail=f"{text[:160]} · from {surface}", actor=requester_name)
        await self._store.add_event(run.id, step="Change drafted",
                                    detail=f"{draft.path} — {draft.summary}", actor="SubstrateOS")
        await self._store.add_event(run.id, step="Preview shown",
                                    detail="Awaiting the requester's confirm — nothing acts until they decide",
                                    actor="SubstrateOS")
        return StartResult(status="preview", run=run)

    # ── shared action guards ───────────────────────────────────────────────────

    async def _guarded_run(self, run_id: str, actor_email: str | None
                           ) -> tuple[RefundRun | None, ActionResult | None]:
        run = await self._store.get(run_id)
        if run is None or run.kind != "github_pr":
            return None, ActionResult(ok=False, status="unknown", message="Unknown run.")
        if not actor_email or actor_email.lower() != (run.requester_email or "").lower():
            await self._store.add_event(run.id, step="Action rejected",
                                        detail=f"Confirm/cancel attempted by {actor_email or 'unknown'} — requester only",
                                        actor="SubstrateOS")
            return None, ActionResult(ok=False, status=run.status,
                                      message="Only the requester can act on this PR.")
        if run.status != "pending_confirm":
            return None, ActionResult(ok=False, status=run.status, pr_url=run.pr_url,
                                      message=f"This run is already {run.status}.")
        return run, None

    # ── Do + Record ────────────────────────────────────────────────────────────

    async def confirm(self, run_id: str, *, actor_email: str | None,
                      actor_name: str) -> ActionResult:
        run, err = await self._guarded_run(run_id, actor_email)
        if err is not None:
            return err
        s = get_settings()
        tenant = s.substrateos_tenant_id
        cfg = await self._github.get_config(tenant)
        token = await self._github.get_user_token(tenant, run.requester_email)
        if cfg is None or not token:
            return ActionResult(ok=False, status=run.status, message=_NOT_CONFIGURED)
        client = self._client_factory(token)
        draft = run.pr_draft
        base_branch_name = f"substrateos/{run.id.lower()}"
        try:
            sha = await client.branch_sha(cfg.owner, cfg.repo, cfg.base_branch)
            branch = base_branch_name
            for attempt in range(2, 7):
                if await client.create_branch(cfg.owner, cfg.repo, branch, sha):
                    break
                branch = f"{base_branch_name}-{attempt}"
            else:
                raise GithubApiError("could not allocate a branch name")
            await client.put_file(cfg.owner, cfg.repo, draft.path,
                                  content=draft.new_content, message=draft.title,
                                  branch=branch, sha=draft.base_sha)
            body = (f"{draft.body}\n\n---\nRaised via SubstrateOS by {run.requester_name} "
                    f"from {run.surface} · run {run.id}.")
            pr_url = await client.create_pr(cfg.owner, cfg.repo, title=draft.title,
                                            body=body, head=branch, base=cfg.base_branch)
        except GithubAuthError:
            await self._store.add_event(run.id, step="Token rejected",
                                        detail="GitHub rejected the user's token — reconnect needed",
                                        actor="SubstrateOS")
            return ActionResult(ok=False, status=run.status,
                                message="GitHub rejected your token — reconnect and try again.")
        except GithubApiError as e:
            run.status = "error"
            await self._store.save(run)
            await self._store.add_event(run.id, step="PR failed", detail=str(e)[:200],
                                        actor="SubstrateOS")
            return ActionResult(ok=False, status="error",
                                message="GitHub refused the change — the run is recorded; "
                                        "check the repo settings.")
        run.status = "completed"
        run.pr_url = pr_url
        await self._store.save(run)
        await self._store.add_event(run.id, step="Confirmed",
                                    detail=f"{actor_name} confirmed the drafted change", actor=actor_name)
        await self._store.add_event(run.id, step="PR created",
                                    detail=f"{pr_url} · branch {branch} · authored as the requester",
                                    actor="SubstrateOS")
        return ActionResult(ok=True, status="completed", pr_url=pr_url)

    async def cancel(self, run_id: str, *, actor_email: str | None,
                     actor_name: str) -> ActionResult:
        run, err = await self._guarded_run(run_id, actor_email)
        if err is not None:
            return err
        run.status = "cancelled"
        await self._store.save(run)
        await self._store.add_event(run.id, step="Cancelled",
                                    detail=f"{actor_name} cancelled — nothing reached GitHub",
                                    actor=actor_name)
        return ActionResult(ok=True, status="cancelled")
