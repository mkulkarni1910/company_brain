from __future__ import annotations

from dataclasses import dataclass

from app.acl.store import ACLStore
from app.domain.chunk import Chunk, SourceDoc
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.chunker import chunk_markdown
from app.retrieval.ai_search_client import AISearchClient


@dataclass
class IngestResult:
    doc_id: str
    chunks_indexed: int


class IngestPipeline:
    def __init__(
        self,
        *,
        embedder: AzureOpenAIClient,
        search: AISearchClient,
        acl_store: ACLStore | None = None,
    ) -> None:
        self._embedder = embedder
        self._search = search
        self._acl_store = acl_store

    async def process(self, doc: SourceDoc) -> IngestResult:
        pieces = chunk_markdown(doc.body, max_tokens=600, overlap_tokens=75)
        if not pieces:
            return IngestResult(doc_id=doc.doc_id, chunks_indexed=0)
        vectors = await self._embedder.embed_batch([p.content for p in pieces])
        chunks = [
            Chunk(
                chunk_id=f"{doc.doc_id}#chunk-{p.chunk_index}",
                doc_id=doc.doc_id,
                tenant_id=doc.tenant_id,
                source=doc.source,
                source_url=doc.source_url,
                title=doc.title,
                content=p.content,
                content_vector=v,
                acl_principals=doc.acl_principals,
                author_id=doc.author_id,
                entities=[],
                created_at=doc.created_at,
                modified_at=doc.modified_at,
                chunk_index=p.chunk_index,
            )
            for p, v in zip(pieces, vectors, strict=True)
        ]
        await self._search.upsert_chunks(chunks)
        if self._acl_store is not None:
            from app.config import get_settings
            await self._acl_store.set_doc_principals(
                tenant_id=doc.tenant_id,
                doc_id=doc.doc_id,
                principals=doc.acl_principals,
                ttl_seconds=get_settings().acl_doc_ttl_seconds,
            )
        return IngestResult(doc_id=doc.doc_id, chunks_indexed=len(chunks))
