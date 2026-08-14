from __future__ import annotations

from datetime import datetime
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask


class StaleWhileRevalidateMenuProvider:
    """Return the last good menu while refreshing an expired catalog."""

    def __init__(self, provider: Any, logger: Any) -> None:
        self._provider = provider
        self._logger = logger
        self._refresh_lock = threading.Lock()
        self._refreshing = False

    def cached_snapshot(self):
        return self._provider.cached_snapshot()

    def snapshot(self):
        cached = self._provider.cached_snapshot()
        if cached is None:
            # Startup performs this first blocking load before the worker is
            # announced as ready to receive customer traffic.
            return self._provider.snapshot()

        cached_at = float(getattr(self._provider, "_cached_at", 0.0))
        cache_seconds = float(getattr(self._provider, "cache_seconds", 30.0))
        if time.monotonic() - cached_at >= cache_seconds:
            self._start_refresh()
        return cached

    def _start_refresh(self) -> None:
        with self._refresh_lock:
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(
            target=self._refresh,
            name="square-catalog-refresh",
            daemon=True,
        ).start()

    def _refresh(self) -> None:
        try:
            objects = self._provider.client.list_catalog()
            snapshot = self._provider._build(objects)
            with self._provider._lock:
                self._provider._cached = snapshot
                self._provider._cached_at = time.monotonic()
        except Exception:
            self._logger.exception(
                "Square catalog background refresh failed; keeping last good menu."
            )
        finally:
            with self._refresh_lock:
                self._refreshing = False


def prepare_app_for_serving(app: Flask, *, version: str) -> None:
    """Warm Square-backed page data before Gunicorn accepts traffic."""

    app.config["APP_VERSION"] = version
    if not app.config.get("USE_SQUARE_DATA"):
        return

    original_provider = app.extensions["menu_provider"]
    if isinstance(original_provider, StaleWhileRevalidateMenuProvider):
        provider = original_provider
    else:
        provider = StaleWhileRevalidateMenuProvider(
            original_provider, app.logger
        )
        app.extensions["menu_provider"] = provider

    try:
        menu = provider.snapshot()
    except Exception:
        app.logger.exception(
            "Square catalog startup warmup failed; the first request will retry."
        )
        return

    commerce = app.extensions.get("square_commerce")
    if commerce is None:
        return
    try:
        commerce.pizza_counts_by_slot(
            variation_ids=menu.pizza_catalog_object_ids,
            now=datetime.now(ZoneInfo(app.config["TIMEZONE"])),
        )
    except Exception:
        app.logger.exception(
            "Square availability startup warmup failed; the first request will retry."
        )
