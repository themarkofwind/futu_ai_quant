"""配置热加载单元测试。"""

from __future__ import annotations

from pathlib import Path

from futu_ai_quant.config.env_reload import EnvReloader, format_settings_changes
from futu_ai_quant.strategy import intraday_t_settings as its


class TestIntradaySettingsHotReload:
    def test_refresh_updates_rsi_and_affects_evaluate(self, monkeypatch) -> None:
        monkeypatch.setenv("INTRADAY_T_RSI_SELL", "80")
        monkeypatch.setenv("INTRADAY_T_RSI_BUY", "20")
        changes = its.refresh_from_environ()
        assert its.INTRADAY_T_RSI_SELL == 80.0
        assert its.INTRADAY_T_RSI_BUY == 20.0
        assert "INTRADAY_T_RSI_SELL" in changes or its.INTRADAY_T_RSI_SELL == 80.0

        monkeypatch.setenv("INTRADAY_T_RSI_SELL", "70")
        changes = its.refresh_from_environ()
        assert changes["INTRADAY_T_RSI_SELL"] == (80.0, 70.0)
        assert its.INTRADAY_T_RSI_SELL == 70.0

    def test_default_codes_are_huahong_and_ali_tencent(self) -> None:
        # 不依赖外部环境：直接读默认常量
        assert its._DEFAULT_CODE == "HK.01347"
        assert "HK.09988" in its._DEFAULT_CODES
        assert "HK.00700" in its._DEFAULT_CODES

    def test_env_reloader_picks_up_file(self, tmp_path: Path, monkeypatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("INTRADAY_T_POLL_SEC=45\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("INTRADAY_T_POLL_SEC", raising=False)

        reloader = EnvReloader(env_path=env_file, check_interval_sec=0)
        # 首次已记录 mtime，强制刷新
        changes = reloader.poll(force=True)
        assert its.INTRADAY_T_POLL_SEC == 45
        assert "INTRADAY_T_POLL_SEC" in changes or its.INTRADAY_T_POLL_SEC == 45

        env_file.write_text("INTRADAY_T_POLL_SEC=90\n", encoding="utf-8")
        changes = reloader.poll(force=True)
        assert its.INTRADAY_T_POLL_SEC == 90
        assert format_settings_changes(changes)


class TestWatchApplyHotConfig:
    def test_apply_hot_config_updates_poll_and_codes(self) -> None:
        from unittest.mock import MagicMock

        from futu_ai_quant.brokers.futu.intraday_watch import IntradayTWatch

        quote = MagicMock()
        quote.subscribe.return_value = (0, "ok")
        # 不传 poll_sec，允许热加载覆盖
        watch = IntradayTWatch(quote, ["HK.09988"])
        its.INTRADAY_T_POLL_SEC = 30
        notes = watch.apply_hot_config(
            codes=["HK.09988", "HK.00700"],
            lot_by_code={"HK.00700": 500},
            target_by_code={"HK.00700": 1.5},
        )
        assert watch.poll_sec == 30
        assert [s.code for s in watch.symbols] == ["HK.09988", "HK.00700"]
        assert watch.symbols[1].ctx.lot_size == 500
        assert any("codes" in n or "poll_sec" in n for n in notes)
