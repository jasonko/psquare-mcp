"""Parsing and request-body helpers for the admin write tools.

All ParentSquare admin write endpoints were reverse-engineered and verified live
(see the vault note "ParentSquare admin API mapping"). This module keeps the
pure, testable logic — positional-row parsing, edit-form field extraction, and
form-body construction — out of the MCP tool layer.
"""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from parentsquare_mcp.models import (
    AdminStudentProfile,
    Grade,
    RosterParent,
    RosterStudent,
)

# --- roster positional-array feeds ------------------------------------------
# `/schools/{id}/roster/students_data` -> {"data": [[...14 cols...], ...]}
_S_ID, _S_GRADE, _S_SIS, _S_NAME, _S_PARENTS, _S_GRADE_POS = 1, 3, 4, 6, 7, 11
_S_STATE, _S_STATUS = 5, 10
# `/schools/{id}/roster/parents_data` -> {"data": [[...12 cols...], ...]}
_P_UID, _P_NAME, _P_STUDENTS, _P_EMAIL, _P_PHONE, _P_REGISTERED = 1, 2, 3, 4, 5, 8
_P_SECONDARY = 6


def parse_roster_students(rows: list[list]) -> list[RosterStudent]:
    """Map students_data positional rows to RosterStudent dataclasses."""
    out: list[RosterStudent] = []
    for r in rows:
        if not r or r[_S_ID] is None:
            continue
        out.append(
            RosterStudent(
                id=int(r[_S_ID]),
                name=r[_S_NAME] or "",
                grade=r[_S_GRADE] or "",
                student_sis_id=r[_S_SIS] or None,
                parents=r[_S_PARENTS] or "",
                grade_position=r[_S_GRADE_POS] if isinstance(r[_S_GRADE_POS], int) else None,
                state_id=r[_S_STATE] or None,
                account_status=r[_S_STATUS] or None,
            )
        )
    return out


def parse_roster_parents(rows: list[list]) -> list[RosterParent]:
    """Map parents_data positional rows to RosterParent dataclasses."""
    out: list[RosterParent] = []
    for r in rows:
        if not r or r[_P_UID] is None:
            continue
        out.append(
            RosterParent(
                user_id=int(r[_P_UID]),
                name=r[_P_NAME] or "",
                students=r[_P_STUDENTS] or "",
                email=r[_P_EMAIL] or None,
                phone=r[_P_PHONE] or None,
                registered=str(r[_P_REGISTERED]).strip().lower() == "yes",
                secondary_phone=r[_P_SECONDARY] or None,
            )
        )
    return out


def parse_grades(soup: BeautifulSoup) -> list[Grade]:
    """Extract grades from the roster add-student modal's grade `<select>`.

    Grades are per-school, so they are discovered at runtime rather than
    hardcoded. Returns non-empty options from `select[name="student[grade_id]"]`.
    """
    select = soup.find("select", attrs={"name": "student[grade_id]"})
    grades: list[Grade] = []
    if not select:
        return grades
    for opt in select.find_all("option"):
        value = (opt.get("value") or "").strip()
        if not value:
            continue
        grades.append(Grade(id=int(value), name=opt.get_text(strip=True)))
    return grades


# --- edit-form field extraction ---------------------------------------------

def _unescape_js(text: str) -> str:
    r"""Undo the JS string escaping in a Rails `.js.erb` response.

    The body embeds HTML as a JS string with ``\"`` and ``\/`` escapes; undo
    those so the markup can be regex-scanned.
    """
    return text.replace("\\/", "/").replace('\\"', '"')


def _parse_input_tags(html: str) -> list[dict[str, str]]:
    """Return an attribute dict for every ``<input>`` tag in ``html``.

    Attribute order varies (``value`` often precedes ``name``), so each tag is
    parsed independently into a name->value dict.
    """
    tags: list[dict[str, str]] = []
    for tag in re.findall(r"<input\b[^>]*>", html):
        attrs = dict(re.findall(r'(\w[\w-]*)="([^"]*)"', tag))
        tags.append(attrs)
    return tags


