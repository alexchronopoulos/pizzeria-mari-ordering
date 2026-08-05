from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass


class SlotUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConfirmedOrder:
    id: str
    confirmation_code: str
    service_at: str


class DemoCapacityStore:
    """Ephemeral demo-only counters; restarting the process clears everything."""

    def __init__(self) -> None:
        self._confirmed: dict[str, int] = {}
        self._lock = threading.Lock()

    def remaining(self, service_at: str, capacity: int) -> int:
        with self._lock:
            confirmed = self._confirmed.get(service_at, 0)
        return max(0, capacity - confirmed)

    def counts_by_slot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._confirmed)

    def confirm_demo_order(
        self,
        *,
        service_at: str,
        pizza_count: int,
        capacity: int,
        **_: object,
    ) -> ConfirmedOrder:
        with self._lock:
            confirmed = self._confirmed.get(service_at, 0)
            if confirmed + pizza_count > capacity:
                raise SlotUnavailableError(
                    "That pickup time can no longer accommodate your order. Please choose another."
                )
            self._confirmed[service_at] = confirmed + pizza_count

        order_id = str(uuid.uuid4())
        return ConfirmedOrder(
            id=order_id,
            confirmation_code=order_id.split("-")[0].upper(),
            service_at=service_at,
        )
