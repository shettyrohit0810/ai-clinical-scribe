"""apply_note_patch — pure unit tests, no LLM, no DB.

Covers: each of the four ops happy-path, malformed/invalid patches, the
move same-section error, and the content-preservation invariant (every
section a patch doesn't target survives byte-identical).
"""

import pytest

from app.note_patch import InvalidPatchError, apply_note_patch

BASE_NOTE = {
    "subjective": "Patient reports knee pain for two weeks.",
    "objective": "Mild swelling noted.",
    "assessment": "Right knee pain, etiology unclear.",
    "plan": "1. Start physical therapy.\n2. Follow up in six weeks.",
}


def _note(**overrides):
    return {**BASE_NOTE, **overrides}


# ---- add ---------------------------------------------------------------


def test_add_appends_to_section():
    patch = {"op": "add", "section": "assessment", "text": "Denies fever."}
    result = apply_note_patch(_note(), patch)
    assert result["assessment"] == "Right knee pain, etiology unclear. Denies fever."


def test_add_to_empty_section_has_no_leading_space():
    patch = {"op": "add", "section": "objective", "text": "Vitals stable."}
    result = apply_note_patch(_note(objective=""), patch)
    assert result["objective"] == "Vitals stable."


def test_add_preserves_other_sections_byte_identical():
    note = _note()
    patch = {"op": "add", "section": "assessment", "text": "Denies fever."}
    result = apply_note_patch(note, patch)
    assert result["subjective"] == note["subjective"]
    assert result["objective"] == note["objective"]
    assert result["plan"] == note["plan"]


# ---- remove --------------------------------------------------------------


def test_remove_deletes_exact_substring():
    patch = {"op": "remove", "section": "subjective", "text": "for two weeks"}
    result = apply_note_patch(_note(), patch)
    assert "for two weeks" not in result["subjective"]
    assert result["subjective"] == "Patient reports knee pain."


def test_remove_preserves_other_sections_byte_identical():
    note = _note()
    patch = {"op": "remove", "section": "subjective", "text": "for two weeks"}
    result = apply_note_patch(note, patch)
    assert result["objective"] == note["objective"]
    assert result["assessment"] == note["assessment"]
    assert result["plan"] == note["plan"]


def test_remove_collapses_double_space_but_preserves_newlines_elsewhere():
    # Removing "1. Start physical therapy.\n" would eat the list's own
    # newline; instead exercise removal from within a single line and
    # confirm the OTHER line's newline-separated formatting survives.
    note = _note(plan="1. Start physical therapy right away.\n2. Follow up in six weeks.")
    patch = {"op": "remove", "section": "plan", "text": " right away"}
    result = apply_note_patch(note, patch)
    assert result["plan"] == "1. Start physical therapy.\n2. Follow up in six weeks."


def test_remove_text_not_found_raises():
    patch = {"op": "remove", "section": "subjective", "text": "does not appear anywhere"}
    with pytest.raises(InvalidPatchError):
        apply_note_patch(_note(), patch)


def test_remove_text_not_found_does_not_mutate_note():
    note = _note()
    patch = {"op": "remove", "section": "subjective", "text": "nonexistent phrase"}
    try:
        apply_note_patch(note, patch)
    except InvalidPatchError:
        pass
    assert note == BASE_NOTE  # input dict untouched


# ---- rewrite ---------------------------------------------------------------


def test_rewrite_replaces_entire_section():
    patch = {"op": "rewrite", "section": "plan", "text": "Refer to orthopedics."}
    result = apply_note_patch(_note(), patch)
    assert result["plan"] == "Refer to orthopedics."


def test_rewrite_preserves_other_sections_byte_identical():
    note = _note()
    patch = {"op": "rewrite", "section": "plan", "text": "Refer to orthopedics."}
    result = apply_note_patch(note, patch)
    assert result["subjective"] == note["subjective"]
    assert result["objective"] == note["objective"]
    assert result["assessment"] == note["assessment"]


# ---- move ---------------------------------------------------------------


