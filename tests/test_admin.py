from __future__ import annotations

import asyncio
import json
import os

from bs4 import BeautifulSoup

from parentsquare_mcp import audit
from parentsquare_mcp.parsers import admin


# --- roster parsing ----------------------------------------------------------

def test_parse_roster_students_maps_positional_columns():
    rows = [
        [None, 56186074, None, "2nd Grade", "SIS-1", "STATE-9", "Doe, Jane", "Alex Doe, Sam Roe",
         "", "", "Active", 4, "No", None],
        [None, None, None, None, None, None, None, None, "", "", None, None, "No", None],  # skipped
    ]
    students = admin.parse_roster_students(rows)
    assert len(students) == 1
    s = students[0]
    assert s.id == 56186074
    assert s.name == "Doe, Jane"
    assert s.grade == "2nd Grade"
    assert s.student_sis_id == "SIS-1"
    assert s.parents == "Alex Doe, Sam Roe"
    assert s.grade_position == 4
    assert s.state_id == "STATE-9"
    assert s.account_status == "Active"


def test_parse_roster_parents_maps_positional_columns():
    rows = [
        [None, 71734099, "Apike, Alex", "Jane Doe (2nd Grade)", "a@example.com",
         "123-555-0100", "123-555-0199", "Jul 7, 2026", "Yes", "No", 1, None],
    ]
    parents = admin.parse_roster_parents(rows)
    assert len(parents) == 1
    p = parents[0]
    assert p.user_id == 71734099
    assert p.name == "Apike, Alex"
    assert p.students == "Jane Doe (2nd Grade)"
    assert p.email == "a@example.com"
    assert p.phone == "123-555-0100"
    assert p.registered is True
    assert p.secondary_phone == "123-555-0199"


def test_parse_grades_from_select():
    html = """
    <select name="student[grade_id]">
      <option value="">Select Grade</option>
      <option value="545988">Test Grade (admin use only)</option>
      <option value="100870">Kindergarten</option>
    </select>
    """
    grades = admin.parse_grades(BeautifulSoup(html, "html.parser"))
    assert [(g.id, g.name) for g in grades] == [
        (545988, "Test Grade (admin use only)"),
        (100870, "Kindergarten"),
    ]


# --- edit-form extraction (value attr precedes name, JS-escaped) -------------

STUDENT_EDIT_JS = (
    r'x(".. <input class=\"form-control\" type=\"text\" value=\"Jane\" '
    r'name=\"student[first_name]\" id=\"student_first_name\" \/>'
    r'<input type=\"text\" value=\"Doe\" name=\"student[last_name]\" \/>'
    r'<input type=\"text\" name=\"student[external_id]\" \/>'
    r'<input value=\"545988\" type=\"hidden\" name=\"student[grade_id]\" \/> ..")'
)


def test_extract_student_edit_fields():
    f = admin.extract_student_edit_fields(STUDENT_EDIT_JS)
    assert f["first_name"] == "Jane"
    assert f["last_name"] == "Doe"
    assert f["external_id"] == ""
    assert f["grade_id"] == "545988"


def test_extract_student_edit_fields_never_reports_sections():
    """The edit form never marks a section <option> selected, so any scraped
    value would be a false empty list that wipes the student's classes."""
    js = STUDENT_EDIT_JS.replace(
        r"..\")",
        r'<select name=\"student[section_ids][]\" data-initval=\"[]\">'
        r"<option value=\"\">Select Class</option>"
        r'<option value=\"5258167\">Mr. Heiko Class</option></select> ..")',
    )
    assert "section_ids" not in admin.extract_student_edit_fields(js)


PARENT_EDIT_JS = (
    r'<input type=\"text\" value=\"Alex\" name=\"user[first_name]\" \/>'
    r'<input type=\"text\" value=\"Apike\" name=\"user[last_name]\" \/>'
    r'<input type=\"hidden\" value=\"104511134\" name=\"user[contacts_attributes][0][id]\" \/>'
    r'<input type=\"hidden\" value=\"104511134\" name=\"user[contacts_attributes][2][id]\" \/>'
)


