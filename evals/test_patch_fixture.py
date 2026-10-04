"""Offline ground truth, scope and timeout checks for the unified-diff patch fixture."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import patch_fixture
from fixtures import file_hashes

REFERENCE_APPLY = '''import re

from .errors import PatchError

HEADER = re.compile(r"@@ -(\\d+)(?:,(\\d+))? \\+(\\d+)(?:,(\\d+))? @@")
NO_NEWLINE = "\\\\ No newline at end of file"


def _lines(text):
    """Split text into [content, ending] pairs, where ending is LF, CRLF or empty."""
    result = []
    for line in re.findall(r"[^\\n]*\\n|[^\\n]+", text):
        ending = "\\r\\n" if line.endswith("\\r\\n") else "\\n" if line.endswith("\\n") else ""
        result.append([line[:len(line) - len(ending)], ending])
    return result


def _parse(patch):
    hunks = []
    for content, ending in _lines(patch):
        if content.startswith("@@"):
            match = HEADER.match(content)
            if not match:
                raise PatchError("malformed hunk header", len(hunks))
            ranges = [int(match.group(index)) if match.group(index) is not None else 1 for index in range(1, 5)]
            hunks.append({"ranges": ranges, "body": []})
        elif hunks:
            hunks[-1]["body"].append((content, ending))
    return hunks


def _entries(index, hunk, reverse):
    old_start, old_count, new_start, new_count = hunk["ranges"]
    entries = []
    for content, ending in hunk["body"]:
        if content == NO_NEWLINE and entries:
            entries[-1]["final"] = True
        elif content[:1] in (" ", "-", "+"):
            entries.append({"kind": content[0], "text": content[1:], "ending": ending, "final": False})
        else:
            raise PatchError("invalid hunk body line: %r" % content, index)
    if reverse:
        swap = {" ": " ", "-": "+", "+": "-"}
        for entry in entries:
            entry["kind"] = swap[entry["kind"]]
        old_start, old_count, new_start, new_count = new_start, new_count, old_start, old_count
    old = [entry for entry in entries if entry["kind"] != "+"]
    new = [entry for entry in entries if entry["kind"] != "-"]
    if len(old) != old_count or len(new) != new_count:
        raise PatchError("hunk body does not match its header counts", index)
    # A zero-length range names the line before the change.
    return old_start - 1 if old_count else old_start, entries, old


def apply_patch(text, patch, *, reverse=False):
    lines = _lines(text)
    output, cursor, offset = [], 0, 0
    for index, hunk in enumerate(_parse(patch)):
        base, entries, old = _entries(index, hunk, reverse)
        stated = base + offset
        candidates = sorted(range(cursor, len(lines) - len(old) + 1),
                            key=lambda start: (abs(start - stated), start < stated))
        position = next((start for start in candidates
                         if all(lines[start + number][0] == entry["text"] for number, entry in enumerate(old))),
                        None)
        if position is None:
            raise PatchError("hunk does not match the text", index)
        end = position + len(old)
        if old and end == len(lines) and old[-1]["final"] != (lines[-1][1] == ""):
            raise PatchError("hunk disagrees with the trailing newline of the text", index)
        output += lines[cursor:position]
        matched = iter(lines[position:end])
        for entry in entries:
            if entry["kind"] == " ":
                output.append(next(matched))
            elif entry["kind"] == "-":
                next(matched)
            else:
                output.append([entry["text"], "" if entry["final"] else entry["ending"]])
        cursor, offset = end, position - base
    output += lines[cursor:]
    return "".join(content + ending for content, ending in output)
'''

REGRESSION_TEST = '''import unittest

from patchkit.apply import apply_patch


class RegressionTests(unittest.TestCase):
    def test_reverse_restores_original(self):
        patch = "@@ -1,2 +1,2 @@\\n-a\\n+A\\n b\\n"
        self.assertEqual(apply_patch("A\\nb\\n", patch, reverse=True), "a\\nb\\n")
'''


def seeded(root):
    repo = root / 'repo'
    patch_fixture.seed(repo)
    return repo, file_hashes(repo)


def repair(repo):
    (repo / 'patchkit/apply.py').write_text(REFERENCE_APPLY)


class PatchFixtureTests(unittest.TestCase):
    def test_reference_passes_every_hidden_check_public_suite_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, before = seeded(Path(directory))
            repair(repo)
            (repo / 'tests/test_regressions.py').write_text(REGRESSION_TEST)
            started = time.monotonic()
            outcome = patch_fixture.grade(repo, before)
            print('\npatch reference grade: %.2fs' % (time.monotonic() - started), file=sys.stderr)
            self.assertTrue(outcome['correct'], outcome['checks'])
            self.assertEqual(set(outcome['checks']), {*patch_fixture.CHECK_NAMES, 'public_suite_pass'})
            self.assertEqual(outcome['changed_files'], ['patchkit/apply.py', 'tests/test_regressions.py'])

    def test_seed_fails_at_least_a_third_of_hidden_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, before = seeded(Path(directory))
            outcome = patch_fixture.grade(repo, before)
            failed = [name for name in patch_fixture.CHECK_NAMES if not outcome['checks'][name]]
            print('\npatch seed fails %d/%d: %s' % (len(failed), len(patch_fixture.CHECK_NAMES),
                                                   ', '.join(failed)), file=sys.stderr)
            self.assertGreaterEqual(len(failed), 8)
            self.assertFalse(outcome['checks']['public_suite_pass'])
            self.assertTrue(outcome['scope_pass'])

    def test_forbidden_supplied_and_symlink_edits_fail_scope(self):
        edits = {
            'patchkit/cli.py': lambda repo: (repo / 'patchkit/cli.py').write_text(
                patch_fixture.CLI.replace('    # TODO: add --fuzz and --reverse flags\n', '').replace(
                    '    parser.add_argument("patch")\n',
                    '    parser.add_argument("patch")\n    parser.add_argument("--reverse", action="store_true")\n')),
            'tests/test_smoke.py': lambda repo: (repo / 'tests/test_smoke.py').write_text(
                patch_fixture.TESTS.replace('self.assertEqual(caught.exception.hunk, 0)', 'pass')),
            'symlink': lambda repo: (repo / 'tests/test_regressions.py').symlink_to(repo / 'tests/test_smoke.py'),
        }
        for name, edit in edits.items():
            with self.subTest(edit=name), tempfile.TemporaryDirectory() as directory:
                repo, before = seeded(Path(directory))
                repair(repo)
                edit(repo)
                outcome = patch_fixture.grade(repo, before)
                self.assertTrue(outcome['functional_pass'], outcome['checks'])
                self.assertFalse(outcome['scope_pass'])
                self.assertFalse(outcome['correct'])

    def test_hanging_candidate_is_graded_false_within_timeout(self):
        hanging = REFERENCE_APPLY.replace('        stated = base + offset\n',
                                          '        stated = base + offset\n        while True:\n            pass\n')
        self.assertNotEqual(hanging, REFERENCE_APPLY)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(patch_fixture, 'CHECK_SECONDS', 0.2), \
                patch.object(patch_fixture, 'CHECKS_SECONDS', 8), \
                patch.object(patch_fixture, 'SUITE_SECONDS', 2):
            repo, before = seeded(Path(directory))
            (repo / 'patchkit/apply.py').write_text(hanging)
            started = time.monotonic()
            outcome = patch_fixture.grade(repo, before)
            self.assertLess(time.monotonic() - started, 12)
            self.assertFalse(outcome['functional_pass'])
            self.assertFalse(outcome['checks']['public_suite_pass'])
            self.assertFalse(outcome['checks']['difflib_generated_cases'])
            self.assertTrue(outcome['checks']['empty_patch_identity'])
            self.assertTrue(outcome['scope_pass'])

if __name__ == '__main__':
    unittest.main()
