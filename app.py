"""
AGENTIC BOM — Streamlit UI
═══════════════════════════════════════════════════════════════════
Drop any engineered-product datasheet. The agent identifies the product,
builds a product-specific schema, populates and validates the BOM, prices
it at floor cost, and scores its confidence — live.

Backend: claude_engine.run_agent()
"""

import time
import html
import pandas as pd
import streamlit as st

from claude_engine import (
    extract_pdf_text, run_agent, bom_to_dataframe, export_excel, _get_key,
    price_manual, SUPPLIER_FACTORS,
)

# ═══════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Agentic BOM",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ═══════════════════════════════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════════════════════════════

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500&family=Syne:wght@700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Mono', monospace;
    background-color: #0a0a0f;
    color: #e8e4db;
}
#MainMenu, footer, header { visibility: hidden; }
.stApp { background: #0a0a0f; }
[data-testid="collapsedControl"] { display: none; }
.block-container { max-width: 1180px; }

[data-testid="stFileUploader"] {
    background: #12121a;
    border: 1px dashed rgba(232,160,32,0.35);
    border-radius: 4px;
    padding: 8px;
}
[data-testid="stFileUploader"]:hover { border-color: rgba(232,160,32,0.7); }

.stButton > button {
    background: #e8a020 !important;
    color: #0a0a0f !important;
    border: none !important;
    border-radius: 2px !important;
    font-family: 'IBM Plex Mono', monospace !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    letter-spacing: 0.1em !important;
    padding: 10px 28px !important;
    text-transform: uppercase !important;
}
.stButton > button:hover { background: #f0b030 !important; }
.stButton > button:disabled {
    background: rgba(232,160,32,0.3) !important;
    color: rgba(10,10,15,0.5) !important;
}

[data-testid="stTabs"] [role="tab"] {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: rgba(232,228,219,0.45);
    border-bottom: 2px solid transparent;
    padding: 8px 20px;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: #e8a020;
    border-bottom-color: #e8a020;
}

[data-testid="stDataFrame"] {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
}

[data-testid="stMetricValue"] {
    font-family: 'Syne', sans-serif;
    font-size: 28px;
    font-weight: 800;
    color: #e8a020;
}
[data-testid="stMetricLabel"] {
    font-size: 10px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: rgba(232,228,219,0.4);
}

hr { border-color: rgba(232,160,32,0.12) !important; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════

if "result" not in st.session_state:
    st.session_state["result"] = None
if "agent_lines" not in st.session_state:
    st.session_state["agent_lines"] = []

# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def render_terminal(lines):
    """Terminal-style block. Lines are HTML-escaped to avoid breakage from
    component names/specs containing <, >, or &."""
    if lines:
        content = "\n".join(html.escape(str(l)) for l in lines)
    else:
        content = "Waiting for input..."
    return f"""
    <div style="background:#060609; border:1px solid rgba(232,160,32,0.2);
                border-radius:4px; padding:24px 28px; font-family:'IBM Plex Mono',monospace;
                font-size:12px; line-height:2; color:#e8a020; min-height:180px;
                white-space:pre-wrap; overflow-y:auto; max-height:320px;">{content}</div>
    """

# ═══════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("### ◈ AGENTIC BOM")
    st.caption("v2.0 · Zetwerk CPT")
    st.divider()
    st.markdown("**Supported products**")
    st.caption("Pumps · Compressors · Agitators\n\n"
               "Valves · Fans · Heat Exchangers\n\n"
               "Cranes · Chillers · Pressure Vessels\n\n"
               "Motors · Turbines · Any engineered product")
    st.divider()
    st.markdown("**Providers**")
    for name, key in [("Claude", "ANTHROPIC_API_KEY"), ("Gemini", "GEMINI_API_KEY"),
                      ("Groq", "GROQ_API_KEY"), ("Mistral", "MISTRAL_API_KEY")]:
        dot = "🟢" if bool(_get_key(key)) else "⚪"
        st.caption(f"{dot} {name}")
    if st.session_state.get("result"):
        st.divider()
        if st.button("↺ New BOM"):
            st.session_state["result"] = None
            st.session_state["agent_lines"] = []
            st.rerun()

# ═══════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════

st.markdown("""
<div style="padding: 48px 0 32px 0;">
  <div style="font-family:'IBM Plex Mono',monospace; font-size:10px;
              letter-spacing:0.35em; color:#e8a020; text-transform:uppercase;
              margin-bottom:16px;">
    Zetwerk · Central Procurement Team · Category 2
  </div>
  <div style="font-family:'Syne',sans-serif; font-size:clamp(36px,6vw,80px);
              font-weight:800; letter-spacing:-0.03em; line-height:0.95;
              color:#e8e4db; margin-bottom:20px;">
    AGENTIC BOM
  </div>
  <div style="font-family:'IBM Plex Mono',monospace; font-size:13px;
              color:rgba(232,228,219,0.45); line-height:1.9; max-width:520px;">
    Drop any engineered-product datasheet. The agent identifies, schemas,
    populates, validates, and prices — for any pump, compressor, agitator,
    valve, heat exchanger, crane, or turbine.
  </div>
</div>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════
# UPLOAD + RUN
# ═══════════════════════════════════════════════════════════════════

uploaded_file = st.file_uploader("", type=["pdf"], label_visibility="collapsed")
st.caption("PDF datasheets, GA drawings, spec sheets — any engineered product")

run_btn = st.button("◈ RUN AGENT", disabled=uploaded_file is None)

# Live terminal placeholder (must exist before the callback references it)
term_placeholder = st.empty()
if st.session_state["agent_lines"] and not st.session_state.get("result"):
    term_placeholder.markdown(render_terminal(st.session_state["agent_lines"]),
                              unsafe_allow_html=True)


def on_progress(line, agent_log):
    st.session_state["agent_lines"].append(line)
    term_placeholder.markdown(render_terminal(st.session_state["agent_lines"]),
                              unsafe_allow_html=True)


if run_btn and uploaded_file:
    st.session_state["agent_lines"] = ["[00:00] ◈ AGENT      Starting..."]
    st.session_state["result"] = None
    term_placeholder.markdown(render_terminal(st.session_state["agent_lines"]),
                              unsafe_allow_html=True)

    file_bytes = uploaded_file.read()
    pdf_text, pdf_err = extract_pdf_text(file_bytes)
    if pdf_err or len(pdf_text.strip()) < 100:
        st.error(f"Could not extract text from PDF. {pdf_err or 'Try a text-based PDF.'}")
        st.stop()

    try:
        result = run_agent(pdf_text, progress_callback=on_progress, price=False)
        st.session_state["result"] = result
        st.rerun()
    except Exception as e:
        import traceback
        st.error(f"Agent error: {e}")
        st.code(traceback.format_exc())

# ═══════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════

result = st.session_state.get("result")
if result:
    sc = result.get("should_cost", {})
    schema = result.get("schema", [])
    conf_pct = int(round(result.get("confidence", 0.0) * 100))

    st.divider()

    # ── KPI ROW ────────────────────────────────────────────────────
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Equipment", str(result.get("equipment_type", "—"))[:24])
    k2.metric("Components", sc.get("component_count", len(result.get("bom", []))))
    k3.metric("Sub-assemblies", len(schema))
    k4.metric("Should-Cost", f"₹{sc.get('total_ex_gst', 0):,}", help="ex-GST floor")
    k5.metric("Confidence", f"{conf_pct}%")

    # ── CONFIDENCE BAR ─────────────────────────────────────────────
    conf_color = "#3d6b4f" if conf_pct >= 85 else "#e8a020" if conf_pct >= 65 else "#c44a2a"
    st.markdown(f"""
    <div style="height:3px; background:rgba(255,255,255,0.05); border-radius:2px; margin:8px 0 24px 0;">
      <div style="height:3px; width:{conf_pct}%; background:{conf_color}; border-radius:2px;
                  transition:width 0.5s ease;"></div>
    </div>
    """, unsafe_allow_html=True)

    if conf_pct < 65:
        st.warning("⚠ Low confidence — engineer review strongly recommended before "
                   "using this BOM for procurement.")

    gaps = result.get("gaps", [])
    warnings = result.get("warnings", [])
    if gaps:
        with st.expander(f"⚠ {len(gaps)} gaps flagged"):
            for g in gaps + warnings:
                st.markdown(f"`{g}`")

    # ── TABS ───────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs(["BOM", "SHOULD-COST", "AGENT LOG", "EXPORT"])

    # --- Tab 1: BOM ---
    with tab1:
        df = bom_to_dataframe(result.get("bom", []))
        if df.empty:
            st.info("No components generated.")
        else:
            options = ["All"] + [f"{s['id']}. {s['name']}" for s in schema]
            pick = st.selectbox("Filter sub-assembly", options)
            view = df if pick == "All" else df[df["Sub_Assembly"] == pick]
            st.dataframe(view, use_container_width=True, hide_index=True)
            types = {}
            for c in result.get("bom", []):
                t = c.get("type", c.get("component_type", "—"))
                types[t] = types.get(t, 0) + 1
            st.caption(f"Manufactured: {types.get('manufactured',0)} | "
                       f"Bought-out: {types.get('bought_out',0)}")

    # --- Tab 2: SHOULD-COST (interactive manual costing) ---
    with tab2:
        bom = result.get("bom", [])
        if not bom:
            st.info("No components to price.")
        else:
            st.markdown("Enter the **raw-material rate (₹/kg)** for each component "
                        "(from your live market source), set **labour/manufacturing**, "
                        "and pick the **supplier type**. Cost updates instantly.")

            c1, c2 = st.columns([1, 1])
            supplier = c1.radio("Supplier type", list(SUPPLIER_FACTORS.keys()),
                                horizontal=True,
                                help="International applies a higher cost factor "
                                     f"(×{SUPPLIER_FACTORS['International']}).")
            labour = c2.slider("Labour / manufacturing (₹/kg of gross weight)",
                               0, 500, 90, step=10)

            # Editable raw-material rate per component
            edit_df = pd.DataFrame([{
                "id": c.get("id"),
                "Component": c.get("description", ""),
                "Material": c.get("material", ""),
                "Type": c.get("type", ""),
                "Weight_kg": c.get("weight_kg", 0),
                "Raw_Rate_₹/kg": float(c.get("raw_material_rate", 0) or 0),
            } for c in bom])

            edited = st.data_editor(
                edit_df, use_container_width=True, hide_index=True,
                column_config={
                    "id": None,
                    "Raw_Rate_₹/kg": st.column_config.NumberColumn(
                        "Raw Rate ₹/kg", min_value=0, step=10,
                        help="Enter the live raw-material price per kg"),
                    "Weight_kg": st.column_config.NumberColumn("Weight kg", disabled=True),
                    "Component": st.column_config.TextColumn(disabled=True),
                    "Material": st.column_config.TextColumn(disabled=True),
                    "Type": st.column_config.TextColumn(disabled=True),
                },
                key="rate_editor", height=320)

            rate_map = {str(int(r["id"])): float(r["Raw_Rate_₹/kg"] or 0)
                        for _, r in edited.iterrows()}

            # Price deterministically from the buyer's inputs
            priced_bom, sc2 = price_manual([dict(c) for c in bom], rate_map,
                                           labour_rate_per_kg=labour, supplier=supplier)
            # write back so Export uses the priced BOM
            st.session_state.result["bom"] = priced_bom
            st.session_state.result["should_cost"] = sc2

            total_ex = max(sc2.get("total_ex_gst", 0), 1)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Raw Material", f"₹{sc2.get('total_raw_material',0):,}")
            m2.metric("Labour/Mfg", f"₹{sc2.get('total_machining',0):,}")
            m3.metric("Total ex-GST", f"₹{sc2.get('total_ex_gst',0):,}")
            m4.metric("Total incl-GST", f"₹{sc2.get('total_incl_gst',0):,}")

            sub_totals = sc2.get("sub_totals", {})
            if sub_totals:
                st.markdown("**By sub-assembly**")
                st.dataframe(pd.DataFrame([
                    {"Sub-assembly": k, "Cost (₹)": f"₹{int(v):,}",
                     "% Share": f"{int(int(v)/total_ex*100)}%"}
                    for k, v in sub_totals.items()
                ]), use_container_width=True, hide_index=True)

            st.caption(f"Supplier: {supplier} (×{SUPPLIER_FACTORS[supplier]}) · "
                       f"Labour ₹{labour}/kg · Raw rates entered by buyer. "
                       "Cost = (raw material + labour) × supplier factor. No AI pricing, no hallucination.")

    # --- Tab 3: AGENT LOG ---
    with tab3:
        log = result.get("agent_log", [])
        lines = [
            f"{e.get('t','')} {'✓' if e.get('result') else '◈'} "
            f"{str(e.get('step','')):<12} {e.get('result') or e.get('action','')}"
            for e in log
        ]
        st.markdown(render_terminal(lines), unsafe_allow_html=True)
        last_t = log[-1]["t"] if log else "—"
        st.caption(f"Total elapsed: {last_t} | Iterations: {result.get('iterations',0)}")

    # --- Tab 4: EXPORT ---
    with tab4:
        try:
            xlsx_bytes = export_excel(result)
            fname = str(result.get("equipment_type", "BOM")).replace(" ", "_")[:40]
            st.download_button(
                label="⬇ DOWNLOAD EXCEL",
                data=xlsx_bytes,
                file_name=f"agentic_bom_{fname}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            st.caption("Excel contains 4 sheets: Agent Summary · BOM · Should-Cost · Agent Log")
        except Exception as e:
            st.error(f"Export error: {e}")