def test_move_relocates_text_between_sections():
    note = _note(
        subjective="Patient reports knee pain. Denies fever.",
        objective="Mild swelling noted.",
    )
    patch = {
        "op": "move",
        "from_section": "subjective",
        "to_section": "objective",
        "text": "Denies fever.",
    }
    result = apply_note_patch(note, patch)
    assert "Denies fever." not in result["subjective"]
    assert result["subjective"] == "Patient reports knee pain."
    assert result["objective"] == "Mild swelling noted. Denies fever."


def test_move_preserves_untouched_sections_byte_identical():
    note = _note(
        subjective="Patient reports knee pain. Denies fever.",
    )
    patch = {
        "op": "move",
        "from_section": "subjective",
        "to_section": "objective",
        "text": "Denies fever.",
    }
    result = apply_note_patch(note, patch)
    assert result["assessment"] == note["assessment"]
    assert result["plan"] == note["plan"]


def test_move_same_section_raises():
    patch = {
        "op": "move",
        "from_section": "subjective",
        "to_section": "subjective",
        "text": "knee pain",
    }
    with pytest.raises(InvalidPatchError):
        apply_note_patch(_note(), patch)


def test_move_text_not_found_in_from_section_raises():
    patch = {
        "op": "move",
        "from_section": "subjective",
        "to_section": "objective",
        "text": "text that was never said",
    }
    with pytest.raises(InvalidPatchError):
        apply_note_patch(_note(), patch)


# ---- malformed / invalid patches ------------------------------------------


@pytest.mark.parametrize(
    "patch",
    [
        {},
        {"op": "unclear"},
        {"op": "delete", "section": "plan", "text": "x"},
        {"section": "plan", "text": "x"},  # missing op
        "not a dict",
        None,
        123,
    ],
)
def test_unrecognized_or_malformed_op_raises(patch):
    with pytest.raises(InvalidPatchError):
        apply_note_patch(_note(), patch)


@pytest.mark.parametrize(
    "patch",
    [
        {"op": "add", "section": "not_a_real_section", "text": "x"},
        {"op": "rewrite", "section": "icd_codes", "text": "x"},
        {"op": "remove", "section": None, "text": "x"},
    ],
)
def test_unknown_section_raises(patch):
    with pytest.raises(InvalidPatchError):
        apply_note_patch(_note(), patch)


@pytest.mark.parametrize(
    "patch",
    [
        {"op": "add", "section": "plan", "text": ""},
        {"op": "add", "section": "plan", "text": "   "},
        {"op": "add", "section": "plan"},  # missing text
        {"op": "add", "section": "plan", "text": 42},
        {"op": "rewrite", "section": "plan", "text": None},
    ],
)
def test_missing_or_empty_text_raises(patch):
    with pytest.raises(InvalidPatchError):
        apply_note_patch(_note(), patch)


def test_invalid_patch_never_mutates_input_note():
    note = _note()
    bad_patches = [
        {"op": "unclear"},
        {"op": "add", "section": "plan", "text": ""},
        {"op": "remove", "section": "plan", "text": "nonexistent"},
        {"op": "move", "from_section": "plan", "to_section": "plan", "text": "1."},
    ]
    for patch in bad_patches:
        try:
            apply_note_patch(note, patch)
        except InvalidPatchError:
            pass
    assert note == BASE_NOTE


# ---- content-preservation invariant, generalized --------------------------


@pytest.mark.parametrize(
    "patch",
    [
        {"op": "add", "section": "assessment", "text": "New finding."},
        {"op": "remove", "section": "subjective", "text": "for two weeks"},
        {"op": "rewrite", "section": "objective", "text": "Normal exam."},
        {
            "op": "move",
            "from_section": "assessment",
            "to_section": "plan",
            "text": "etiology unclear",
        },
    ],
)
def test_untouched_sections_always_survive_byte_identical(patch):
    """For any valid patch, every section name it does not name (as
    section/from_section/to_section) must be unchanged in the result."""
    note = _note()
    touched = {patch.get("section"), patch.get("from_section"), patch.get("to_section")}
    result = apply_note_patch(note, patch)
    for section in BASE_NOTE:
        if section not in touched:
            assert result[section] == note[section], f"{section} was unexpectedly changed"