def extract_student_edit_fields(js_text: str) -> dict[str, str]:
    """Extract current student field values from the edit-form JS response.

    Returns keys: first_name, last_name, external_id, grade_id (as strings;
    missing values are "").

    Class enrollment is deliberately **not** returned. The form's
    ``<select name="student[section_ids][]">`` lists the grade's available
    classes but never marks any option ``selected`` (``data-initval`` is always
    ``"[]"``) — ParentSquare loads the current enrollment separately via JS. Any
    value scraped here would be an empty list for every student, and feeding
    that back into the full-replace ``student[section_ids][]`` field wiped the
    student's classes. Read classes via ``get_student``'s GraphQL ``sections``.
    """
    html = _unescape_js(js_text)
    fields: dict[str, str] = {"first_name": "", "last_name": "", "external_id": "", "grade_id": ""}
    wanted = {
        "student[first_name]": "first_name",
        "student[last_name]": "last_name",
        "student[external_id]": "external_id",
        "student[grade_id]": "grade_id",
    }
    for attrs in _parse_input_tags(html):
        key = wanted.get(attrs.get("name", ""))
        if key:
            fields[key] = attrs.get("value", "")
    return fields


def extract_parent_edit_fields(js_text: str) -> dict[str, str]:
    """Extract current parent field values from the edit_institute_user JS.

    Returns keys: first_name, last_name, contact_id (the shared email/phone
    contact record id, "" if none found).
    """
    html = _unescape_js(js_text)
    fields = {"first_name": "", "last_name": "", "contact_id": ""}
    for attrs in _parse_input_tags(html):
        name = attrs.get("name", "")
        if name == "user[first_name]":
            fields["first_name"] = attrs.get("value", "")
        elif name == "user[last_name]":
            fields["last_name"] = attrs.get("value", "")
        elif re.fullmatch(r"user\[contacts_attributes\]\[\d+\]\[id\]", name) and not fields["contact_id"]:
            fields["contact_id"] = attrs.get("value", "")
    return fields


# --- GraphQL student detail --------------------------------------------------

STUDENT_PROFILE_QUERY = """
query StudentProfileView($studentId: Int!) {
  studentProfileView(studentId: $studentId) {
    studentId
    fullName
    firstName
    lastName
    schoolId
    schoolName
    gradeName
    externalId
    parents { fullName profilePath }
    sections { name period room teachers { fullName } }
  }
}
"""


def parse_student_profile(data: dict) -> AdminStudentProfile | None:
    """Map a StudentProfileView GraphQL payload to AdminStudentProfile."""
    view = (data or {}).get("studentProfileView")
    if not view:
        return None
    parents = [
        {"name": p.get("fullName", ""), "profile_path": p.get("profilePath", "")}
        for p in (view.get("parents") or [])
    ]
    sections = [
        {
            "name": s.get("name", ""),
            "period": s.get("period", ""),
            "room": s.get("room", ""),
            "teachers": [t.get("fullName", "") for t in (s.get("teachers") or [])],
        }
        for s in (view.get("sections") or [])
    ]
    return AdminStudentProfile(
        student_id=int(view["studentId"]),
        full_name=view.get("fullName", ""),
        first_name=view.get("firstName", ""),
        last_name=view.get("lastName", ""),
        school_id=int(view.get("schoolId") or 0),
        school_name=view.get("schoolName", ""),
        grade_name=view.get("gradeName", ""),
        student_sis_id=view.get("externalId") or None,
        parents=parents,
        sections=sections,
    )


# --- form-body builders ------------------------------------------------------

def build_add_student_body(first_name: str, last_name: str, grade_id: int, sis_id: str = "") -> dict:
    """Body for ``POST /schools/{school_id}/students``.

    ``student[section_ids][]`` is deliberately omitted — see
    ``build_edit_student_body``. A new student has no classes to preserve, but
    sending the key serves no purpose either.
    """
    return {
        "student[first_name]": first_name,
        "student[last_name]": last_name,
        "student[external_id]": sis_id or "",
        "student[grade_id]": str(grade_id),
        "commit": "Add Student",
    }


