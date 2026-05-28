from fastapi import APIRouter, Depends

from app.deps import get_ingest_pipeline
from app.domain.chunk import SourceDoc
from app.ingest.pipeline import IngestPipeline, IngestResult

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/ingest", response_model=None)
async def ingest(
    doc: SourceDoc,
    pipeline: IngestPipeline = Depends(get_ingest_pipeline),
) -> dict[str, int | str]:
    result: IngestResult = await pipeline.process(doc)
    return {"doc_id": result.doc_id, "chunks_indexed": result.chunks_indexed}
