"""Persistencia local: historial de llamadas y contactos (SQLite, %APPDATA%)."""

from __future__ import annotations

import csv
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .config import config_dir

DB_FILE_NAME = "sipip.db"

DIRECTION_OUTGOING = "outgoing"
DIRECTION_INCOMING = "incoming"

STATUS_COMPLETED = "completada"
STATUS_NO_ANSWER = "sin respuesta"
STATUS_REJECTED = "rechazada"
STATUS_FAILED = "fallida"
STATUS_MISSED = "perdida"
STATUS_ONGOING = "en curso"

DIRECTION_LABELS = {
    DIRECTION_OUTGOING: "Realizada",
    DIRECTION_INCOMING: "Recibida",
}
STATUS_LABELS = {
    STATUS_COMPLETED: "Completada",
    STATUS_NO_ANSWER: "Sin respuesta",
    STATUS_REJECTED: "Rechazada",
    STATUS_FAILED: "Fallida",
    STATUS_MISSED: "Perdida",
    STATUS_ONGOING: "En curso",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    direction   TEXT    NOT NULL,
    peer        TEXT    NOT NULL DEFAULT '',
    number      TEXT    NOT NULL DEFAULT '',
    started_at  REAL    NOT NULL,
    duration    INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL,
    account     TEXT    NOT NULL DEFAULT '',
    call_type   TEXT    NOT NULL DEFAULT '',
    cost        REAL    NOT NULL DEFAULT 0,
    notes       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_call_log_started ON call_log (started_at DESC);

CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    number     TEXT NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    favorite   INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    UNIQUE (name, number)
);
CREATE INDEX IF NOT EXISTS idx_contacts_favorite ON contacts (favorite DESC, name);
"""


@dataclass(frozen=True)
class CallRecord:
    id: int
    direction: str
    peer: str
    number: str
    started_at: float
    duration: int
    status: str
    account: str
    notes: str
    call_type: str = ""
    cost: float = 0.0

    @property
    def direction_label(self) -> str:
        return DIRECTION_LABELS.get(self.direction, self.direction)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    number: str
    email: str
    favorite: bool
    created_at: float


class Database:
    """Punto unico de acceso a los datos locales del softphone."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / DB_FILE_NAME)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(_SCHEMA)
        self._migrate()
        self._connection.commit()

    def _migrate(self) -> None:
        """Aplica columnas nuevas a bases creadas con un esquema anterior."""
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(call_log)")}
        if "call_type" not in columns:
            self._connection.execute(
                "ALTER TABLE call_log ADD COLUMN call_type TEXT NOT NULL DEFAULT ''"
            )
        if "cost" not in columns:
            self._connection.execute(
                "ALTER TABLE call_log ADD COLUMN cost REAL NOT NULL DEFAULT 0"
            )

    def close(self) -> None:
        try:
            self._connection.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    @property
    def calls(self) -> "CallLogStore":
        return CallLogStore(self._connection)

    @property
    def contacts(self) -> "ContactStore":
        return ContactStore(self._connection)

    def prune_calls(self, keep_days: int) -> int:
        if keep_days <= 0:
            return 0
        limit = time.time() - keep_days * 86400
        cursor = self._connection.execute("DELETE FROM call_log WHERE started_at < ?", (limit,))
        self._connection.commit()
        return cursor.rowcount or 0


class CallLogStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        direction: str,
        number: str,
        status: str,
        peer: str = "",
        duration: int = 0,
        account: str = "",
        notes: str = "",
        call_type: str = "",
        cost: float = 0.0,
        started_at: float | None = None,
    ) -> int:
        cursor = self._connection.execute(
            "INSERT INTO call_log (direction, peer, number, started_at, duration, status,"
            " account, call_type, cost, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                direction,
                peer,
                number,
                started_at if started_at is not None else time.time(),
                max(0, int(duration)),
                status,
                account,
                call_type,
                round(float(cost), 2),
                notes,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid or 0)

    def update(
        self,
        record_id: int,
        status: str,
        duration: int | None = None,
        cost: float | None = None,
    ) -> None:
        sets = ["status = ?"]
        parameters: list[object] = [status]
        if duration is not None:
            sets.append("duration = ?")
            parameters.append(max(0, int(duration)))
        if cost is not None:
            sets.append("cost = ?")
            parameters.append(round(float(cost), 2))
        parameters.append(record_id)
        self._connection.execute(
            f"UPDATE call_log SET {', '.join(sets)} WHERE id = ?", parameters
        )
        self._connection.commit()

    def delete(self, record_id: int) -> None:
        self._connection.execute("DELETE FROM call_log WHERE id = ?", (record_id,))
        self._connection.commit()

    def clear(self) -> int:
        cursor = self._connection.execute("DELETE FROM call_log")
        self._connection.commit()
        return cursor.rowcount or 0

    def list(
        self,
        direction: str | None = None,
        search: str = "",
        limit: int = 500,
    ) -> list[CallRecord]:
        clauses: list[str] = []
        parameters: list[object] = []
        if direction in (DIRECTION_INCOMING, DIRECTION_OUTGOING):
            clauses.append("direction = ?")
            parameters.append(direction)
        if search.strip():
            clauses.append("(number LIKE ? OR peer LIKE ? OR account LIKE ?)")
            term = f"%{search.strip()}%"
            parameters.extend([term, term, term])

        sql = "SELECT * FROM call_log"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC LIMIT ?"
        parameters.append(limit)

        rows = self._connection.execute(sql, parameters).fetchall()
        return [
            CallRecord(
                id=int(row["id"]),
                direction=str(row["direction"]),
                peer=str(row["peer"]),
                number=str(row["number"]),
                started_at=float(row["started_at"]),
                duration=int(row["duration"]),
                status=str(row["status"]),
                account=str(row["account"]),
                notes=str(row["notes"]),
                call_type=str(row["call_type"]),
                cost=float(row["cost"]),
            )
            for row in rows
        ]

    def missed_count(self, since: float | None = None) -> int:
        if since is None:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM call_log WHERE status = ?", (STATUS_MISSED,)
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM call_log WHERE status = ? AND started_at >= ?",
                (STATUS_MISSED, since),
            ).fetchone()
        return int(row["total"] if row else 0)

    def total_count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS total FROM call_log").fetchone()
        return int(row["total"] if row else 0)

    def export_csv(self, path: str | os.PathLike[str], records: list[CallRecord]) -> None:
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(
                ["Fecha", "Hora", "Direccion", "Numero", "Contacto", "Duracion (s)",
                 "Estado", "Cuenta", "Tarifa", "Costo"]
            )
            for record in records:
                stamp = time.localtime(record.started_at)
                writer.writerow(
                    [
                        time.strftime("%Y-%m-%d", stamp),
                        time.strftime("%H:%M:%S", stamp),
                        record.direction_label,
                        record.number,
                        record.peer,
                        record.duration,
                        record.status_label,
                        record.account,
                        record.call_type,
                        f"{record.cost:.2f}",
                    ]
                )


class ContactStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, name: str, number: str, email: str = "", favorite: bool = False) -> int:
        cursor = self._connection.execute(
            "INSERT INTO contacts (name, number, email, favorite, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (name.strip(), number.strip(), email.strip(), int(favorite), time.time()),
        )
        self._connection.commit()
        return int(cursor.lastrowid or 0)

    def update(
        self,
        contact_id: int,
        name: str,
        number: str,
        email: str = "",
        favorite: bool = False,
    ) -> None:
        self._connection.execute(
            "UPDATE contacts SET name = ?, number = ?, email = ?, favorite = ? WHERE id = ?",
            (name.strip(), number.strip(), email.strip(), int(favorite), contact_id),
        )
        self._connection.commit()

    def delete(self, contact_id: int) -> None:
        self._connection.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        self._connection.commit()

    def list(self, search: str = "") -> list[Contact]:
        sql = "SELECT * FROM contacts"
        parameters: list[object] = []
        if search.strip():
            sql += " WHERE name LIKE ? OR number LIKE ? OR email LIKE ?"
            term = f"%{search.strip()}%"
            parameters.extend([term, term, term])
        sql += " ORDER BY favorite DESC, name COLLATE NOCASE ASC"

        rows = self._connection.execute(sql, parameters).fetchall()
        return [
            Contact(
                id=int(row["id"]),
                name=str(row["name"]),
                number=str(row["number"]),
                email=str(row["email"]),
                favorite=bool(row["favorite"]),
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]

    def find_by_number(self, number: str) -> Contact | None:
        normalized = number.strip()
        for contact in self.list():
            if contact.number == normalized:
                return contact
        return None

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS total FROM contacts").fetchone()
        return int(row["total"] if row else 0)
