"""Unit tests for the incremental tagged-section parser.

The chunk-boundary test is the load-bearing one: it feeds a full note ONE
CHARACTER AT A TIME and asserts the reassembled output is byte-identical to
feeding it whole — i.e. no chunking pattern the vendor produces can corrupt
a note.
"""

import json

from app.stream_parser import TaggedStreamParser

FULL_NOTE = (
    "<subjective>Knee pain for two weeks, worse on stairs.</subjective>\n"
    "<objective>Mild effusion. ROM 0-115.</objective>\n"
    "<assessment>Right knee osteoarthritis (M17.11).</assessment>\n"
    "<plan>1. X-ray. 2. NSAIDs. 3. Follow up in 6 weeks.</plan>\n"
    '<icd_codes>[{"code": "M17.11", "description": "Unilateral primary '
    'osteoarthritis, right knee"}]</icd_codes>'
)


def collect(events):
    """Reassemble parser events into {section: text} + codes + flags."""
    sections: dict[str, str] = {}
    codes = None
    sentinel = False
    for name, section, data in events:
        if name == "section":
            sections[section] = sections.get(section, "") + data
        elif name == "icd_codes":
            codes = data
        elif name == "no_clinical_content":
            sentinel = True
    return sections, codes, sentinel


def run_parser(text: str, chunk_size: int):
    parser = TaggedStreamParser()
    events = []
    for i in range(0, len(text), chunk_size):
        events.extend(parser.feed(text[i : i + chunk_size]))
    events.extend(parser.close())
    return collect(events)


def test_whole_note_single_chunk():
    sections, codes, sentinel = run_parser(FULL_NOTE, chunk_size=len(FULL_NOTE))
    assert sections["subjective"] == "Knee pain for two weeks, worse on stairs."
    assert sections["objective"] == "Mild effusion. ROM 0-115."
    assert "M17.11" in sections["assessment"]
    assert sections["plan"].startswith("1. X-ray.")
    assert codes == [
        {"code": "M17.11", "description": "Unilateral primary osteoarthritis, right knee"}
    ]
    assert not sentinel


def test_char_by_char_equals_single_chunk():
    # Every possible tag-split-across-chunks case in one test.
    assert run_parser(FULL_NOTE, chunk_size=1) == run_parser(FULL_NOTE, len(FULL_NOTE))


def test_awkward_chunk_sizes_all_agree():
    reference = run_parser(FULL_NOTE, len(FULL_NOTE))
    for size in (2, 3, 5, 7, 13):
        assert run_parser(FULL_NOTE, size) == reference, f"chunk_size={size}"


def test_no_clinical_content_sentinel():
    sections, codes, sentinel = run_parser("<no_clinical_content/>", 1)
    assert sentinel
    assert sections == {}
    assert codes is None


def test_malformed_icd_json_degrades_to_empty_list():
    text = "<subjective>ok</subjective><icd_codes>[{broken json</icd_codes>"
    sections, codes, _ = run_parser(text, 4)
    assert sections["subjective"] == "ok"
    assert codes == []  # note still delivered; codes are additive


def test_unterminated_section_flushes_on_close():
    parser = TaggedStreamParser()
    events = parser.feed("<plan>Start physical therapy")
    events += parser.close()
    sections, _, _ = collect(events)
    assert sections["plan"] == "Start physical therapy"


def test_prose_between_sections_is_discarded():
    text = "preamble<subjective>a</subjective> noise <plan>b</plan>trailing"
    sections, _, _ = run_parser(text, 3)
    assert sections == {"subjective": "a", "plan": "b"}


def test_literal_angle_bracket_in_content_survives():
    text = "<objective>BP <html-ish 120/80, temp 98.6</objective>"
    sections, _, _ = run_parser(text, 5)
    assert sections["objective"] == "BP <html-ish 120/80, temp 98.6"


def test_icd_codes_are_buffered_not_streamed():
    parser = TaggedStreamParser()
    partial = parser.feed('<icd_codes>[{"code": "I10"')
    assert partial == []  # nothing until the tag completes
    rest = parser.feed(', "description": "HTN"}]</icd_codes>')
    _, codes, _ = collect(rest)
    assert codes == [{"code": "I10", "description": "HTN"}]
