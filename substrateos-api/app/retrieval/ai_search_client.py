from __future__ import annotations

import re
from datetime import UTC, datetime

from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

from app.acl.enforcement import build_acl_filter
from app.config import get_settings
from app.domain.chunk import Chunk
from app.domain.identity import User
from app.domain.search import SearchHit, SearchPage, SourceFacet

# Page size for the purge scan/delete loop; small enough to batch, monkeypatched in tests.
_DELETE_PAGE = 1000

# Azure AI Search rejects document keys containing dot, slash, or backslash — even
# with allowUnsafeKeys=true (which only relaxes chars like ',', ':', '#'). Connector
# doc_ids embed SharePoint hostnames (omkar.sharepoint.com) and email addresses,
# which contain dots, so any key derived from a doc_id must be normalized.
_UNSAFE_KEY_CHARS = re.compile(r"[./\\]")


def safe_doc_key(key: str) -> str:
    """Make a string usable as an Azure AI Search document key.

    Only dot/slash/backslash are disallowed (given allowUnsafeKeys on upsert), so
    those are replaced with underscore; everything else is left intact to keep keys
    readable and stable across re-syncs. Must be applied at chunk_id creation so the
    same value is used for upsert, lookup, and delete.
    """
    return _UNSAFE_KEY_CHARS.sub("_", key)


def _snippet_from(row: dict) -> str:
    """Prefer a search highlight fragment; strip <b>/<em> tags; else start of content."""
    hl = row.get("@search.highlights") or {}
    frags = hl.get("content") or hl.get("title") or []
    text = frags[0] if frags else (row.get("content") or "")[:200]
    for tag in ("<b>", "</b>", "<em>", "</em>"):
        text = text.replace(tag, "")
    return text.strip()


def _to_search_doc(c: Chunk) -> dict:
    d = c.model_dump(mode="python")
    d["created_at"] = c.created_at.isoformat()
    d["modified_at"] = c.modified_at.isoformat()
    return d


def _from_search_doc(d: dict) -> Chunk:
    return Chunk.model_validate(d)


