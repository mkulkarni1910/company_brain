"""One-shot: create or update the substrateos-content-{tenant} index in AI Search."""

from __future__ import annotations

from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

from app.config import get_settings


def build_index(name: str) -> SearchIndex:
    fields = [
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="tenant_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="source_url", type=SearchFieldDataType.String, retrievable=True),
        SearchField(name="title", type=SearchFieldDataType.String, searchable=True, retrievable=True),
        SearchField(name="content", type=SearchFieldDataType.String, searchable=True, retrievable=True),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=3072,
            vector_search_profile_name="default-hnsw",
            retrievable=False,
            searchable=True,
        ),
        SimpleField(
            name="acl_principals",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        # facetable: search_page requests an author_id facet ("Who from") — a facet
        # on a non-facetable field 400s the whole query.
        SimpleField(name="author_id", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(
            name="entities",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
            facetable=True,
        ),
        SimpleField(
            name="created_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True
        ),
        SimpleField(
            name="modified_at",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, retrievable=True),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
        profiles=[VectorSearchProfile(name="default-hnsw", algorithm_configuration_name="default-hnsw")],
    )

    semantic = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="substrateos-semantic",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                    keywords_fields=[SemanticField(field_name="entities")],
                ),
            )
        ]
    )

    return SearchIndex(name=name, fields=fields, vector_search=vector_search, semantic_search=semantic)


def main() -> None:
    s = get_settings()
    if s.azure_ai_search_key:
        from azure.core.credentials import AzureKeyCredential

        cred = AzureKeyCredential(s.azure_ai_search_key)
    else:
        cred = DefaultAzureCredential()
    client = SearchIndexClient(endpoint=s.azure_ai_search_endpoint, credential=cred)
    idx = build_index(s.azure_ai_search_index)
    client.create_or_update_index(idx)
    print(f"OK: {idx.name} on {s.azure_ai_search_endpoint}")


if __name__ == "__main__":
    main()
