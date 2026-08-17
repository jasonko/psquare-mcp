"""Tests for student class enrollment parsing, body building, and tools."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from parentsquare_mcp.parsers import enrollment
from parentsquare_mcp import server


# --- parsing -----------------------------------------------------------------

SECTION_STUDENTS = {
    "data": [
        {
            "id": "56341978",
            "type": "student",
            "attributes": {
                "id": 56341978, "first_name": "Test", "last_name": "Student4",
                "full_name": "Test Student4", "external_id": None,
            },
        },
        {
            "id": "56332978",
            "type": "student",
            "attributes": {
                "id": 56332978, "first_name": "Test", "last_name": "Student3",
                "full_name": "Test Student3", "external_id": "321",
            },
        },
    ],
    "included": [],
}


def test_parse_class_students_sorted_by_name():
    students = enrollment.parse_class_students(SECTION_STUDENTS)
    assert [s.student_id for s in students] == [56332978, 56341978]
    assert students[0].name == "Test Student3"
    assert students[0].student_sis_id == "321"
    assert students[1].student_sis_id is None


def test_parse_class_students_empty():
    assert enrollment.parse_class_students({"data": [], "included": []}) == []
    assert enrollment.parse_class_students({}) == []


ASSIGN_CLASSES_HTML = """
<tr id="student_56332978" data-id="56332978" data-grade-id="545988">
  <li id="student-sections-data-56332978">
    <span class="student-sections-56332978" style="display:none"
          data-section-id="50234079" data-section-name="test class 2"> </span>
    <span class="student-sections-56332978" style="display:none"
          data-section-id="50241987" data-section-name="Music"> </span>
  </li>
</tr>
<tr id="student_56341978" data-id="56341978">
  <li id="student-sections-data-56341978">
    <span class="student-sections-56341978" style="display:none"
          data-section-id="50234079" data-section-name="test class 2"> </span>
  </li>
