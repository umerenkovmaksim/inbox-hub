from pathlib import Path

import httpx
import respx

from university_pz_parser.settings import SdoSettings
from university_pz_parser.sources.sdo.client import SdoHttp

FX = Path(__file__).parents[2] / "fixtures" / "sdo"
BASE = "https://sdo.test"


@respx.mock
async def test_login_and_relogin_and_head():
    login_html = (FX / "login.html").read_text()
    course_html = (FX / "course_3058.html").read_text()
    respx.get(f"{BASE}/login/index.php").respond(text=login_html)
    post = respx.post(f"{BASE}/login/index.php").respond(
        status_code=303, headers={"Location": f"{BASE}/my/"}
    )
    respx.get(f"{BASE}/my/").respond(text='<script>M.cfg = {"sesskey":"SK"}</script>')
    course = respx.get(f"{BASE}/course/view.php", params={"id": "1"})
    course.side_effect = [
        httpx.Response(200, text=login_html),
        httpx.Response(200, text=course_html),
    ]
    respx.head(f"{BASE}/pluginfile.php/1/a.pdf").respond(headers={"ETag": '"abc"'})
    async with httpx.AsyncClient(follow_redirects=True) as http:
        sdo = SdoHttp(http, SdoSettings(url=BASE, username="u", password="p"))
        await sdo.login()
        assert sdo.sesskey == "SK"
        assert b"username=u" in post.calls[0].request.content
        assert "data-number" in await sdo.page("/course/view.php?id=1")
        assert post.call_count == 2
        assert await sdo.head_remote_id(f"{BASE}/pluginfile.php/1/a.pdf") == "etag:abc"
