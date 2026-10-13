"""Persistencia local: historial de llamadas, contactos y poda."""

from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path

from sipclient.datastore import (
    DIRECTION_INCOMING,
    DIRECTION_OUTGOING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_MISSED,
    Database,
)


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.database = Database(Path(":memory:"))
        self.addCleanup(self.database.close)


class CallLogStoreTest(DatabaseTestCase):
    def test_add_and_list_newest_first(self) -> None:
        self.database.calls.add(DIRECTION_OUTGOING, "100", STATUS_COMPLETED, started_at=1000.0, duration=65)
        self.database.calls.add(DIRECTION_INCOMING, "101", STATUS_MISSED, started_at=2000.0)

        records = self.database.calls.list()
        self.assertEqual([record.number for record in records], ["101", "100"])
        self.assertEqual(records[0].status_label, "Perdida")
        self.assertEqual(records[1].direction_label, "Realizada")
        self.assertEqual(records[1].duration, 65)

    def test_filter_by_direction(self) -> None:
        self.database.calls.add(DIRECTION_OUTGOING, "100", STATUS_COMPLETED, started_at=1000.0)
        self.database.calls.add(DIRECTION_INCOMING, "200", STATUS_COMPLETED, started_at=2000.0)

        outgoing = self.database.calls.list(direction=DIRECTION_OUTGOING)
        incoming = self.database.calls.list(direction=DIRECTION_INCOMING)
        self.assertEqual([record.number for record in outgoing], ["100"])
        self.assertEqual([record.number for record in incoming], ["200"])
        self.assertEqual(len(self.database.calls.list(direction="nonsense")), 2)

    def test_search_matches_number_peer_and_account(self) -> None:
        self.database.calls.add(
            DIRECTION_OUTGOING, "100", STATUS_COMPLETED, peer="Ana", account="ext 100"
        )
        self.database.calls.add(DIRECTION_OUTGOING, "200", STATUS_COMPLETED, peer="Luis", account="ext 200")

        self.assertEqual([r.number for r in self.database.calls.list(search="luis")], ["200"])
        self.assertEqual([r.number for r in self.database.calls.list(search="ext 100")], ["100"])
        self.assertEqual(self.database.calls.list(search="99"), [])

    def test_update_status_and_duration(self) -> None:
        record_id = self.database.calls.add(
            DIRECTION_OUTGOING, "100", STATUS_FAILED, duration=0
        )
        self.database.calls.update(record_id, STATUS_COMPLETED, duration=42)
        record = self.database.calls.list()[0]
        self.assertEqual(record.status, STATUS_COMPLETED)
        self.assertEqual(record.duration, 42)

        self.database.calls.update(record_id, STATUS_FAILED)
        self.assertEqual(self.database.calls.list()[0].duration, 42)

    def test_cost_and_call_type_roundtrip(self) -> None:
        record_id = self.database.calls.add(
            DIRECTION_OUTGOING, "6500", STATUS_COMPLETED,
            call_type="Tigo a Tigo", cost=0.0,
        )
        self.database.calls.update(record_id, STATUS_COMPLETED, duration=65, cost=15.87)
        record = self.database.calls.list()[0]
        self.assertEqual(record.call_type, "Tigo a Tigo")
        self.assertEqual(record.cost, 15.87)
        self.assertEqual(record.duration, 65)

    def test_delete_and_clear(self) -> None:
        first = self.database.calls.add(DIRECTION_OUTGOING, "100", STATUS_COMPLETED)
        self.database.calls.add(DIRECTION_OUTGOING, "101", STATUS_COMPLETED)

        self.database.calls.delete(first)
        self.assertEqual(len(self.database.calls.list()), 1)

        self.assertEqual(self.database.calls.clear(), 1)
        self.assertEqual(self.database.calls.list(), [])
        self.assertEqual(self.database.calls.total_count(), 0)

    def test_counts(self) -> None:
        self.database.calls.add(DIRECTION_INCOMING, "100", STATUS_MISSED, started_at=time.time() - 86400 * 3)
        self.database.calls.add(DIRECTION_INCOMING, "101", STATUS_MISSED, started_at=time.time())
        self.database.calls.add(DIRECTION_OUTGOING, "102", STATUS_COMPLETED, started_at=time.time())

        self.assertEqual(self.database.calls.total_count(), 3)
        self.assertEqual(self.database.calls.missed_count(), 2)
        self.assertEqual(self.database.calls.missed_count(since=time.time() - 60), 1)

    def test_export_csv(self) -> None:
        self.database.calls.add(
            DIRECTION_OUTGOING, "100", STATUS_COMPLETED, peer="Ana", duration=30, account="ext 100"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "historial.csv"
            self.database.calls.export_csv(path, self.database.calls.list())

            with open(path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle, delimiter=";"))

        self.assertEqual(rows[0][2], "Direccion")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3], "100")
        self.assertEqual(rows[1][4], "Ana")
        self.assertEqual(rows[1][5], "30")
        self.assertEqual(rows[1][6], "Completada")

    def test_prune_keeps_recent(self) -> None:
        old = time.time() - 86400 * 30
        self.database.calls.add(DIRECTION_OUTGOING, "100", STATUS_COMPLETED, started_at=old)
        self.database.calls.add(DIRECTION_OUTGOING, "101", STATUS_COMPLETED, started_at=time.time())

        self.assertEqual(self.database.prune_calls(7), 1)
        self.assertEqual([r.number for r in self.database.calls.list()], ["101"])
        self.assertEqual(self.database.prune_calls(0), 0)

    def test_limit(self) -> None:
        for index in range(10):
            self.database.calls.add(DIRECTION_OUTGOING, str(index), STATUS_COMPLETED, started_at=float(index))
        self.assertEqual(len(self.database.calls.list(limit=3)), 3)


