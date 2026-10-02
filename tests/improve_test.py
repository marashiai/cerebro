#!/usr/bin/env python3
"""Focused tests for the self-improvement utility and history helpers."""

import json
import os
import shutil
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib", "python"))

from improve_history_lib import (  # noqa: E402
    add_run,
    fast_count_since_last_meta,
    last_timestamp,
)
from improve_utility import compute_utility  # noqa: E402


def main():
    fixture = os.path.join(ROOT, "tests", "fixtures", "improve", "home")
    with tempfile.TemporaryDirectory() as temporary:
        home = os.path.join(temporary, "home")
        shutil.copytree(fixture, home)

        # Four real-schema `kind:user` prompts, one completed review, one
        # explicit correction, and three failures among four child outcomes
        # (including Claude/Pi errors and a pair-pump stall):
        # .4*.25 + .3*.75 + .3*.75 = .55. The incomplete assistant log is not
        # guessed to be either success or failure.
        utility = compute_utility(home)
        assert utility == 0.55, utility
        assert compute_utility(home, "2026-01-03T00:00:00Z") == 0.5

        history = os.path.join(home, "improvement-history.json")
        assert last_timestamp(history) == ""
        first = add_run(
            history, "fast", "2026-02-01T00:00:00Z", 2,
            "HILL CLIMB: ISSUES FOUND", "0.55",
        )
        second = add_run(
            history, "fast", "2026-02-02T00:00:00Z", 0,
            "HILL CLIMB: NO CHANGES RECOMMENDED", "0.8",
        )
        assert (first, second) == ("improve-0001", "improve-0002")
        assert last_timestamp(history) == "2026-02-02T00:00:00Z"
        assert fast_count_since_last_meta(history) == 2
        add_run(
            history, "meta", "2026-02-02T00:01:00Z", 1,
            "META CLIMB: ISSUES FOUND", "0.8",
        )
        assert fast_count_since_last_meta(history) == 0
        with open(history, encoding="utf-8") as handle:
            data = json.load(handle)
        assert [run["type"] for run in data["runs"]] == ["fast", "fast", "meta"]
        unsupported = {"parent", "accepted_count", "delta_u", "selections"}
        assert not unsupported.intersection(data["runs"][0])

        with open(history, "w", encoding="utf-8") as handle:
            handle.write("not json\n")
        try:
            last_timestamp(history)
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError("corrupt history was silently treated as empty")

    print("all checks passed")


if __name__ == "__main__":
    main()
