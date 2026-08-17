from __future__ import annotations

import asyncio
import json

from parentsquare_mcp.models import ClassStaff
from parentsquare_mcp.parsers import classes


# --- sections_mini -----------------------------------------------------------

SECTIONS_MINI = [
    {
        "id": 5239712, "external_id": "Room 5", "grade_names": "Kindergarten",
        "grade_ids": "100870", "name": "Ms. Park's Class", "display_name": None,
        "sis_name": "Ms. Park's Class", "student_count": 21, "posts_count": 3,
        "teachers": "Jiyoung Park, Lainey Archer", "assistants": 0, "room_parents": 4,
        "section_class_status": "visible_to_all",
    },
    {"id": None, "name": "skipped"},
]


def test_parse_sections_mini_maps_rows_and_skips_idless():
    result = classes.parse_sections_mini(SECTIONS_MINI)
    assert len(result) == 1
    c = result[0]
    assert c.id == 5239712
    assert c.name == "Ms. Park's Class"
    assert c.grade_names == "Kindergarten"
    assert c.grade_ids == "100870"
    assert c.external_id == "Room 5"
    assert c.teachers == "Jiyoung Park, Lainey Archer"
    assert c.room_parents == 4
    assert c.student_count == 21
    assert c.visibility_status == "visible_to_all"


# --- section detail (JSON:API included join) ---------------------------------

SECTION_DETAIL = {
    "data": {
        "id": "50234079", "type": "section",
        "attributes": {"id": 50234079, "external_id": "Room 5", "name": "Test Class",
                       "sis_name": "Test Class", "display_name": None, "active": True},
        "relationships": {"grades": {"data": [{"id": "545988", "type": "grade"}]}},
    },
    "included": [
        {"id": "15498439", "type": "user",
         "attributes": {"id": 15498439, "first_name": "Rhonda", "last_name": "Parent"}},
        {"id": "77027009", "type": "user",
         "attributes": {"id": 77027009, "first_name": "Tim", "last_name": "Teacher"}},
        {"id": "43431950", "type": "section_staff_association",
         "attributes": {"id": 43431950, "role": "ROOM_PARENT", "class_title": "Room Parent"},
         "relationships": {"user": {"data": {"id": "15498439", "type": "user"}}}},
        {"id": "59265874", "type": "section_staff_association",
         "attributes": {"id": 59265874, "role": "TEACHER", "class_title": "Teacher"},
         "relationships": {"user": {"data": {"id": "77027009", "type": "user"}}}},
    ],
}


def test_parse_section_detail_joins_included_users():
    d = classes.parse_section_detail(SECTION_DETAIL)
    assert d.id == 50234079
    assert d.name == "Test Class"
    assert d.external_id == "Room 5"
    assert d.grade_ids == ["545988"]
    # teachers sort before room parents
    assert [s.role for s in d.staff] == ["TEACHER", "ROOM_PARENT"]
    teacher = d.staff[0]
    assert teacher.assoc_id == 59265874
    assert teacher.user_id == 77027009
    assert teacher.name == "Tim Teacher"
    assert d.staff[1].class_title == "Room Parent"


def test_parse_section_detail_none_when_empty():
    assert classes.parse_section_detail({}) is None


# --- staff_data --------------------------------------------------------------

def test_parse_roster_staff_maps_positional_columns():
    rows = [
        [None, 14770659, "Brady, Julie", [], "julie@example.com", "415-555-0100", "",
         'Admin <span class="ps-note-light">|</span> PS Admin', "Mar 24, 2022", "Yes", "No",
         32284546, None],
        [None, None, "skipped", [], "", "", "", "", "", "No", "No", None, None],
    ]
    staff = classes.parse_roster_staff(rows)
    assert len(staff) == 1
    s = staff[0]
    assert s.user_id == 14770659
    assert s.name == "Brady, Julie"
    assert s.email == "julie@example.com"
    assert s.role_title == "Admin | PS Admin"  # HTML stripped
    assert s.registered is True
    assert s.sua_id == 32284546


