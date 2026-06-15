from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import pytest
from futu import KLType

from futu_ai_quant.indicators import kline_cache


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    kline_cache.clear_kline_cache()
    yield
    kline_cache.clear_kline_cache()


class TestKlineCache:
    def test_daily_cache_disabled_when_ttl_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_ENABLED", True)
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_TTL_SEC", 0)
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_DIR", tmp_path)

        frame = pd.DataFrame([{"close": 100.0, "volume": 1000}])
        kline_cache.put_cached_kline("HK.00700", KLType.K_DAY, 60, frame)
        assert kline_cache.get_cached_kline("HK.00700", KLType.K_DAY, 60) is None
        assert list(tmp_path.glob("*.json")) == []

        kline_cache.put_cached_kline("HK.00700", KLType.K_WEEK, 52, frame)
        assert kline_cache.get_cached_kline("HK.00700", KLType.K_WEEK, 52) is not None

    def test_memory_hit_skips_stale(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_ENABLED", True)
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_TTL_SEC", 60)
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_DIR", tmp_path)

        frame = pd.DataFrame([{"close": 100.0, "volume": 1000}])
        kline_cache.put_cached_kline("HK.00700", KLType.K_DAY, 60, frame)

        cached = kline_cache.get_cached_kline("HK.00700", KLType.K_DAY, 60)
        assert cached is not None
        assert float(cached.iloc[0]["close"]) == 100.0

        key = kline_cache.cache_key("HK.00700", KLType.K_DAY, 60)
        kline_cache._MEMORY[key] = (time.time() - 120, frame.to_dict(orient="records"))
        stale_path = kline_cache._disk_path(key)
        stale_path.write_text(
            json.dumps({"fetched_at": time.time() - 120, "rows": frame.to_dict(orient="records")}),
            encoding="utf-8",
        )
        assert kline_cache.get_cached_kline("HK.00700", KLType.K_DAY, 60) is None

    def test_disk_cache_roundtrip(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_ENABLED", True)
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_TTL_SEC", 300)
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_DIR", tmp_path)

        frame = pd.DataFrame([{"close": 88.5, "high": 90.0, "low": 87.0, "volume": 500}])
        kline_cache.put_cached_kline("HK.09988", KLType.K_WEEK, 52, frame)
        kline_cache.clear_kline_cache()

        cached = kline_cache.get_cached_kline("HK.09988", KLType.K_WEEK, 52)
        assert cached is not None
        assert float(cached.iloc[0]["close"]) == 88.5

        disk_file = list(tmp_path.glob("*.json"))
        assert len(disk_file) == 1
        payload = json.loads(disk_file[0].read_text(encoding="utf-8"))
        assert payload["rows"][0]["close"] == 88.5

    def test_fetch_uses_cache_without_api(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_ENABLED", True)
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_TTL_SEC", 300)
        monkeypatch.setattr(kline_cache, "KLINE_CACHE_DIR", tmp_path)

        frame = pd.DataFrame([{"close": 300.0, "volume": 1}])
        kline_cache.put_cached_kline("HK.00700", KLType.K_DAY, 60, frame)

        called = {"count": 0}

        class FakeQuote:
            def request_history_kline(self, *args, **kwargs):
                called["count"] += 1
                raise AssertionError("不应调用 OpenD API")

        ret, kline, _ = kline_cache.fetch_history_kline_cached(
            FakeQuote(),  # type: ignore[arg-type]
            "HK.00700",
            KLType.K_DAY,
            60,
        )
        assert called["count"] == 0
        assert ret == 0
        assert float(kline.iloc[0]["close"]) == 300.0
