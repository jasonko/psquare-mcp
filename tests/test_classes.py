from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

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


# --- edit_staff --------------------------------------------------------------

# Trimmed from a live GET /schools/13749/users/15498485/edit_institute_user?role=STAFF
# (a staff member with both an email and a phone, plus a staff ID field).
STAFF_EDIT_JS = r"""$('#edit-user-modal-container').html("<form action=\"/schools/13749/users/15498485/update_institute_user\">
<input type=\"hidden\" name=\"_method\" value=\"patch\" />
<input placeholder=\"First Name\" type=\"text\" value=\"Emily\" name=\"user[first_name]\" />
<input placeholder=\"Last Name\" type=\"text\" value=\"Day\" name=\"user[last_name]\" />
<input value=\"emidoan@gmail.com\" data-original=\"emidoan@gmail.com\" type=\"text\" name=\"user[contacts_attributes][0][email]\" />
<input type=\"hidden\" value=\"17935545\" name=\"user[contacts_attributes][0][id]\" />
<input disabled=\"disabled\" type=\"text\" name=\"user[contacts_attributes][1][email]\" />
<input value=\"415-734-6804\" type=\"text\" name=\"user[contacts_attributes][2][phone]\" />
<input type=\"hidden\" value=\"17935545\" name=\"user[contacts_attributes][2][id]\" />
<input type=\"hidden\" name=\"role\" value=\"STAFF\" />
<input value=\"Reading Specialist\" type=\"text\" name=\"user[school_user_associations_attributes][0][school_title]\" />
<input value=\"13749\" type=\"hidden\" name=\"user[school_user_associations_attributes][0][school_id]\" />
<input type=\"hidden\" value=\"34661746\" name=\"user[school_user_associations_attributes][0][id]\" />
<input type=\"text\" name=\"staff_contacts[manual][17935545][external_id]\" value=\"5007\" />
<input type=\"hidden\" name=\"staff_contacts[manual][17935545][id]\" value=\"17935545\" />
<select name=\"invited_school_section_ids[]\" multiple=\"multiple\"><option value=\"50234079\">Room 1<\/option><\/select>
<\/form>");"""


def test_extract_staff_edit_fields_reads_the_live_form():
    f = classes.extract_staff_edit_fields(STAFF_EDIT_JS)
    assert f == {
        "first_name": "Emily",
        "last_name": "Day",
        "contact_id": "17935545",
        "sua_id": "34661746",
        "school_id": "13749",
        "school_title": "Reading Specialist",
        "staff_contact_id": "17935545",
        "staff_external_id": "5007",
    }


def test_extract_staff_edit_fields_ignores_class_assignments():
    # The select lists every class but marks none selected, so echoing it back
    # would blank the staff member's assignments (same trap as student sections).
    assert "invited_school_section_ids" not in "".join(
        classes.extract_staff_edit_fields(STAFF_EDIT_JS)
    )


def test_build_edit_staff_body_preserves_title_and_school_association():
    b = classes.build_edit_staff_body(
        school_id=13749, first_name="Emily", last_name="Day", contact_id="17935545",
        sua_id="34661746", school_title="Reading Specialist", staff_contact_id="17935545",
    )
    assert b["_method"] == "patch"
    assert b["role"] == "STAFF"
    assert b["user[school_user_associations_attributes][0][id]"] == "34661746"
    assert b["user[school_user_associations_attributes][0][school_id]"] == "13749"
    assert b["user[school_user_associations_attributes][0][school_title]"] == "Reading Specialist"
    assert b["commit"] == "Save"


def test_build_edit_staff_body_omits_untouched_contact_and_class_fields():
    b = classes.build_edit_staff_body(
        school_id=13749, first_name="Emily", last_name="Day", contact_id="17935545",
        sua_id="34661746", school_title="Reading Specialist", staff_contact_id="17935545",
    )
    assert not [k for k in b if "contacts_attributes" in k]
    assert not [k for k in b if k.startswith("staff_contacts")]
    assert "invited_school_section_ids[]" not in b