# --- role handling -----------------------------------------------------------

def test_normalize_role_accepts_friendly_spellings():
    assert classes.normalize_role("room parent") == "ROOM_PARENT"
    assert classes.normalize_role("Room-Parent") == "ROOM_PARENT"
    assert classes.normalize_role("teacher") == "TEACHER"


def test_normalize_role_rejects_unknown():
    try:
        classes.normalize_role("principal")
    except ValueError as exc:
        assert "ROOM_PARENT" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


# --- body builders -----------------------------------------------------------

def test_build_add_class_body_matches_captured_shape():
    b = classes.build_add_class_body(13749, "test class", ["545988"])
    assert b == {"school_id": 13749, "data": {"attributes": {"name": "test class"},
                                              "relationships": {"grades": [{"id": "545988"}]}}}


def test_build_add_class_body_omits_relationships_without_grades():
    assert "relationships" not in classes.build_add_class_body(13749, "x", [])["data"]


def test_build_edit_class_body_matches_captured_shape():
    b = classes.build_edit_class_body(13749, 50234079, "test class 2", "", ["545988"])
    assert b["id"] == "50234079"
    assert b["school_id"] == 13749
    assert b["data"]["attributes"] == {"external_id": "", "name": "test class 2"}
    assert b["data"]["relationships"]["grades"] == [{"id": "545988"}]


def test_build_staff_replace_body_matches_captured_shape():
    staff = [
        ClassStaff(assoc_id=59265874, user_id=77027009, role="TEACHER",
                   class_title="teacher title", first_name="Tim", last_name="Teacher"),
        ClassStaff(assoc_id=None, user_id=71853816, role="ROOM_PARENT",
                   class_title="", first_name="Test", last_name="Parent4"),
    ]
    b = classes.build_staff_replace_body(staff)
    assert b["data"][0] == {
        "id": "59265874", "type": "section_staff_association",
        "attributes": {"role": "TEACHER", "class_title": "teacher title",
                       "user": {"id": "77027009", "first_name": "Tim", "last_name": "Teacher"}},
    }
    new = b["data"][1]
    assert new["id"] is None                       # new links send a null id
    assert new["attributes"]["class_title"] == "Room Parent"  # default title per role


def test_build_staff_replace_body_empty_uses_clear_sentinel():
    # ParentSquare's clear-all payload is a single empty-user object, not []
    assert classes.build_staff_replace_body([]) == {"data": [{"attributes": {"user": {}}}]}


def test_build_add_staff_body_matches_captured_form():
    b = classes.build_add_staff_body(
        13749, "Tim", "Teacher", email="t@example.com", phone="650-555-1234",
        staff_id="234", title="Teacher", role="STAFF", section_ids=[50234079],
    )
    assert b["user[first_name]"] == "Tim"
    assert b["user[contacts_attributes][0][external_id]"] == "234"
    assert b["user[school_user_associations_attributes][0][school_id]"] == "13749"
    assert b["user[school_user_associations_attributes][0][role]"] == "STAFF"
    assert b["user[school_user_associations_attributes][0][school_title]"] == "Teacher"
    assert b["invited_school_section_ids[]"] == ["50234079"]
    assert b["no_flash"] == "true"


def test_build_add_staff_body_admin_role_and_no_sections():
    b = classes.build_add_staff_body(13749, "A", "B", role="ADMIN")
    assert b["user[school_user_associations_attributes][0][role]"] == "ADMIN"
    assert "invited_school_section_ids[]" not in b


def test_build_visibility_body_defaults_to_a_dated_change():
    b = classes.build_visibility_body(13749, [1, 2], visible=True, date="2026-08-17")
    assert b == {"ids": "1,2", "school_id": 13749, "visibility_start_date": "2026-08-17"}


def test_build_visibility_body_hide_uses_end_date():
    b = classes.build_visibility_body(13749, [1], visible=False, date="2026-06-30")
    assert b == {"ids": "1", "school_id": 13749, "visibility_end_date": "2026-06-30"}