def test_extract_parent_edit_fields():
    f = admin.extract_parent_edit_fields(PARENT_EDIT_JS)
    assert f["first_name"] == "Alex"
    assert f["last_name"] == "Apike"
    assert f["contact_id"] == "104511134"


# --- GraphQL profile ---------------------------------------------------------

def test_parse_student_profile():
    data = {
        "studentProfileView": {
            "studentId": 56186074,
            "fullName": "Jane Doe",
            "firstName": "Jane",
            "lastName": "Doe",
            "schoolId": 13749,
            "schoolName": "Test School",
            "gradeName": "2nd Grade",
            "externalId": "SIS-1",
            "parents": [{"fullName": "Alex Doe", "profilePath": "/x"}],
            "sections": [{"name": "Class", "period": "1", "room": "A",
                          "teachers": [{"fullName": "Ms. T"}]}],
        }
    }
    p = admin.parse_student_profile(data)
    assert p.student_id == 56186074
    assert p.student_sis_id == "SIS-1"
    assert p.parents == [{"name": "Alex Doe", "profile_path": "/x"}]
    assert p.sections[0]["teachers"] == ["Ms. T"]


def test_parse_student_profile_none():
    assert admin.parse_student_profile({"studentProfileView": None}) is None


# --- body builders -----------------------------------------------------------

def test_build_add_student_body():
    b = admin.build_add_student_body("Jane", "Doe", 545988, "SIS-1")
    assert b["student[first_name]"] == "Jane"
    assert b["student[grade_id]"] == "545988"
    assert b["student[external_id]"] == "SIS-1"
    assert b["commit"] == "Add Student"


def test_build_add_student_body_sends_an_empty_section_ids_key():
    """Regression: omitting student[section_ids][] makes the create 500.

    Verified live against school 13749 — three otherwise byte-identical POSTs to
    /schools/13749/students:

      - key omitted, Referer present -> HTTP 500 (student still created)
      - key present and empty, Referer present -> HTTP 200 text/javascript reload
      - key present and empty, Referer absent -> HTTP 200 text/javascript reload

    So the key, not the headers, is what the create action needs: Rails sees
    ``nil`` instead of ``[""]`` and crashes *after* committing the record. The
    500 looked like a ParentSquare bug for months; it was a missing param.
    """
    b = admin.build_add_student_body("Jane", "Doe", 545988)
    assert b["student[section_ids][]"] == ""


def test_add_student_body_matches_the_roster_form_submission():
    """The built body must match the Add Student form's own POST, param for param."""
    from urllib.parse import parse_qsl

    # captured from DevTools on /schools/13749/roster/add_remove_students
    browser = (
        "utf8=%E2%9C%93&authenticity_token=TOKEN&student%5Bfirst_name%5D=testfirst"
        "&student%5Blast_name%5D=testlast&student%5Bexternal_id%5D="
        "&student%5Bgrade_id%5D=545988&student%5Bsection_ids%5D%5B%5D="
        "&commit=Add+Student"
    )
    # utf8 + authenticity_token are injected by PSClient.post_form, not the builder
    expected = {k: v for k, v in parse_qsl(browser, keep_blank_values=True)
                if k not in ("utf8", "authenticity_token")}
    assert admin.build_add_student_body("testfirst", "testlast", 545988) == expected


def test_build_edit_student_body_uses_patch():
    b = admin.build_edit_student_body("Jane", "Doe", "545988", "SIS-9")
    assert b["_method"] == "patch"
    assert b["student[external_id]"] == "SIS-9"
    assert b["commit"] == "Save"


def test_build_edit_student_body_omits_sections_by_default():
    """Regression: sending student[section_ids][] is a full replace, so an edit
    that isn't changing enrollment must omit the key or it wipes every class."""
    b = admin.build_edit_student_body("Jane", "Doe", "545988", "SIS-9")
    assert "student[section_ids][]" not in b


