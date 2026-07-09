"""Incremental parser for the tagged-section note stream.

Pure and I/O-free by design: feed() text chunks in, get events out. That
isolation exists so the trickiest part of streaming — tags split across
arbitrary chunk boundaries — is covered by fast deterministic unit tests
(tests/test_stream_parser.py feeds a whole note one character at a time).

Events emitted:
    ("section", name, text)      incremental text for a SOAP section
    ("icd_codes", list)          parsed codes, once the tag completes
    ("no_clinical_content", None)

The icd_codes section is buffered rather than streamed: partial JSON is
useless to the client, and malformed JSON degrades to [] (the note is still
delivered; codes are additive).
"""

import json

SOAP_SECTIONS = ("subjective", "objective", "assessment", "plan")
_ALL_SECTIONS = SOAP_SECTIONS + ("icd_codes",)
_SENTINEL_TAG = "no_clinical_content/"
# Longest legitimate tag body; anything longer after '<' is content, not a tag.
_MAX_TAG_LEN = max(len(t) for t in (*_ALL_SECTIONS, _SENTINEL_TAG)) + 2

Event = tuple[str, str | None, object]


class TaggedStreamParser:
    def __init__(self) -> None:
        self._buffer = ""
        self._section: str | None = None
        self._icd_raw = ""

    def feed(self, chunk: str) -> list[Event]:
        self._buffer += chunk
        events: list[Event] = []
        while True:
            if self._section is None:
                if not self._consume_next_open_tag(events):
                    break
            else:
                if not self._consume_section_text(events):
                    break
        return events

    def close(self) -> list[Event]:
        """Flush anything left if the stream ended mid-section (e.g. the
        model was cut off): partial content still reaches the UI."""
        events: list[Event] = []
        if self._section is not None and self._buffer:
            self._emit_text(events, self._buffer)
            self._buffer = ""
        if self._section == "icd_codes":
            self._emit_icd(events)
        self._section = None
        return events

    # ---- internals -----------------------------------------------------

    def _consume_next_open_tag(self, events: list[Event]) -> bool:
        """Advance past inter-section noise to the next opening tag.
        Returns False when more input is needed."""
        lt = self._buffer.find("<")
        if lt == -1:
            self._buffer = ""  # prose outside any tag is discarded by contract
            return False
        self._buffer = self._buffer[lt:]
        gt = self._buffer.find(">")
        if gt == -1:
            if len(self._buffer) > _MAX_TAG_LEN:
                self._buffer = self._buffer[1:]  # '<' was content, not a tag
                return True
            return False  # possibly a partial tag — wait for more
        tag = self._buffer[1:gt]
        self._buffer = self._buffer[gt + 1 :]
        if tag == _SENTINEL_TAG:
            events.append(("no_clinical_content", None, None))
        elif tag in _ALL_SECTIONS:
            self._section = tag
            if tag == "icd_codes":
                self._icd_raw = ""
        # unknown tags are skipped silently
        return True

    def _consume_section_text(self, events: list[Event]) -> bool:
        """Emit text inside the current section up to its closing tag.
        Returns False when more input is needed."""
        closing = f"</{self._section}>"
        idx = self._buffer.find(closing)
        if idx != -1:
            self._emit_text(events, self._buffer[:idx])
            self._buffer = self._buffer[idx + len(closing) :]
            if self._section == "icd_codes":
                self._emit_icd(events)
            self._section = None
            return True
        # No complete closing tag yet: emit everything except a trailing
        # run that could be the *start* of the closing tag (the
        # split-across-chunks case), which we hold back.
        safe_len = len(self._buffer) - self._holdback_len(closing)
        if safe_len > 0:
            self._emit_text(events, self._buffer[:safe_len])
            self._buffer = self._buffer[safe_len:]
        return False

    def _holdback_len(self, closing: str) -> int:
        """Length of the longest buffer suffix that is a prefix of the
        closing tag — the only bytes that might not be section text."""
        for k in range(min(len(self._buffer), len(closing) - 1), 0, -1):
            if self._buffer.endswith(closing[:k]):
                return k
        return 0

    def _emit_text(self, events: list[Event], text: str) -> None:
        if not text:
            return
        if self._section == "icd_codes":
            self._icd_raw += text
        else:
            events.append(("section", self._section, text))

    def _emit_icd(self, events: list[Event]) -> None:
        try:
            parsed = json.loads(self._icd_raw)
            codes = [
                {"code": str(c["code"]), "description": str(c.get("description", ""))}
                for c in parsed
                if isinstance(c, dict) and "code" in c
            ]
        except (json.JSONDecodeError, TypeError):
            codes = []  # note still delivered; codes are additive
        events.append(("icd_codes", None, codes))
        self._icd_raw = ""
