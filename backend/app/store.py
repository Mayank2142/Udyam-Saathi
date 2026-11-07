from collections.abc import Callable
from pathlib import Path
from threading import Lock

from .domain import Case


class RevisionConflict(Exception):
    pass


class OrderExists(Exception):
    pass


class PreviewStore:
    """Ephemeral, explicitly enabled local preview."""

    def __init__(self):
        self._cases: dict[str, Case] = {}
        self._lock = Lock()

    def read(self, owner: str) -> Case | None:
        with self._lock:
            return self._cases.get(owner)

    def change(self, owner: str, update: Callable[[Case | None], Case]) -> Case:
        with self._lock:
            case = update(self._cases.get(owner))
            self._cases[owner] = case
            return case


class PostgresStore:
    def __init__(self, url: str):
        import psycopg

        self._connect = lambda: psycopg.connect(url, connect_timeout=5)
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(5577001)")
                for migration in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")):
                    cursor.execute(migration.read_text(encoding="utf-8"))

    def read(self, owner: str) -> Case | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM owner_cases WHERE owner_key = %s", (owner,)).fetchone()
        return Case.model_validate(row[0]) if row and row[0] else None

    def claim(self, guest: str, owner: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM owner_cases WHERE owner_key = %s FOR UPDATE", (guest,),
            ).fetchone()
            if not row or not row[0]:
                return
            inserted = conn.execute(
                "INSERT INTO owner_cases(owner_key, payload) SELECT %s, payload FROM owner_cases "
                "WHERE owner_key = %s ON CONFLICT DO NOTHING RETURNING owner_key", (owner, guest),
            ).fetchone()
            if inserted:
                conn.execute("UPDATE quote_revisions SET owner_key = %s WHERE owner_key = %s", (owner, guest))
                conn.execute("DELETE FROM owner_cases WHERE owner_key = %s", (guest,))

    def change(self, owner: str, update: Callable[[Case | None], Case]) -> Case:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            conn.execute("INSERT INTO owner_cases (owner_key) VALUES (%s) ON CONFLICT DO NOTHING", (owner,))
            payload = conn.execute(
                "SELECT payload FROM owner_cases WHERE owner_key = %s FOR UPDATE", (owner,),
            ).fetchone()[0]
            previous = Case.model_validate(payload) if payload else None
            case = update(previous)
            if previous and case.revision != previous.revision and case.quote != previous.quote:
                if conn.execute(
                    "SELECT 1 FROM activation_cases WHERE owner_key = %s AND payload ? 'order'", (owner,),
                ).fetchone():
                    raise OrderExists()
            if case.quote and (not previous or not previous.quote or previous.quote.id != case.quote.id):
                conn.execute(
                    "INSERT INTO quote_revisions (id, owner_key, case_revision, payload) VALUES (%s, %s, %s, %s)",
                    (case.quote.id, owner, case.revision, Jsonb(case.quote.model_dump(mode="json"))),
                )
            elif case.quote and case.accepted_at:
                conn.execute(
                    "UPDATE quote_revisions SET accepted_at = %s WHERE id = %s AND accepted_at IS NULL",
                    (case.accepted_at, case.quote.id),
                )
            conn.execute(
                "UPDATE owner_cases SET payload = %s, updated_at = now() WHERE owner_key = %s",
                (Jsonb(case.model_dump(mode="json")), owner),
            )
        return case
