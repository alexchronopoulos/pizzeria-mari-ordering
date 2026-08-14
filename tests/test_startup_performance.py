from __future__ import annotations

import time
from types import SimpleNamespace

from flask import Flask

from app.startup_performance import (
    StaleWhileRevalidateMenuProvider,
    prepare_app_for_serving,
)


class FakeProvider:
    def __init__(self) -> None:
        self.client = SimpleNamespace(list_catalog=self._list_catalog)
        self.cache_seconds = 30
        self._cached = None
        self._cached_at = 0.0
        self._lock = __import__("threading").Lock()
        self.catalog_requests = 0

    def cached_snapshot(self):
        return self._cached

    def snapshot(self):
        if self._cached is None:
            self._cached = self._build(self._list_catalog())
            self._cached_at = time.monotonic()
        return self._cached

    def _list_catalog(self):
        self.catalog_requests += 1
        return [{"version": self.catalog_requests}]

    def _build(self, objects):
        return SimpleNamespace(
            version=objects[0]["version"],
            pizza_catalog_object_ids={"PIZZA"},
        )


class FakeCommerce:
    def __init__(self) -> None:
        self.warmed = False

    def pizza_counts_by_slot(self, *, variation_ids, now):
        assert variation_ids == {"PIZZA"}
        self.warmed = True
        return {}


def test_startup_warms_square_data_and_sets_release_version():
    app = Flask(__name__)
    provider = FakeProvider()
    commerce = FakeCommerce()
    app.config.update(
        APP_VERSION="0.18.23",
        USE_SQUARE_DATA=True,
        TIMEZONE="America/New_York",
    )
    app.extensions["menu_provider"] = provider
    app.extensions["square_commerce"] = commerce

    prepare_app_for_serving(app, version="0.18.24")

    assert app.config["APP_VERSION"] == "0.18.24"
    assert provider.catalog_requests == 1
    assert commerce.warmed is True
    assert isinstance(
        app.extensions["menu_provider"],
        StaleWhileRevalidateMenuProvider,
    )


def test_stale_menu_returns_immediately_and_refreshes_in_background():
    provider = FakeProvider()
    first = provider.snapshot()
    wrapper = StaleWhileRevalidateMenuProvider(
        provider, Flask(__name__).logger
    )
    provider._cached_at = 0.0

    returned = wrapper.snapshot()
    deadline = time.monotonic() + 1.0
    while (
        provider.cached_snapshot().version < 2
        and time.monotonic() < deadline
    ):
        time.sleep(0.005)

    assert returned is first
    assert provider.catalog_requests == 2
    assert provider.cached_snapshot().version == 2


def test_fresh_warmed_menu_does_not_read_square_again_for_page_load():
    provider = FakeProvider()
    warmed = provider.snapshot()
    wrapper = StaleWhileRevalidateMenuProvider(
        provider, Flask(__name__).logger
    )

    returned = wrapper.snapshot()

    assert returned is warmed
    assert provider.catalog_requests == 1
