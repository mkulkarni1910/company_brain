from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^/([a-z0-9][a-z0-9-]*)(?:\s|$)")

ROUTER_PROMPT = (
    "You are a skill router. Given a user query and a catalog of named skills, "
    "return the slug of the most relevant skill if one clearly applies. "
    "Be conservative — only return a skill when the match is strong and unambiguous. "
    "Respond ONLY with valid JSON: {\"skill\": \"<slug>\"} or {\"skill\": null}. "
    "No other text."
)


class SkillRouter:
    """Resolves which skill (if any) applies to a given query.

    Touch point 1: explicit /slug prefix — fast path, no LLM call.
    Touch point 2: LLM auto-routing using the flash model — only when no explicit slug.
    """

    def __init__(self, *, skill_store, llm) -> None:
        self._store = skill_store
        self._llm = llm

    async def resolve_skill(self, query: str):
        """Returns ResolvedSkill | None."""
        m = _SLUG_RE.match(query.lstrip())
        if m:
            return await self._resolve_explicit(query, slug=m.group(1))
        return await self._resolve_auto(query)

    async def _resolve_explicit(self, query: str, slug: str):
        from app.domain.skill import ResolvedSkill
        skill = await self._store.get_by_slug(slug, enabled_only=True)
        if skill is None:
            return None
        clean = _SLUG_RE.sub("", query.lstrip(), count=1).lstrip()
        return ResolvedSkill(
            id=skill.id, slug=skill.slug, name=skill.name,
            system_prompt=skill.system_prompt, clean_query=clean or query,
            workflow=skill.workflow,
        )

    async def _resolve_auto(self, query: str):
        from app.domain.skill import ResolvedSkill
        catalog = await self._store.get_catalog()
        if not catalog:
            return None
        slug = await self._llm_route(query, catalog)
        if not slug:
            return None
        skill = await self._store.get_by_slug(slug, enabled_only=True)
        if skill is None:
            return None
        return ResolvedSkill(
            id=skill.id, slug=skill.slug, name=skill.name,
            system_prompt=skill.system_prompt, clean_query=query,
            workflow=skill.workflow,
        )

    async def _llm_route(self, query: str, catalog: list[dict]) -> str | None:
        messages = [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": f"Catalog: {json.dumps(catalog)}\n\nUser query: {query}"},
        ]
        try:
            text = await self._llm.complete(
                messages=messages, deployment="skill_router", temperature=0.0, max_tokens=60
            )
            match = re.search(r'\{.*?\}', text, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group(0))
            return data.get("skill") or None
        except Exception as e:  # noqa: BLE001
            logger.warning("Skill router LLM call failed: %s", e)
            return None
