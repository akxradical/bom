"""
AGENTIC BOM — Universal Should-Cost Engine (Streamlit UI)
═══════════════════════════════════════════════════════════════════
Drop any engineered-product datasheet. The agent identifies the product,
builds a product-specific schema, populates the BOM, validates completeness,
prices it from live market rates, and scores its confidence — live.

Backend: claude_engine.run_agent()
Deploy : Streamlit Cloud — pumpbom.streamlit.app  (github: akxradical/bom)
"""

import time
import pandas as pd
import streamlit as st

from claude_engine import (
    extract_pdf_text, run_agent, bom_to_dataframe, export_excel,
)

# ═══════════════════════════════════════════════════════════════════
# PAGE CONFIG + THEME
# ═══════════════════════════════════════════════════════════════════

st.set_page_config(page_title="Agentic BOM", page_icon="◈", layout="wide",
                   initial_sidebar_state="collapsed")

# Palette
BG      = "#0a0a0f"   # near-black
PANEL   = "#12121a"
ACCENT  = "#e8a020"   # amber/gold
STEEL   = "#4a7a9b"   # steel blue
MOSS    = "#3d6b4f"   # success green
RED     = "#c0504d"
MUTE    = "#8b8b9b"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

.stApp {{ background: {BG}; color: #e6e6ee; }}
#MainMenu, footer, header {{ visibility: hidden; }}
.block-container {{ padding-top: 2rem; max-width: 1200px; }}

h1, h2, h3, .wordmark {{ font-family: 'Space Grotesk', sans-serif !important; letter-spacing: -0.5px; }}
.mono, code, pre {{ font-family: 'IBM Plex Mono', monospace !important; }}

.wordmark {{
    font-size: 42px; font-weight: 700; color: #fff; line-height: 1;
}}
.wordmark .dot {{ color: {ACCENT}; }}
.tagline {{ color: {MUTE}; font-family: 'IBM Plex Mono', monospace; font-size: 14px; margin-top: 6px; }}

.panel {{
    background: {PANEL}; border: 1px solid #23232f; border-radius: 10px;
    padding: 18px 20px; margin: 12px 0;
}}
.sec-label {{
    font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: 2px;
    text-transform: uppercase; color: {ACCENT}; margin-bottom: 8px;
}}

/* Terminal log */
.terminal {{
    background: #06060a; border: 1px solid #1d1d28; border-radius: 8px;
    padding: 16px 18px; font-family: 'IBM Plex Mono', monospace; font-size: 13px;
    line-height: 1.7; color: #c9d1d9; min-height: 90px; max-height: 360px;
    overflow-y: auto; white-space: pre-wrap;
}}
.terminal .ok {{ color: {MOSS}; }}
.terminal .run {{ color: {ACCENT}; }}

/* KPI cards */
.kpi {{
    background: {PANEL}; border: 1px solid #23232f; border-radius: 10px;
    padding: 16px 18px; text-align: center; height: 100%;
}}
.kpi .v {{ font-family: 'Space Grotesk', sans-serif; font-size: 26px; font-weight: 700; color: #fff; }}
.kpi .l {{ font-family: 'IBM Plex Mono', monospace; font-size: 10px; letter-spacing: 1px;
           text-transform: uppercase; color: {MUTE}; margin-top: 4px; }}
.kpi .v.amber {{ color: {ACCENT}; }}
.kpi .v.green {{ color: #5fbf7f; }}
.kpi .v.red   {{ color: #e06b67; }}

.stButton > button {{
    background: {ACCENT}; color: #0a0a0f; border: none; border-radius: 8px;
    font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 15px;
    padding: 10px 28px; letter-spacing: 0.5px;
}}
.stButton > button:hover {{ background: #ffb733; color: #000; }}
.stDownloadButton > button {{
    background: {MOSS}; color: #fff; border: none; border-radius: 8px;
    font-family: 'Space Grotesk', sans-serif; font-weight: 700;
}}

.stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
.stTabs [data-baseweb="tab"] {{
    background: {PANEL}; border-radius: 8px 8px 0 0; color: {MUTE};
    font-family: 'IBM Plex Mono', monospace; font-size: 13px;
}}
.stTabs [aria-selected="true"] {{ background: {STEEL}; color: #fff; }}

div[data-testid="stFileUploader"] {{
    background: {PANEL}; border: 1.5px dashed #34343f; border-radius: 10px; padding: 8px;
}}
.warn-box {{
    background: rgba(192,80,77,0.12); border: 1px solid {RED}; border-radius: 8px;
    padding: 12px 16px; color: #f0b8b6; font-family: 'IBM Plex Mono', monospace; font-size: 13px;
}}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════

if "result" not in st.session_state: st.session_state.result = None
if "log_lines" not in st.session_state: st.session_state.log_lines = []

# ═══════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════

st.markdown(
    '<div class="wordmark">AGENTIC<span class="dot">·</span>BOM</div>'
    '<div class="tagline">Drop any engineered product datasheet. '
    'The agent reads it, builds the BOM, and prices it at floor cost.</div>',
    unsafe_allow_html=True)

st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════
# UPLOAD + RUN
# ═══════════════════════════════════════════════════════════════════

st.markdown('<div class="sec-label">◈ Input</div>', unsafe_allow_html=True)
uploaded = st.file_uploader(
    "Datasheet PDF (pump, compressor, agitator, valve, fan, heat exchanger, crane, chiller, ...)",
    type=["pdf"], label_visibility="collapsed")

run = st.button("▶  RUN AGENT", use_container_width=False, disabled=(uploaded is None))


def _classify_line(line):
    """Wrap terminal line with color span based on its marker."""
    safe = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    if " ✓ " in safe: return f'<span class="ok">{safe}</span>'
    if " ◈ " in safe: return f'<span class="run">{safe}</span>'
    return safe


if run and uploaded is not None:
    # ── extract text ───────────────────────────────────────────────
    pdf_bytes = uploaded.read()
    pdf_text, err = extract_pdf_text(pdf_bytes)
    if err or not pdf_text.strip():
        st.markdown(f'<div class="warn-box">Could not read PDF text: {err or "empty document"}.</div>',
                    unsafe_allow_html=True)
        st.stop()

    st.markdown('<div class="sec-label">◈ Agent Log</div>', unsafe_allow_html=True)
    log_box = st.empty()
    st.session_state.log_lines = []

    def progress_cb(line, agent_log):
        # engine sends the latest formatted terminal line
        lines = st.session_state.log_lines
        # replace a running (◈) line with its ✓ completion for same step prefix
        lines.append(line)
        body = "<br>".join(_classify_line(l) for l in lines[-40:])
        log_box.markdown(f'<div class="terminal">{body}</div>', unsafe_allow_html=True)

    try:
        result = run_agent(pdf_text, progress_callback=progress_cb)
        st.session_state.result = result
    except Exception as e:
        import traceback
        st.markdown(f'<div class="warn-box">Agent error: {e}</div>', unsafe_allow_html=True)
        st.code(traceback.format_exc())
        st.stop()

# ═══════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════

result = st.session_state.result
if result:
    bom = result.get("bom", [])
    sc = result.get("should_cost", {})
    conf = result.get("confidence", 0.0)
    df = bom_to_dataframe(bom)

    conf_pct = int(round(conf * 100))
    if conf >= 0.85:   conf_cls, conf_word = "green", "HIGH"
    elif conf >= 0.65: conf_cls, conf_word = "amber", "MEDIUM"
    else:              conf_cls, conf_word = "red", "LOW"

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    st.markdown('<div class="sec-label">◈ Results</div>', unsafe_allow_html=True)

    # ── KPI ROW ────────────────────────────────────────────────────
    k1, k2, k3, k4, k5 = st.columns(5)
    total_exgst = sc.get("total_ex_gst", 0)
    kpis = [
        (k1, result.get("equipment_type", "—")[:26], "Equipment", ""),
        (k2, str(sc.get("component_count", len(bom))), "Components", ""),
        (k3, str(len(result.get("schema", []))), "Sub-assemblies", ""),
        (k4, f"₹{total_exgst:,}", "Should-Cost (ex-GST)", "amber"),
        (k5, f"{conf_pct}%", f"Confidence · {conf_word}", conf_cls),
    ]
    for col, val, label, cls in kpis:
        col.markdown(
            f'<div class="kpi"><div class="v {cls}">{val}</div>'
            f'<div class="l">{label}</div></div>', unsafe_allow_html=True)

    if conf < 0.65:
        st.markdown(
            '<div class="warn-box" style="margin-top:12px">⚠ Low confidence — '
            'engineer review strongly recommended before this BOM/cost is used.</div>',
            unsafe_allow_html=True)

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    # ── TABS ───────────────────────────────────────────────────────
    t_bom, t_cost, t_log, t_export = st.tabs(
        ["📋 BOM Table", "💰 Should-Cost", "🧠 Agent Log", "⬇ Export"])

    # --- BOM Table ---
    with t_bom:
        if df.empty:
            st.info("No components generated.")
        else:
            subs = ["All"] + sorted(df["Sub_Assembly"].dropna().unique().tolist())
            pick = st.selectbox("Filter by sub-assembly", subs, index=0)
            view = df if pick == "All" else df[df["Sub_Assembly"] == pick]
            st.dataframe(view, use_container_width=True, hide_index=True, height=460)
            st.caption(f"{len(view)} of {len(df)} components shown")

    # --- Should-Cost ---
    with t_cost:
        sub_totals = sc.get("sub_totals", {})
        if sub_totals:
            chart_df = pd.DataFrame(
                {"Sub-assembly": list(sub_totals.keys()),
                 "Cost (₹)": list(sub_totals.values())}).set_index("Sub-assembly")
            st.bar_chart(chart_df, color=ACCENT, height=300)

        c1, c2, c3 = st.columns(3)
        c1.markdown(f'<div class="kpi"><div class="v">₹{sc.get("total_raw_material",0):,}</div>'
                    f'<div class="l">Raw Material</div></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="kpi"><div class="v">₹{sc.get("total_machining",0):,}</div>'
                    f'<div class="l">Machining</div></div>', unsafe_allow_html=True)
        c3.markdown(f'<div class="kpi"><div class="v amber">₹{sc.get("total_incl_gst",0):,}</div>'
                    f'<div class="l">Total incl-GST</div></div>', unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if sub_totals:
            bt = pd.DataFrame({"Sub-assembly": list(sub_totals.keys()),
                               "Cost ₹ (ex-GST)": [f"₹{int(v):,}" for v in sub_totals.values()]})
            st.dataframe(bt, use_container_width=True, hide_index=True)

        top5 = sc.get("top5_drivers", [])
        if top5:
            st.markdown('<div class="sec-label">Top cost drivers</div>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(top5), use_container_width=True, hide_index=True)

        st.caption(sc.get("note", ""))

    # --- Agent Log ---
    with t_log:
        log = result.get("agent_log", [])
        if log:
            lines = []
            for e in log:
                mark = "✓" if e.get("result") else "◈"
                lines.append(f"{e.get('t','')} {mark} {e.get('step',''):<10} "
                             f"{e.get('result') or e.get('action','')}")
            body = "<br>".join(_classify_line(l) for l in lines)
            st.markdown(f'<div class="terminal" style="max-height:480px">{body}</div>',
                        unsafe_allow_html=True)
        meta = []
        if result.get("manufacturer"): meta.append(f"Manufacturer: {result['manufacturer']}")
        if result.get("model"): meta.append(f"Model: {result['model']}")
        meta.append(f"Iterations: {result.get('iterations',0)}")
        meta.append(f"Gaps flagged: {len(result.get('gaps',[]))}")
        st.caption(" · ".join(meta))
        if result.get("gaps"):
            for g in result["gaps"]:
                st.markdown(f'<div class="warn-box" style="margin-top:6px">• {g}</div>',
                            unsafe_allow_html=True)

    # --- Export ---
    with t_export:
        st.markdown('<div class="sec-label">Download</div>', unsafe_allow_html=True)
        st.write("Full workbook: Agent Summary · BOM (by sub-assembly) · Should-Cost · Agent Log.")
        try:
            xls = export_excel(result)
            fname = (result.get("equipment_type", "BOM").split("(")[0].strip()
                     .replace(" ", "_").replace("/", "-") or "BOM")
            st.download_button(
                "⬇  Download Excel (.xlsx)", data=xls.getvalue(),
                file_name=f"{fname}_should_cost.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=False)
        except Exception as e:
            st.markdown(f'<div class="warn-box">Export error: {e}</div>', unsafe_allow_html=True)

else:
    st.markdown(
        f'<div class="panel" style="color:{MUTE};font-family:IBM Plex Mono,monospace;font-size:13px">'
        'Upload a datasheet and press <b style="color:#e8a020">RUN AGENT</b>. '
        'The agent identifies the product, builds a product-specific sub-assembly '
        'schema, populates and validates the BOM, then prices it at floor cost '
        '(raw material + machining, no overhead, no margin) with a confidence score.'
        '</div>', unsafe_allow_html=True)
