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
        [None, 56186074, None, "2nd Grade", "SIS-1", None, "Doe, Jane", "Alex Doe, Sam Roe",
         "", "", None, 4, "No", None],
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


def test_parse_roster_parents_maps_positional_columns():
    rows = [
        [None, 71734099, "Apike, Alex", "Jane Doe (2nd Grade)", "a@example.com",
         "123-555-0100", "", "Jul 7, 2026", "Yes", "No", 1, None],
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
    assert f["section_ids"] == []


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


def test_build_edit_student_body_uses_patch():
    b = admin.build_edit_student_body("Jane", "Doe", "545988", "SIS-9")
    assert b["_method"] == "patch"
    assert b["student[external_id]"] == "SIS-9"
    assert b["commit"] == "Save"


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
