import httpx
import respx

from university_pz_parser.settings import SdoSettings
from university_pz_parser.sources.sdo.rest import SdoRest

BASE = "https://sdo.test"


@respx.mock
async def test_contents_to_pages():
    respx.post(f"{BASE}/login/token.php").respond(json={"token": "T"})

    def server(request):
        fn = dict(httpx.QueryParams(request.content.decode()))["wsfunction"]
        return httpx.Response(
            200,
            json={
                "core_course_get_contents": [
                    {
                        "section": 0,
                        "name": "Общее",
                        "summary": "<p>Про курс</p>",
                        "modules": [
                            {
                                "id": 90905,
                                "modname": "assign",
                                "name": "Проект",
                                "url": f"{BASE}/mod/assign/view.php?id=90905",
                                "instance": 7,
                                "description": "",
                                "dates": [
                                    {"label": "Срок сдачи:", "timestamp": 1789592400}
                                ],
                            },
                            {
                                "id": 92422,
                                "modname": "resource",
                                "name": "Лекция 1",
                                "url": "u",
                                "contents": [
                                    {
                                        "type": "file",
                                        "filename": "l1.pdf",
                                        "fileurl": f"{BASE}/webservice/pluginfile.php/1/l1.pdf?forcedownload=1",
                                        "timemodified": 1789000000,
                                        "filesize": 10,
                                        "mimetype": "application/pdf",
                                    }
                                ],
                            },
                        ],
                    },
                ],
                "mod_assign_get_assignments": {
                    "courses": [
                        {
                            "id": 3058,
                            "assignments": [
                                {
                                    "id": 7,
                                    "cmid": 90905,
                                    "intro": "<p>Сделать проект</p>",
                                    "duedate": 1789592400,
                                    "allowsubmissionsfromdate": 1789506000,
                                    "introattachments": [
                                        {
                                            "filename": "p.docx",
                                            "fileurl": f"{BASE}/webservice/pluginfile.php/2/p.docx",
                                            "timemodified": 1789000001,
                                            "filesize": 5,
                                            "mimetype": "x",
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
                "mod_assign_get_submission_status": {
                    "lastattempt": {"submission": {"status": "submitted"}},
                    "feedback": {"gradefordisplay": "5", "plugins": []},
                },
            }[fn],
        )

    respx.post(f"{BASE}/webservice/rest/server.php").mock(side_effect=server)
    async with httpx.AsyncClient() as http:
        rest = SdoRest(http, SdoSettings(url=BASE, username="u", password="p"))
        assert await rest.token() == "T"
        page, details = await rest.course_pages(3058)
    assert page.sections[0].summary == "Про курс"
    assert [m.cmid for m in page.modules] == [90905, 92422]
    d = details[90905]
    assert d.text == "Сделать проект"
    assert (
        d.files[0].remote_id.startswith("rest:")
        and "token=T" not in d.files[0].remote_id
    )
    assert d.status["Состояние ответа на задание"] == "submitted"
    assert d.grade.value == 5.0
    assert details[92422].files[0].name == "l1.pdf"