# --- JSON:API response interpretation ----------------------------------------

def test_json_write_ok():
    assert classes.json_write_ok(200) and classes.json_write_ok(204)
    assert not classes.json_write_ok(404) and not classes.json_write_ok(500)


def test_json_write_error_prefers_api_message():
    text = json.dumps({"errors": [{"detail": "Name has already been taken"}]})
    assert "Name has already been taken" in classes.json_write_error(422, text)
    assert "Something broke" in classes.json_write_error(500, json.dumps({"error": "Something broke"}))


def test_json_write_error_falls_back_to_snippet():
    assert "404" in classes.json_write_error(404, "<html>Not Found</html>")


# --- tool behaviour: read-modify-write ---------------------------------------

class _FakeResp:
    def __init__(self, status_code=200, text="{}"):
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.text = text

    def json(self):
        return json.loads(self.text)


def _patch_class_tools(monkeypatch, tmp_path, detail_payload=SECTION_DETAIL):
    """Point the class tools at a fake client and capture the PUT body."""
    from parentsquare_mcp import server

    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    sent: dict = {}

    class _FakeClient:
        def get_json(self, path, params=None):
            return detail_payload

        def send_json(self, method, path, payload):
            sent["method"], sent["path"], sent["payload"] = method, path, payload
            return _FakeResp()

    monkeypatch.setattr(server, "_app", lambda ctx: type("A", (), {"client": _FakeClient()})())
    return sent


def test_remove_class_staff_by_role_preserves_other_staff(monkeypatch, tmp_path):
    from parentsquare_mcp.server import remove_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(remove_class_staff(50234079, role="ROOM_PARENT", context="CTX"))
    assert msg.startswith("✅") and "Rhonda Parent" in msg
    assert sent["method"] == "PUT"
    assert sent["path"] == "/api/v2/sections/50234079/staff"
    # the teacher survives, the room parent is dropped
    assert [d["attributes"]["role"] for d in sent["payload"]["data"]] == ["TEACHER"]
    assert sent["payload"]["data"][0]["id"] == "59265874"


def test_remove_class_staff_requires_a_target(monkeypatch, tmp_path):
    from parentsquare_mcp.server import remove_class_staff

    _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(remove_class_staff(50234079, context="CTX"))
    assert msg.startswith("❌") and "refusing" in msg


def test_remove_class_staff_no_match_makes_no_request(monkeypatch, tmp_path):
    from parentsquare_mcp.server import remove_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(remove_class_staff(50234079, user_ids=[999], context="CTX"))
    assert msg.startswith("ℹ️")
    assert sent == {}