def test_build_edit_staff_body_shares_one_contact_id_across_email_and_phone():
    b = classes.build_edit_staff_body(
        school_id=13749, first_name="Emily", last_name="Day", contact_id="17935545",
        sua_id="34661746", school_title="Admin", staff_contact_id="17935545",
        email="new@example.com", phone="415-555-0000",
    )
    assert b["user[contacts_attributes][0][email]"] == "new@example.com"
    assert b["user[contacts_attributes][0][id]"] == "17935545"
    assert b["user[contacts_attributes][2][phone]"] == "415-555-0000"
    assert b["user[contacts_attributes][2][id]"] == "17935545"


def test_build_edit_staff_body_can_clear_email_but_not_by_omission():
    b = classes.build_edit_staff_body(
        school_id=13749, first_name="E", last_name="D", contact_id="17935545",
        sua_id="34661746", school_title="Admin", email="",
    )
    assert b["user[contacts_attributes][0][email]"] == ""


def test_build_edit_staff_body_sets_staff_external_id():
    b = classes.build_edit_staff_body(
        school_id=13749, first_name="E", last_name="D", contact_id="17935545",
        sua_id="34661746", school_title="Admin", staff_contact_id="17935545",
        staff_external_id="5007",
    )
    assert b["staff_contacts[manual][17935545][external_id]"] == "5007"
    assert b["staff_contacts[manual][17935545][id]"] == "17935545"


def test_build_edit_staff_body_skips_staff_id_without_a_contact_record():
    b = classes.build_edit_staff_body(
        school_id=13749, first_name="E", last_name="D", contact_id="",
        sua_id="34661746", school_title="Admin", staff_external_id="5007",
    )
    assert not [k for k in b if k.startswith("staff_contacts")]


def test_build_edit_staff_body_carries_the_users_actual_role():
    # Sending a role that conflicts with the existing school_user_association is
    # rejected by Rails ("This would create conflicting roles").
    b = classes.build_edit_staff_body(
        school_id=13749, first_name="X", last_name="Admin", contact_id="",
        sua_id="4624", school_title="Admin", role="ADMIN",
    )
    assert b["role"] == "ADMIN"
    assert classes.build_edit_staff_body(
        school_id=13749, first_name="E", last_name="D", contact_id="", sua_id="1",
        school_title="",
    )["role"] == "STAFF"


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


def test_parallel_class_staff_writes_are_serialized(monkeypatch, tmp_path):
    """A stale full-replace must not resurrect another call's removal."""
    from parentsquare_mcp import server

    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    state = [
        ClassStaff(assoc_id=1, user_id=101, role="TEACHER", class_title="Teacher"),
        ClassStaff(assoc_id=2, user_id=102, role="TEACHER", class_title="Teacher"),
        ClassStaff(assoc_id=3, user_id=103, role="TEACHER", class_title="Teacher"),
    ]

    async def fake_load_class(app, context, section_id):
        snapshot = SimpleNamespace(name="Fake Class", staff=deepcopy(state))
        await asyncio.sleep(0)
        return snapshot, None

    async def fake_put_class_staff(app, context, tool, args, section_id, staff):
        await asyncio.sleep(0)
        state[:] = deepcopy(staff)
        return "✅ Success."

    monkeypatch.setattr(server, "_load_class", fake_load_class)
    monkeypatch.setattr(server, "_put_class_staff", fake_put_class_staff)

    async def run_parallel_removals():
        app = SimpleNamespace(client=object(), section_membership_write_lock=asyncio.Lock())
        monkeypatch.setattr(server, "_app", lambda ctx: app)
        return await asyncio.gather(
            server.remove_class_staff(9001, user_ids=[101], context="CTX"),
            server.remove_class_staff(9001, user_ids=[102], context="CTX"),
        )

    results = asyncio.run(run_parallel_removals())
    assert all(result.startswith("✅") for result in results)
    assert [staff.user_id for staff in state] == [103]


