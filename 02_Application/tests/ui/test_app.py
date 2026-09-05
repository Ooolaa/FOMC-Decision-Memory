import json
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[2]


class StreamlitAppTests(unittest.TestCase):
    def setUp(self):
        self.app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()

    def assert_page_clean(self, title):
        self.assertEqual(list(self.app.exception), [])
        self.assertEqual(self.app.title[0].value, title)

    def test_app_and_launcher_use_current_decision_trace_display_database(self):
        display_database = "fomc_simulation.decision_trace_50_display.sqlite"
        for path in (ROOT / "app.py", ROOT / "run_app.ps1"):
            text = path.read_text(encoding="utf-8")
            self.assertIn(display_database, text)
        launcher = (ROOT / "run_app.ps1").read_text(encoding="utf-8")
        streamlit_config = (ROOT / ".streamlit" / "config.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn("--server.address 127.0.0.1", launcher)
        self.assertIn('address = "127.0.0.1"', streamlit_config)
        self.assertIn(
            "decision_trace_50_v5_atomic_monitor_segmentation_v3\\qa_queue.json",
            launcher,
        )
        self.assertNotIn("decision_trace_50_v4\\qa_queue.json", launcher)

    def test_forecast_tables_explain_variants_and_show_units_consistently(self):
        model_table = next(
            item.value
            for item in self.app.dataframe
            if "研究模型" in item.value.columns
        )
        self.assertEqual(
            model_table.columns.tolist(),
            [
                "研究模型",
                "本次政策判斷",
                "政策命中率（45 場，%）",
                "反對票綜合分數（0–1）",
            ],
        )
        self.assertEqual(
            model_table["研究模型"].tolist(),
            [
                "匿名總體資料模型",
                "具名委員反應模型",
                "匿名委員反應模型",
                "具名委員證據模型",
            ],
        )
        self.assertEqual(
            model_table["政策命中率（45 場，%）"].tolist(),
            ["97.78", "97.78", "97.78", "93.33"],
        )
        self.assertEqual(
            model_table["反對票綜合分數（0–1）"].tolist(),
            ["0.11", "0.31", "0.12", "0.29"],
        )
        self.assertTrue(
            any("四個模型的輸入組合不同" in item.value for item in self.app.caption)
        )

        feature_table = next(
            item.value
            for item in self.app.dataframe
            if item.value.columns.tolist()
            == ["特徵", "數值", "單位", "計算方式"]
        )
        self.assertEqual(
            feature_table["數值"].tolist(),
            ["3.30", "4.10", "-0.20", "0.20", "1.54", "0.39", "3.63"],
        )
        self.assertEqual(
            feature_table["單位"].tolist(),
            ["%", "%", "百分點", "%", "百分點", "百分點", "%"],
        )
        self.assertEqual(
            feature_table["計算方式"].tolist(),
            [
                "本期指數與一年前相比",
                "直接取最新值",
                "最新值減一年前數值",
                "本期人數與一年前相比",
                "直接取最新利差",
                "10年殖利率減2年殖利率",
                "目標區間上下限平均",
            ],
        )
        markdown_text = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("模型實際使用的衍生特徵", markdown_text)
        self.assertIn("原始序列最新可見值", markdown_text)

        evidence_table = next(
            item.value
            for item in self.app.dataframe
            if "經濟序列" in item.value.columns
        )
        self.assertEqual(
            evidence_table.columns.tolist(),
            ["經濟序列", "觀測日期", "當時可見版本日", "數值", "單位"],
        )
        self.assertTrue(
            evidence_table["數值"].map(
                lambda value: len(value.partition(".")[2]) == 2
            ).all()
        )
        self.assertEqual(
            dict(zip(evidence_table["經濟序列"], evidence_table["單位"])),
            {
                "CPIAUCSL": "指數（1982–1984＝100）",
                "UNRATE": "%",
                "PAYEMS": "千人",
                "BAA10Y": "百分點",
                "DGS10": "%",
                "DGS2": "%",
                "DFEDTARL": "%",
                "DFEDTARU": "%",
            },
        )

    def test_all_three_pages_render_from_frozen_artifacts(self):
        self.assertEqual(
            self.app.radio[0].options,
            ["下次會議預測", "決策重播", "歷史測試結果"],
        )
        self.assert_page_clean("下次會議預測")
        forecast_metrics = {item.label: item.value for item in self.app.metric}
        self.assertEqual(forecast_metrics["預測政策方向"], "維持利率")
        self.assertEqual(forecast_metrics["下一場會議"], "2026/9/15–16")
        self.assertTrue(
            any(
                "2026 年 12 位公開市場委員會委員" in item.value
                for item in self.app.info
            )
        )
        self.assertTrue(
            any("12 / 12 位投票委員" in item.value for item in self.app.success)
        )
        self.assertIn("選擇有投票權委員", [item.label for item in self.app.selectbox])
        self.assertTrue(
            any("重要公開發言" in item.label for item in self.app.expander)
        )
        self.assertTrue(
            any("過去投票結果" in item.label for item in self.app.expander)
        )
        self.assertTrue(
            any("推定關注議題" in item.label for item in self.app.expander)
        )
        self.assertIn(
            "用人工智慧統整預測理由",
            [button.label for button in self.app.button],
        )
        self.assertIn(
            "多模型綜合預測",
            [item.value for item in self.app.subheader],
        )
        homepage_text = "\n".join(
            str(item.value)
            for collection in (
                self.app.title,
                self.app.header,
                self.app.subheader,
                self.app.caption,
                self.app.info,
                self.app.warning,
                self.app.success,
                self.app.markdown,
                self.app.button,
            )
            for item in collection
        )
        for jargon in (
            "AI",
            "API",
            "FOMC",
            "Frozen",
            "LLM",
            "named_persona_reaction",
            "有序羅吉特",
        ):
            self.assertNotIn(jargon, homepage_text)

        self.app.radio[0].set_value("決策重播").run()
        case_selector = next(
            item for item in self.app.selectbox if item.label == "會議／決策案例"
        )
        golden_case = next(
            option for option in case_selector.options if "FOMC-2022-03-15" in option
        )
        case_selector.set_value(golden_case).run()
        self.assert_page_clean("決策重播")
        replay_metrics = {item.label: item.value for item in self.app.metric}
        self.assertEqual(replay_metrics["實際決策"], "升息")
        self.assertTrue(
            any(
                "來源資料庫內容雜湊" in item.value
                and "02f96292422ece4556e952902a4660c663652d9eaff8b470e75eec3dc7c91187"
                in item.value
                and "證據內容雜湊" in item.value
                for item in self.app.caption
            )
        )

        self.app.radio[0].set_value("歷史測試結果").run()
        self.assert_page_clean("歷史測試結果")
        history_metrics = {item.label: item.value for item in self.app.metric}
        self.assertEqual(history_metrics["測試會議"], "45 場")
        self.assertEqual(history_metrics["四個模型政策猜對率"], "93.3%－97.8%")
        self.assertEqual(history_metrics["簡單延續判斷"], "82.2%")
        self.assertEqual(history_metrics["最佳反對票整體表現"], "0.31 / 1.00")
        history_table = next(
            item.value
            for item in self.app.dataframe
            if "政策猜對率" in item.value.columns
        )
        self.assertEqual(len(history_table), 4)
        self.assertEqual(
            history_table.columns.tolist(),
            [
                "模型",
                "場次",
                "政策猜對率",
                "預測反對有多準",
                "實際反對抓到多少",
                "反對票整體表現（0－1）",
            ],
        )
        history_text = "\n".join(
            str(item.value)
            for collection in (
                self.app.title,
                self.app.caption,
                self.app.info,
                self.app.warning,
            )
            for item in collection
        )
        self.assertIn("歷史測試不等於未來準確率", history_text)
        self.assertIn("僅用會議日期也能達到 91.1%", history_text)
        for jargon in ("假設監控", "模擬與證據", "變體", "精確率", "召回率", "F1"):
            self.assertNotIn(jargon, history_text)

    def test_replay_selector_lists_all_meetings_and_controls_base_case(self):
        self.app.radio[0].set_value("決策重播").run()
        case_selector = next(
            item for item in self.app.selectbox if item.label == "會議／決策案例"
        )

        self.assertEqual(len(case_selector.options), 166)
        self.assertTrue(
            any("完整決策脈絡" in option for option in case_selector.options)
        )
        base_case = next(
            option
            for option in case_selector.options
            if "FOMC-2026-07-28" in option and "政策／投票／經濟資料" in option
        )
        case_selector.set_value(base_case).run()

        self.assert_page_clean("決策重播")
        replay_metrics = {item.label: item.value for item in self.app.metric}
        self.assertEqual(replay_metrics["會議"], "2026-07-28/29")
        self.assertTrue(
            any("沒有完整決策脈絡" in item.value for item in self.app.info)
        )
        vote_tables = [
            item.value
            for item in self.app.dataframe
            if set(item.value.columns) == {"委員", "投票", "是否異議"}
        ]
        self.assertEqual(len(vote_tables), 1)
        self.assertTrue(set(vote_tables[0]["投票"]).issubset({"贊成", "反對"}))

    def test_user_facing_layout_prioritizes_conclusions_and_readable_replay(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('[data-testid="stSidebar"]', source)
        self.assertIn(".dm-hero", source)
        self.assertIn(".dm-badge-gold", source)
        self.assertIn(".dm-note", source)
        self.assertLessEqual(source.count("st.json("), 1)

        self.assertEqual(
            [item.label for item in self.app.metric[:4]],
            ["預測政策方向", "預測投票結構", "下一場會議", "目前目標區間"],
        )
        homepage_markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("dm-hero", homepage_markdown)
        self.assertIn("預測反對（", homepage_markdown)

        self.app.radio[0].set_value("決策重播").run()
        replay_filter = next(
            item
            for item in self.app.checkbox
            if item.label == "只列有完整決策脈絡的會議"
        )
        replay_filter.set_value(True).run()
        case_selector = next(
            item for item in self.app.selectbox if item.label == "會議／決策案例"
        )
        self.assertLess(len(case_selector.options), 166)
        golden_case = next(
            option for option in case_selector.options if "FOMC-2022-03-15" in option
        )
        case_selector.set_value(golden_case).run()
        self.assertIn(
            "當時可見的關鍵序列（各最近 24 筆）",
            [item.value for item in self.app.subheader],
        )
        self.assertEqual(len(self.app.get("vega_lite_chart")), 2)
        replay_markdown = "\n".join(item.value for item in self.app.markdown)
        self.assertIn("消費者物價年增率（%）", replay_markdown)
        self.assertIn("失業率（%）", replay_markdown)
        replay_chart_specs = [
            json.loads(item.proto.spec)
            for item in self.app.get("vega_lite_chart")
        ]
        for spec in replay_chart_specs:
            self.assertEqual(spec["height"], 220)
            self.assertEqual(spec["encoding"]["x"]["type"], "temporal")
            self.assertEqual(spec["encoding"]["x"]["axis"]["format"], "%Y-%m")
            self.assertEqual(spec["encoding"]["x"]["axis"]["labelAngle"], -45)
            self.assertFalse(spec["encoding"]["y"]["scale"]["zero"])
        expander_labels = [item.label for item in self.app.expander]
        self.assertIn("官方證據來源", expander_labels)
        self.assertIn("資料來源與驗證", expander_labels)
        assumption_table = next(
            item.value
            for item in self.app.dataframe
            if "假設陳述" in item.value.columns
        )
        self.assertIn("監控序列", assumption_table.columns)
        self.assertIn("門檻", assumption_table.columns)

        self.app.radio[0].set_value("歷史測試結果").run()
        self.assert_page_clean("歷史測試結果")
        self.assertIn("history_table.style.map", source)

    def test_interface_is_fomc_only_without_enterprise_domain(self):
        self.assertNotIn("領域", [item.label for item in self.app.selectbox])
        visible_text = "\n".join(
            str(item.value)
            for collection in (
                self.app.title,
                self.app.header,
                self.app.subheader,
                self.app.caption,
                self.app.info,
                self.app.markdown,
            )
            for item in collection
        )
        self.assertNotIn("企業示意案例", visible_text)
        self.assertNotIn("synthetic / composite", visible_text)


if __name__ == "__main__":
    unittest.main()
