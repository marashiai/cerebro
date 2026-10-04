"""Offline ground truth, scope and timeout checks for the inventory reservation fixture."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import inventory_fixture
from native import TestJournal
from runtime import file_hashes

REFERENCE_STORE = '''import json
from pathlib import Path


def load_events(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def append_event(path, event):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\\n")
'''

REFERENCE_LEDGER = '''import math

from .store import append_event, load_events


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(name + " must be a nonempty string")


def _quantity(value):
    if type(value) is not int or value <= 0:
        raise ValueError("quantity must be a positive int")


def _ttl(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("ttl must be a positive finite number")


class Ledger:
    def __init__(self, path):
        self.path = path
        self.stock = {}
        self.reservations = {}
        self.requests = {}
        for event in load_events(path):
            self._apply(event)

    def _record(self, event):
        append_event(self.path, event)
        self._apply(event)

    def _apply(self, event):
        kind = event["type"]
        if kind == "stock":
            self.stock[event["sku"]] = self.stock.get(event["sku"], 0) + event["quantity"]
        elif kind == "reserved":
            identifier = "r%d" % (len(self.reservations) + 1)
            self.reservations[identifier] = {
                "id": identifier, "request_id": event["request_id"], "sku": event["sku"],
                "quantity": event["quantity"], "status": "active", "expires_at": event["expires_at"]}
            self.requests[event["request_id"]] = identifier
        else:
            reservation = self.reservations[event["id"]]
            reservation["status"] = kind
            if kind == "committed":
                self.stock[reservation["sku"]] -= reservation["quantity"]

    def _sku(self, sku):
        _text(sku, "sku")
        if sku not in self.stock:
            raise KeyError(sku)

    def _find(self, identifier):
        if identifier not in self.reservations:
            raise KeyError(identifier)
        return self.reservations[identifier]

    def add_stock(self, sku, quantity):
        _text(sku, "sku")
        _quantity(quantity)
        self._record({"type": "stock", "sku": sku, "quantity": quantity})

    def reserve(self, request_id, sku, quantity, *, now, ttl):
        _text(request_id, "request_id")
        _text(sku, "sku")
        _quantity(quantity)
        _ttl(ttl)
        if request_id in self.requests:
            existing = self.reservations[self.requests[request_id]]
            if (existing["sku"], existing["quantity"]) != (sku, quantity):
                raise ValueError("request_id was used for a different reservation")
            return existing["id"]
        self._sku(sku)
        self.expire(now=now)
        if quantity > self.available(sku):
            raise ValueError("insufficient stock")
        self._record({"type": "reserved", "request_id": request_id, "sku": sku,
                      "quantity": quantity, "expires_at": now + ttl})
        return "r%d" % len(self.reservations)

    def release(self, reservation_id):
        if self._find(reservation_id)["status"] != "active":
            return False
        self._record({"type": "released", "id": reservation_id})
        return True

    def commit(self, reservation_id, *, now):
        reservation = self._find(reservation_id)
        if reservation["status"] != "active":
            return False
        if now >= reservation["expires_at"]:
            self._record({"type": "expired", "id": reservation_id})
            return False
        self._record({"type": "committed", "id": reservation_id})
        return True

    def expire(self, *, now):
        stale = [identifier for identifier, reservation in self.reservations.items()
                 if reservation["status"] == "active" and now >= reservation["expires_at"]]
        for identifier in stale:
            self._record({"type": "expired", "id": identifier})
        return len(stale)

    def on_hand(self, sku):
        self._sku(sku)
        return self.stock[sku]

    def available(self, sku):
        self._sku(sku)
        held = sum(reservation["quantity"] for reservation in self.reservations.values()
                   if reservation["sku"] == sku and reservation["status"] == "active")
        return self.stock[sku] - held

    def reservation(self, reservation_id):
        return dict(self._find(reservation_id))

    def skus(self):
        return sorted(self.stock)
'''

REGRESSION_TEST = '''import tempfile
import unittest
from pathlib import Path

from inventory.ledger import Ledger


class RegressionTests(unittest.TestCase):
    def test_reopen_keeps_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            ledger = Ledger(path)
            ledger.add_stock("a", 2)
            ledger.reserve("q", "a", 1, now=0, ttl=5)
            self.assertEqual(Ledger(path).available("a"), 1)
'''


def seeded(root):
    repo = root / 'repo'
    inventory_fixture.seed(repo)
    return repo, file_hashes(repo)


def repair(repo):
    (repo / 'inventory/ledger.py').write_text(REFERENCE_LEDGER)
    (repo / 'inventory/store.py').write_text(REFERENCE_STORE)


class InventoryFixtureTests(unittest.TestCase):
    def test_reference_passes_every_hidden_check_public_suite_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, before = seeded(Path(directory))
            repair(repo)
            (repo / 'tests/test_regressions.py').write_text(REGRESSION_TEST)
            started = time.monotonic()
            outcome = inventory_fixture.grade(repo, before)
            print('\ninventory reference grade: %.2fs' % (time.monotonic() - started), file=sys.stderr)
            self.assertTrue(outcome['correct'], outcome['checks'])
            self.assertEqual(set(outcome['checks']), {*inventory_fixture.CHECK_NAMES, 'public_suite_pass'})
            self.assertEqual(outcome['changed_files'], sorted(inventory_fixture.ALLOWED))

    def test_seed_fails_at_least_a_third_of_hidden_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, before = seeded(Path(directory))
            outcome = inventory_fixture.grade(repo, before)
            failed = [name for name in inventory_fixture.CHECK_NAMES if not outcome['checks'][name]]
            print('\ninventory seed fails %d/%d: %s' % (len(failed), len(inventory_fixture.CHECK_NAMES),
                                                       ', '.join(failed)), file=sys.stderr)
            self.assertGreaterEqual(len(failed), 7)
            self.assertFalse(outcome['checks']['public_suite_pass'])
            self.assertTrue(outcome['scope_pass'])

    def test_forbidden_supplied_and_symlink_edits_fail_scope(self):
        edits = {
            'inventory/pricing.py': lambda repo: (repo / 'inventory/pricing.py').write_text(
                'from decimal import Decimal\n\n\ndef unit_price(total, quantity, currency="EUR"):\n'
                '    return Decimal(total) / quantity\n'),
            'tests/test_smoke.py': lambda repo: (repo / 'tests/test_smoke.py').write_text(
                inventory_fixture.TESTS.replace('self.assertEqual(ledger.available("apple"), 3)', 'pass')),
            'symlink': lambda repo: (repo / 'tests/test_regressions.py').symlink_to(repo / 'tests/test_smoke.py'),
        }
        for name, edit in edits.items():
            with self.subTest(edit=name), tempfile.TemporaryDirectory() as directory:
                repo, before = seeded(Path(directory))
                repair(repo)
                edit(repo)
                outcome = inventory_fixture.grade(repo, before)
                self.assertTrue(outcome['functional_pass'], outcome['checks'])
                self.assertFalse(outcome['scope_pass'])
                self.assertFalse(outcome['correct'])

    def test_hanging_candidate_is_graded_false_within_timeout(self):
        hanging = REFERENCE_LEDGER.replace(
            '    def reserve(self, request_id, sku, quantity, *, now, ttl):\n',
            '    def reserve(self, request_id, sku, quantity, *, now, ttl):\n        while True:\n            pass\n')
        self.assertNotEqual(hanging, REFERENCE_LEDGER)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(inventory_fixture, 'CHECK_SECONDS', 0.2), \
                patch.object(inventory_fixture, 'CHECKS_SECONDS', 8), \
                patch.object(inventory_fixture, 'SUITE_SECONDS', 2):
            repo, before = seeded(Path(directory))
            repair(repo)
            (repo / 'inventory/ledger.py').write_text(hanging)
            started = time.monotonic()
            outcome = inventory_fixture.grade(repo, before)
            self.assertLess(time.monotonic() - started, 12)
            self.assertFalse(outcome['functional_pass'])
            self.assertFalse(outcome['checks']['public_suite_pass'])
            self.assertFalse(outcome['checks']['replay_identical'])
            self.assertTrue(outcome['checks']['skus_sorted'])
            self.assertTrue(outcome['scope_pass'])

    def test_public_receipt_binds_supplied_tests_in_subdirectory_to_final_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo, _ = seeded(root)
            repair(repo)
            inventory_fixture.profile(root)
            profile = json.loads((root / 'receipt-profile.json').read_text())
            self.assertEqual(profile['test_file'], 'tests/test_smoke.py')
            self.assertEqual(profile['test_identities'], list(inventory_fixture.IDENTITIES))
            journal = TestJournal(root, 'inventory-worker', 'execute')
            environment = {**os.environ, **journal.environment(), 'CEREBRO_CHILD_ROLE': 'execute'}
            run = subprocess.run([sys.executable, '-m', 'unittest', '-v'], cwd=repo,
                                 capture_output=True, text=True, env=environment)
            self.assertEqual(run.returncode, 0, run.stderr)
            receipts = journal.take(repo, 'inventory-thread')
            self.assertEqual(len(receipts), 1)
            self.assertTrue(receipts[0]['passed'])
            self.assertEqual(receipts[0]['source'], file_hashes(repo))


if __name__ == '__main__':
    unittest.main()