</tr>
<tr id="student_56041346"><li id="student-sections-data-56041346"></li></tr>
"""


def test_parse_student_sections_map():
    m = enrollment.parse_student_sections_map(ASSIGN_CLASSES_HTML)
    assert sorted(m) == [56332978, 56341978]
    assert [c["section_id"] for c in m[56332978]] == [50234079, 50241987]
    assert m[56332978][1]["name"] == "Music"
    # a student with no classes has no spans and is simply absent
    assert 56041346 not in m


def test_parse_student_sections_map_tolerates_attribute_order():
    html = (
        '<span data-section-name="Art" style="display:none" '
        'data-section-id="99" class="student-sections-7"></span>'
    )
    assert enrollment.parse_student_sections_map(html) == {
        7: [{"section_id": 99, "name": "Art"}]
    }


def test_parse_student_sections_map_deduplicates():
    html = (
        '<span class="student-sections-7" data-section-id="99" data-section-name="Art"></span>'
        '<span class="student-sections-7" data-section-id="99" data-section-name="Art"></span>'
    )
    assert enrollment.parse_student_sections_map(html) == {
        7: [{"section_id": 99, "name": "Art"}]
    }


def test_parse_student_sections_map_empty():
    assert enrollment.parse_student_sections_map("") == {}
    assert enrollment.parse_student_sections_map("<div>nothing</div>") == {}


# --- body builders -----------------------------------------------------------

def test_build_add_students_body():
    b = enrollment.build_add_students_body(50234079, [56332978, 56341978])
    assert b == {
        "section_id": "50234079",
        "data": [
            {"type": "student", "id": "56332978"},
            {"type": "student", "id": "56341978"},
        ],
    }


def test_build_student_sections_body():
    b = enrollment.build_student_sections_body(56332978, [50234079, 50241987])
    assert b == {
        "student_id": "56332978",
        "data": [
            {"id": "50234079", "type": "section"},
            {"id": "50241987", "type": "section"},
        ],
    }


def test_build_student_sections_body_empty_clears():
    assert enrollment.build_student_sections_body(7, []) == {"student_id": "7", "data": []}


# --- tool behaviour ----------------------------------------------------------

class FakeClient:
    """Records writes and serves canned reads."""

    def __init__(self, roster=None, sections_html=""):
        self.roster = roster if roster is not None else SECTION_STUDENTS
        self.sections_html = sections_html
        self.writes = []
        self.status = 200

    def get_json(self, path, params=None):
        return self.roster

    def get_html(self, path, params=None):
        return self.sections_html

    def send_json(self, method, path, payload):
        self.writes.append((method, path, payload))
        return SimpleNamespace(status_code=self.status, text="{}")


def _ctx(client):
    app = SimpleNamespace(client=client, mfa_state=None)
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


@pytest.fixture(autouse=True)
def _enable_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))


def test_add_class_students_skips_already_enrolled():
    client = FakeClient()
    msg = asyncio.run(server.add_class_students(50234079, [56332978], context=_ctx(client)))
    assert "no change" in msg.lower()
    assert client.writes == []


def test_add_class_students_only_sends_new_ids():
    client = FakeClient()
    msg = asyncio.run(
        server.add_class_students(50234079, [56332978, 99999], context=_ctx(client))
    )
    assert msg.startswith("✅")
    method, path, payload = client.writes[0]
    assert method == "PUT"
    assert path == "/api/v2/sections/50234079/add_students"
    assert payload["data"] == [{"type": "student", "id": "99999"}]
    assert "already enrolled" in msg


def test_add_class_students_refuses_empty_list():
    client = FakeClient()
    msg = asyncio.run(server.add_class_students(50234079, [], context=_ctx(client)))
    assert msg.startswith("❌")
    assert client.writes == []


def test_remove_class_students_refuses_empty_list():
    """Guard: an empty list must never be read as 'remove everyone'."""
    client = FakeClient()
    msg = asyncio.run(
        server.remove_class_students(13749, 50234079, [], context=_ctx(client))
    )
    assert msg.startswith("❌")
    assert client.writes == []


def test_remove_class_students_preserves_other_classes():
    client = FakeClient(sections_html=ASSIGN_CLASSES_HTML)
    msg = asyncio.run(
        server.remove_class_students(13749, 50234079, [56332978], context=_ctx(client))
    )
    assert msg.startswith("✅")
    method, path, payload = client.writes[0]
    assert method == "PUT"
    assert path == "/api/v2/students/56332978/sections"
    # dropped 50234079, kept the student's other class
    assert payload["data"] == [{"id": "50241987", "type": "section"}]


def test_remove_class_students_ignores_students_not_in_class():
    client = FakeClient(sections_html=ASSIGN_CLASSES_HTML)
    msg = asyncio.run(
        server.remove_class_students(13749, 50234079, [12345], context=_ctx(client))
    )
    assert "no change" in msg.lower()
    assert client.writes == []


def test_remove_class_students_last_class_sends_empty_list():
    client = FakeClient(sections_html=ASSIGN_CLASSES_HTML)
    asyncio.run(
        server.remove_class_students(13749, 50234079, [56341978], context=_ctx(client))
    )
    _, path, payload = client.writes[0]
    assert path == "/api/v2/students/56341978/sections"
    assert payload["data"] == []


def test_move_student_to_class_swaps_one_and_keeps_the_rest():
    client = FakeClient(sections_html=ASSIGN_CLASSES_HTML)
    msg = asyncio.run(
        server.move_student_to_class(13749, 56332978, 777, from_section_id=50234079,
                                     context=_ctx(client))
    )
    assert msg.startswith("✅")
    _, path, payload = client.writes[0]
    assert path == "/api/v2/students/56332978/sections"
    ids = [d["id"] for d in payload["data"]]
    assert ids == ["50241987", "777"]  # Music kept, test class 2 swapped for 777


def test_move_student_to_class_without_from_adds_only():
    client = FakeClient(sections_html=ASSIGN_CLASSES_HTML)
    asyncio.run(server.move_student_to_class(13749, 56341978, 777, context=_ctx(client)))
    _, _, payload = client.writes[0]
    assert [d["id"] for d in payload["data"]] == ["50234079", "777"]


def test_move_student_to_class_rejects_wrong_source():
    client = FakeClient(sections_html=ASSIGN_CLASSES_HTML)
    msg = asyncio.run(
        server.move_student_to_class(13749, 56341978, 777, from_section_id=999,
                                     context=_ctx(client))
    )
    assert msg.startswith("❌")
    assert client.writes == []


def test_move_student_to_class_rejects_same_source_and_target():
    client = FakeClient(sections_html=ASSIGN_CLASSES_HTML)
    msg = asyncio.run(
        server.move_student_to_class(13749, 56332978, 50234079, from_section_id=50234079,
                                     context=_ctx(client))
    )
    assert msg.startswith("❌")
    assert client.writes == []


def test_writes_are_gated(monkeypatch):
    monkeypatch.setenv("PS_ENABLE_WRITES", "0")
    client = FakeClient()
    for coro in (
        server.add_class_students(50234079, [1], context=_ctx(client)),
        server.remove_class_students(13749, 50234079, [1], context=_ctx(client)),
        server.move_student_to_class(13749, 1, 2, context=_ctx(client)),
    ):
        assert asyncio.run(coro) == server.WRITES_DISABLED_MESSAGE
    assert client.writes == []


def test_writes_are_audited(monkeypatch, tmp_path):
    log = tmp_path / "audit.log"
    monkeypatch.setenv("PS_AUDIT_LOG", str(log))
    client = FakeClient()
    asyncio.run(server.add_class_students(50234079, [99999], context=_ctx(client)))
    entries = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert any(e["tool"] == "add_class_students" and e["ok"] for e in entries)


def test_failed_write_reports_error():
    client = FakeClient()
    client.status = 422
    msg = asyncio.run(server.add_class_students(50234079, [99999], context=_ctx(client)))
    assert msg.startswith("❌")
