"""One-shot: DELETE and recreate the search index (destroys indexed data — re-ingest after).

Needed when an existing field's attributes change (e.g. facetable), which
create_or_update_index cannot apply in place.
"""

from __future__ import annotations

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient

from app.config import get_settings
from scripts.create_search_index import build_index


def main() -> None:
    s = get_settings()
    if s.azure_ai_search_key:
        from azure.core.credentials import AzureKeyCredential

        cred = AzureKeyCredential(s.azure_ai_search_key)
    else:
        cred = DefaultAzureCredential()
    client = SearchIndexClient(endpoint=s.azure_ai_search_endpoint, credential=cred)
    try:
        client.delete_index(s.azure_ai_search_index)
        print(f"deleted: {s.azure_ai_search_index}")
    except ResourceNotFoundError:
        print(f"not found (creating fresh): {s.azure_ai_search_index}")
    idx = build_index(s.azure_ai_search_index)
    client.create_index(idx)
    print(f"created: {idx.name} on {s.azure_ai_search_endpoint}")


if __name__ == "__main__":
    main()
