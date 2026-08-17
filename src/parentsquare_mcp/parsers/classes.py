"""Parsing and request-body building for classes (sections) and staff.

ParentSquare calls a classroom a **section**. Three surfaces are involved:

- ``GET /api/v2/schools/{id}/sections_mini`` — the whole class list in one call
  (names, grades, teacher names, room-parent counts).
- ``GET /api/v2/sections/{id}?include_staff=true`` — one class plus its
  ``section_staff_association`` records, which is the read half of every
  staff change.
- ``GET /schools/{id}/roster/staff_data`` — the staff roster, a positional-array
  feed in the same family as ``students_data`` / ``parents_data``.

Unlike the student/guardian admin writes (Rails form posts answering with a UJS
script), the class endpoints are JSON:API: they take ``application/json`` and
answer with JSON, so success is a 2xx and failure carries a JSON error payload.
"""
from __future__ import annotations

import json
import re

from parentsquare_mcp.models import ClassDetail, ClassStaff, RosterStaff, SchoolClass

# Roles a section_staff_association can carry (from ParentSquare's own admin JS).
STAFF_ROLES = ("TEACHER", "ASSISTANT", "ROOM_PARENT")

# Default class_title per role, matching what the admin UI writes.
_DEFAULT_TITLES = {"TEACHER": "Teacher", "ASSISTANT": "Assistant", "ROOM_PARENT": "Room Parent"}


def default_class_title(role: str) -> str:
    return _DEFAULT_TITLES.get(role.upper(), "")


def normalize_role(role: str) -> str:
    """Map a user-supplied role to ParentSquare's enum, or raise ValueError.

    Accepts friendly spellings ("room parent", "teacher") as well as the exact
    enum values.
    """
    key = re.sub(r"[\s-]+", "_", (role or "").strip()).upper()
    if key in STAFF_ROLES:
        return key
    raise ValueError(f"Invalid role {role!r}. Use one of: {', '.join(STAFF_ROLES)}.")


# --- reads -------------------------------------------------------------------

def parse_sections_mini(data: list[dict]) -> list[SchoolClass]:
    """Map ``sections_mini`` rows to SchoolClass dataclasses."""
    classes: list[SchoolClass] = []
    for row in data or []:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        classes.append(
            SchoolClass(
                id=int(row["id"]),
                name=row.get("name") or row.get("sis_name") or "",
                grade_names=row.get("grade_names") or "",
                grade_ids=row.get("grade_ids") or "",
                external_id=row.get("external_id") or "",
                display_name=row.get("display_name"),
                sis_name=row.get("sis_name"),
                teachers=row.get("teachers") or "",
                assistants=int(row.get("assistants") or 0),
                room_parents=int(row.get("room_parents") or 0),
                student_count=int(row.get("student_count") or 0),
                posts_count=int(row.get("posts_count") or 0),
                visibility_status=row.get("section_class_status") or "",
            )
        )
    return classes


def parse_section_detail(payload: dict) -> ClassDetail | None:
    """Map ``/api/v2/sections/{id}?include_staff=true`` to a ClassDetail.

    The staff list must be assembled by joining the ``section_staff_association``
    entries in ``included`` to the ``user`` entries also in ``included`` (via
    ``relationships.user.data.id``). ParentSquare's own client flattens this into
    ``data.section_staff_associations``, but the raw response has no such key.
    """
    data = (payload or {}).get("data")
    if not data:
        return None
    attrs = data.get("attributes", {})
    included = payload.get("included", []) or []
    users = {u["id"]: u.get("attributes", {}) for u in included if u.get("type") == "user"}

    staff: list[ClassStaff] = []
    for item in included:
        if item.get("type") != "section_staff_association":
            continue
        a = item.get("attributes", {})
        user_ref = ((item.get("relationships") or {}).get("user") or {}).get("data") or {}
        user_id = user_ref.get("id")
        if user_id is None:
            continue
        u = users.get(str(user_id), {})
        staff.append(
            ClassStaff(
                assoc_id=int(a["id"]) if a.get("id") is not None else None,
                user_id=int(user_id),
                role=a.get("role") or "",
                class_title=a.get("class_title") or "",
                first_name=u.get("first_name") or "",
                last_name=u.get("last_name") or "",
            )
        )
    staff.sort(key=lambda s: (STAFF_ROLES.index(s.role) if s.role in STAFF_ROLES else 9, s.name))

    grades = (((data.get("relationships") or {}).get("grades") or {}).get("data")) or []
    return ClassDetail(
        id=int(attrs.get("id") or data.get("id")),
        name=attrs.get("name") or attrs.get("sis_name") or "",
        external_id=attrs.get("external_id") or "",
        display_name=attrs.get("display_name"),
        sis_name=attrs.get("sis_name"),
        active=bool(attrs.get("active", True)),
        grade_ids=[str(g["id"]) for g in grades if g.get("id") is not None],
        staff=staff,
    )