def test_section_ids_rule_is_opposite_for_create_and_edit():
    """The asymmetry is deliberate and both halves are load-bearing.

    create: the key must be present-and-empty or the POST 500s (after saving).
    edit:   the key must be absent or Rails clears every class enrolment.
    """
    create = admin.build_add_student_body("Jane", "Doe", 545988)
    edit = admin.build_edit_student_body("Jane", "Doe", "545988")
    assert create["student[section_ids][]"] == ""
    assert "student[section_ids][]" not in edit


def test_build_edit_student_body_replaces_sections_when_given():
    b = admin.build_edit_student_body("Jane", "Doe", "545988", section_ids=["1", "2"])
    assert b["student[section_ids][]"] == ["1", "2"]


def test_build_edit_student_body_explicit_empty_list_clears():
    b = admin.build_edit_student_body("Jane", "Doe", "545988", section_ids=[])
    assert b["student[section_ids][]"] == ""


def test_build_edit_parent_body_omits_kids_and_shares_contact_id():
    b = admin.build_edit_parent_body("Alex", "Apike", "104511134", email="new@x.com", phone="1")
    assert b["_method"] == "patch"
    assert b["user[contacts_attributes][0][email]"] == "new@x.com"
    assert b["user[contacts_attributes][0][id]"] == "104511134"
    assert b["user[contacts_attributes][2][phone]"] == "1"
    assert b["user[contacts_attributes][2][id]"] == "104511134"
    assert not any("kids_attributes" in k for k in b)


def test_build_edit_parent_body_only_email():
    b = admin.build_edit_parent_body("Alex", "Apike", "104511134", email="new@x.com")
    assert "user[contacts_attributes][0][email]" in b
    assert not any("[2][phone]" in k for k in b)


def test_build_link_guardian_body_adds_new_kid_only():
    b = admin.build_link_guardian_body(13749, 56186948, 545988)
    kid_keys = [k for k in b if "kids_attributes" in k]
    idx = {k.split("][")[0].split("[")[-1] for k in kid_keys}  # the numeric key
    assert len(idx) == 1
    assert any(v == "56186948" for v in b.values())
    assert b["_method"] == "patch"


# --- response interpretation -------------------------------------------------

def test_write_succeeded_true_on_reload_js():
    assert admin.write_succeeded(200, "text/javascript; charset=utf-8",
                                 "$('#page_loading').show(); window.location.reload(true);")


def test_write_succeeded_false_on_html_error():
    assert not admin.write_succeeded(404, "text/html; charset=utf-8", "<html>404</html>")


def test_write_succeeded_false_on_200_non_js():
    assert not admin.write_succeeded(200, "text/html", "<form>errors</form>")


# --- read-back verification predicates --------------------------------------

_GUARDIANS = [
    {"name": "Alex Doe", "profile_path": "/schools/13749/users/71853816"},
    {"name": "Sam  Roe", "profile_path": "/schools/13749/users/42"},
]


def test_guardian_present_matches_case_and_whitespace_insensitively():
    assert admin.guardian_present(_GUARDIANS, "sam", "roe")
    assert admin.guardian_present(_GUARDIANS, "Alex", "Doe")


def test_guardian_present_false_when_absent():
    assert not admin.guardian_present(_GUARDIANS, "Test", "Parent4")
    assert not admin.guardian_present([], "Test", "Parent4")


def test_guardian_linked_matches_user_id_in_profile_path():
    assert admin.guardian_linked(_GUARDIANS, 71853816)
    assert admin.guardian_linked(_GUARDIANS, "42")


def test_guardian_linked_false_when_user_id_absent():
    assert not admin.guardian_linked(_GUARDIANS, 99999)
    assert not admin.guardian_linked([], 42)


def test_roster_has_student_matches_last_comma_first():
    students = admin.parse_roster_students([
        [None, 56341978, None, "Test Grade (admin use only)", None, None,
         "Student4, Test", "", None, None, None, 0],
    ])
    assert admin.roster_has_student(students, "Test", "Student4")
    assert not admin.roster_has_student(students, "Test", "Student5")


