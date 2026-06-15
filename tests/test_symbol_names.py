from __future__ import annotations

from futu_ai_quant.market.symbol_names import _merge_entry, display_name


class TestSymbolNames:
    def test_display_name_prefers_chinese(self) -> None:
        entry = {"name_zh": "腾讯控股", "name_en": "TENCENT"}
        assert display_name(entry, code="HK.00700") == "腾讯控股"

    def test_merge_entry_uses_tencent_zh_when_futu_english(self) -> None:
        entry = _merge_entry(
            "HK.06675",
            futu_name="SENASIC",
            position_name="SENASIC",
            tencent={"name_zh": "琻捷电子", "name_en_hint": "SENASIC"},
        )
        assert entry["name_en"] == "SENASIC"
        assert entry["name_zh"] == "琻捷电子"

    def test_merge_entry_uses_futu_when_chinese(self) -> None:
        entry = _merge_entry(
            "HK.00001",
            futu_name="长和",
            position_name="CKH HOLDINGS",
        )
        assert entry["name_zh"] == "长和"
        assert entry["name_en"] == "CKH HOLDINGS"
