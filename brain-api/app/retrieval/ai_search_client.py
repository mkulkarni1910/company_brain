from __future__ import annotations

from functools import lru_cache

from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

from app.acl.enforcement import build_acl_filter
from app.config import get_settings
from app.domain.chunk import Chunk
from app.domain.identity import User


def _to_search_doc(c: Chunk) -> dict:
    d = c.model_dump(mode="python")
    # AI Search expects ISO format datetimes
    d["created_at"] = c.created_at.isoformat()
    d["modified_at"] = c.modified_at.isoformat()
    return d


def _from_search_doc(d: dict) -> Chunk:
    return Chunk.model_validate(d)


@lru_cache(maxsize=1)
def _client() -> SearchClient:
    s = get_settings()
    return SearchClient(
        endpoint=s.azure_ai_search_endpoint,
        index_name=s.azure_ai_search_index,
        credential=DefaultAzureCredential(),
    )


class AISearchClient:
    def __init__(self) -> None:
        self._cli = _client()

    async def upsert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        # chunk_id values contain '#', which AI Search rejects as an unsafe key
        # unless allowUnsafeKeys=true is passed as a query parameter.
        await self._cli.merge_or_upload_documents(
            documents=[_to_search_doc(c) for c in chunks],
            params={"allowUnsafeKeys": "true"},
        )

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
            semantic_configuration_name="brain-semantic",
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
            # restore content_vector as empty (we don't retrieve it)
            r["content_vector"] = []
            chunks.append(_from_search_doc(r))
        return chunks
