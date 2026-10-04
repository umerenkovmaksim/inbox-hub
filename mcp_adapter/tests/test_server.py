import respx
from study_inbox_mcp import server


@respx.mock
def test_tools_call_api(tmp_path, monkeypatch):
    monkeypatch.setenv("INBOX_DOWNLOAD_DIR", str(tmp_path))
    server.configure("https://inbox.test", "tok")
    items = respx.get("https://inbox.test/items").respond(
        json={"items": [], "count": 0}
    )
    respx.post("https://inbox.test/items/receive").respond(json={"updated": 2})
    respx.get("https://inbox.test/files/abc").respond(
        content=b"pdf", headers={"content-disposition": 'attachment; filename="l1.pdf"'}
    )
    assert (
        server.get_items("2026-10-01T00:00:00+03:00", "2026-10-02T00:00:00+03:00")[
            "count"
        ]
        == 0
    )
    assert items.calls[0].request.headers["Authorization"] == "Bearer tok"
    assert items.calls[0].request.url.params["from"] == "2026-10-01T00:00:00+03:00"
    assert server.mark_received([1, 2]) == {"updated": 2}
    path = server.get_file("abc")
    assert path.endswith("abc_l1.pdf") and open(path, "rb").read() == b"pdf"
