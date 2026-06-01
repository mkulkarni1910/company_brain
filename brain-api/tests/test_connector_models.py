from app.connectors.models import Connection, SyncJob, RemoteFile, ActivityEntry

def test_connection_defaults():
    c = Connection(connection_id="c1", tenant_id="t", type="sharepoint",
                   site_id="s1", name="Sales", web_url="https://x")
    assert c.status == "pending" and c.item_count == 0 and c.error is None

def test_syncjob_progress_roundtrip():
    j = SyncJob(job_id="j1", tenant_id="t", connection_id="c1")
    j.total = 5; j.processed = 2; j.skipped = 1
    assert SyncJob.model_validate_json(j.model_dump_json()).processed == 2

def test_remote_file():
    f = RemoteFile(drive_id="d", item_id="i", name="a.docx", mime=None,
                   web_url="https://x", size=10)
    assert f.name == "a.docx"
