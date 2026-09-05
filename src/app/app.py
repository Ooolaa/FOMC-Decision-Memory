from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from decision_memory.ai_member_explanation import (
    DEFAULT_MODEL,
    AiExplanationError,
    classify_ai_error,
    generate_member_explanation,
    user_scope_key_available,
)
from decision_memory.next_meeting_forecast import build_next_meeting_forecast
from decision_memory.ui_variant_artifacts import (
    build_voter_vote_comparison,
    load_completed_variant_case,
    load_completed_variant_matrix,
)


ROOT = Path(__file__).resolve().parent
SOURCE_DB = ROOT / "fred_fomc_real.sqlite"
FORMAL_APP_DB = (ROOT / "fomc_simulation.sqlite").resolve()
DISPLAY_APP_DB = (
    ROOT / "fomc_simulation.decision_trace_50_display.sqlite"
).resolve()
DEFAULT_APP_DB = DISPLAY_APP_DB if DISPLAY_APP_DB.is_file() else FORMAL_APP_DB
APP_DB = Path(os.environ.get("FOMC_APP_DB", DEFAULT_APP_DB)).resolve()
COMMUNICATIONS_DB = (
    ROOT / "fomc_simulation.transcript_segmentation_v3_candidate.sqlite"
).resolve()
OFFICIAL_FORECAST_CONTEXT = (
    ROOT / "fixtures/next_meeting_official_context_2026-09-01.json"
).resolve()
FORWARD_ENSEMBLE_ARTIFACT = (
    ROOT
    / "artifacts/forecast/fomc_2026_09_15_ensemble_v1/ensemble_forecast.json"
).resolve()
REACTION_ARTIFACT = ROOT / "artifacts/reaction/pooled_ordered_logit_v1.json"
REACTION_FEATURE_CONTRACT = (
    ROOT / "model_spec/reaction_feature_contract_hackathon_r5_v1.json"
)
PROFILE_CARDS_ARTIFACT = (
    ROOT / "artifacts/reaction/fomc_2022_03_15_profile_cards_v1.json"
)
EVALUATION_ARTIFACT = (
    ROOT / "artifacts/evaluation/frozen_45_policy_baselines_v1.json"
)
ALERT_AUDIT_ARTIFACT = (
    ROOT / "artifacts/evaluation/statement_alert_audit_v1.json"
)
RATE_CONSTRAINT_AUDIT_ARTIFACT = (
    ROOT / "artifacts/evaluation/rate_only_censoring_audit_v1.json"
)
SOURCE_REFRESH_AUDIT = (
    ROOT / "artifacts/evaluation/source_refresh_2026-09-01_audit.json"
)
SIMULATION_ARTIFACT = (
    ROOT / "artifacts/cache/fomc_2022_03_15_offline_baseline.json"
)
VARIANT_SPEC = ROOT / "evaluation_spec/hackathon_r5_variants_v1.json"
VARIANT_ARTIFACT_ROOT = ROOT / "artifacts/codex_subscription/r5_variants_v2"
VARIANT_MATRIX_ARTIFACT = (
    ROOT / "artifacts/evaluation/r5_subscription_variant_matrix_v1.json"
)
VOTE_EVALUATION_ARTIFACT = (
    ROOT / "artifacts/evaluation/frozen_45_vote_baselines_candidate_v1.json"
)
FOMC_ASSUMPTION_ID = "assumption-inflation_transitory_v1"


