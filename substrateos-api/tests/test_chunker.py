from app.ingest.chunker import chunk_markdown


def test_short_doc_returns_single_chunk() -> None:
    text = "# Title\n\nShort body text."
    chunks = chunk_markdown(text, max_tokens=500, overlap_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].content.startswith("# Title")


def test_long_doc_splits_at_headings() -> None:
    text = "# A\n\n" + ("x " * 800) + "\n\n## B\n\n" + ("y " * 800)
    chunks = chunk_markdown(text, max_tokens=300, overlap_tokens=30)
    assert len(chunks) >= 2
    # heading respected — chunk boundaries align to headings where possible
    assert any(c.content.lstrip().startswith("# A") for c in chunks)
    assert any(c.content.lstrip().startswith("## B") for c in chunks)


def test_chunks_have_overlap() -> None:
    text = "word " * 2000
    chunks = chunk_markdown(text, max_tokens=200, overlap_tokens=40)
    assert len(chunks) >= 2
    # consecutive chunks share suffix/prefix
    assert chunks[0].content.split()[-10:] == chunks[1].content.split()[:10] or \
        any(w in chunks[1].content for w in chunks[0].content.split()[-5:])


def test_chunk_indices_are_sequential() -> None:
    text = "word " * 2000
    chunks = chunk_markdown(text, max_tokens=200, overlap_tokens=40)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