def _strip_html(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", value or "").split())


def parse_roster_staff(rows: list) -> list[RosterStaff]:
    """Map ``staff_data`` positional rows to RosterStaff dataclasses.

    Columns: 0 checkbox, 1 user_id, 2 name, 3 staff id, 4 email, 5 phone,
    6 secondary phone, 7 role|title (HTML), 8 record created, 9 registered,
    10 SIS synced, 11 sua_id, 12 actions.
    """
    staff: list[RosterStaff] = []
    for row in rows or []:
        if not isinstance(row, list) or len(row) < 12 or row[1] is None:
            continue
        staff_id = row[3]
        if isinstance(staff_id, list):
            staff_id = ", ".join(str(x) for x in staff_id)
        staff.append(
            RosterStaff(
                user_id=int(row[1]),
                name=row[2] or "",
                staff_id=str(staff_id or ""),
                email=row[4] or None,
                phone=row[5] or None,
                secondary_phone=row[6] or None,
                role_title=_strip_html(row[7] or ""),
                record_created=row[8] or "",
                registered=(row[9] == "Yes"),
                sua_id=int(row[11]) if row[11] is not None else None,
            )
        )
    return staff


# --- write bodies ------------------------------------------------------------

def build_add_class_body(school_id: int, name: str, grade_ids: list[str]) -> dict:
    """Body for ``POST /api/v2/schools/{school_id}/sections``.

    ``relationships`` is omitted entirely when no grades are given, matching the
    admin UI. Note the grades array is a bare list of ``{"id": …}`` — not the
    JSON:API ``{"data": […]}`` wrapper.
    """
    body: dict = {"school_id": school_id, "data": {"attributes": {"name": name}}}
    if grade_ids:
        body["data"]["relationships"] = {"grades": [{"id": str(g)} for g in grade_ids]}
    return body


def build_edit_class_body(
    school_id: int, section_id: int, name: str, external_id: str, grade_ids: list[str]
) -> dict:
    """Body for ``PATCH /api/v2/sections/{section_id}``.

    Always sends the full attribute set — callers must merge unchanged values in
    first (read-modify-write), since this endpoint replaces what it is given.
    """
    return {
        "school_id": school_id,
        "id": str(section_id),
        "data": {
            "attributes": {"external_id": external_id or "", "name": name},
            "relationships": {"grades": [{"id": str(g)} for g in grade_ids]},
        },
    }


def build_staff_replace_body(staff: list[ClassStaff]) -> dict:
    """Body for ``PUT /api/v2/sections/{section_id}/staff``.

    This endpoint is a **full replace**: any association missing from the list is
    deleted. An empty list is sent as ParentSquare's own clear-all sentinel —
    a single object with an empty user, not ``{"data": []}``.
    """
    if not staff:
        return {"data": [{"attributes": {"user": {}}}]}
    return {
        "data": [
            {
                "id": str(s.assoc_id) if s.assoc_id else None,
                "type": "section_staff_association",
                "attributes": {
                    "role": s.role,
                    "class_title": s.class_title or default_class_title(s.role),
                    "user": {
                        "id": str(s.user_id),
                        "first_name": s.first_name,
                        "last_name": s.last_name,
                    },
                },
            }
            for s in staff
        ]
    }


def build_add_staff_body(
    school_id: int,
    first_name: str,
    last_name: str,
    email: str = "",
    phone: str = "",
    staff_id: str = "",
    title: str = "",
    role: str = "STAFF",
    section_ids: list[int] | None = None,
) -> dict:
    """Body for ``POST /schools/{school_id}/users`` (the add-staff roster form).

    Same endpoint as add_parent; the ``school_user_associations_attributes`` role
    (``STAFF`` or ``ADMIN``) is what makes this a staff account. Any section ids
    passed in ``invited_school_section_ids[]`` link the new user to those classes
    at creation time.
    """
    body = {
        "user[first_name]": first_name,
        "user[last_name]": last_name,
        "user[email]": email or "",
        "user[phone]": phone or "",
        "user[contacts_attributes][0][external_id]": staff_id or "",
        "user[school_user_associations_attributes][0][school_id]": str(school_id),
        "user[school_user_associations_attributes][0][id]": "",
        "user[school_user_associations_attributes][0][school_title]": title or "",
        "user[school_user_associations_attributes][0][role]": role,
        "no_flash": "true",
    }
    if section_ids:
        body["invited_school_section_ids[]"] = [str(s) for s in section_ids]  # type: ignore[assignment]
    return body


def build_visibility_body(
    school_id: int, section_ids: list[int], visible: bool, date: str
) -> dict:
    """Body for ``PUT /api/v2/schools/{school_id}/sections/bulk_update_class_visibility``.

    Showing classes sets ``visibility_start_date``; hiding them sets
    ``visibility_end_date``. The date is required and takes effect on that day —
    the admin UI's "now" option simply submits today's date. (Its ``clear_field``
    variant only *unschedules* a pending date and is a silent no-op on a class
    that is already hidden, so it is deliberately not used here.)
    """
    return {
        "ids": ",".join(str(s) for s in section_ids),
        "school_id": school_id,
        "visibility_start_date" if visible else "visibility_end_date": date,
    }


# --- response interpretation -------------------------------------------------

def json_write_ok(status_code: int) -> bool:
    """True if a JSON:API write succeeded. These endpoints answer 2xx on success."""
    return 200 <= status_code < 300


def json_write_error(status_code: int, text: str) -> str:
    """Extract a human-readable error out of a failed JSON:API response.

    Mirrors the admin UI's ``checkAjaxError``: prefer a JSON ``errors`` /
    ``error`` / ``message`` field, and fall back to a trimmed body snippet.
    """
    try:
        data = json.loads(text or "")
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            parts = []
            for e in errors:
                if isinstance(e, dict):
                    parts.append(e.get("detail") or e.get("title") or e.get("message") or str(e))
                else:
                    parts.append(str(e))
            return f"HTTP {status_code}: " + "; ".join(p for p in parts if p)
        for key in ("error", "message", "errors"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return f"HTTP {status_code}: {value}"
    snippet = " ".join((text or "").split())[:200]
    return f"HTTP {status_code}" + (f": {snippet}" if snippet else "")