# --- invitations -------------------------------------------------------------

# Real captured flash bodies (verified live 2026-07-09 against Test Grade records).
_SINGLE_INVITE_OK = (
    '$(".flash-message").replaceWith("\\n<div class=\\"flash-message\\">\\n'
    '<div role=\\"alert\\" class=\\"alert alert-dismissable alert-success\\">\\n'
    '<span id=\\"flash_notice\\">Successfully sent invitation email to user.<\\/span>\\n'
    '<\\/div>\\n<\\/div>");'
)
_BULK_INVITE_OK = (
    '$(".flash-message").replaceWith("\\n<div class=\\"flash-message\\">\\n'
    '<div role=\\"alert\\" class=\\"alert alert-dismissable alert-success\\">\\n'
    '<span id=\\"flash_notice\\">Successfully sent email/text to 2 unregistered '
    'out of 2 selected users<\\/span>\\n<\\/div>\\n<\\/div>");'
)
_INVITE_FAIL = (
    '$(".flash-message").replaceWith("\\n<div class=\\"flash-message\\">\\n'
    '<div role=\\"alert\\" class=\\"alert alert-dismissable alert-danger\\">\\n'
    '<span id=\\"flash_alert\\">Something went wrong.<\\/span>\\n<\\/div>\\n<\\/div>");'
)
_STAFF_VALIDATION_FAIL = (
    '$("#modal-add-user-error").html("\\n<div class=\\"flash-message\\">\\n'
    '<div role=\\"alert\\" class=\\"alert alert-dismissable alert-danger\\">\\n'
    '<span id=\\"flash_alert\\">Email is required for staff users.<\\/span>\\n'
    '<button class=\\"close\\">×<\\/button>\\n'
    '<\\/div>\\n<\\/div>\\n");'
)


def test_write_succeeded_true_on_success_flash():
    assert admin.write_succeeded(200, "text/javascript; charset=utf-8", _SINGLE_INVITE_OK)
    assert admin.write_succeeded(200, "text/javascript; charset=utf-8", _BULK_INVITE_OK)


def test_write_succeeded_false_on_danger_flash_even_with_reload():
    # A generic reload/loading script alongside an error flash must not read as success.
    body = _INVITE_FAIL + " $('#page_loading').show();"
    assert not admin.write_succeeded(200, "text/javascript; charset=utf-8", body)


def test_write_succeeded_false_on_staff_modal_validation_error():
    assert not admin.write_succeeded(
        200, "text/javascript; charset=utf-8", _STAFF_VALIDATION_FAIL
    )


def test_parse_flash_message_single_and_bulk():
    assert admin.parse_flash_message(_SINGLE_INVITE_OK) == "Successfully sent invitation email to user."
    assert (
        admin.parse_flash_message(_BULK_INVITE_OK)
        == "Successfully sent email/text to 2 unregistered out of 2 selected users"
    )
    assert admin.parse_flash_message(_INVITE_FAIL) == "Something went wrong."
    assert admin.parse_flash_message(_STAFF_VALIDATION_FAIL) == "Email is required for staff users."
    assert admin.parse_flash_message("$('#page_loading').show();") is None


def test_build_bulk_invite_body():
    b = admin.build_bulk_invite_body([71672653, 71853816])
    assert b == {"ids": "71672653,71853816", "role": "PARENT", "selected": 2}


# --- write gate + audit ------------------------------------------------------

def test_writes_enabled_default_off(monkeypatch):
    monkeypatch.delenv("PS_ENABLE_WRITES", raising=False)
    assert audit.writes_enabled() is False
    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    assert audit.writes_enabled() is True
    monkeypatch.setenv("PS_ENABLE_WRITES", "no")
    assert audit.writes_enabled() is False


