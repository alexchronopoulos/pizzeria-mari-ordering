from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path


class SlotUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConfirmedOrder:
    id: str
    confirmation_code: str
    service_at: str


class SQLiteCapacityStore:
    """Local prototype store.

    BEGIN IMMEDIATE serializes writers so two checkouts cannot both claim the
    final pizza capacity. Production will use the same conditional operation in
    a DynamoDB transaction.
    """

    def __init__(self, database_path: str):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS slot_capacity (
                    service_at TEXT PRIMARY KEY,
                    confirmed_pizzas INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    confirmation_code TEXT NOT NULL UNIQUE,
                    service_at TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    customer_email TEXT NOT NULL,
                    customer_phone TEXT,
                    notes TEXT,
                    tip_cents INTEGER NOT NULL,
                    subtotal_cents INTEGER NOT NULL DEFAULT 0,
                    tax_cents INTEGER NOT NULL DEFAULT 0,
                    total_cents INTEGER NOT NULL DEFAULT 0,
                    pizza_count INTEGER NOT NULL,
                    cart_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(orders)").fetchall()
            }
            for column in ("subtotal_cents", "tax_cents", "total_cents"):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE orders ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )

    def remaining(self, service_at: str, capacity: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT confirmed_pizzas FROM slot_capacity WHERE service_at = ?",
                (service_at,),
            ).fetchone()
        confirmed = int(row["confirmed_pizzas"]) if row else 0
        return max(0, capacity - confirmed)

    def confirm_demo_order(
        self,
        *,
        service_at: str,
        pizza_count: int,
        capacity: int,
        customer: dict,
        notes: str,
        tip_cents: int,
        cart: list[dict],
        subtotal_cents: int = 0,
        tax_cents: int = 0,
        total_cents: int = 0,
    ) -> ConfirmedOrder:
        order_id = str(uuid.uuid4())
        confirmation_code = order_id.split("-")[0].upper()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT confirmed_pizzas FROM slot_capacity WHERE service_at = ?",
                (service_at,),
            ).fetchone()
            confirmed = int(row["confirmed_pizzas"]) if row else 0
            if confirmed + pizza_count > capacity:
                raise SlotUnavailableError(
                    "That pickup time can no longer accommodate your order. Please choose another."
                )

            connection.execute(
                """
                INSERT INTO slot_capacity(service_at, confirmed_pizzas)
                VALUES (?, ?)
                ON CONFLICT(service_at) DO UPDATE SET
                    confirmed_pizzas = confirmed_pizzas + excluded.confirmed_pizzas
                """,
                (service_at, pizza_count),
            )
            connection.execute(
                """
                INSERT INTO orders(
                    id, confirmation_code, service_at, customer_name,
                    customer_email, customer_phone, notes, tip_cents,
                    subtotal_cents, tax_cents, total_cents, pizza_count,
                    cart_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DEMO_CONFIRMED')
                """,
                (
                    order_id,
                    confirmation_code,
                    service_at,
                    customer["name"],
                    customer["email"],
                    customer.get("phone", ""),
                    notes,
                    tip_cents,
                    subtotal_cents,
                    tax_cents,
                    total_cents,
                    pizza_count,
                    json.dumps(cart),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        return ConfirmedOrder(order_id, confirmation_code, service_at)
