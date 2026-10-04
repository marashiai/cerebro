#!/usr/bin/env python3
"""Unit tests for the PTY MCP ANSI stream parser (lib/python/cerebro_mcp_ansi.py).

Pure stdlib (no third-party deps), so this runs on the suite's baseline
python3 (3.9 OK) -- Pattern A, always runs. The parser is the part worth
pinning: it turns raw PTY bytes into the normalized text the MCP server's
cerebro_wait/cerebro_read return, so a regression here (a missed ANSI strip, a broken
spinner collapse, a miscounted tail) would surface as garbled event text to
every controller.

Run by tests/run.sh (Pattern A block, like improve_test.py)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib", "python"))

from cerebro_mcp_ansi import AnsiStreamParser  # noqa: E402


def _feed(parser, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    parser.feed(data)
    return parser


def main():
    # --- ANSI stripping: CSI color, OSC title, cursor moves, erase-line ---
    p = AnsiStreamParser()
    _feed(p, "\x1b[31mred\x1b[0m\x1b]0;title\x07\x1b[2;3Hmove\x1b[2K")
    assert p.text_since(0) == "redmove", repr(p.text_since(0))

    # --- an ESC sequence split across two feeds is still swallowed ---
    p = AnsiStreamParser()
    _feed(p, "\x1b"); _feed(p, "[31m"); _feed(p, "red")
    assert p.text_since(0) == "red", repr(p.text_since(0))

    # --- a single-char ESC sequence (ESC c reset) is swallowed ---
    p = AnsiStreamParser()
    _feed(p, "\x1bc"); _feed(p, "x")
    assert p.text_since(0) == "x", repr(p.text_since(0))

    # --- DCS body is swallowed, BEL-terminated ---
    p = AnsiStreamParser()
    _feed(p, "\x1bPqbinary\x07after")
    assert p.text_since(0) == "after", repr(p.text_since(0))

    # --- \r clear-on-CR collapses spinner frames to their final state ---
    p = AnsiStreamParser()
    _feed(p, "\r-\r\\\r|\r/\rdone\n")
    assert p.text_since(0) == "done\n", repr(p.text_since(0))

    # --- CRLF line endings keep their line content (PTY ONLCR turns \n into
    # \r\n; \r must NOT wipe the line when followed by \n) ---
    p = AnsiStreamParser()
    _feed(p, "line1\r\nline2\r\nline3\r\n")
    assert p.text_since(0) == "line1\nline2\nline3\n", repr(p.text_since(0))
    # mixed: a CRLF line, then a \r-spinner on the next line, then CRLF again
    p = AnsiStreamParser()
    _feed(p, "ok\r\n\rA\rB\rfinal\r\n")
    assert p.text_since(0) == "ok\nfinal\n", repr(p.text_since(0))
    # a bare trailing \r (no following char yet) does not wipe what came before
    p = AnsiStreamParser()
    _feed(p, "half\r")
    assert p.text_since(0) == "half", repr(p.text_since(0))

    # --- \b drops the last char of the current line ---
    p = AnsiStreamParser()
    _feed(p, "oops\b\b ok\n")
    # "oops" -> \b -> "oop" -> \b -> "oo" -> " ok" -> "oo ok"
    assert p.text_since(0) == "oo ok\n", repr(p.text_since(0))

    # --- \t expands to the next 8-col tab stop ---
    p = AnsiStreamParser()
    _feed(p, "a\tb\tc\n")
    # "a"(col1) -> tab to col8 (7 spaces) -> "b"(col9) -> tab to col16 (7 sp) -> "c"
    assert p.text_since(0) == "a" + " " * 7 + "b" + " " * 7 + "c\n", repr(p.text_since(0))

    # --- incremental reads via cursor: text_since returns only the new part ---
    p = AnsiStreamParser()
    _feed(p, "first\n")
    c1 = p.cursor
    assert p.text_since(0) == "first\n"
    _feed(p, "second\n")
    assert p.text_since(c1) == "second\n", repr(p.text_since(c1))
    assert p.text_since(0) == "first\nsecond\n"

    # --- tail_lines returns the last N content lines (trailing empty dropped) ---
    p = AnsiStreamParser()
    _feed(p, "l1\nl2\nl3\n")
    assert p.tail_lines(2) == "l2\nl3", repr(p.tail_lines(2))
    assert p.tail_lines(1) == "l3", repr(p.tail_lines(1))
    assert p.tail_lines(99) == "l1\nl2\nl3", repr(p.tail_lines(99))
    assert p.tail_lines(0) == ""

    # --- a mid-line (no trailing \n) active line is kept as content ---
    p = AnsiStreamParser()
    _feed(p, "l1\nl2\npartial")
    assert p.tail_lines(2) == "l2\npartial", repr(p.tail_lines(2))
    assert p.tail_lines(1) == "partial", repr(p.tail_lines(1))

    # --- split UTF-8 multi-byte char across two feeds decodes correctly ---
    p = AnsiStreamParser()
    _feed(p, b"\xc3"); _feed(p, b"\xa9!")  # U+00E9 (e-acute) then '!'
    assert p.text_since(0) == "é!", repr(p.text_since(0))

    # --- buffer cap: feeding >5000 lines drops the oldest, keeps the cap ---
    p = AnsiStreamParser()
    for i in range(5200):
        _feed(p, "x%d\n" % i)
    assert p.num_lines == 5000, p.num_lines          # capped at 5000
    # 5201 lines total -> 201 dropped -> oldest retained is "x201"
    assert p.tail_lines(1) == "x5199", repr(p.tail_lines(1))
    assert p.text_since(0).startswith("x201\n"), p.text_since(0)[:12]
    assert p.text_since(0).endswith("x5199\n"), p.text_since(0)[-12:]
    assert p.cursor > 0                              # monotonic / sane

    # --- a fresh parser has one (empty) current line ---
    assert AnsiStreamParser().num_lines == 1

    print("all checks passed")


if __name__ == "__main__":
    main()