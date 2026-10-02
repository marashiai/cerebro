#!/usr/bin/env python3
# cerebro PTY MCP: ANSI stream parser -> normalized line buffer.
#
# Pure stdlib (no third-party deps), so it is independently unit-testable on a
# Python that does not have the `mcp` SDK installed. cerebro_mcp_server.py imports
# this; tests/cerebro_mcp_unit.py exercises it directly.
#
# Stateful incremental parser: raw bytes -> normalized text. Strips CSI/OSC/DCS/
# APC and other ESC sequences. `\r` returns to column 0 but does not clear the
# line immediately: the NEXT char decides -- `\r\n` is a CRLF line ending (keep
# the line, just start a new one), while `\r` followed by other content is an
# overwrite (a spinner frame replacing the previous one) and clears the line
# first. This is what lets a PTY's ONLCR output (which turns the child's `\n`
# into `\r\n`) NOT wipe every line, while still collapsing `\r`-spinner frames to
# their final state. `\n` starts a new line; `\b` drops the last char; `\t`
# expands to the next 8-col stop. Carries an in-escape state so a sequence split
# across two reads is handled, and an incremental UTF-8 decoder so a multi-byte
# char split across reads is handled. This is a minimal terminal model for
# chat-style TUIs (cerebro's native chat); it does not emulate
# alternate-screen cursor addressing.
from __future__ import annotations

import codecs
from typing import Optional

_MAX_LINES = 5000  # cap a session's normalized line buffer


class AnsiStreamParser:
    def __init__(self) -> None:
        self._dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._lines: list[str] = [""]
        self._cursor: int = 0          # total normalized chars ever emitted
        self._dropped: int = 0         # chars dropped from the front (cap)
        self._state = "ground"         # ground | esc | csi | osc | dcs | apc
        self._intermediate = 0         # chars left to swallow in an esc sequence
        self._at_col0 = False          # set by \r; cleared by the next char

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def num_lines(self) -> int:
        return len(self._lines)

    def _emit(self, s: str) -> None:
        if not s:
            return
        self._lines[-1] += s
        self._cursor += len(s)

    def _newline(self) -> None:
        self._lines.append("")
        self._cursor += 1  # the "\n" separator that text_since() reproduces
        self._at_col0 = False
        self._cap()

    def _cap(self) -> None:
        if len(self._lines) <= _MAX_LINES:
            return
        drop = len(self._lines) - _MAX_LINES
        # chars dropped = each dropped line's length + its trailing "\n" separator
        dropped_chars = sum(len(self._lines[i]) for i in range(drop)) + drop
        del self._lines[:drop]
        self._dropped += dropped_chars

    def _full_text(self) -> str:
        return "\n".join(self._lines)

    def text_since(self, cursor: int) -> str:
        """Normalized text appended at/after `cursor` (clamped to retained)."""
        full = self._full_text()
        base = self._cursor - len(full)  # absolute cursor at start of retained
        start = cursor - base
        if start < 0:
            start = 0
        return full[start:]

    def tail_lines(self, n: int) -> str:
        n = max(0, n)
        if not n:
            return ""
        # Drop a trailing empty *current* line (the active line, no content yet)
        # so the last N content lines are returned -- otherwise asking for the
        # last 2 lines of "a\nb\n" yields "b\n" (1 line + an empty line), not
        # "a\nb". A non-empty active line is kept (mid-line output is content).
        lines = self._lines
        if lines and lines[-1] == "":
            lines = lines[:-1]
        return "\n".join(lines[-n:])

    def feed(self, data: bytes) -> None:
        s = self._dec.decode(data)
        for c in s:
            self._consume(c)

    def _consume(self, c: str) -> None:
        # `\r` set _at_col0; the next char decides what it meant. \r\n is a CRLF
        # line ending (keep the line content); \r followed by other content is an
        # overwrite from column 0 (a spinner frame) -> clear the line first. See
        # the module docstring for why this matters with PTY ONLCR output.
        if self._at_col0:
            if c == "\n":
                self._at_col0 = False          # CRLF: \r was just the line ending
            elif c == "\r":
                return                          # consecutive \r: stay at column 0
            else:
                self._lines[-1] = ""            # overwrite: clear, then emit c
                self._at_col0 = False
        st = self._state
        if st == "ground":
            self._ground(c)
        elif st == "esc":
            self._esc(c)
        elif st == "csi":
            self._csi(c)
        elif st in ("osc", "dcs", "apc"):
            self._string_terminator(c)
        if self._intermediate > 0:
            self._intermediate -= 1

    def _ground(self, c: str) -> None:
        o = ord(c)
        if c == "\x1b":
            self._state = "esc"
        elif c == "\r":
            self._at_col0 = True         # return to column 0; next char decides
        elif c == "\n":
            self._newline()
        elif c == "\b":
            if self._lines[-1]:
                self._lines[-1] = self._lines[-1][:-1]
        elif c == "\t":
            col = len(self._lines[-1]) % 8
            self._emit(" " * (8 - col))
        elif o >= 0x20:
            self._emit(c)
        # other C0 controls: ignore

    def _esc(self, c: str) -> None:
        if c == "[":
            self._state = "csi"
        elif c == "]":
            self._state = "osc"
        elif c == "P":
            self._state = "dcs"
        elif c in ("_", "X", "^"):
            self._state = "apc"
        elif c in "()*+-./#":
            # charset designator / DEC line size: swallow one intermediate char
            self._state = "ground"
            self._intermediate = 1
        else:
            # single-char ESC sequence (ESC c, ESC =, ESC >, ESC D, ...): done
            self._state = "ground"

    def _csi(self, c: str) -> None:
        # All CSI sequences (colors, cursor moves, clear-screen, scroll, EL) are
        # discarded -- sufficient for chat-style output. Spinners clear via \r
        # (clear-on-CR) so EL is not needed for frame collapse.
        if 0x40 <= ord(c) <= 0x7E:
            self._state = "ground"      # final byte: sequence done
        # params (0x30-0x3f) and intermediates (0x20-0x2f): accumulate, stay in csi

    def _string_terminator(self, c: str) -> None:
        # OSC/DCS/APC end at BEL (\x07) or ST (ESC \).
        if c == "\x07":
            self._state = "ground"
        elif c == "\x1b":
            # ESC \ (ST) ends the string; an ESC here begins that terminator.
            self._state = "ground"
            # Swallow the following '\' if present: re-enter ground and let it be
            # consumed as a normal char (it prints, harmless) -- simpler than a
            # dedicated ST sub-state for a case that is vanishingly rare in chat.
        # else: swallow string body