def test_add_class_staff_appends_without_dropping_existing(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(add_class_staff(50234079, [71853816], "room parent", context="CTX"))
    assert msg.startswith("✅")
    roles = [(d["attributes"]["role"], d["attributes"]["user"]["id"]) for d in sent["payload"]["data"]]
    assert ("TEACHER", "77027009") in roles
    assert ("ROOM_PARENT", "15498439") in roles      # pre-existing room parent kept
    assert ("ROOM_PARENT", "71853816") in roles      # newly added


def test_add_class_staff_role_change_reuses_assoc_id(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(add_class_staff(50234079, [15498439], "TEACHER", context="CTX"))
    assert msg.startswith("✅")
    changed = [d for d in sent["payload"]["data"] if d["attributes"]["user"]["id"] == "15498439"]
    assert len(changed) == 1
    assert changed[0]["id"] == "43431950"           # updates in place, no duplicate row
    assert changed[0]["attributes"]["role"] == "TEACHER"


def test_add_class_staff_noop_when_already_assigned(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(add_class_staff(50234079, [77027009], "TEACHER", context="CTX"))
    assert msg.startswith("ℹ️")
    assert sent == {}


def test_add_class_staff_rejects_bad_role(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(add_class_staff(50234079, [1], "principal", context="CTX"))
    assert msg.startswith("❌")
    assert sent == {}


def test_edit_class_preserves_unchanged_fields(monkeypatch, tmp_path):
    from parentsquare_mcp.server import edit_class

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(edit_class(50234079, 13749, name="Renamed", context="CTX"))
    assert msg.startswith("✅")
    assert sent["method"] == "PATCH"
    attrs = sent["payload"]["data"]["attributes"]
    assert attrs["name"] == "Renamed"
    assert attrs["external_id"] == "Room 5"                          # preserved
    assert sent["payload"]["data"]["relationships"]["grades"] == [{"id": "545988"}]  # preserved


def test_set_class_visibility_rejects_bad_date(monkeypatch, tmp_path):
    from parentsquare_mcp.server import set_class_visibility

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(set_class_visibility(13749, [1], True, date="Aug 20", context="CTX"))
    assert msg.startswith("❌")
    assert sent == {}


def test_set_class_visibility_defaults_to_today(monkeypatch, tmp_path):
    from datetime import date

    from parentsquare_mcp.server import set_class_visibility

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(set_class_visibility(13749, [50234079], True, context="CTX"))
    assert msg.startswith("✅")
    assert sent["path"].endswith("/sections/bulk_update_class_visibility")
    assert sent["payload"]["visibility_start_date"] == date.today().isoformat()


def test_add_class_returns_new_section_id(monkeypatch, tmp_path):
    from parentsquare_mcp import server

    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))

    class _FakeClient:
        def send_json(self, method, path, payload):
            return _FakeResp(text=json.dumps({"data": {"attributes": {"id": 50234079}}}))

    monkeypatch.setattr(server, "_app", lambda ctx: type("A", (), {"client": _FakeClient()})())
    msg = asyncio.run(server.add_class(13749, "New Class", [545988], context="CTX"))
    assert "50234079" in msg and "hidden" in msg


def test_add_class_requires_a_grade(monkeypatch, tmp_path):
    from parentsquare_mcp import server

    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    monkeypatch.setattr(server, "_app", lambda ctx: type("A", (), {"client": None})())
    assert asyncio.run(server.add_class(13749, "x", [], context="CTX")).startswith("❌")


def test_add_class_staff_adds_several_people_in_one_put(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(
        add_class_staff(50234079, [71853816, 71853817], "ROOM_PARENT", context="CTX")
    )
    assert msg.startswith("✅")
    rows = [(d["attributes"]["role"], d["attributes"]["user"]["id"]) for d in sent["payload"]["data"]]
    assert ("TEACHER", "77027009") in rows           # untouched
    assert ("ROOM_PARENT", "15498439") in rows       # untouched
    assert ("ROOM_PARENT", "71853816") in rows
    assert ("ROOM_PARENT", "71853817") in rows
    assert len(rows) == 4


def test_add_class_staff_skips_people_who_already_hold_the_role(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(
        add_class_staff(50234079, [15498439, 71853816], "ROOM_PARENT", context="CTX")
    )
    assert msg.startswith("✅") and "already had the role" in msg
    ids = [d["attributes"]["user"]["id"] for d in sent["payload"]["data"]]
    assert ids.count("15498439") == 1                # not duplicated
    assert "71853816" in ids


def test_add_class_staff_dedupes_and_noops_when_all_present(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(
        add_class_staff(50234079, [15498439, 15498439], "ROOM_PARENT", context="CTX")
    )
    assert msg.startswith("ℹ️")
    assert sent == {}


def test_add_class_staff_rejects_empty_user_ids(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(add_class_staff(50234079, [], "ROOM_PARENT", context="CTX"))
    assert msg.startswith("❌")
    assert sent == {}


def test_remove_class_staff_removes_several_people_in_one_put(monkeypatch, tmp_path):
    from parentsquare_mcp.server import remove_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(
        remove_class_staff(50234079, user_ids=[77027009, 15498439], context="CTX")
    )
    assert msg.startswith("✅") and "Removed 2" in msg
    assert sent["payload"]["data"] == [{"attributes": {"user": {}}}]   # clear-all sentinel


def test_remove_class_staff_user_ids_and_role_intersect(monkeypatch, tmp_path):
    from parentsquare_mcp.server import remove_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(
        remove_class_staff(50234079, user_ids=[77027009, 15498439], role="ROOM_PARENT", context="CTX")
    )
    assert msg.startswith("✅") and "Removed 1" in msg
    # the teacher was in user_ids but isn't a ROOM_PARENT, so they stay
    assert [d["attributes"]["role"] for d in sent["payload"]["data"]] == ["TEACHER"]


def test_remove_class_staff_rejects_empty_user_ids(monkeypatch, tmp_path):
    from parentsquare_mcp.server import remove_class_staff

    sent = _patch_class_tools(monkeypatch, tmp_path)
    msg = asyncio.run(remove_class_staff(50234079, user_ids=[], context="CTX"))
    assert msg.startswith("❌") and "refusing" in msg
    assert sent == {}


# --- add_staff: class assignment follow-up ------------------------------------

STAFF_ROSTER = {"data": [
    [None, 77027760, "Teacher, Apitest", ["APITEST1"], "new@example.com", "", "",
     'Staff <span class="ps-note-light">|</span> API Test Teacher', "Aug 17, 2026", "No", "No",
     32284999, None],
]}


class _FakeUjsResp:
    """A Rails UJS success response, as post_form returns."""
    status_code = 200
    headers = {"content-type": "text/javascript; charset=utf-8"}
    text = "window.location.reload();"


def _patch_add_staff(monkeypatch, tmp_path, roster=STAFF_ROSTER):
    from parentsquare_mcp import server

    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    calls: dict = {"puts": []}

    class _FakeClient:
        def post_form(self, path, data):
            calls["form_path"], calls["form"] = path, data
            return _FakeUjsResp()

        def get_json(self, path, params=None):
            return roster if "staff_data" in path else SECTION_DETAIL

        def send_json(self, method, path, payload):
            calls["puts"].append((path, payload))
            return _FakeResp()

    monkeypatch.setattr(server, "_app", lambda ctx: type("A", (), {"client": _FakeClient()})())
    return calls


def test_add_staff_assigns_the_new_teacher_to_the_given_classes(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_staff

    calls = _patch_add_staff(monkeypatch, tmp_path)
    msg = asyncio.run(add_staff(13749, "Apitest", "Teacher", email="new@example.com",
                                section_ids=[50234079], context="CTX"))
    assert msg.startswith("✅") and "user_id 77027760" in msg
    assert "Assigned as TEACHER to class(es) 50234079" in msg
    path, payload = calls["puts"][0]
    assert path == "/api/v2/sections/50234079/staff"
    rows = [(d["attributes"]["role"], d["attributes"]["user"]["id"]) for d in payload["data"]]
    assert ("TEACHER", "77027760") in rows
    assert ("TEACHER", "77027009") in rows        # the existing teacher is preserved


def test_add_staff_without_sections_makes_no_class_call(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_staff

    calls = _patch_add_staff(monkeypatch, tmp_path)
    msg = asyncio.run(add_staff(13749, "Apitest", "Teacher", context="CTX"))
    assert msg.startswith("✅") and "list_staff" in msg
    assert calls["puts"] == []


def test_add_staff_warns_when_the_new_user_cannot_be_found(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_staff

    calls = _patch_add_staff(monkeypatch, tmp_path, roster={"data": []})
    msg = asyncio.run(add_staff(13749, "Ghost", "Person", email="ghost@example.com",
                                section_ids=[50234079], context="CTX"))
    assert "⚠️" in msg and "could not be found" in msg
    assert calls["puts"] == []                    # created, but no class touched


def test_add_staff_rejects_a_bad_class_role(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_staff

    calls = _patch_add_staff(monkeypatch, tmp_path)
    msg = asyncio.run(add_staff(13749, "Apitest", "Teacher", section_ids=[50234079],
                                class_role="principal", context="CTX"))
    assert msg.startswith("❌")
    assert "form" not in calls                    # nothing was created