st.set_page_config(
    page_title="聯準會決策預測實驗室",
    page_icon="🏦",
    layout="wide",
)
st.markdown(
    """
    <style>
      :root {
        --primary:#1B7F93; --cyan:#54B5C6; --orange:#E03F19;
        --navy:#0E2540; --navy-light:#16375C; --gold:#FFB43E;
        --green:#1E6B45; --red:#B32D0F; --red-bg:#FDECE7;
        --canvas:#F4F6F9; --card:#FFFFFF; --border:#DFE5EC;
        --text:#1C2733; --muted:#5B6B7B;
      }
      .block-container {
        max-width:1240px; padding-top:1.25rem; padding-bottom:3rem;
      }
      [data-testid="stHeading"] h1 { margin-bottom:.25rem; }
      [data-testid="stHeading"] h3 {
        padding-left:.6rem; border-left:4px solid var(--primary);
        margin-top:1.75rem;
      }
      [data-testid="stSidebar"] { min-width:320px; max-width:320px; }
      [data-testid="stMetric"] {
        background:var(--card); border-radius:12px; padding:.8rem .9rem;
        box-shadow:0 1px 2px rgba(16,24,40,.04);
      }
      [data-testid="stMetricLabel"] {
        color:var(--muted); font-size:.8rem;
      }
      [data-testid="stMetricValue"] {
        font-size:1.35rem; font-weight:600; letter-spacing:-.01em;
      }
      .dm-hero {
        background:linear-gradient(135deg,#0E2540 0%,#1B7F93 100%);
        color:#FFFFFF; padding:1.25rem 1.5rem; border-radius:14px;
        font-size:1.15rem; line-height:1.7; margin:.5rem 0 1rem;
      }
      .dm-hero-eyebrow {
        font-size:.8rem; opacity:.8; letter-spacing:.04em; margin-bottom:.15rem;
      }
      .dm-hero strong { color:#FFFFFF; font-weight:700; }
      .dm-hero .dm-dissenters { color:var(--gold); font-weight:700; }
      .dm-badge, .dm-badge-gold {
        display:inline-block; padding:.2rem .55rem; border-radius:999px;
        font-size:.78rem; font-weight:600; letter-spacing:.02em;
      }
      .dm-badge {
        border:1px solid rgba(27,127,147,.35);
        background:rgba(27,127,147,.08); color:var(--primary);
      }
      .dm-badge-gold {
        border:1px solid rgba(255,180,62,.55);
        background:rgba(255,180,62,.12); color:#8A5A00;
      }
      .dm-note {
        background:var(--card); border:1px solid var(--border);
        border-radius:10px; padding:.9rem 1.1rem; color:#3B4753;
        font-size:.88rem; margin:.5rem 0;
      }
      .dm-note ul { margin:.4rem 0 0; padding-left:1.2rem; }
      .dm-warning {
        border-left:4px solid var(--gold); padding:.65rem .9rem;
        background:#FFF8E8; margin:.5rem 0 1rem;
      }
      .dm-source { color:var(--muted); font-size:.82rem; }
      [data-testid="stExpander"] { margin-top:.5rem; }
      [data-testid="stExpander"] details {
        background:var(--card); border:1px solid var(--border);
        border-radius:10px; overflow:hidden;
      }
      [data-testid="stDataFrame"] {
        background:var(--card); border:1px solid var(--border);
        border-radius:10px;
      }
      .dm-brand { padding:.25rem 0 .9rem; }
      .dm-brand-title {
        color:#FFFFFF; font-size:1.1rem; font-weight:700; line-height:1.45;
      }
      .dm-brand-subtitle { color:#8FD3DF; font-size:.78rem; margin-top:.15rem; }
      [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
        color:#B7C6D8;
      }
      [data-testid="stSidebar"] [data-testid="stRadio"] > label {
        color:#8FD3DF; font-size:.72rem; font-weight:600;
        letter-spacing:.08em; text-transform:uppercase;
      }
      [data-testid="stSidebar"] [data-testid="stRadioGroup"] > label {
        padding:.4rem .6rem; border-radius:8px; transition:background .15s ease;
      }
      [data-testid="stSidebar"] [data-testid="stRadioGroup"] > label:hover {
        background:rgba(255,255,255,.06);
      }
      [data-testid="stSidebar"] [data-testid="stRadioGroup"] label[data-baseweb="radio"]:has(input[type="radio"]:checked) {
        background:rgba(84,181,198,.18);
      }
      [data-testid="stSidebar"] [data-testid="stRadioGroup"] label[data-baseweb="radio"] p {
        font-size:.95rem;
      }
      .dm-sidebar-foot { color:#8FA3BA; font-size:.78rem; line-height:1.5; }
      [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {
        visibility: hidden;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def _connect(path: Path, *, writable: bool = False) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    mode = "rw" if writable else "ro"
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode={mode}", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@st.cache_data(show_spinner=False)
def _meeting_replay(meeting_id: str) -> dict:
    app = _connect(APP_DB)
    source = _connect(SOURCE_DB)
    try:
        meeting = source.execute(
            """
            SELECT meeting_start_date, meeting_end_date, information_cutoff_date_et
            FROM fomc_meeting WHERE meeting_id = ?
            """,
            (meeting_id,),
        ).fetchone()
        outcome = app.execute(
            """
            SELECT action_class, target_rate, target_lower, target_upper,
                   source_document_id
            FROM meeting_outcome WHERE meeting_id = ?
            """,
            (meeting_id,),
        ).fetchone()
        evidence_document = (
            app.execute(
                """
                SELECT document_id, content_hash
                FROM document_source
                WHERE document_id = ?
                """,
                (outcome["source_document_id"],),
            ).fetchone()
            if outcome is not None
            else None
        )
        votes = app.execute(
            """
            SELECT participant_vote.participant_id, participant.display_name,
                   participant_vote.voter_choice,
                   participant_vote.dissent
            FROM participant_vote
            JOIN participant USING (participant_id)
            WHERE participant_vote.meeting_id = ?
            ORDER BY participant_vote.dissent, participant.display_name
            """,
            (meeting_id,),
        ).fetchall()
        decision_id = f"fomc-{meeting_id}"
        case = app.execute(
            "SELECT context_json FROM decision_case WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        trace = app.execute(
            """
            SELECT options_json, debate_json, decision_json, vote_json,
                   extractor_version
            FROM decision_trace WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        assumptions = app.execute(
            """
            SELECT assumption_id, claim, monitor_series_id, monitor_operator,
                   threshold_value, monitor_rule_version
            FROM decision_assumption WHERE decision_id = ?
            """,
            (decision_id,),
        ).fetchall()
        series_rows = source.execute(
            """
            SELECT snapshot.series_id, snapshot.observation_date, vintage.value_num
            FROM meeting_snapshot_value AS snapshot
            JOIN observation_vintage AS vintage
              ON vintage.series_id = snapshot.series_id
             AND vintage.observation_date = snapshot.observation_date
             AND vintage.realtime_start = snapshot.realtime_start
            WHERE snapshot.meeting_id = ?
              AND snapshot.series_id IN ('CPIAUCSL', 'UNRATE')
            ORDER BY snapshot.series_id, snapshot.observation_date
            """,
            (meeting_id,),
        ).fetchall()
    finally:
        source.close()
        app.close()
    if meeting is None or outcome is None or evidence_document is None:
        raise RuntimeError(f"Replay fixture is incomplete: {meeting_id}")
    return {
        "meeting": dict(meeting),
        "outcome": dict(outcome),
        "evidence_document": dict(evidence_document),
        "votes": [dict(row) for row in votes],
        "replay_tier": "full" if case is not None and trace is not None else "base",
        "context": json.loads(case[0]) if case is not None else None,
        "trace": (
            {
                "options": json.loads(trace[0]),
                "debate": json.loads(trace[1]),
                "decision": json.loads(trace[2]),
                "vote": json.loads(trace[3]),
                "extractor_version": trace[4],
            }
            if trace is not None
            else None
        ),
        "assumptions": [dict(row) for row in assumptions],
        "series": [dict(row) for row in series_rows],
    }


@st.cache_data(show_spinner=False)
def _fomc_case_catalog() -> list[dict]:
    source = _connect(SOURCE_DB)
    app = _connect(APP_DB)
    try:
        meetings = source.execute(
            """
            SELECT meeting_id, meeting_start_date, meeting_end_date
            FROM fomc_meeting
            ORDER BY meeting_start_date DESC
            """
        ).fetchall()
        full_meeting_ids = {
            str(row[0]).removeprefix("fomc-")
            for row in app.execute(
                """
                SELECT decision_case.decision_id
                FROM decision_case
                JOIN decision_trace USING (decision_id)
                WHERE decision_case.domain = 'fomc'
                """
            )
        }
    finally:
        app.close()
        source.close()
    return [
        {
            "meeting_id": str(row["meeting_id"]),
            "label": (
                f"{row['meeting_id']} · "
                f"{'完整決策脈絡' if row['meeting_id'] in full_meeting_ids else '政策／投票／經濟資料'}"
            ),
            "replay_tier": (
                "full" if row["meeting_id"] in full_meeting_ids else "base"
            ),
        }
        for row in meetings
    ]


@st.cache_data(show_spinner=False)
def _fomc_monitor() -> dict:
    app = _connect(APP_DB)
    try:
        assumption = app.execute(
            """
            SELECT claim, monitor_series_id, monitor_operator, threshold_value,
                   monitor_rule_version
            FROM decision_assumption WHERE assumption_id = ?
            """,
            (FOMC_ASSUMPTION_ID,),
        ).fetchone()
        events = app.execute(
            """
            SELECT event_type, occurred_at, payload_json
            FROM assumption_event WHERE assumption_id = ?
            ORDER BY occurred_at, event_id
            """,
            (FOMC_ASSUMPTION_ID,),
        ).fetchall()
    finally:
        app.close()
    if assumption is None:
        raise RuntimeError("聯準會假設監控資料不存在")
    event_map = {
        row["event_type"]: {
            "occurred_at": row["occurred_at"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in events
    }
    return {"assumption": dict(assumption), "events": event_map}


@st.cache_data(show_spinner=False)
def _load_artifacts() -> tuple[dict, dict, dict]:
    return tuple(
        json.loads(path.read_text(encoding="utf-8"))
        for path in (REACTION_ARTIFACT, EVALUATION_ARTIFACT, SIMULATION_ARTIFACT)
    )


@st.cache_data(show_spinner=False)
def _load_reaction_feature_contract() -> dict:
    contract = json.loads(REACTION_FEATURE_CONTRACT.read_text(encoding="utf-8"))
    if contract.get("decision_status") != "APPROVED":
        raise ValueError("Reaction feature contract is not approved")
    return contract


@st.cache_data(show_spinner=False)
def _load_alert_audit() -> dict:
    return json.loads(ALERT_AUDIT_ARTIFACT.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def _load_rate_constraint_audit() -> dict:
    return json.loads(RATE_CONSTRAINT_AUDIT_ARTIFACT.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def _load_source_refresh_audit() -> dict:
    return json.loads(SOURCE_REFRESH_AUDIT.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def _load_profile_cards() -> dict:
    return json.loads(PROFILE_CARDS_ARTIFACT.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def _load_variant_spec() -> dict:
    return json.loads(VARIANT_SPEC.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def _load_completed_variant(variant_id: str, meeting_id: str) -> dict | None:
    return load_completed_variant_case(
        VARIANT_ARTIFACT_ROOT,
        variant_id=variant_id,
        meeting_id=meeting_id,
    )


@st.cache_data(show_spinner=False)
def _load_completed_matrix() -> dict | None:
    return load_completed_variant_matrix(
        VARIANT_MATRIX_ARTIFACT,
        workspace_root=ROOT,
    )


@st.cache_data(show_spinner=False)
def _load_next_meeting_forecast() -> dict:
    return build_next_meeting_forecast(
        SOURCE_DB,
        APP_DB,
        REACTION_ARTIFACT,
        EVALUATION_ARTIFACT,
        VOTE_EVALUATION_ARTIFACT,
        communications_database=COMMUNICATIONS_DB,
        official_context_path=OFFICIAL_FORECAST_CONTEXT,
        ensemble_artifact_path=FORWARD_ENSEMBLE_ARTIFACT,
    )


def _format_target(outcome: dict) -> str:
    if outcome["target_rate"] is not None:
        return f"{outcome['target_rate']:.2f}%"
    return f"{outcome['target_lower']:.2f}–{outcome['target_upper']:.2f}%"


def _format_two_decimals(value: float) -> str:
    return format(
        Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        ".2f",
    )


ACTION_LABELS = {
    "CUT": "降息",
    "HOLD": "維持利率",
    "HIKE": "升息",
}
VOTE_LABELS = {"FOR": "贊成", "AGAINST": "反對"}
SERIES_LABELS = {
    "CPIAUCSL": "消費者物價指數（1982–84＝100）",
    "UNRATE": "失業率（%）",
    "PAYEMS": "非農就業（千人）",
    "BAA10Y": "BAA－10 年期公債利差（百分點）",
    "DGS10": "10 年期公債殖利率（%）",
    "DGS2": "2 年期公債殖利率（%）",
    "DFEDTARL": "聯邦資金利率目標下限（%）",
    "DFEDTARU": "聯邦資金利率目標上限（%）",
}
REPLAY_SERIES_LABELS = {
    "CPIAUCSL": "消費者物價年增率（%）",
    "UNRATE": "失業率（%）",
}
MODEL_LABELS = {
    "majority": "全數維持利率基準",
    "persistence": "延續性基準",
    "pooled_reaction": "總體反應基準",
    "majority_deterministic": "全數維持利率基準",
    "persistence_deterministic": "延續性基準",
    "pooled_reaction_deterministic": "總體反應基準",
    "naked_frozen_llm": "匿名總體資料模擬",
    "named_persona_reaction": "具名委員與歷史反應",
    "anonymous_persona_reaction": "匿名委員與歷史反應",
    "named_persona_no_reaction": "具名委員但不使用歷史反應",
    "date_only_memorization_probe": "僅會議日期的答案記憶測試",
}
FORECAST_MODEL_DETAILS = {
    "naked_frozen_llm": {
        "label": "匿名總體資料模型",
        "description": "只使用會前總體資料與會議規則，不提供委員姓名、個人公開證據或歷史反應卡。",
    },
    "named_persona_reaction": {
        "label": "具名委員反應模型",
        "description": "提供委員姓名、個人過去公開發言與投票證據，以及由歷史資料建立的反應卡。",
    },
    "anonymous_persona_reaction": {
        "label": "匿名委員反應模型",
        "description": "保留每位委員的公開證據與歷史反應卡，但移除姓名，用來檢查姓名本身是否影響預測。",
    },
    "named_persona_no_reaction": {
        "label": "具名委員證據模型",
        "description": "提供委員姓名與個人公開證據，但不加入歷史反應卡，用來衡量反應卡帶來的差異。",
    },
}


def _localized_vote_frame(votes: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "委員": row["display_name"],
                "投票": VOTE_LABELS.get(row["voter_choice"], row["voter_choice"]),
                "是否異議": "是" if row["dissent"] else "否",
            }
            for row in votes
        ]
    )


def _vote_cell_style(value: str) -> str:
    if value == "反對":
        return "color:#B32D0F;background-color:#FDECE7;font-weight:bold"
    if value == "贊成":
        return "color:#1E6B45"
    return ""


def _vote_row_style(row: pd.Series) -> list[str]:
    style = _vote_cell_style(row["投票"])
    return [style] * len(row)


def _replay_series_display_frame(
    series_frame: pd.DataFrame, series_id: str
) -> pd.DataFrame:
    display_frame = series_frame.loc[
        series_frame["series_id"] == series_id,
        ["observation_date", "value_num"],
    ].copy()
    display_frame["observation_date"] = pd.to_datetime(
        display_frame["observation_date"], errors="coerce"
    )
    display_frame["value_num"] = pd.to_numeric(
        display_frame["value_num"], errors="coerce"
    )
    display_frame = display_frame.dropna(subset=["observation_date"]).sort_values(
        "observation_date"
    )
    if series_id == "CPIAUCSL":
        months = display_frame["observation_date"].dt.to_period("M")
        values_by_month = pd.Series(display_frame["value_num"].values, index=months)
        prior_values = (months - 12).map(values_by_month)
        display_frame["value_num"] = (
            display_frame["value_num"] / prior_values - 1
        ) * 100
    return display_frame.dropna(subset=["value_num"]).tail(24)


def render_next_meeting_forecast() -> None:
    st.title("下次會議預測")
    st.caption("以會前可得資料綜合預測政策方向與逐位投票者的贊成／反對。")

    forecast = _load_next_meeting_forecast()
    policy = forecast["policy_prediction"]
    ensemble = policy["ensemble"]
    voter_forecast = forecast["voter_forecast"]
    predicted_dissenters = [
        row for row in voter_forecast["rows"] if row["predicted_vote"] == "AGAINST"
    ]
    target_range = (
        f"{forecast['policy_context']['lower_rate']:.2f}–"
        f"{forecast['policy_context']['upper_rate']:.2f}%"
    )
    if predicted_dissenters:
        dissent_summary = (
            '預測反對：<span class="dm-dissenters">'
            + "、".join(escape(row["display_name"]) for row in predicted_dissenters)
            + "</span>"
        )
    else:
        dissent_summary = "預測全體一致"
    st.markdown(
        '<div class="dm-hero">'
        '<div class="dm-hero-eyebrow">下一場會議 · 2026 年 9 月 15–16 日</div>'
        '<div>預測 '
        f'<strong>{ACTION_LABELS[policy["action_class"]]}</strong>（{target_range}），'
        f'投票 <strong>{len(voter_forecast["rows"]) - len(predicted_dissenters)} 贊成－'
        f'{len(predicted_dissenters)} 反對</strong>，{dissent_summary}。</div></div>',
        unsafe_allow_html=True,
    )
    summary_columns = st.columns(4)
    summary_columns[0].metric(
        "預測政策方向", ACTION_LABELS[policy["action_class"]], border=True
    )
    summary_columns[1].metric(
        "預測投票結構",
        f"{len(voter_forecast['rows']) - len(predicted_dissenters)}–"
        f"{len(predicted_dissenters)}",
        help="贊成票－反對票；投票者採官方 2026 年公開市場委員會委員名單。",
        border=True,
    )
    summary_columns[2].metric("下一場會議", "2026/9/15–16", border=True)
    summary_columns[3].metric(
        "目前目標區間",
        target_range,
        border=True,
    )
    st.markdown(
        '<span class="dm-badge">會前鎖定綜合預測</span> '
        '<span class="dm-badge-gold">官方 2026 投票委員名單</span>',
        unsafe_allow_html=True,
    )
    st.info(
        "投票者採聯準會公布的 2026 年 12 位公開市場委員會委員；"
        "這是有投票權名單，不是會前出席保證。"
    )
    if ensemble["combined"]["policy"]["consensus_reached"]:
        st.success(
            f"四個研究模型中有 {ensemble['combined']['policy']['support_count']} 個"
            f"一致預測「{ACTION_LABELS[policy['action_class']]}」。"
        )
    else:
        st.warning(
            "研究模型未達三票共識；首頁改採較穩定的延續性判斷。"
        )

    st.subheader("多模型綜合預測")
    model_rows = pd.DataFrame(
        [
            {
                "研究模型": FORECAST_MODEL_DETAILS[row["model_key"]]["label"],
                "本次政策判斷": ACTION_LABELS[row["policy_action"]],
                "政策命中率（45 場，%）": (
                    _format_two_decimals(row["historical_policy_accuracy"] * 100)
                ),
                "反對票綜合分數（0–1）": _format_two_decimals(
                    row["historical_dissent_f1"]
                ),
            }
            for row in ensemble["model_rows"]
        ]
    )
    st.dataframe(
        model_rows,
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "四個模型的輸入組合不同；『具名／匿名』表示是否提供委員身分，"
        "『反應』表示是否提供歷史反應卡。四者使用相同截止日的會前總體資料。"
    )
    with st.expander("查看模型差異與分數說明"):
        for details in FORECAST_MODEL_DETAILS.values():
            st.markdown(f"**{details['label']}**：{details['description']}")
        st.caption(
            "政策命中率＝固定 45 場測試中政策方向判斷正確的比例；"
            "反對票綜合分數介於 0 與 1，綜合衡量反對票預測的查準率與召回率，"
            "數值越高越好。畫面數值統一顯示至小數點後兩位。"
        )
        st.caption(
            f"結果已於 {ensemble['locked_at']} 鎖定，資料截止日為 "
            f"{ensemble['forecast_as_of']}。政策方向需至少三個模型同意；若未達門檻，"
            "才改用延續性判斷。歷史命中率可能受到答案記憶影響，因此不視為未來準確率。"
            "本次會前資料採精簡的最新特徵，與歷史固定測試資料不完全相同，歷史分數只供參考。"
        )

    with st.expander("查看其他政策參考"):
        st.write(
            "延續性判斷："
            f"{ACTION_LABELS[policy['persistence_action_class']]}，"
            f"固定 45 場測試命中率 {policy['frozen_accuracy']['persistence']:.1%}。"
        )
        st.write(
            "總體機率模型："
            f"{ACTION_LABELS[policy['ordered_logit_action_class']]}，"
            f"固定 45 場測試命中率 {policy['frozen_accuracy']['pooled_reaction']:.1%}。"
        )
        probability_rows = pd.DataFrame(
            [
                {"政策方向": ACTION_LABELS[action], "參考機率": probability}
                for action, probability in policy["probabilities"].items()
            ]
        )
        st.dataframe(
            probability_rows.style.format({"參考機率": "{:.1%}"}),
            width="stretch",
            hide_index=True,
        )
        st.caption("這組機率只用來觀察總體資料訊號，不決定首頁最終結果。")

    st.subheader("逐位投票預測")
    vote_rows = pd.DataFrame(
        [
            {
                "投票者": row["display_name"],
                "角色": {
                    "chair": "主席",
                    "vice_chair": "副主席",
                }.get(row["role"], "委員"),
                "預測投票": VOTE_LABELS[row["predicted_vote"]],
                "反對支持模型": (
                    f"{row['against_support_count']} / {row['ensemble_model_count']}"
                ),
                "會前歷史投票數": row["prior_vote_count"],
                "歷史異議率": row["prior_dissent_rate"],
            }
            for row in voter_forecast["rows"]
        ]
    )
    if predicted_dissenters:
        st.markdown(
            f"**預測反對（{len(predicted_dissenters)} 位）：** "
            + "、".join(row["display_name"] for row in predicted_dissenters)
        )
    else:
        st.markdown("**預測全體贊成**")
    st.dataframe(
        vote_rows.style.map(_vote_cell_style, subset=["預測投票"]).format(
            {"歷史異議率": "{:.1%}"}
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "逐位投票由四個研究模型共同判斷；至少兩個模型預測反對，才列為反對票。"
        "兩票規則已預先固定，真正成效仍須由本次及後續會議驗證。"
    )

    evidence_coverage = sum(
        bool(row["important_communications"]) for row in voter_forecast["rows"]
    )
    if evidence_coverage == len(voter_forecast["rows"]):
        st.success(
            f"官方公開發言證據覆蓋：{evidence_coverage} / "
            f"{len(voter_forecast['rows'])} 位投票委員。"
        )
    else:
        missing_names = [
            row["display_name"]
            for row in voter_forecast["rows"]
            if not row["important_communications"]
        ]
        st.warning(
            f"官方公開發言證據覆蓋：{evidence_coverage} / "
            f"{len(voter_forecast['rows'])}；待補：{'、'.join(missing_names)}。"
        )
    st.caption(
        f"[2026 年公開市場委員會委員官方來源]({voter_forecast['membership_source_url']})"
    )

    st.subheader("點選委員查看預測依據")
    selected_name = st.selectbox(
        "選擇有投票權委員",
        [row["display_name"] for row in voter_forecast["rows"]],
        help="依聯準會公布的 2026 年公開市場委員會委員名單；不等同會前出席保證。",
    )
    selected_member = next(
        row
        for row in voter_forecast["rows"]
        if row["display_name"] == selected_name
    )
    member_columns = st.columns(4)
    member_columns[0].metric(
        "預測投票",
        VOTE_LABELS[selected_member["predicted_vote"]],
        border=True,
    )
    member_columns[1].metric(
        "歷史異議率",
        f"{selected_member['prior_dissent_rate']:.1%}",
        border=True,
    )
    member_columns[2].metric(
        "歷史投票數",
        selected_member["prior_vote_count"],
        border=True,
    )
    member_columns[3].metric(
        "反對支持模型",
        f"{selected_member['against_support_count']} / "
        f"{selected_member['ensemble_model_count']}",
        border=True,
    )
    st.markdown(
        '<div class="dm-note"><strong>這個預測如何得出</strong><ul>'
        f'<li>政策方向：四個研究模型有 {ensemble["combined"]["policy"]["support_count"]} 個支持「'
        f'{ACTION_LABELS[policy["action_class"]]}」。</li>'
        f'<li>個人投票：有 {selected_member["against_support_count"]}／'
        f'{selected_member["ensemble_model_count"]} 個模型預測反對，因此綜合預測為「'
        f'{VOTE_LABELS[selected_member["predicted_vote"]]}」。</li>'
        f'<li>歷史脈絡：這位委員過去 {selected_member["prior_vote_count"]} 次投票中，'
        f'異議率為 {selected_member["prior_dissent_rate"]:.1%}。</li>'
        '<li>下方公開發言、過去投票與推定議題可用來核對預測依據；'
        '推定議題不是委員自述。</li></ul></div>',
        unsafe_allow_html=True,
    )

    with st.expander("重要公開發言", expanded=True):
        communications = selected_member["important_communications"]
        if communications:
            for index, item in enumerate(communications, start=1):
                link_text = f"{item['publication_date']}｜官方公開發言 {index}"
                if item["source_url"]:
                    st.markdown(
                        f"**[{link_text}]({item['source_url']})**"
                    )
                else:
                    st.markdown(f"**{link_text}**")
                st.caption(
                    f"官方文件編號：{item['document_id']}｜"
                    f"政策相關度分數：{item['importance_score']}"
                )
                if item["excerpt"] and item.get("text_kind") == "source_summary":
                    st.write(item["excerpt"])
                    st.caption("本段為官方頁面內容摘要；請點標題開啟原始官方文件。")
                elif item["excerpt"]:
                    st.caption(
                        "這筆資料目前只保存官方原文節錄；為避免未經稽核的翻譯扭曲政策語意，"
                        "請點連結查看原文，並以本頁的中文關注議題作為輔助。"
                    )
        else:
            st.info(
                "本機資料在截止日前沒有這位委員可安全使用的公開發言；"
                "系統不以其他人物或截止日後文件補齊。"
            )

    with st.expander("過去投票結果"):
        history = pd.DataFrame(
            [
                {
                    "會議": item["meeting_id"].removeprefix("FOMC-"),
                    "實際政策": ACTION_LABELS.get(
                        item["actual_policy_action"], item["actual_policy_action"]
                    ),
                    "投票": VOTE_LABELS.get(
                        item["voter_choice"], item["voter_choice"]
                    ),
                    "是否異議": "是" if item["dissent"] else "否",
                }
                for item in selected_member["vote_history"]
            ]
        )
        st.dataframe(history, width="stretch", hide_index=True)

    with st.expander("推定關注議題"):
        concerns = selected_member["inferred_concerns"]
        if concerns:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "推定議題": item["label"],
                            "規則式分數": item["score"],
                            "支持文件編號": "、".join(item["evidence_ids"]),
                        }
                        for item in concerns
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
            st.warning(
                "這些是依截止日前公開發言做的關鍵詞規則推定，不是委員自述，"
                "也不是目前投票模型的輸入。"
            )
        else:
            st.info("沒有足夠的截止日前公開發言可推定關注議題。")

    ai_key_available = user_scope_key_available()
    if not ai_key_available:
        key_status = "找不到 Windows 使用者層級服務金鑰；已鎖定的預測與證據不受影響。"
    else:
        key_status = "已偵測 Windows 使用者層級服務金鑰；按下按鈕後產生繁體中文說明。"
    action_columns = st.columns([2, 3])
    with action_columns[0]:
        generate_explanation = st.button(
            "用人工智慧統整預測理由",
            disabled=not ai_key_available,
            type="secondary",
        )
    action_columns[1].caption(key_status)
    if generate_explanation:
        explanation_key = f"member_explanation_{selected_member['participant_id']}"
        explanation_meta_key = explanation_key + "_meta"
        try:
            with st.spinner("正在用已顯示的證據產生稽核說明……"):
                st.session_state[explanation_key] = generate_member_explanation(
                    forecast,
                    selected_member,
                )
                st.session_state[explanation_meta_key] = {
                    "model": os.environ.get("FOMC_AI_EXPLAIN_MODEL", DEFAULT_MODEL),
                    "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
        except AiExplanationError as error:
            st.error(error.user_message)
            st.caption(f"診斷代碼：{error.code}")
        except Exception as error:
            classified = classify_ai_error(error)
            st.error(classified.user_message)
            st.caption(f"診斷代碼：{classified.code}")
    explanation_key = f"member_explanation_{selected_member['participant_id']}"
    if explanation_key in st.session_state:
        explanation_meta = st.session_state.get(explanation_key + "_meta", {})
        if explanation_meta:
            st.success(
                "人工智慧已完成統整｜"
                f"{explanation_meta['completed_at']}"
            )
        st.markdown("#### 人工智慧稽核說明")
        st.write(st.session_state[explanation_key])
        st.caption(
            "人工智慧僅重述畫面中的公開證據與規則結果，"
            "不會改變政策或投票預測。"
        )

    with st.expander("查看會前經濟特徵與資料日期"):
        feature_display = {
            "cpi_yoy": ("消費者物價年增率", "%", "本期指數與一年前相比"),
            "unemployment_level": ("失業率", "%", "直接取最新值"),
            "unemployment_12m_change": (
                "失業率12個月變化",
                "百分點",
                "最新值減一年前數值",
            ),
            "payroll_yoy": ("非農就業年增率", "%", "本期人數與一年前相比"),
            "credit_spread_baa10y": (
                "BAA–10年期公債利差",
                "百分點",
                "直接取最新利差",
            ),
            "yield_curve_10y_2y": (
                "10年－2年期殖利率曲線",
                "百分點",
                "10年殖利率減2年殖利率",
            ),
            "policy_midpoint": (
                "政策利率中點",
                "%",
                "目標區間上下限平均",
            ),
        }
        evidence_units = {
            "CPIAUCSL": "指數（1982–1984＝100）",
            "UNRATE": "%",
            "PAYEMS": "千人",
            "BAA10Y": "百分點",
            "DGS10": "%",
            "DGS2": "%",
            "DFEDTARL": "%",
            "DFEDTARU": "%",
        }
        st.markdown("#### 模型實際使用的衍生特徵")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "特徵": feature_display[name][0],
                        "數值": _format_two_decimals(value),
                        "單位": feature_display[name][1],
                        "計算方式": feature_display[name][2],
                    }
                    for name, value in forecast["features"].items()
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.markdown("#### 原始序列最新可見值")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "經濟序列": row["series_id"],
                        "觀測日期": row["observation_date"],
                        "當時可見版本日": row["visible_version_date"],
                        "數值": _format_two_decimals(row["value"]),
                        "單位": evidence_units[row["series_id"]],
                    }
                    for row in forecast["feature_evidence"]
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "所有數值統一顯示至小數點後兩位。百分點表示兩個百分率之間的差，"
            "不等同於百分比變動；觀測日期是統計值所屬期間，當時可見版本日是該值可被使用的日期。"
        )
    st.markdown(
        '<div class="dm-note">'
        f'資料截至 {forecast["forecast_as_of"]}；下一場日程來源：'
        '聯準會官方公開市場委員會日程。狀態：政策與投票為會前鎖定綜合預測；'
        '人工智慧僅在按鈕觸發後統整已顯示證據。</div>',
        unsafe_allow_html=True,
    )


def render_replay(meeting_id: str | None) -> None:
    st.title("決策重播")
    st.caption("只重播當時可見資訊；會後政策聲明與會議紀要僅作標準答案與稽核證據。")
    if meeting_id is None:
        raise ValueError("請先選擇一場聯準會會議")
    replay = _meeting_replay(meeting_id)
    meeting = replay["meeting"]
    outcome = replay["outcome"]
    columns = st.columns(4)
    start = meeting["meeting_start_date"]
    end = meeting["meeting_end_date"]
    end_label = end if start == end else f"{start}/{end[-2:]}"
    columns[0].metric("會議", end_label, border=True)
    columns[1].metric(
        "資訊截止日", meeting["information_cutoff_date_et"], border=True
    )
    columns[2].metric(
        "實際決策", ACTION_LABELS[outcome["action_class"]], border=True
    )
    columns[3].metric("新目標區間", _format_target(outcome), border=True)
    if replay["replay_tier"] == "full":
        extractor = replay["trace"]["extractor_version"]
        trace_label = (
            "人工稽核決策脈絡"
            if extractor == "human-audited-official-docs-v1"
            else "模型抽取決策脈絡／抽樣人工檢查"
        )
        st.markdown(
            f'<span class="dm-badge">{trace_label}</span> '
            '<span class="dm-badge">會後證據隔離</span>',
            unsafe_allow_html=True,
        )
    else:
        st.info(
            "這場會議沒有完整決策脈絡；以下只顯示可驗證的會議日期、"
            "政策結果、逐人投票與當時可見經濟資料，不生成具名歷史發言。"
        )

    series_frame = pd.DataFrame(replay["series"])
    if not series_frame.empty:
        st.subheader("當時可見的關鍵序列（各最近 24 筆）")
        series_ids = series_frame["series_id"].drop_duplicates().tolist()
        chart_colors = ("#54B5C6", "#E03F19")
        for start_index in range(0, len(series_ids), 2):
            chart_columns = st.columns(2)
            for offset, sid in enumerate(series_ids[start_index : start_index + 2]):
                latest = _replay_series_display_frame(series_frame, sid)
                label = REPLAY_SERIES_LABELS.get(sid, SERIES_LABELS.get(sid, sid))
                with chart_columns[offset]:
                    st.markdown(f"**{label}**")
                    if len(latest) < 2:
                        st.caption("資料不足，無法繪圖")
                        continue
                    value_min = float(latest["value_num"].min())
                    value_max = float(latest["value_num"].max())
                    value_range = value_max - value_min
                    y_padding = value_range * 0.08
                    if y_padding == 0:
                        y_padding = max(abs(value_min) * 0.08, 0.1)
                    chart = (
                        alt.Chart(latest)
                        .mark_line(
                            color=chart_colors[(start_index + offset) % 2]
                        )
                        .encode(
                            x=alt.X(
                                "observation_date:T",
                                axis=alt.Axis(
                                    format="%Y-%m", labelAngle=-45, title=None
                                ),
                            ),
                            y=alt.Y(
                                "value_num:Q",
                                title=None,
                                scale=alt.Scale(
                                    zero=False,
                                    nice=True,
                                    domain=[
                                        value_min - y_padding,
                                        value_max + y_padding,
                                    ],
                                ),
                            ),
                            tooltip=[
                                alt.Tooltip(
                                    "observation_date:T", title="日期", format="%Y-%m"
                                ),
                                alt.Tooltip(
                                    "value_num:Q", title="數值", format=".2f"
                                ),
                            ],
                        )
                        .properties(height=220)
                        .configure_axis(
                            gridColor="#E6EBF0",
                            labelColor="#5B6B7B",
                            domainColor="#DFE5EC",
                        )
                        .configure_view(strokeWidth=0)
                    )
                    st.altair_chart(chart, width="stretch")

    if replay["replay_tier"] == "base":
        st.subheader("實際政策與投票")
        vote_frame = _localized_vote_frame(replay["votes"])
        st.dataframe(
            vote_frame.style.apply(_vote_row_style, axis=1),
            width="stretch",
            hide_index=True,
        )
        with st.expander("資料來源與驗證"):
            st.caption(
                f"結果證據：{outcome['source_document_id']} · "
                f"證據內容雜湊：{replay['evidence_document']['content_hash']}"
            )
        return

    tab_context, tab_options, tab_debate, tab_decision = st.tabs(
        ["情境", "選項", "討論", "決策與投票"]
    )
    with tab_context:
        st.write(replay["context"]["summary"])
        with st.expander("官方證據來源"):
            for reference in replay["context"].get("evidence_refs", []):
                reference_meta = []
                if reference.get("document_id"):
                    reference_meta.append(f"官方文件編號：{reference['document_id']}")
                if reference.get("locator"):
                    reference_meta.append(f"文件位置：{reference['locator']}")
                if reference_meta:
                    st.caption("｜".join(reference_meta))
                if reference.get("excerpt"):
                    st.caption(f"原文節錄：{reference['excerpt']}")
    with tab_options:
        for option in replay["trace"]["options"]:
            st.markdown(f"**{option['description']}**")
            st.caption(option["evidence_refs"][0]["excerpt"])
    with tab_debate:
        for item in replay["trace"]["debate"]:
            st.write(item["position"])
            st.caption(item["reasoning"])
            st.caption("官方證據：" + item["evidence_refs"][0]["excerpt"])
    with tab_decision:
        decision = replay["trace"]["decision"]
        st.markdown(
            f"- **動作：** {ACTION_LABELS.get(decision.get('action_class'), decision.get('action_class'))}\n"
            f"- **目標區間：** {_format_target(decision)}\n"
            f"- **理由：** {decision.get('rationale', '未提供')}"
        )
        if decision.get("evidence_refs"):
            with st.expander("決策證據"):
                for reference in decision["evidence_refs"]:
                    reference_meta = []
                    if reference.get("document_id"):
                        reference_meta.append(f"官方文件編號：{reference['document_id']}")
                    if reference.get("locator"):
                        reference_meta.append(f"文件位置：{reference['locator']}")
                    if reference_meta:
                        st.caption("｜".join(reference_meta))
                    if reference.get("excerpt"):
                        st.caption(f"原文節錄：{reference['excerpt']}")
        known_decision_fields = {
            "action_class",
            "evidence_refs",
            "rationale",
            "target_lower",
            "target_rate",
            "target_upper",
        }
        raw_decision = {
            key: value
            for key, value in decision.items()
            if key not in known_decision_fields
        }
        if raw_decision:
            with st.expander("原始決策紀錄"):
                st.json(raw_decision, expanded=False)
        vote_frame = _localized_vote_frame(replay["votes"])
        st.dataframe(
            vote_frame.style.apply(_vote_row_style, axis=1),
            width="stretch",
            hide_index=True,
        )
    st.subheader("可監控假設")
    assumption_labels = {
        "assumption_id": "編號",
        "claim": "假設陳述",
        "monitor_series_id": "監控序列",
        "monitor_operator": "判定方式",
        "threshold_value": "門檻",
        "status": "狀態",
        "monitor_rule_version": "監控規則版本",
    }
    operator_labels = {
        "LT": "小於",
        "LTE": "小於或等於",
        "EQ": "等於",
        "GTE": "大於或等於",
        "GT": "大於",
    }
    assumption_rows = []
    for assumption in replay["assumptions"]:
        display_row = {}
        for key in assumption_labels:
            if key not in assumption:
                continue
            value = assumption[key]
            if key == "monitor_series_id":
                value = SERIES_LABELS.get(value, value)
            elif key == "monitor_operator":
                value = operator_labels.get(value, value)
            display_row[assumption_labels[key]] = value
        for key, value in assumption.items():
            if key not in assumption_labels:
                display_row[key] = value
        assumption_rows.append(display_row)
    st.dataframe(
        pd.DataFrame(assumption_rows),
        width="stretch",
        hide_index=True,
        column_config={
            "假設陳述": st.column_config.TextColumn(width="large"),
            "編號": st.column_config.TextColumn(width="small"),
        },
    )
    source_refresh = _load_source_refresh_audit()
    with st.expander("資料來源與驗證"):
        st.caption(
            f"擷取器：{replay['trace']['extractor_version']} · 結果證據："
            f"{outcome['source_document_id']}"
        )
        st.caption(
            f"來源資料庫內容雜湊：{source_refresh['after_sha256']} · "
            f"證據內容雜湊：{replay['evidence_document']['content_hash']}"
        )


def render_monitor() -> None:
    st.title("假設監控")
    st.caption("以可觀察的政策聲明措辭與利率行動，分開量測聯準會的承認落差與政策節奏。")
    monitor = _fomc_monitor()
    events = monitor["events"]
    contradiction = events["CONTRADICTION"]
    flip = events["STATEMENT_FLIP"]
    response = events["POLICY_RESPONSE"]
    recognition = flip["payload"]["recognition_lag_days"]
    action = response["payload"]["action_lag_days"]
    total = response["payload"]["response_lag_days"]
    columns = st.columns(3)
    columns[0].metric("可觀察的承認落差", f"{recognition} 天", border=True)
    columns[1].metric("僅利率行動落差", f"{action} 天", border=True)
    columns[2].metric("完整反應落差", f"{total} 天", border=True)
    timeline = pd.DataFrame(
        [
            {"事件": "首次反證", "日期": contradiction["occurred_at"][:10]},
            {"事件": "政策聲明措辭翻轉", "日期": flip["occurred_at"][:10]},
            {"事件": "同方向政策利率變動", "日期": response["occurred_at"][:10]},
        ]
    )
    st.dataframe(timeline, width="stretch", hide_index=True)
    st.markdown(
        '<div class="dm-warning">承認落差是預先登記政策聲明片語的可觀察代理，'
        '不是對內在認知的讀心；行動落差可能是刻意的政策節奏，不宣稱是失憶。</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "規則：暫時性通膨反證／聲明片語翻轉／僅利率行動；"
        "非利率工具不會將政策反應事件標記為完成。"
    )

    alert_audit = _load_alert_audit()
    rate_constraint_audit = _load_rate_constraint_audit()
    st.subheader("聲明警示規則稽核")
    audit_columns = st.columns(4)
    audit_columns[0].metric(
        "反證前規則誤觸發",
        f"{alert_audit['temporal_false_alarm_count']} / "
        f"{alert_audit['pre_contradiction_statement_count']}",
        border=True,
    )
    cooccurrence_count = alert_audit["support_flip_cooccurrence_count"]
    audit_columns[1].metric(
        "支持／翻轉共存樣本",
        "0（不可估）" if cooccurrence_count == 0 else str(cooccurrence_count),
        border=True,
    )
    audit_columns[2].metric(
        "已稽核聲明", str(alert_audit["statement_count"]), border=True
    )
    audit_columns[3].metric(
        "固定測試集利率受限場次",
        f"{rate_constraint_audit['constrained_case_count']} / "
        f"{rate_constraint_audit['case_count']}",
        border=True,
    )
    st.caption(
        "這是預先登記文字規則的機械控制稽核，不是人工語意真值。反證前只有 1 份"
        "政策聲明，且沒有支持／翻轉共存樣本；因此不能宣稱整體誤報率已被精確估計。"
    )
    st.caption(
        "僅利率行動的完整反應落差，在固定 45 場測試中有 9 場受利率下限約束；"
        "設限可能具有系統性。旗艦承認落差量測政策聲明措辭，"
        "不受這個利率約束設限。"
    )

def render_simulation() -> None:
    st.title("模擬與證據")
    st.caption("固定離線產物；評估不由另一個語言模型當唯一裁判。")
    participant_mode = st.selectbox("參與者識別", ["顯示姓名", "匿名"])
    reaction_mode = st.selectbox("反應輪廓", ["開啟", "關閉"])
    variant_lookup = {
        ("顯示姓名", "開啟"): "named_persona_reaction",
        ("匿名", "開啟"): "anonymous_persona_reaction",
        ("顯示姓名", "關閉"): "named_persona_no_reaction",
    }
    variant_id = variant_lookup.get((participant_mode, reaction_mode))
    variant_spec = _load_variant_spec()
    variant = next(
        (
            item
            for item in variant_spec["variants"]
            if item["variant_id"] == variant_id
        ),
        None,
    )
    if variant is None:
        st.warning("此控制組合不在固定的第五版變體規格，系統不會臨時創造結果。")
        executed_variant = None
    else:
        try:
            executed_variant = _load_completed_variant(
                variant_id, "FOMC-2022-03-15"
            )
        except ValueError as error:
            st.error(f"變體產物驗證失敗：{error}")
            return
        if executed_variant is None:
            st.info(
                f"所選變體：{MODEL_LABELS[variant_id]}。"
                "完整 45 場評估尚未封存，因此下方維持固定離線備援結果。"
            )
        else:
            st.success(
                f"所選變體：{MODEL_LABELS[variant_id]}。45／45 場訂閱額度開發執行"
                "已完成並通過內容雜湊驗證；未產生平台額外費用。"
            )
    reaction, evaluation, simulation = _load_artifacts()
    feature_contract = _load_reaction_feature_contract()
    actual_case = next(
        item
        for item in evaluation["per_case"]
        if item["meeting_id"] == simulation["output"]["meeting_id"]
    )
    columns = st.columns(4)
    columns[0].metric(
        "真實政策", ACTION_LABELS[actual_case["actual"]], border=True
    )
    columns[1].metric(
        "延續性基準", ACTION_LABELS[actual_case["persistence"]], border=True
    )
    columns[2].metric(
        "總體反應模型",
        ACTION_LABELS[actual_case["pooled_reaction"]],
        border=True,
    )
    columns[3].metric(
        "離線合成投票",
        f"{simulation['validation']['for_count']}-0",
        border=True,
    )
    st.error(
        "這個基準錯誤預測維持利率；真實結果是升息。流程可驗證，不代表模型已足夠好。"
    )

    metrics_rows = []
    for name, values in evaluation["metrics"].items():
        metrics_rows.append(
            {
                "模型": MODEL_LABELS.get(name, name),
                "正確率": values["accuracy"],
                "各類綜合分數": values["macro_f1"],
                "平均方向誤差": values["mean_absolute_action_error"],
                "維持利率時的錯誤行動": values["false_action_count_on_hold"],
            }
        )
    st.subheader("45 場固定政策測試")
    st.dataframe(
        pd.DataFrame(metrics_rows).style.format(
            {
                "正確率": "{:.1%}",
                "各類綜合分數": "{:.3f}",
                "平均方向誤差": "{:.3f}",
            }
        ),
        width="stretch",
        hide_index=True,
    )

    try:
        variant_matrix = _load_completed_matrix()
    except ValueError as error:
        st.error(f"完整變體矩陣驗證失敗：{error}")
        return
    if variant_matrix is not None:
        matrix_source = pd.DataFrame(variant_matrix["rows"])
        matrix_source["variant_id"] = matrix_source["variant_id"].map(MODEL_LABELS)
        matrix_rows = matrix_source[
            [
                "variant_id",
                "n",
                "policy_accuracy",
                "policy_action_mae",
                "dissent_precision",
                "dissent_recall",
                "dissent_f1",
            ]
        ].rename(
            columns={
                "variant_id": "變體",
                "n": "樣本數",
                "policy_accuracy": "政策正確率",
                "policy_action_mae": "平均方向誤差",
                "dissent_precision": "異議精確率",
                "dissent_recall": "異議召回率",
                "dissent_f1": "異議綜合分數",
            }
        )
        st.subheader("第五版固定 45 場完整變體矩陣")
        matrix_display = matrix_rows.copy()
        matrix_display["政策正確率"] = matrix_display["政策正確率"].map(
            lambda value: f"{value:.1%}"
        )
        matrix_display["平均方向誤差"] = matrix_display["平均方向誤差"].map(
            lambda value: f"{value:.3f}"
        )
        for column in ("異議精確率", "異議召回率"):
            matrix_display[column] = matrix_display[column].map(
                lambda value: "無資料" if pd.isna(value) else f"{value:.1%}"
            )
        matrix_display["異議綜合分數"] = matrix_display["異議綜合分數"].map(
            lambda value: "無資料" if pd.isna(value) else f"{value:.3f}"
        )
        st.dataframe(
            matrix_display,
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "矩陣包含 3 個確定性基準與 5 個單次固定 45 場語言模型"
            "開發變體；僅會議日期的測試正確率必須解讀為答案記憶警示，"
            "不是可部署表現。"
        )
        st.warning(
            "具名委員與歷史反應模型的政策正確率，與其他兩個語言模型變體"
            "並列 97.8%，且異議綜合分數最高；但僅會議日期的測試也達 91.1%，"
            "顯示歷史答案記憶風險。它現已納入首頁的會前鎖定綜合預測，"
            "但真正成效要等會後結果才能驗證。"
        )

    if executed_variant is not None:
        aggregate = executed_variant["status"]["aggregate"]
        st.subheader("所選變體：固定 45 場訂閱額度開發結果")
        selected_metrics = pd.DataFrame(
            [
                {
                    "變體": MODEL_LABELS[variant_id],
                    "樣本數": aggregate["case_count"],
                    "政策正確率": aggregate["policy_accuracy"],
                    "平均方向誤差": aggregate["policy_action_mae"],
                    "異議基準率": aggregate["dissent_base_rate"],
                    "異議精確率": aggregate["dissent_precision"],
                    "異議召回率": aggregate["dissent_recall"],
                    "異議綜合分數": aggregate["dissent_f1"],
                }
            ]
        )
        st.dataframe(
            selected_metrics.style.format(
                {
                    "政策正確率": "{:.1%}",
                    "平均方向誤差": "{:.3f}",
                    "異議基準率": "{:.1%}",
                    "異議精確率": "{:.1%}",
                    "異議召回率": "{:.1%}",
                    "異議綜合分數": "{:.3f}",
                }
            ),
            width="stretch",
            hide_index=True,
        )

        report = executed_variant["report"]
        vote_truth = _meeting_replay(report["meeting_id"])["votes"]
        vote_comparison = build_voter_vote_comparison(
            report,
            vote_truth,
            reveal_identity=participant_mode == "顯示姓名",
        )
        st.subheader("核心預測：逐位已知投票者的贊成／反對")
        st.caption(
            "投票者名單是會前輸入；贊成／反對才是預測目標。"
            "本表不把是否猜中誰出席列為分數。"
        )
        vote_metrics = st.columns(4)
        vote_metrics[0].metric(
            "已知投票者", str(vote_comparison["voter_count"]), border=True
        )
        vote_metrics[1].metric(
            "正確投票預測",
            f"{vote_comparison['correct_count']} / {vote_comparison['voter_count']}",
            border=True,
        )
        vote_metrics[2].metric(
            "預測異議者",
            str(len(vote_comparison["predicted_dissenters"])),
            border=True,
        )
        vote_metrics[3].metric(
            "實際異議者",
            str(len(vote_comparison["actual_dissenters"])),
            border=True,
        )
        vote_rows = pd.DataFrame(vote_comparison["rows"]).rename(
            columns={
                "voter": "已知投票者" if participant_mode == "顯示姓名" else "匿名投票者",
                "predicted_choice": "預測投票",
                "actual_choice": "實際投票",
                "correct": "是否正確",
                "dissent_result": "異議結果",
            }
        )
        st.dataframe(vote_rows, width="stretch", hide_index=True)
        if vote_comparison["missed_dissenters"]:
            st.error(
                "漏掉的實際異議者："
                + ", ".join(vote_comparison["missed_dissenters"])
            )
        if vote_comparison["false_alarm_dissenters"]:
            st.warning(
                "誤報的異議者："
                + ", ".join(vote_comparison["false_alarm_dissenters"])
            )
        if not vote_comparison["missed_dissenters"] and not vote_comparison[
            "false_alarm_dissenters"
        ]:
            st.success("所有逐位投票者的贊成／反對預測均符合標籤。")

    st.subheader("參與者反應輪廓卡")
    if reaction_mode == "關閉":
        st.warning("反應輪廓已關閉；系統不會偷用或顯示輪廓產物。")
    else:
        profiles = _load_profile_cards()
        labels = []
        cards_by_label = {}
        for index, card in enumerate(profiles["cards"], start=1):
            role = "主席" if card["is_chair"] else (
                "投票者" if card["is_voter"] else "參與者"
            )
            identity = (
                card["display_name"]
                if participant_mode == "顯示姓名"
                else f"參與者 {index:02d}"
            )
            label = f"{identity} · {role}"
            labels.append(label)
            cards_by_label[label] = card
        selected_label = st.selectbox("參與者輪廓卡", labels)
        selected_card = cards_by_label[selected_label]
        st.markdown(f"**輪廓參與者：** {selected_label}")
        profile_columns = st.columns(3)
        profile_columns[0].metric(
            "會前歷史投票", selected_card["prior_vote_count"], border=True
        )
        profile_columns[1].metric(
            "會前歷史異議", selected_card["prior_dissent_count"], border=True
        )
        dissent_rate = selected_card["prior_dissent_rate"]
        profile_columns[2].metric(
            "歷史異議率",
            "無資料" if dissent_rate is None else f"{dissent_rate:.1%}",
            border=True,
        )
        coefficient_frame = pd.DataFrame(
            {
                "總體特徵": list(selected_card["macro_coefficients"].keys()),
                "共用係數": list(
                    selected_card["macro_coefficients"].values()
                ),
            }
        ).set_index("總體特徵")
        st.bar_chart(coefficient_frame, color="#7F74B5")
        st.caption(
            f"{profiles['participant_count']} 張可讀的委員輪廓卡；"
            f"訓練會議數 {profiles['training_meeting_count']}（2006–2020），"
            f"估計{'已' if reaction['converged'] else '未'}收旂。個人欄位只使用本場會議前的投票／"
            "異議紀錄；總體係數來自共用模型，沒有估計個人係數。"
            "此模型目前低於延續性基準，因此只作可解釋參考。"
        )
        st.caption(
            "比賽特徵規格："
            f"{feature_contract['approved_proxy_series_id']} 是窄義信用情勢代理序列，"
            "不是完整金融情勢指標。"
        )

    if executed_variant is None:
        with st.expander("查看離線合成討論與主席提案"):
            st.code(
                json.dumps(simulation["output"], ensure_ascii=False, indent=2),
                language="json",
            )
        st.markdown(
            '<div class="dm-warning">此執行為已封存離線基準，額外費用為 0；'
            '不以離線模板冒充尚未完成的變體結果。</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"離線執行編號：{simulation['run_id']} · "
            f"資料切分內容雜湊：{evaluation['split_manifest']['manifest_hash']}"
        )
    else:
        report = executed_variant["report"]
        case_evaluation = report["evaluation"]
        with st.expander("查看所選變體的合成討論、主席提案與投票"):
            st.code(
                json.dumps(report["model_output"], ensure_ascii=False, indent=2),
                language="json",
            )
        st.markdown(
            '<div class="dm-warning">此為訂閱額度的單次開發評估，'
            '不是正式平台升級測試；沒有重複執行、信賴區間或抽樣變異估計。</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"案例 {report['meeting_id']} · "
            f"預測={ACTION_LABELS[case_evaluation['predicted_action']]} · "
            f"實際={ACTION_LABELS[case_evaluation['actual_action']]} · 平台額外費用=0 · "
            f"案例資料包內容雜湊：{executed_variant['case']['model_bundle_hash']}"
        )


def render_historical_results() -> None:
    st.title("歷史測試結果")
    st.caption(
        "這一頁只回答兩個問題：模型過去有沒有猜對政策方向？"
        "有沒有抓到委員的反對票？"
    )
    matrix = _load_completed_matrix()
    if matrix is None:
        st.info("歷史測試結果尚未完整封存。")
        return

    rows_by_id = {row["variant_id"]: row for row in matrix["rows"]}
    model_ids = tuple(FORECAST_MODEL_DETAILS)
    missing = [model_id for model_id in model_ids if model_id not in rows_by_id]
    if missing:
        st.error("歷史測試資料不完整，無法顯示。")
        return

    model_rows = [rows_by_id[model_id] for model_id in model_ids]
    policy_rates = [row["policy_accuracy"] for row in model_rows]
    persistence_rate = rows_by_id["persistence_deterministic"]["policy_accuracy"]
    best_dissent_score = max(row["dissent_f1"] for row in model_rows)
    summary_columns = st.columns(2)
    summary_columns[0].metric("測試會議", f"{matrix['case_count']} 場", border=True)
    summary_columns[1].metric(
        "四個模型政策猜對率",
        f"{min(policy_rates):.1%}－{max(policy_rates):.1%}",
        border=True,
    )
    comparison_columns = st.columns(2)
    comparison_columns[0].metric(
        "簡單延續判斷", f"{persistence_rate:.1%}", border=True
    )
    comparison_columns[1].metric(
        "最佳反對票整體表現",
        f"{best_dissent_score:.2f} / 1.00",
        border=True,
    )

    st.info(
        "閱讀方式：政策方向猜對率越高越好；"
        "反對票的三個欄位是看模型有沒有真正找到少數反對者，"
        "0 代表完全沒抓到，1 代表完全正確。"
    )
    table_rows = []
    for model_id, row in zip(model_ids, model_rows):
        table_rows.append(
            {
                "模型": FORECAST_MODEL_DETAILS[model_id]["label"],
                "場次": row["n"],
                "政策猜對率": f"{row['policy_accuracy']:.1%}",
                "預測反對有多準": f"{row['dissent_precision']:.1%}",
                "實際反對抓到多少": f"{row['dissent_recall']:.1%}",
                "反對票整體表現（0－1）": f"{row['dissent_f1']:.2f}",
            }
        )
    history_table = pd.DataFrame(table_rows)
    higher_is_better = (
        "政策猜對率",
        "預測反對有多準",
        "實際反對抓到多少",
        "反對票整體表現（0－1）",
    )
    maxima = {
        column: max(float(str(value).rstrip("%")) for value in history_table[column])
        for column in higher_is_better
    }
    first_column = higher_is_better[0]
    history_style = history_table.style.map(
        lambda value: (
            "color:#1E6B45;font-weight:bold"
            if float(str(value).rstrip("%")) == maxima[first_column]
            else ""
        ),
        subset=[first_column],
    )
    for column in higher_is_better[1:]:
        history_style = history_style.map(
            lambda value, maximum=maxima[column]: (
                "color:#1E6B45;font-weight:bold"
                if float(str(value).rstrip("%")) == maximum
                else ""
            ),
            subset=[column],
        )
    st.dataframe(history_style, width="stretch", hide_index=True)
    st.caption("首頁會把這四個模型一起使用，不是只選其中一個。")
    st.warning(
        "歷史測試不等於未來準確率。僅用會議日期也能達到 91.1%，"
        "顯示模型可能記得歷史答案；而且表現最好的模型也只抓到約 "
        "30.4% 的實際反對票，這仍是目前最難的部分。"
    )


st.sidebar.markdown(
    '<div class="dm-brand">'
    '<div class="dm-brand-title">🏦 聯準會決策預測實驗室</div>'
    '<div class="dm-brand-subtitle">會前鎖定 · 官方證據 · 可稽核</div>'
    "</div>",
    unsafe_allow_html=True,
)
st.sidebar.caption(
    "下次會議預測：先看政策與投票結論。  \n"
    "決策重播：回到當時資訊檢視決策。  \n"
    "歷史測試結果：比較模型過往表現。"
)
page = st.sidebar.radio(
    "頁面",
    ["下次會議預測", "決策重播", "歷史測試結果"],
)
selected_meeting_id = None
if page == "決策重播":
    case_catalog = _fomc_case_catalog()
    full_only = st.sidebar.checkbox("只列有完整決策脈絡的會議", value=False)
    visible_catalog = (
        [item for item in case_catalog if item["replay_tier"] == "full"]
        if full_only
        else case_catalog
    )
    labels = [item["label"] for item in visible_catalog]
    selected_label = st.sidebar.selectbox("會議／決策案例", labels)
    selected_meeting_id = next(
        item["meeting_id"]
        for item in visible_catalog
        if item["label"] == selected_label
    )
    full_count = sum(item["replay_tier"] == "full" for item in case_catalog)
    st.sidebar.caption(
        f"完整重播 {full_count} 場｜基礎政策案例 {len(case_catalog) - full_count} 場"
    )
st.sidebar.markdown("---")
st.sidebar.markdown(
    '<div class="dm-sidebar-foot">'
    "所有會議回放與模型輸出均明確標記來源／合成資料邊界。"
    "</div>",
    unsafe_allow_html=True,
)

try:
    if page == "下次會議預測":
        render_next_meeting_forecast()
    elif page == "決策重播":
        render_replay(selected_meeting_id)
    else:
        render_historical_results()
except Exception as error:
    st.error(f"系統資料載入失敗：{error}")
    st.exception(error)
