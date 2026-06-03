import io
from app.connectors.extract import extract_text, is_supported

def test_plain_text():
    assert extract_text(b"hello world", "text/plain", "a.txt") == "hello world"

def test_markdown():
    assert "# Title" in extract_text(b"# Title\n\nbody", "text/markdown", "a.md")

def test_html_strips_tags():
    out = extract_text(b"<html><body><h1>Hi</h1><p>there</p></body></html>", "text/html", "a.html")
    assert "Hi" in out and "there" in out and "<" not in out

def test_csv_passthrough():
    assert "a,b" in extract_text(b"a,b\n1,2", "text/csv", "a.csv")

def test_docx_roundtrip():
    import docx
    d = docx.Document()
    d.add_paragraph("Quarterly plan")
    buf = io.BytesIO(); d.save(buf)
    out = extract_text(buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx")
    assert "Quarterly plan" in out

def test_unsupported_returns_none():
    assert extract_text(b"\x00\x01", "image/png", "a.png") is None
    assert is_supported("a.png", "image/png") is False
    assert is_supported("a.docx", None) is True

def test_corrupt_supported_file_returns_none():
    assert extract_text(b"not a real docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx") is None