def build_edit_student_body(
    first_name: str,
    last_name: str,
    grade_id: str,
    sis_id: str = "",
    section_ids: list[str] | None = None,
) -> dict:
    """Body for ``POST /schools/{id}/students/{sid}`` (Rails ``_method=patch``).

    ``student[section_ids][]`` is a **full replace**: whatever is sent becomes
    the student's complete class list, and an empty value clears every class.
    Omitting the key entirely leaves the existing enrollment untouched — the same
    way ``build_edit_parent_body`` omits ``kids_attributes``.

    So ``section_ids=None`` (the default) omits the key and preserves the
    student's classes. Pass an explicit list only to *replace* the enrollment;
    an explicit ``[]`` clears it.

    This must not be derived from the edit form: that form renders the grade's
    available classes but never marks any ``<option>`` as ``selected`` (its
    ``data-initval`` is always ``"[]"``), because the current enrollment is
    loaded separately by JS. Scraping it yields ``[]`` for every student, which
    previously made every edit silently unenroll the student from all classes.
    Use ``get_student``'s GraphQL ``sections`` to read current classes.
    """
    body = {
        "_method": "patch",
        "student[first_name]": first_name,
        "student[last_name]": last_name,
        "student[external_id]": sis_id or "",
        "student[grade_id]": str(grade_id),
        "commit": "Save",
    }
    if section_ids is not None:
        # requests encodes list values as repeated keys
        body["student[section_ids][]"] = section_ids or ""  # type: ignore[assignment]
    return body


def build_add_parent_body(
    school_id: int,
    student_id: int,
    grade_id: int,
    first_name: str,
    last_name: str,
    email: str = "",
    phone: str = "",
) -> dict:
    return {
        "user[first_name]": first_name,
        "user[last_name]": last_name,
        "user[email]": email or "",
        "user[phone]": phone or "",
        "user[reg_status]": "EMAIL_VALIDATION_PENDING",
        "user[school_user_associations_attributes][0][school_id]": str(school_id),
        "user[school_user_associations_attributes][0][role]": "PARENT",
        "user[school_user_associations_attributes][0][id]": "",
        "user[kids_attributes][0][grade_id]": str(grade_id),
        "user[kids_attributes][0][id]": str(student_id),
        "user[kids_attributes][0][school_id]": str(school_id),
        "user[kids_attributes][0][_destroy]": "false",
        "commit": "Add",
    }


def build_edit_parent_body(
    first_name: str,
    last_name: str,
    contact_id: str,
    email: str | None = None,
    phone: str | None = None,
) -> dict:
    """Build the update_institute_user PATCH body.

    Existing kid links are intentionally omitted — verified safe (Rails leaves
    omitted nested associations untouched). email/phone share one contact record
    addressed at indices 0 (email) and 2 (phone) with the same contact id.
    """
    body: dict = {
        "_method": "patch",
        "user[first_name]": first_name,
        "user[last_name]": last_name,
        "role": "PARENT",
        "commit": "Save",
    }
    if email is not None:
        body["user[contacts_attributes][0][email]"] = email
        body["user[contacts_attributes][0][id]"] = contact_id
    if phone is not None:
        body["user[contacts_attributes][2][phone]"] = phone
        body["user[contacts_attributes][2][id]"] = contact_id
    return body


def build_link_guardian_body(school_id: int, student_id: int, grade_id: int) -> dict:
    """Build an update_institute_user PATCH that adds one kid link.

    Only the new kid is sent (existing links are left untouched). A unique
    numeric key is used for the new nested record.
    """
    key = str(int(time.time() * 1000))
    return {
        "_method": "patch",
        "role": "PARENT",
        f"user[kids_attributes][{key}][grade_id]": str(grade_id),
        f"user[kids_attributes][{key}][id]": str(student_id),
        f"user[kids_attributes][{key}][school_id]": str(school_id),
        f"user[kids_attributes][{key}][_destroy]": "false",
        "commit": "Save",
    }