class ContactStoreTest(DatabaseTestCase):
    def test_add_and_list_favorites_first(self) -> None:
        self.database.contacts.add("Luis", "200")
        self.database.contacts.add("Ana", "100", favorite=True)

        contacts = self.database.contacts.list()
        self.assertEqual([contact.name for contact in contacts], ["Ana", "Luis"])
        self.assertTrue(contacts[0].favorite)
        self.assertEqual(self.database.contacts.count(), 2)

    def test_search(self) -> None:
        self.database.contacts.add("Ana", "100", "ana@empresa.com")
        self.database.contacts.add("Luis", "200")

        self.assertEqual([c.name for c in self.database.contacts.list(search="luis")], ["Luis"])
        self.assertEqual([c.name for c in self.database.contacts.list(search="empresa.com")], ["Ana"])
        self.assertEqual(self.database.contacts.list(search="zzz"), [])

    def test_update_and_delete(self) -> None:
        contact_id = self.database.contacts.add("Ana", "100")
        self.database.contacts.update(contact_id, "Ana Lopez", "101", "ana@x.com", True)

        contact = self.database.contacts.list()[0]
        self.assertEqual(contact.name, "Ana Lopez")
        self.assertEqual(contact.number, "101")
        self.assertEqual(contact.email, "ana@x.com")
        self.assertTrue(contact.favorite)

        self.database.contacts.delete(contact_id)
        self.assertEqual(self.database.contacts.count(), 0)

    def test_duplicate_pair_is_rejected(self) -> None:
        self.database.contacts.add("Ana", "100")
        with self.assertRaises(Exception):
            self.database.contacts.add("Ana", "100")

    def test_find_by_number(self) -> None:
        self.database.contacts.add("Ana", "100")
        self.assertIsNotNone(self.database.contacts.find_by_number("100"))
        self.assertIsNone(self.database.contacts.find_by_number("999"))

    def test_strips_whitespace(self) -> None:
        contact_id = self.database.contacts.add("  Ana  ", "  100  ", "  a@b.c  ")
        stored = self.database.contacts.list()[0]
        self.assertEqual(stored.name, "Ana")
        self.assertEqual(stored.number, "100")
        self.assertEqual(stored.email, "a@b.c")
        self.assertGreater(contact_id, 0)


class DatabaseFileTest(unittest.TestCase):
    def test_data_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "sipip.db"
            with Database(path) as database:
                database.calls.add(DIRECTION_OUTGOING, "100", STATUS_COMPLETED)
                database.contacts.add("Ana", "100")

            self.assertTrue(path.exists())
            with Database(path) as reopened:
                self.assertEqual(reopened.calls.total_count(), 1)
                self.assertEqual(reopened.contacts.count(), 1)


if __name__ == "__main__":
    unittest.main()