def test_class_staff_serialization_is_global_across_sections(monkeypatch, tmp_path):
    from parentsquare_mcp import server

    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    active_writes = 0
    max_active_writes = 0

    async def fake_load_class(app, context, section_id):
        nonlocal active_writes, max_active_writes
        active_writes += 1
        max_active_writes = max(max_active_writes, active_writes)
        await asyncio.sleep(0)
        user_id = 101 if section_id == 9001 else 102
        staff = [ClassStaff(assoc_id=user_id, user_id=user_id, role="TEACHER")]
        return SimpleNamespace(name=f"Fake Class {section_id}", staff=staff), None

    async def fake_put_class_staff(app, context, tool, args, section_id, staff):
        nonlocal active_writes
        await asyncio.sleep(0)
        active_writes -= 1
        return "✅ Success."

    monkeypatch.setattr(server, "_load_class", fake_load_class)
    monkeypatch.setattr(server, "_put_class_staff", fake_put_class_staff)

    async def run_parallel_removals():
        app = SimpleNamespace(client=object(), section_membership_write_lock=asyncio.Lock())
        monkeypatch.setattr(server, "_app", lambda ctx: app)
        return await asyncio.gather(
            server.remove_class_staff(9001, user_ids=[101], context="CTX"),
            server.remove_class_staff(9002, user_ids=[102], context="CTX"),
        )

    results = asyncio.run(run_parallel_removals())
    assert all(result.startswith("✅") for result in results)
    assert max_active_writes == 1


def test_class_staff_tools_warn_agents_not_to_parallelize():
    from parentsquare_mcp import server

    assert "Never call this tool" in server.add_class_staff.__doc__
    assert "Never call this tool" in server.remove_class_staff.__doc__
    assert "never run class-staff or student-enrollment writes" in server.MCP_INSTRUCTIONS


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


class _FakeErrorPageResp:
    """ParentSquare's branded HTML error page, returned at HTTP 500."""
    status_code = 500
    headers = {"content-type": "text/html; charset=utf-8"}
    text = "<!DOCTYPE html><html><body>Something went wrong. Support Code: abc</body></html>"


class _FakeRejectionResp:
    """An explicit rejection: HTTP 200 UJS carrying a danger flash."""
    status_code = 200
    headers = {"content-type": "text/javascript; charset=utf-8"}
    text = "$('#modal-add-user-error').html('<div class=\"alert-danger\">Email taken</div>');"


def _patch_add_staff(monkeypatch, tmp_path, roster=STAFF_ROSTER, post_resp=None):
    from parentsquare_mcp import server

    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    calls: dict = {"puts": []}

    class _FakeClient:
        def post_form(self, path, data):
            calls["form_path"], calls["form"] = path, data
            return post_resp or _FakeUjsResp()

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


# A 5xx from ParentSquare is a crash, not a verdict — the account may well have
# been created (as it always is by POST /schools/{id}/students), so add_staff
# reads the roster back before telling anyone a retry is safe.

def test_add_staff_reports_success_when_5xx_but_the_account_exists(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_staff

    _patch_add_staff(monkeypatch, tmp_path, post_resp=_FakeErrorPageResp())
    msg = asyncio.run(add_staff(13749, "Apitest", "Teacher", email="new@example.com",
                                context="CTX"))
    assert msg.startswith("✅") and "500" in msg and "do not retry" in msg.lower()


def test_add_staff_reports_failure_when_5xx_and_the_account_is_absent(monkeypatch, tmp_path):
    from parentsquare_mcp.server import add_staff

    _patch_add_staff(monkeypatch, tmp_path, roster={"data": []},
                     post_resp=_FakeErrorPageResp())
    msg = asyncio.run(add_staff(13749, "Ghost", "Person", email="ghost@example.com",
                                context="CTX"))
    assert msg.startswith("❌") and "500" in msg


def test_add_staff_trusts_an_explicit_rejection_over_the_roster(monkeypatch, tmp_path):
    """A 200 + alert-danger means rejected; the roster match is a pre-existing user."""
    from parentsquare_mcp.server import add_staff

    _patch_add_staff(monkeypatch, tmp_path, post_resp=_FakeRejectionResp())
    msg = asyncio.run(add_staff(13749, "Apitest", "Teacher", email="new@example.com",
                                context="CTX"))
    assert msg.startswith("❌")
