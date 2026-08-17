"""Parsing and body building for student class enrollment.

Reverse-engineered from the admin ``RostersAssignClassController`` bundle and
verified live. Two write endpoints back these tools:

* ``PUT /api/v2/sections/{id}/add_students`` — **additive**. Students already in
  the class are untouched; omitted students are *not* removed. This is the safe
  bulk primitive used for start-of-year assignment.
* ``PUT /api/v2/students/{id}/sections`` — **full replace**. Whatever is sent
  becomes the student's complete class list, so removals and moves must
  read-modify-write.

Both answer with real JSON, so use ``json_write_ok`` / ``json_write_error`` from
``parsers.classes`` — not the Rails UJS helpers.

There is no reverse read: ``GET /api/v2/students/{id}/sections`` 404s, and the
GraphQL ``StudentProfileSectionView`` exposes no section id. The whole school's
student -> sections map instead comes from the ``roster/assign_classes`` page,
which renders every student server-side (one request for the entire roster).
"""

from __future__ import annotations

import re

from parentsquare_mcp.models import ClassStudent


def parse_class_students(payload: dict) -> list[ClassStudent]:
    """Map ``GET /api/v2/sections/{id}/students`` to ClassStudent dataclasses."""
    out: list[ClassStudent] = []
    for item in (payload or {}).get("data", []) or []:
        attrs = item.get("attributes", {}) or {}
        raw_id = attrs.get("id") or item.get("id")
        if raw_id is None:
            continue
        out.append(
            ClassStudent(
                student_id=int(raw_id),
                first_name=attrs.get("first_name") or "",
                last_name=attrs.get("last_name") or "",
                full_name=attrs.get("full_name") or "",
                student_sis_id=attrs.get("external_id") or None,
            )
        )
    out.sort(key=lambda s: (s.last_name.lower(), s.first_name.lower()))
    return out


# One hidden span per class the student belongs to, e.g.
#   <span class="student-sections-56332978" style="display:none"
#         data-section-id="50234079" data-section-name="test class 2"></span>
# Attributes are extracted independently of their order, the same way
# ``parsers.admin._parse_input_tags`` does, so markup reordering can't break it.
_SPAN_TAG = re.compile(r"<span\b[^>]*\bclass=\"student-sections-\d+\"[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')


def parse_student_sections_map(html: str) -> dict[int, list[dict]]:
    """Map every student -> their classes from the ``assign_classes`` page.

    One request returns the whole school, which is why removals and moves read
    this rather than querying per student. Students with no classes simply have
    no spans and are absent from the result; callers must treat a missing key as
    "no classes", not "unknown student".

    Returns ``{student_id: [{"section_id": int, "name": str}, ...]}``.
    """
    out: dict[int, list[dict]] = {}
    for tag in _SPAN_TAG.findall(html or ""):
        attrs = dict(_ATTR.findall(tag))
        cls = attrs.get("class", "")
        section_raw = attrs.get("data-section-id")
        m = re.fullmatch(r"student-sections-(\d+)", cls.strip())
        if not m or not section_raw or not section_raw.isdigit():
            continue
        student_id = int(m.group(1))
        section_id = int(section_raw)
        entry = out.setdefault(student_id, [])
        if not any(e["section_id"] == section_id for e in entry):
            entry.append({"section_id": section_id, "name": attrs.get("data-section-name", "")})
    return out


def build_add_students_body(section_id: int, student_ids: list[int]) -> dict:
    """Body for ``PUT /api/v2/sections/{section_id}/add_students`` (additive)."""
    return {
        "section_id": str(section_id),
        "data": [{"type": "student", "id": str(s)} for s in student_ids],
    }


def build_student_sections_body(student_id: int, section_ids: list[int]) -> dict:
    """Body for ``PUT /api/v2/students/{student_id}/sections``.

    A **full replace** — callers must merge the student's unchanged classes in
    first. An empty list clears every class, which is why the tools built on
    this never derive the list from an unverified source.
    """
    return {
        "student_id": str(student_id),
        "data": [{"id": str(s), "type": "section"} for s in section_ids],
    }