class AISearchClient:
    def __init__(self) -> None:
        s = get_settings()
        if s.azure_ai_search_key:
            from azure.core.credentials import AzureKeyCredential

            self._credential = None
            cred = AzureKeyCredential(s.azure_ai_search_key)
        else:
            self._credential = DefaultAzureCredential()
            cred = self._credential
        self._cli = SearchClient(
            endpoint=s.azure_ai_search_endpoint,
            index_name=s.azure_ai_search_index,
            credential=cred,
        )

    async def aclose(self) -> None:
        await self._cli.close()
        if self._credential is not None:
            await self._credential.close()

    async def upsert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        await self._cli.merge_or_upload_documents(
            documents=[_to_search_doc(c) for c in chunks],
            params={"allowUnsafeKeys": "true"},
        )

    async def delete_tenant_docs(self, *, tenant_id: str) -> int:
        """Delete indexed documents for the tenant. Returns the count deleted.

        Keys are collected up-front, so callers should stop ingestion before
        purging to avoid missing documents written concurrently. Azure AI Search
        caps $skip at 100,000, so tenants larger than that are not fully covered.
        """
        flt = f"tenant_id eq '{tenant_id.replace("'", "''")}'"
        keys: list[str] = []
        skip = 0
        while True:
            res = await self._cli.search(
                search_text="*", filter=flt, select=["chunk_id"], top=_DELETE_PAGE, skip=skip
            )
            batch = [r["chunk_id"] async for r in res]
            keys.extend(batch)
            if len(batch) < _DELETE_PAGE:
                break
            skip += _DELETE_PAGE
        if not keys:
            return 0
        for i in range(0, len(keys), _DELETE_PAGE):
            await self._cli.delete_documents(
                documents=[{"chunk_id": k} for k in keys[i : i + _DELETE_PAGE]]
            )
        return len(keys)

    async def delete_source_docs(self, *, tenant_id: str, source: str) -> int:
        """Delete indexed documents for a specific source within the tenant."""
        safe_t = tenant_id.replace("'", "''")
        safe_s = source.replace("'", "''")
        flt = f"tenant_id eq '{safe_t}' and source eq '{safe_s}'"
        keys: list[str] = []
        skip = 0
        while True:
            res = await self._cli.search(
                search_text="*", filter=flt, select=["chunk_id"], top=_DELETE_PAGE, skip=skip
            )
            batch = [r["chunk_id"] async for r in res]
            keys.extend(batch)
            if len(batch) < _DELETE_PAGE:
                break
            skip += _DELETE_PAGE
        if not keys:
            return 0
        for i in range(0, len(keys), _DELETE_PAGE):
            await self._cli.delete_documents(
                documents=[{"chunk_id": k} for k in keys[i : i + _DELETE_PAGE]]
            )
        return len(keys)

    async def hybrid_search(
        self, *, query: str, user: User, vector: list[float], top: int = 30
    ) -> list[Chunk]:
        flt = build_acl_filter(user)
        vector_query = VectorizedQuery(
            vector=vector, k_nearest_neighbors=50, fields="content_vector"
        )
        results = await self._cli.search(
            search_text=query,
            vector_queries=[vector_query],
            query_type="semantic",
            semantic_configuration_name="substrateos-semantic",
            filter=flt,
            top=top,
            select=[
                "chunk_id", "doc_id", "tenant_id", "source", "source_url", "title",
                "content", "acl_principals", "author_id", "entities", "created_at",
                "modified_at", "chunk_index",
            ],
        )
        chunks: list[Chunk] = []
        async for r in results:
            r["content_vector"] = []
            chunks.append(_from_search_doc(r))
        return chunks

    async def lookup_docs(self, *, doc_ids: list[str], user: User) -> dict[str, Chunk]:
        """Fetch one chunk of metadata per doc_id, applying the user's ACL filter.
        Returns only docs the user can access (the ACL enforcement point for Discover)."""
        if not doc_ids:
            return {}
        ids = ",".join(d.replace("'", "''") for d in doc_ids)
        flt = f"({build_acl_filter(user)}) and search.in(doc_id, '{ids}', ',')"
        results = await self._cli.search(
            search_text="*",
            filter=flt,
            top=min(1000, len(doc_ids) * 20),
            select=[
                "chunk_id", "doc_id", "tenant_id", "source", "source_url", "title",
                "content", "acl_principals", "author_id", "entities", "created_at",
                "modified_at", "chunk_index",
            ],
        )
        out: dict[str, Chunk] = {}
        async for r in results:
            r["content_vector"] = []
            c = _from_search_doc(r)
            out.setdefault(c.doc_id, c)
        return out

    async def count_docs(self, *, tenant_id: str) -> int | None:
        """Distinct-ish indexed chunk count for a tenant (chunk-level; honest approximation
        of 'items indexed'). Returns None on any search error so the tile shows '—'."""
        try:
            safe = tenant_id.replace("'", "''")
            results = await self._cli.search(
                search_text="*",
                filter=f"tenant_id eq '{safe}'",
                top=0,
                include_total_count=True,
            )
            return int(await results.get_count() or 0)
        except Exception:  # noqa: BLE001 — degrade to unknown
            return None

    async def search_page(
        self, *, query: str, user: User, vector: list[float], top: int = 10, skip: int = 0,
        sources: list[str] | None = None, date_from: datetime | None = None,
        author_id: str | None = None,
    ) -> SearchPage:
        """Faceted, ACL-filtered result page (one hit per doc). Degrades to an empty
        page on any search error."""
        def esc(s: str) -> str:
            return s.replace("'", "''")

        parts = [f"({build_acl_filter(user)})"]
        if sources:
            ids = ",".join(esc(s) for s in sources)
            # source IDs are internal slugs (no commas); ACL is AND-ed so over-broad sources still can't leak.
            parts.append(f"search.in(source, '{ids}', ',')")
        if date_from is not None:
            dt = date_from if date_from.tzinfo else date_from.replace(tzinfo=UTC)
            parts.append(f"modified_at ge {dt.isoformat()}")
        if author_id:
            parts.append(f"author_id eq '{esc(author_id)}'")
        flt = " and ".join(parts)

        vq = VectorizedQuery(vector=vector, k_nearest_neighbors=50, fields="content_vector")
        try:
            results = await self._cli.search(
                search_text=query,
                vector_queries=[vq],
                query_type="semantic",
                semantic_configuration_name="substrateos-semantic",
                filter=flt,
                top=max(top * 4, 20),
                # NOTE: skip is chunk-level, not doc-level — reliable only for the first page.
                skip=skip,
                facets=["source,count:10", "author_id,count:10"],
                include_total_count=True,
                highlight_fields="content,title",
                highlight_pre_tag="<b>",
                highlight_post_tag="</b>",
                select=[
                    "chunk_id", "doc_id", "tenant_id", "source", "source_url", "title",
                    "content", "author_id", "acl_principals", "created_at", "modified_at",
                    "chunk_index",
                ],
            )
            hits: dict[str, SearchHit] = {}
            async for r in results:
                doc_id = r["doc_id"]
                if doc_id in hits:
                    continue
                hits[doc_id] = SearchHit(
                    doc_id=doc_id, title=r["title"], source=r["source"],
                    source_url=r["source_url"], author_id=r.get("author_id"),
                    modified_at=r["modified_at"], snippet=_snippet_from(r),
                )
            facets_raw = await results.get_facets() or {}
            total = await results.get_count() or 0
        except Exception:  # noqa: BLE001 - search surface degrades to empty
            return SearchPage(results=[], facets=[], total=0)

        facets = [
            SourceFacet(source=f["value"], count=int(f["count"]))
            for f in (facets_raw.get("source") or [])
        ]
        author_facets = [
            (f["value"], int(f["count"]))
            for f in (facets_raw.get("author_id") or [])
            if f.get("value")
        ]
        return SearchPage(results=list(hits.values())[:top], facets=facets,
                          author_facets=author_facets, total=total)