def build_bulk_invite_body(user_ids: list[int]) -> dict:
    """Build the JSON body for the bulk parent-invite endpoint.

    POST /schools/{id}/users/invite expects a comma-joined ``ids`` string (not a
    JSON array), a ``role``, and the selected count. ParentSquare skips any ids
    that are already registered.
    """
    return {
        "ids": ",".join(str(int(u)) for u in user_ids),
        "role": "PARENT",
        "selected": len(user_ids),
    }


# --- response interpretation -------------------------------------------------

def write_succeeded(status_code: int, content_type: str, body: str) -> bool:
    """Return True if a form-write response indicates success.

    Success = HTTP 200 with a ``text/javascript`` body that is either a page
    reload directive (student/parent roster writes) or a success flash
    (``alert-success``, used by the invite endpoints). An ``alert-danger`` /
    ``alert-error`` flash is treated as failure even if a generic reload/loading
    script is also present. Failures otherwise return an HTML error page (e.g.
    404) or a non-200 status.
    """
    if status_code != 200:
        return False
    if "javascript" not in (content_type or "").lower():
        return False
    low = body.lower()
    if "alert-danger" in low or "alert-error" in low:
        return False
    if "alert-success" in low:
        return True
    return "reload" in body or "page_loading" in body


# --- read-back verification --------------------------------------------------
# A 200 JS "reload" response only proves ParentSquare *accepted* the form POST;
# it does not prove the record persisted (some silent failures still reload).
# The write tools re-read authoritative state after a write and confirm the
# change with these predicates before reporting success.

def _norm_name(s: str) -> str:
    """Collapse whitespace and lowercase for tolerant name comparison."""
    return " ".join((s or "").split()).strip().lower()


def guardian_present(guardians: list[dict], first_name: str, last_name: str) -> bool:
    """True if a guardian named ``first last`` is in a student's guardian list.

    ``guardians`` is the ``parents`` list from ``parse_student_profile``
    (each ``{"name": ..., "profile_path": ...}``).
    """
    target = _norm_name(f"{first_name} {last_name}")
    return any(_norm_name(g.get("name", "")) == target for g in guardians)


def guardian_linked(guardians: list[dict], user_id: int) -> bool:
    """True if a guardian whose profile path references ``user_id`` is present."""
    needle = f"/users/{int(user_id)}"
    return any(needle in (g.get("profile_path") or "") for g in guardians)


def roster_has_student(students, first_name: str, last_name: str) -> bool:
    """True if the roster (``RosterStudent`` list) has a ``Last, First`` match."""
    target = _norm_name(f"{last_name}, {first_name}")
    return any(_norm_name(getattr(s, "name", "")) == target for s in students)


def parse_flash_message(body: str) -> str | None:
    """Extract ParentSquare's alert text from a Rails UJS response.

    Rails UJS responses embed HTML inside a JavaScript string. Normal writes
    replace ``.flash-message``; validation errors from the staff modal instead
    populate ``#modal-add-user-error``. Decode the embedded markup first, then
    prefer the explicit ``flash_notice``/``flash_alert`` span and fall back to
    an ARIA alert container.
    """
    decoded = (body or "").replace("\\/", "/").replace('\\"', '"')
    soup = BeautifulSoup(decoded, "html.parser")

    for selector in (
        "#modal-add-user-error [id^='flash_']",
        "#modal-edit-user-error [id^='flash_']",
        ".flash-message [id^='flash_']",
        "[id^='flash_']",
    ):
        node = soup.select_one(selector)
        if node:
            text = " ".join(node.get_text(" ", strip=True).split())
            if text:
                return text

    for selector in (
        "#modal-add-user-error [role='alert']",
        "#modal-edit-user-error [role='alert']",
        ".flash-message [role='alert']",
    ):
        node = soup.select_one(selector)
        if node:
            text = " ".join(node.get_text(" ", strip=True).split())
            if text:
                return text
    return None