def test_audit_write_appends_jsonl(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setenv("PS_AUDIT_LOG", str(log))
    audit.audit_write("add_student", {"first_name": "Jane"}, True, "HTTP 200")
    audit.audit_write("edit_student", {"student_id": 1}, False, "blocked")
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["tool"] == "add_student"
    assert rec["ok"] is True
    assert rec["args"] == {"first_name": "Jane"}
    assert "timestamp" in rec


# --- write-gate decorator ----------------------------------------------------

def test_write_gated_blocks_and_audits_when_disabled(tmp_path, monkeypatch):
    from parentsquare_mcp.audit import WRITES_DISABLED_MESSAGE
    from parentsquare_mcp.server import _write_gated

    log = tmp_path / "audit.log"
    monkeypatch.setenv("PS_AUDIT_LOG", str(log))
    monkeypatch.delenv("PS_ENABLE_WRITES", raising=False)

    calls: list[tuple] = []

    @_write_gated
    async def add_student(school_id: int, first_name: str, context=None):
        calls.append((school_id, first_name))
        return "ran"

    result = asyncio.run(add_student(13749, "Jane", context="CTX"))
    assert result == WRITES_DISABLED_MESSAGE
    assert calls == []  # tool body never invoked
    rec = json.loads(log.read_text().strip())
    assert rec["tool"] == "add_student"
    assert rec["ok"] is False
    assert rec["args"] == {"school_id": 13749, "first_name": "Jane"}  # context excluded
    assert "blocked" in rec["detail"]


def test_write_gated_runs_when_enabled(monkeypatch):
    from parentsquare_mcp.server import _write_gated

    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    calls: list[tuple] = []

    @_write_gated
    async def add_student(school_id: int, first_name: str, context=None):
        calls.append((school_id, first_name))
        return "ran"

    assert asyncio.run(add_student(13749, "Jane", context="CTX")) == "ran"
    assert calls == [(13749, "Jane")]


def test_write_gated_preserves_signature():
    import inspect

    from parentsquare_mcp.server import _write_gated

    async def add_student(school_id: int, first_name: str, context=None):
        return "ran"

    wrapped = _write_gated(add_student)
    assert list(inspect.signature(wrapped).parameters) == ["school_id", "first_name", "context"]


# --- read-back-aware write result formatting ---------------------------------

class _FakeResp:
    def __init__(self, status_code=200, content_type="text/javascript", text="window.location.reload(true);"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.text = text


def test_write_result_verified_reports_verified(tmp_path, monkeypatch):
    from parentsquare_mcp.server import _write_result_verified

    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    msg = _write_result_verified("add_parent", {}, _FakeResp(), True)
    assert "verified" in msg.lower() and msg.startswith("✅")


def test_write_result_verified_warns_when_not_found(tmp_path, monkeypatch):
    from parentsquare_mcp.server import _write_result_verified

    log = tmp_path / "audit.log"
    monkeypatch.setenv("PS_AUDIT_LOG", str(log))
    msg = _write_result_verified("add_parent", {}, _FakeResp(), False)
    assert msg.startswith("⚠️") and "did NOT persist" in msg
    assert json.loads(log.read_text().strip())["ok"] is False  # 200 body, still audited as failure


def test_write_result_verified_unverified_when_readback_unavailable(tmp_path, monkeypatch):
    from parentsquare_mcp.server import _write_result_verified

    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    msg = _write_result_verified("add_parent", {}, _FakeResp(), None)
    assert msg.startswith("✅") and "could not run" in msg


def test_write_result_verified_http_failure_short_circuits(tmp_path, monkeypatch):
    from parentsquare_mcp.server import _write_result_verified

    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    resp = _FakeResp(status_code=404, content_type="text/html", text="<html>nope</html>")
    msg = _write_result_verified("add_parent", {}, resp, False)
    assert msg.startswith("❌") and "404" in msg


# --- ParentSquare's post-create 500 (server-side render crash) ----------------
# POST /schools/{id}/students commits the student and then 500s rendering the
# following page, so a *successful* create looks like a hard failure. The
# read-back, not the status code, decides — a blind retry duplicates a student
# and there is no API route to delete one.

_ERROR_PAGE = "<!DOCTYPE html><html><body>Successfully added student! Support Code: abc</body></html>"


def _server_error_resp():
    return _FakeResp(status_code=500, content_type="text/html", text=_ERROR_PAGE)


def test_write_result_verified_500_with_readback_reports_success(tmp_path, monkeypatch):
    from parentsquare_mcp.server import _write_result_verified

    log = tmp_path / "audit.log"
    monkeypatch.setenv("PS_AUDIT_LOG", str(log))
    msg = _write_result_verified("add_student", {}, _server_error_resp(), True)
    assert msg.startswith("✅")
    assert "500" in msg and "do not retry" in msg.lower()
    assert json.loads(log.read_text().strip())["ok"] is True


def test_write_result_verified_500_without_readback_reports_failure(tmp_path, monkeypatch):
    from parentsquare_mcp.server import _write_result_verified

    log = tmp_path / "audit.log"
    monkeypatch.setenv("PS_AUDIT_LOG", str(log))
    msg = _write_result_verified("add_student", {}, _server_error_resp(), False)
    assert msg.startswith("❌") and "500" in msg
    assert json.loads(log.read_text().strip())["ok"] is False


def test_write_result_verified_500_with_unavailable_readback_warns(tmp_path, monkeypatch):
    from parentsquare_mcp.server import _write_result_verified

    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    msg = _write_result_verified("add_student", {}, _server_error_resp(), None)
    assert msg.startswith("⚠️") and "do not retry blindly" in msg.lower()


def test_write_result_verified_explicit_rejection_ignores_readback(tmp_path, monkeypatch):
    """A 4xx / alert-danger is ParentSquare *rejecting* the write, not crashing."""
    from parentsquare_mcp.server import _write_result_verified

    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    rejected = _FakeResp(
        status_code=200,
        content_type="text/javascript",
        text="$('#student_errors').html('<div class=\"alert-danger\">Name taken</div>');",
    )
    msg = _write_result_verified("add_student", {}, rejected, True)
    assert msg.startswith("❌")


# --- add_student tool end-to-end ---------------------------------------------

_ROSTER_ROW = [None, 61232363, None, "Test Grade", "", "", "Probealpha, Probealpha", "",
               "", "", "Active", 1, "No", None]


class _FakeClient:
    def __init__(self, post_resp, roster_rows):
        self._post_resp = post_resp
        self._roster_rows = roster_rows
        self.posts = []

    def post_form(self, path, data):
        self.posts.append((path, data))
        return self._post_resp

    def get_json(self, path, params=None):
        return {"data": self._roster_rows}


def _run_add_student(client):
    from types import SimpleNamespace

    from parentsquare_mcp import server

    app = SimpleNamespace(client=client, mfa_state=None)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))
    return asyncio.run(
        server.add_student(13749, "Probealpha", "Probealpha", 545988, context=ctx)
    )


def test_add_student_reports_success_when_500_but_student_created(tmp_path, monkeypatch):
    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    client = _FakeClient(_server_error_resp(), [_ROSTER_ROW])
    msg = _run_add_student(client)
    assert msg.startswith("✅")
    assert "500" in msg and "do not retry" in msg.lower()
    assert client.posts[0][0] == "/schools/13749/students"


def test_add_student_reports_failure_when_500_and_student_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))
    msg = _run_add_student(_FakeClient(_server_error_resp(), []))
    assert msg.startswith("❌") and "500" in msg


def test_add_student_warns_when_readback_raises(tmp_path, monkeypatch):
    """A broken read-back must not mask the write or invite a retry."""
    monkeypatch.setenv("PS_ENABLE_WRITES", "1")
    monkeypatch.setenv("PS_AUDIT_LOG", str(tmp_path / "audit.log"))

    class _Boom(_FakeClient):
        def get_json(self, path, params=None):
            raise RuntimeError("network down")

    msg = _run_add_student(_Boom(_server_error_resp(), []))
    assert msg.startswith("⚠️") and "do not retry blindly" in msg.lower()
