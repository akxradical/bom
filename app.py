"""
Automated BOM Generation System v3.0
4 core capabilities:
  1. Structured BOM with component-level material specifications
  2. Sub-assembly grouping from real dissection data
  3. Material traceability — MOC linked to fluid-temperature rules
  4. Weight schedule per sub-assembly for foundation & crane selection
Author: Ayush Kamle
"""

import streamlit as st
import pandas as pd
import time
from engine import (
    load_db, extract_pdf_text, parse_specs, detect_multi_pump,
    generate_bom, export_bom_excel, calc_specific_speed,
    group_bom, build_weight_schedule, crane_category,
    get_store, log_feedback, log_correction, log_pattern,
    SECTION_ORDER, HIERARCHY,
)
try:
    from pricer import price_bom, build_cost_summary, MATERIAL_RATES_PER_KG
    PRICING_AVAILABLE = True
except Exception:
    PRICING_AVAILABLE = False


def _show_pricing_results(priced_df, cost_sum, specs, tier, mi, cs):
    """Render the full cost estimation results."""

    # ── Top cost metrics ─────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    ex_gst  = cost_sum["total_ex_gst"]
    gst_amt = cost_sum["total_gst"]
    incl    = cost_sum["total_incl_gst"]
    comps   = cost_sum["component_count"]

    for col, (lbl, val, clr) in zip([c1,c2,c3,c4], [
        ("Ex-GST Total",     f"₹{ex_gst:,.0f}",  "#58a6ff"),
        ("GST (18%)",        f"₹{gst_amt:,.0f}", "#ffa657"),
        ("Total incl. GST",  f"₹{incl:,.0f}",    "#3fb950"),
        ("Components Priced",str(comps),          "#79c0ff"),
    ]):
        col.markdown(
            f'<div class="metric-tile">'
            f'<div class="metric-value" style="color:{clr};font-size:20px;">{val}</div>'
            f'<div class="metric-label">{lbl}</div></div>',
            unsafe_allow_html=True)

    st.markdown("---")

    tab1, tab2, tab3 = st.tabs(["📊 Cost Breakdown", "📋 Detailed Line Items", "⬇ Export"])

    # ── TAB 1: Cost Breakdown ────────────────────────────────────
    with tab1:
        col_a, col_b = st.columns([3, 2])

        with col_a:
            st.markdown('<div class="sec-hdr">Cost by Sub-Assembly</div>',
                        unsafe_allow_html=True)
            sub_totals = cost_sum.get("sub_totals", {})
            max_v = max(sub_totals.values()) if sub_totals else 1
            for sub, val in sub_totals.items():
                pct = int(val / max_v * 100)
                pct_of_total = val / max(cost_sum["total_ex_gst"], 1) * 100
                st.markdown(
                    f'<div style="margin:5px 0;">'
                    f'<div style="display:flex;justify-content:space-between;">'
                    f'<span style="color:#8b949e;font-size:12px;">{sub[:35]}</span>'
                    f'<span style="color:#58a6ff;font-family:IBM Plex Mono;font-size:12px;">'
                    f'₹{val:,.0f} ({pct_of_total:.1f}%)</span></div>'
                    f'<div style="background:#21262d;border-radius:3px;height:8px;">'
                    f'<div style="background:#1f6feb;width:{pct}%;'
                    f'border-radius:3px;height:8px;"></div></div></div>',
                    unsafe_allow_html=True)

        with col_b:
            st.markdown('<div class="sec-hdr">Top 5 Cost Drivers</div>',
                        unsafe_allow_html=True)
            for item in cost_sum.get("top5_drivers", []):
                name = str(item.get("Component_Name", item.get("Description","—")))[:35]
                val2 = item.get("Total_Price_INR", 0)
                conf = item.get("Price_Confidence","—")
                conf_color = "#3fb950" if conf=="high" else "#ffa657" if conf=="medium" else "#8b949e"
                st.markdown(
                    f'<div style="padding:6px 0;border-bottom:1px solid #21262d;">'
                    f'<div style="color:#e6edf3;font-size:12px;">{name}</div>'
                    f'<div style="display:flex;justify-content:space-between;">'
                    f'<span style="color:#58a6ff;font-family:IBM Plex Mono;font-size:12px;">'
                    f'₹{val2:,.0f}</span>'
                    f'<span style="color:{conf_color};font-size:10px;">{conf}</span>'
                    f'</div></div>',
                    unsafe_allow_html=True)

            st.markdown("---")
            # Confidence breakdown
            st.markdown('<div class="sec-hdr">Price Confidence</div>',
                        unsafe_allow_html=True)
            conf_d = cost_sum.get("confidence", {})
            for level, cnt in conf_d.items():
                clr = "#3fb950" if level=="high" else "#ffa657" if level=="medium" else "#8b949e"
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:3px 0;">'
                    f'<span style="color:{clr};font-size:12px;">●  {level.title()}</span>'
                    f'<span style="color:#8b949e;font-size:12px;">{cnt} items</span>'
                    f'</div>', unsafe_allow_html=True)

        # Disclaimer
        st.markdown(
            f'<div class="card-amber" style="margin-top:16px;">'
            f'⚠️  <b style="color:#d29922;">Indicative Estimate Only</b><br>'
            f'<span style="color:#8b949e;font-size:12px;">'
            f'{cost_sum.get("note","")}</span></div>',
            unsafe_allow_html=True)

    # ── TAB 2: Detailed Line Items ───────────────────────────────
    with tab2:
        show_cols = []
        for c in ["No", "Category", "Sub_Assembly",
                  "Component_Name", "Description",
                  "Material_Spec", "MOC",
                  "Qty_Per_Unit", "Qty",
                  "Weight_kg",
                  "Unit_Price_INR", "Price_Basis",
                  "Qty_Num", "Total_Price_INR",
                  "GST_Rate_%", "GST_Amount_INR", "Price_With_GST",
                  "Price_Confidence", "Price_Source", "Price_Notes"]:
            if c in priced_df.columns:
                show_cols.append(c)

        # Format for display
        disp = priced_df[show_cols].copy()
        for col in ["Unit_Price_INR","Total_Price_INR","GST_Amount_INR","Price_With_GST"]:
            if col in disp.columns:
                disp[col] = disp[col].apply(
                    lambda x: f"₹{int(x):,}" if pd.notna(x) and x else "—")

        st.dataframe(disp, use_container_width=True, height=500, hide_index=True)
        st.caption(
            f"Total (ex-GST): ₹{cost_sum['total_ex_gst']:,} | "
            f"GST: ₹{cost_sum['total_gst']:,} | "
            f"Total (incl. GST): ₹{cost_sum['total_incl_gst']:,}")

    # ── TAB 3: Export ────────────────────────────────────────────
    with tab3:
        st.markdown('<div class="sec-hdr">Download Priced BOM</div>',
                    unsafe_allow_html=True)

        # CSV download
        csv_df = priced_df.copy()
        st.download_button(
            "⬇ Download Priced BOM (CSV)",
            csv_df.to_csv(index=False),
            f"Priced_BOM_{pd.Timestamp.now().strftime('%d%b%Y')}.csv",
            "text/csv", use_container_width=False)

        st.markdown("---")
        st.markdown(
            '<div class="card">'
            '<div class="sec-hdr">Cost Summary</div>', unsafe_allow_html=True)

        summary_lines = [
            ("Total BOM Value (Ex-GST)",    f"₹{cost_sum['total_ex_gst']:,}"),
            ("GST @ 18%",                   f"₹{cost_sum['total_gst']:,}"),
            ("Total BOM Value (Incl. GST)", f"₹{cost_sum['total_incl_gst']:,}"),
            ("Number of Line Items",        str(cost_sum['component_count'])),
            ("Live Market Lookups",         str(cost_sum['api_calls_used'])),
            ("Generated",                   pd.Timestamp.now().strftime("%d-%b-%Y %H:%M")),
            ("Fluid Service",               (specs or {}).get("fluid","—")),
            ("Motor",                       f"{(specs or {}).get('motor_kw') or (cs or {}).get('motor_kw_calc','—')} kW"),
            ("BOM Method",                  "Tier 1 — Database" if tier=="tier1" else "Tier 2 — Physics"),
        ]
        for lbl, val in summary_lines:
            st.markdown(
                f'<div class="kv-row"><span class="kv-lbl">{lbl}</span>'
                f'<span class="kv-val">{val}</span></div>',
                unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown(
            '<div class="card-amber">'
            '⚠️  Prices are indicative market estimates for budget planning purposes.<br>'
            '<span style="color:#8b949e;font-size:12px;">'
            'Actual procurement prices will vary based on vendor, quantity, '
            'delivery terms, and market conditions at time of order.</span>'
            '</div>', unsafe_allow_html=True)

        # Re-run pricing
        st.markdown("---")
        if st.button("🔄 Re-run Pricing (refresh market data)", use_container_width=False):
            st.session_state.priced_df   = None
            st.session_state.cost_summary = None
            st.rerun()


# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BOM Generator",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
html,body,[class*="css"]{ font-family:'IBM Plex Sans',sans-serif; }
#MainMenu,footer,header{ visibility:hidden; }
.stApp{ background:#0d1117; color:#e6edf3; }
[data-testid="stSidebar"]{ background:#161b22; border-right:1px solid #30363d; }

.card     { background:#161b22; border:1px solid #30363d;  border-radius:8px; padding:18px 22px; margin-bottom:14px; }
.card-blue{ background:#0d1b2a; border:1px solid #1f6feb;  border-radius:8px; padding:18px 22px; margin-bottom:14px; }
.card-green{background:#0d1f0d; border:1px solid #238636;  border-radius:8px; padding:18px 22px; margin-bottom:14px; }
.card-amber{background:#1f1200; border:1px solid #d29922;  border-radius:8px; padding:18px 22px; margin-bottom:14px; }
.card-purple{background:#130d1f;border:1px solid #8957e5;  border-radius:8px; padding:18px 22px; margin-bottom:14px; }

.sec-hdr{ font-size:11px; font-weight:600; color:#8b949e; text-transform:uppercase;
          letter-spacing:1.2px; margin-bottom:10px; border-bottom:1px solid #21262d;
          padding-bottom:6px; }
.kv-row { display:flex; justify-content:space-between; padding:5px 0;
          border-bottom:1px solid #21262d; }
.kv-lbl { color:#8b949e; font-size:12px; }
.kv-val { color:#e6edf3; font-family:'IBM Plex Mono',monospace; font-size:12px; }
.kv-blue{ color:#58a6ff; font-family:'IBM Plex Mono',monospace; font-size:12px; }

.badge-t1{ display:inline-block; padding:2px 10px; border-radius:20px;
           font-size:11px; font-weight:600; font-family:'IBM Plex Mono',monospace;
           background:#0d4429; color:#3fb950; border:1px solid #238636; }
.badge-t2{ display:inline-block; padding:2px 10px; border-radius:20px;
           font-size:11px; font-weight:600; font-family:'IBM Plex Mono',monospace;
           background:#0d1b2a; color:#79c0ff; border:1px solid #1f6feb; }
.badge-lrn{display:inline-block; padding:2px 10px; border-radius:20px;
           font-size:11px; font-weight:600; font-family:'IBM Plex Mono',monospace;
           background:#130d1f; color:#d2a8ff; border:1px solid #8957e5; }

.metric-tile{ background:#161b22; border:1px solid #30363d; border-radius:8px;
              padding:14px; text-align:center; }
.metric-value{ font-family:'IBM Plex Mono',monospace; font-size:24px;
               font-weight:600; color:#58a6ff; }
.metric-label{ font-size:10px; color:#8b949e; text-transform:uppercase;
               letter-spacing:.8px; margin-top:4px; }

/* section headers in BOM grouped view */
.bom-section{ background:#1a3a5c; padding:6px 14px; border-radius:5px;
              margin-top:12px; margin-bottom:3px; display:flex;
              justify-content:space-between; align-items:center; }
.bom-sub    { background:#1c2d40; padding:4px 14px; border-radius:4px;
              margin-bottom:3px; }

.spec-tag{ display:inline-block; background:#1f2937; border:1px solid #374151;
           border-radius:4px; padding:2px 8px; font-family:'IBM Plex Mono',monospace;
           font-size:11px; color:#60a5fa; margin:2px; }

/* weight bar */
.wt-bar{ background:#21262d; border-radius:3px; height:10px; margin:2px 0; }
.wt-fill{ border-radius:3px; height:10px; }

.logo-text{ font-family:'IBM Plex Mono',monospace; font-size:19px;
            font-weight:700; color:#58a6ff; }
.logo-sub { font-size:10px; color:#8b949e; letter-spacing:1.5px; text-transform:uppercase; }

.stButton>button{ background:#238636; color:white; border:none;
                  border-radius:6px; font-weight:500; width:100%; }
.stDownloadButton>button{ background:#1f6feb; color:white; border:none;
                           border-radius:6px; font-weight:500; width:100%; }
[data-testid="stFileUploader"]{ background:#161b22; border:2px dashed #30363d; border-radius:8px; }
.stTextInput input,.stNumberInput input,.stSelectbox select{
    background:#161b22!important; border:1px solid #30363d!important;
    color:#e6edf3!important; border-radius:6px!important;
    font-family:'IBM Plex Mono',monospace!important; }
.stTabs [data-baseweb="tab"]{ background:transparent; color:#8b949e; }
.stTabs [aria-selected="true"]{ color:#58a6ff; border-bottom:2px solid #58a6ff; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────────────────────────
def _init():
    D = {"page":"upload","specs":{},"raw_text":"","pdf_name":"",
         "bom_df":None,"tier":None,"match_info":None,"calc_summary":None,
         "db":None,"store":None,"confirmed":False,
         "priced_df":None,"cost_summary":None}
    for k,v in D.items():
        if k not in st.session_state: st.session_state[k]=v
_init()

@st.cache_resource(show_spinner=False)
def _load_db(): return load_db()

# ─── helpers ─────────────────────────────────────────────────────
def _kv(label, val, blue=False):
    cls = "kv-blue" if blue else "kv-val"
    st.markdown(
        f'<div class="kv-row"><span class="kv-lbl">{label}</span>'
        f'<span class="{cls}">{val}</span></div>',
        unsafe_allow_html=True)

def _metric(label, value, color="#58a6ff"):
    return (f'<div class="metric-tile"><div class="metric-value" '
            f'style="color:{color}">{value}</div>'
            f'<div class="metric-label">{label}</div></div>')

SECTION_COLORS = {
    "A. PUMP HYDRAULICS":        "#1a3a5c",
    "B. ROTATING ASSEMBLY":      "#1a3a5c",
    "C. BEARINGS & LUBRICATION": "#2e5984",
    "D. SHAFT SEALING":          "#2e5984",
    "E. DRIVE & COUPLING":       "#366092",
    "F. MOTOR / DRIVER":         "#17375e",
    "G. STRUCTURAL":             "#4f6228",
    "H. PIPING & NOZZLES":       "#4f6228",
    "I. FASTENERS & GASKETS":    "#595959",
    "J. INSTRUMENTATION":        "#595959",
    "K. ACOUSTIC & SAFETY":      "#7f7f7f",
    "L. COMPLETE ASSEMBLY":      "#1f4e79",
    "Z. OTHER":                  "#444444",
}

# ─────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="logo-text">⚙ BOM GEN</div>', unsafe_allow_html=True)
    st.markdown('<div class="logo-sub">v3.0 — Sub-assembly Breakdown</div>', unsafe_allow_html=True)
    st.markdown("---")

    pages = {
        "upload":   "📄  Upload / Manual Entry",
        "review":   "🔍  Review Specs",
        "generate": "⚙️   Generate",
        "output":   "📋  BOM Output",
        "pricing":  "💰  Cost Estimation",
        "weights":  "⚖️   Weight Schedule",
        "moc":      "🔬  Material Traceability",
        "learn":    "🧠  Confirm & Learn",
        "stats":    "📊  Learning Stats",
        "database": "🗄️   Database Explorer",
    }
    for pid, lbl in pages.items():
        active = st.session_state.page == pid
        if st.button(lbl, key=f"nav_{pid}", use_container_width=True,
                     type="primary" if active else "secondary"):
            st.session_state.page = pid
            st.rerun()

    st.markdown("---")
    try:
        db    = _load_db()
        store = get_store()
        st.session_state.db    = db
        st.session_state.store = store
        st.markdown(
            f'<div style="font-size:11px;color:#8b949e;line-height:2.2;">'
            f'<b style="color:#58a6ff">{len(db["pumps"])}</b> pumps in DB<br>'
            f'<b style="color:#58a6ff">{len(db["comps"])}</b> components<br>'
            f'<b style="color:#3fb950">{store["stats"]["total_sessions"]}</b> sessions confirmed<br>'
            f'<b style="color:#d2a8ff">{store["stats"]["corrections"]}</b> corrections learned'
            f'</div>', unsafe_allow_html=True)
    except Exception as e:
        st.error(f"DB error: {e}")

    st.markdown("---")
    st.markdown(
        '<div style="font-size:9px;color:#484f58;text-align:center;line-height:1.8;">'
        '• Structured BOM with MOC specs<br>'
        '• 12-section sub-assembly grouping<br>'
        '• Fluid-temperature material traceability<br>'
        '• Weight schedule + crane selection'
        '</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# PAGE 1 — UPLOAD
# ═══════════════════════════════════════════════════════════════════
if st.session_state.page == "upload":
    st.markdown("## 📄 Upload Equipment Datasheet")

    c1, c2 = st.columns([3,2])
    with c1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">PDF Upload</div>', unsafe_allow_html=True)
        uploaded = st.file_uploader("Drop a pump datasheet or GA drawing PDF",
                                    type=["pdf"],
                                    help="Digital PDFs. Scanned = use manual entry.")
        if uploaded:
            st.session_state.pdf_name = uploaded.name
            with st.spinner("Extracting text..."):
                text, err = extract_pdf_text(uploaded.read())
            if err:
                st.error(f"PDF error: {err}")
            elif not text.strip():
                st.warning("No text extracted — try Manual Entry →")
            else:
                st.success(f"✅ {len(text.split())} words extracted")
                st.session_state.raw_text = text
                store = st.session_state.store or get_store()

                # ── Multi-pump detection ──────────────────────────
                multi_segs = detect_multi_pump(text)
                if multi_segs:
                    st.markdown(
                        f'<div class="card-amber">⚠️  <b style="color:#d29922;">' +
                        f'{len(multi_segs)} pump specifications detected in this document.</b><br>' +
                        f'<span style="color:#8b949e;font-size:12px;">' +
                        f'Select which pump to generate the BOM for:</span></div>',
                        unsafe_allow_html=True)
                    options = [s["label"][:60] for s in multi_segs]
                    chosen = st.selectbox("Select pump:", options)
                    idx = options.index(chosen)
                    chosen_text = multi_segs[idx]["text"]
                    with st.spinner("Parsing selected pump specs..."):
                        specs = parse_specs(chosen_text, store.get("patterns",[]))
                    st.session_state.specs = specs
                    found = {k:v for k,v in specs.items() if v is not None}
                    st.markdown(f"**Specs for: {chosen}**")
                    for k,v in found.items():
                        st.markdown(f'<span class="spec-tag">{k}: {v}</span>',
                                    unsafe_allow_html=True)
                    if st.button("Continue to Review →", type="primary"):
                        st.session_state.page = "review"; st.rerun()
                else:
                    # Single pump
                    with st.spinner("Parsing specs..."):
                        specs = parse_specs(text, store.get("patterns",[]))
                    st.session_state.specs = specs
                    found = {k:v for k,v in specs.items() if v is not None}
                    st.markdown("**Specs found:**")
                    for k,v in found.items():
                        st.markdown(f'<span class="spec-tag">{k}: {v}</span>',
                                    unsafe_allow_html=True)
                    if st.button("Continue to Review →", type="primary"):
                        st.session_state.page = "review"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">Manual Entry</div>', unsafe_allow_html=True)
        with st.form("manual"):
            flow  = st.number_input("Flow (m³/h)",  0.0, step=1.0)
            head  = st.number_input("Head (m)",      0.0, step=1.0)
            speed = st.number_input("Speed (RPM)",   0, value=1450, step=50)
            motor = st.number_input("Motor (kW)",    0.0, step=1.0)
            temp  = st.number_input("Temp (°C)",     0.0, value=30.0, step=5.0)
            fluid = st.selectbox("Fluid", [
                "Clear Water","Caustic Liquor (Alumina)",
                "Live Steam Condensate","Process Condensate",
                "Slurry","Dilute Sulphuric Acid","Crude Oil",
                "Seawater","Cooling Water","Boiler Feed Water",
            ])
            model  = st.text_input("Model (optional)")
            stages = st.number_input("Stages", 1, value=1)
            if st.form_submit_button("Use These Specs →"):
                dens={"Clear Water":1000,"Caustic Liquor (Alumina)":1244,
                      "Live Steam Condensate":930,"Process Condensate":990,
                      "Slurry":1300,"Dilute Sulphuric Acid":1050,
                      "Crude Oil":870,"Seawater":1025,"Cooling Water":998,
                      "Boiler Feed Water":950}.get(fluid,1000)
                st.session_state.specs={
                    "flow_m3h":  flow or None, "head_m":   head or None,
                    "speed_rpm": speed or None,"motor_kw": motor or None,
                    "temp_c": temp, "fluid": fluid, "density_kgm3": dens,
                    "stages": stages, "model": model.strip() or None,
                }
                st.session_state.page="review"; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# PAGE 2 — REVIEW
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "review":
    st.markdown("## 🔍 Review & Confirm Specifications")
    specs = st.session_state.specs
    if not specs:
        st.warning("No specs. Please upload or enter manually.")
        if st.button("← Back"): st.session_state.page="upload"; st.rerun()
        st.stop()

    c1,c2 = st.columns(2)
    with c1:
        st.markdown('<div class="card-blue">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">Hydraulic Parameters</div>', unsafe_allow_html=True)
        flow  = st.number_input("Flow (m³/h)", value=float(specs.get("flow_m3h") or 0), min_value=0.0, step=1.0)
        head  = st.number_input("Head (m)",    value=float(specs.get("head_m")   or 0), min_value=0.0, step=1.0)
        speed = st.number_input("Speed (RPM)", value=int(  specs.get("speed_rpm") or 1450), min_value=0, step=50)
        motor = st.number_input("Motor (kW)",  value=float(specs.get("motor_kw") or 0), min_value=0.0, step=1.0)
        if flow>0 and head>0:
            Ns = calc_specific_speed(flow, head, speed or 1450)
            cls_txt = ("Radial — HSC" if Ns<1500 else "Mixed — VTP" if Ns<4000 else "Axial")
            st.markdown(
                f'<div style="margin-top:8px;padding:8px;background:#0d1f0d;'
                f'border-radius:5px;border:1px solid #238636;">'
                f'<span style="color:#3fb950;font-family:IBM Plex Mono;font-size:12px;">'
                f'Ns = {Ns:.0f} → {cls_txt}</span></div>',
                unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="card-blue">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">Service Parameters</div>', unsafe_allow_html=True)
        fluid_opts=["Clear Water","Caustic Liquor (Alumina)","Live Steam Condensate",
                    "Process Condensate","Slurry","Dilute Sulphuric Acid","Crude Oil",
                    "Seawater","Cooling Water","Boiler Feed Water","Other"]
        cur=specs.get("fluid","Clear Water")
        if cur not in fluid_opts: fluid_opts.insert(0,cur)
        fluid  = st.selectbox("Fluid", fluid_opts, index=fluid_opts.index(cur))
        temp   = st.number_input("Temp (°C)",    value=float(specs.get("temp_c") or 30), min_value=0.0, step=5.0)
        dens_d = {"Clear Water":1000,"Caustic Liquor (Alumina)":1244,"Live Steam Condensate":930,
                  "Process Condensate":990,"Slurry":1300,"Dilute Sulphuric Acid":1050,
                  "Crude Oil":870,"Seawater":1025}
        dens   = st.number_input("Density (kg/m³)", value=float(specs.get("density_kgm3") or dens_d.get(fluid,1000)), min_value=500.0, step=10.0)
        stages = st.number_input("Stages",    value=int(  specs.get("stages") or 1), min_value=1)
        model  = st.text_input("Model",       value=str(  specs.get("model")  or ""))
        st.markdown("</div>", unsafe_allow_html=True)

    # Tier 1 preview
    db = st.session_state.db
    if db:
        from engine import tier1_match
        pr, ps, pt = tier1_match({"flow_m3h":flow or None,"head_m":head or None,
                                   "model":model,"fluid":fluid}, db)
        if pr is not None and ps>=30:
            st.markdown(
                f'<div class="card-green"><span class="badge-t1">TIER 1 MATCH</span>&nbsp;&nbsp;'
                f'<b style="color:#3fb950">{pr["Model"]}</b><br>'
                f'<span style="color:#8b949e;font-size:12px;">Score: {ps}/100 | {pt}</span></div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="card"><span class="badge-t2">TIER 2 — PHYSICS MODE</span><br>'
                '<span style="color:#8b949e;font-size:12px;">BOM will be calculated from engineering formulas.</span></div>',
                unsafe_allow_html=True)

    ca,cb = st.columns(2)
    with ca:
        if st.button("← Back", use_container_width=True):
            st.session_state.page="upload"; st.rerun()
    with cb:
        if st.button("Generate BOM →", type="primary", use_container_width=True):
            st.session_state.specs={
                "flow_m3h":  float(flow)  if flow>0  else None,
                "head_m":    float(head)  if head>0  else None,
                "speed_rpm": int(speed)   if speed>0 else None,
                "motor_kw":  float(motor) if motor>0 else None,
                "temp_c":    float(temp), "fluid": str(fluid),
                "density_kgm3": float(dens), "stages": int(stages),
                "model":     model.strip() or None,
            }
            st.session_state.confirmed=False
            st.session_state.page="generate"; st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 3 — GENERATE
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "generate":
    st.markdown("## ⚙️ Generating BOM...")
    specs=st.session_state.specs; db=st.session_state.db
    store=st.session_state.store or get_store()
    if not specs or not db: st.error("Missing specs or DB."); st.stop()

    prog=st.progress(0); status=st.empty()
    steps=[(10,"Loading database..."),(25,"Checking Tier 1 match..."),(50,"Calculating Ns and pump type..."),
           (65,"Applying material compatibility rules..."),(80,"Building sub-assembly structure..."),
           (92,"Computing weight schedule..."),(100,"Complete ✓")]
    for pct,msg in steps:
        prog.progress(pct)
        status.markdown(f'<p style="color:#8b949e;font-family:IBM Plex Mono;font-size:12px;">{msg}</p>',
                        unsafe_allow_html=True)
        time.sleep(0.25)
    try:
        bom,tier,mi,cs = generate_bom(specs, db, store)
        st.session_state.bom_df=bom; st.session_state.tier=tier
        st.session_state.match_info=mi; st.session_state.calc_summary=cs
        groups=group_bom(bom)
        wts,crane,found=build_weight_schedule(
            (mi or {}).get("pump_id",""), tier, cs)
        st.session_state["_groups"]=groups
        st.session_state["_wts"]=wts
        st.session_state["_crane"]=crane
        st.session_state["_found"]=found
        st.success(f"✅ {len(bom)} components | {len(groups)} sub-assemblies | "
                   f"{'Tier 1' if tier=='tier1' else 'Tier 2'}")
        time.sleep(0.4)
        st.session_state.page="output"; st.rerun()
    except Exception as e:
        import traceback
        st.error(f"Error: {e}"); st.code(traceback.format_exc())


# ═══════════════════════════════════════════════════════════════════
# PAGE 4 — BOM OUTPUT (sub-assembly grouped)
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "output":
    bom=st.session_state.bom_df; tier=st.session_state.tier
    mi=st.session_state.match_info; cs=st.session_state.calc_summary
    specs=st.session_state.specs
    groups=st.session_state.get("_groups") or group_bom(bom)
    wts=st.session_state.get("_wts",{})
    crane=st.session_state.get("_crane","—")

    if bom is None: st.warning("No BOM."); st.stop()

    st.markdown("## 📋 Bill of Materials — Sub-Assembly View")

    if tier=="tier1":
        st.markdown(
            f'<div class="card-green"><span class="badge-t1">TIER 1 — DATABASE MATCH</span>&nbsp;&nbsp;'
            f'<b style="color:#3fb950;font-size:14px;">{mi["model"]}</b><br>'
            f'<span style="color:#8b949e;font-size:12px;">Score: {mi["score"]}/100 | '
            f'ID: {mi["pump_id"]}</span></div>', unsafe_allow_html=True)
    else:
        lrn = '&nbsp;<span class="badge-lrn">⚡ Correction Applied</span>' \
              if (cs or {}).get("learned_correction") else ""
        st.markdown(
            f'<div class="card-blue"><span class="badge-t2">TIER 2 — PHYSICS</span>&nbsp;&nbsp;'
            f'<b style="color:#79c0ff;font-size:14px;">{(cs or {}).get("pump_type","")}</b>'
            f'{lrn}<br><span style="color:#8b949e;font-size:12px;">'
            f'Ns = {(cs or {}).get("specific_speed_Ns","—")} | '
            f'Seal: {(cs or {}).get("seal_plan","—")}</span></div>',
            unsafe_allow_html=True)

    # Metrics
    total_wt = wts.get("TOTAL PACKAGE") or wts.get("total_kg","—")
    mkw = (specs or {}).get("motor_kw") or (cs or {}).get("motor_kw_calc","—")
    cols = st.columns(5)
    for col,(lbl,val,col_c) in zip(cols,[
        ("Components",    len(bom),         "#58a6ff"),
        ("Sub-Assemblies",len(groups),       "#79c0ff"),
        ("Flow",          f"{(specs or {}).get('flow_m3h','—')} m³/h","#58a6ff"),
        ("Motor",         f"{mkw} kW",       "#58a6ff"),
        ("Total Weight",  f"{total_wt} kg",  "#3fb950"),
    ]):
        col.markdown(_metric(lbl,val,col_c), unsafe_allow_html=True)

    # ── Inline weight summary (always visible, not hidden in separate page) ──
    if wts:
        skip_keys = {"Heaviest Single Lift","Heaviest Lift Item",
                     "TOTAL PACKAGE","total_kg"}
        wt_items = {k:v for k,v in wts.items()
                    if k not in skip_keys and isinstance(v,(int,float))}
        if wt_items:
            max_w = max(wt_items.values()) if wt_items else 1
            with st.expander(f"⚖️ Weight Schedule — {total_wt} kg total | {crane}", expanded=False):
                wc1, wc2 = st.columns([3,2])
                with wc1:
                    for comp, kg in wt_items.items():
                        pct = int(kg/max_w*100)
                        st.markdown(
                            f'<div style="margin:3px 0;">' +
                            f'<div style="display:flex;justify-content:space-between;">' +
                            f'<span style="color:#8b949e;font-size:11px;">{comp}</span>' +
                            f'<span style="color:#58a6ff;font-family:IBM Plex Mono;font-size:11px;">{kg} kg</span>' +
                            f'</div>' +
                            f'<div style="background:#21262d;border-radius:2px;height:6px;">' +
                            f'<div style="background:#1f6feb;width:{pct}%;border-radius:2px;height:6px;"></div>' +
                            f'</div></div>',
                            unsafe_allow_html=True)
                    basis = "Real GA drawing data" if tier=="tier1" else "Empirical estimate ±20%"
                    st.caption(f"Basis: {basis}")
                with wc2:
                    hlift = wts.get("Heaviest Single Lift", total_wt)
                    hitem = wts.get("Heaviest Lift Item","—")
                    st.markdown('<div class="card">', unsafe_allow_html=True)
                    _kv("Heaviest Lift Item", str(hitem)[:35])
                    _kv("Heaviest Lift Wt", f"{hlift} kg", blue=True)
                    _kv("Crane Required", crane, blue=True)
                    pid2 = (mi or {}).get("pump_id","")
                    _,_,found2 = build_weight_schedule(pid2, tier, cs)
                    _kv("Static Load",  f"{found2['static_kg']} kg")
                    _kv("Dynamic Load", f"±{found2['dynamic_kg']} kg", blue=True)
                    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")

    tab1, tab2 = st.tabs(["🔩 Sub-Assembly Groups", "📋 Flat Table"])

    # ── TAB: GROUPED VIEW ────────────────────────────────────────
    with tab1:
        KEY_T1 = ["No","Component_ID","Sub_Assembly","Category",
                  "Component_Name","Material_Spec","Qty_Per_Unit","Unit","Weight_kg","Vendor_Name"]
        KEY_T2 = ["No","Component_ID","Sub_Assembly","Category",
                  "Description","MOC","MOC_Rule","Qty","Weight_kg","Notes"]

        current_sec = None
        for sec, sub, gdf in groups:
            # Section header
            if sec != current_sec:
                current_sec = sec
                color = SECTION_COLORS.get(sec,"#444444")
                cnt   = sum(len(g) for s2,sb,g in groups if s2==sec)
                st.markdown(
                    f'<div class="bom-section" style="background:{color};">'
                    f'<span style="color:#fff;font-weight:700;font-size:11px;'
                    f'letter-spacing:1px;">{sec}</span>'
                    f'<span style="color:#ffffffaa;font-size:11px;">{cnt} items</span></div>',
                    unsafe_allow_html=True)

            # Sub-assembly label
            st.markdown(
                f'<div class="bom-sub">'
                f'<span style="color:#8b949e;font-size:11px;">▶ {sub}</span></div>',
                unsafe_allow_html=True)

            is_t1 = "Component_Name" in gdf.columns
            show  = [c for c in (KEY_T1 if is_t1 else KEY_T2) if c in gdf.columns]
            st.dataframe(gdf[show], use_container_width=True,
                         hide_index=True, height=min(35*len(gdf)+40,280))

        st.caption(f"{len(bom)} components across {len(groups)} sub-assemblies")

    # ── TAB: FLAT TABLE ──────────────────────────────────────────
    with tab2:
        fc1,fc2 = st.columns([2,2])
        with fc1:
            cats=["All"]+sorted(bom["Category"].dropna().unique().tolist()) \
                 if "Category" in bom.columns else ["All"]
            cf=st.selectbox("Category filter",cats)
        with fc2:
            rc="Req_Type" if "Req_Type" in bom.columns else None
            rf="All"
            if rc:
                reqs=["All"]+sorted(bom[rc].dropna().unique().tolist())
                rf=st.selectbox("Required type",reqs)
        disp=bom.copy()
        if cf!="All" and "Category" in disp.columns:
            disp=disp[disp["Category"]==cf]
        if rf!="All" and rc:
            disp=disp[disp[rc]==rf]
        st.dataframe(disp, use_container_width=True, height=430, hide_index=True)
        st.caption(f"{len(disp)} of {len(bom)} components")

    st.markdown("---")
    ec1,ec2,ec3,ec4,ec5 = st.columns([2,2,1,1,1])
    with ec1:
        try:
            pid = (mi or {}).get("pump_id","")
            buf = export_bom_excel(bom, specs, tier, mi, cs, pid)
            fn  = f"BOM_{(specs or {}).get('model','') or 'generated'}_{pd.Timestamp.now().strftime('%d%b%Y')}.xlsx"
            st.download_button("⬇ Excel (4 tabs)",buf,fn,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)
        except Exception as e:
            st.error(f"Export: {e}")
    with ec2:
        st.download_button("⬇ CSV",bom.to_csv(index=False),
            f"BOM_{pd.Timestamp.now().strftime('%d%b%Y')}.csv",
            "text/csv", use_container_width=True)
    with ec3:
        if st.button("💰 Cost Est.", use_container_width=True):
            st.session_state.page="pricing"; st.rerun()
    with ec4:
        if st.button("⚖️ Weights", use_container_width=True):
            st.session_state.page="weights"; st.rerun()
    with ec5:
        if st.button("🔄 New BOM", use_container_width=True):
            for k in ["specs","bom_df","tier","match_info","calc_summary",
                      "raw_text","_groups","_wts","_crane","_found",
                      "priced_df","cost_summary"]:
                st.session_state[k] = {} if k=="specs" else None
            st.session_state.page="upload"; st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 5 — WEIGHT SCHEDULE
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "weights":
    bom=st.session_state.bom_df; tier=st.session_state.tier
    mi=st.session_state.match_info; cs=st.session_state.calc_summary
    specs=st.session_state.specs

    if bom is None:
        st.warning("Generate a BOM first.")
        if st.button("← Generate"): st.session_state.page="generate"; st.rerun()
        st.stop()

    st.markdown("## ⚖️ Weight Schedule & Lifting Requirements")

    pid  = (mi or {}).get("pump_id","")
    wts, crane, found = build_weight_schedule(pid, tier, cs)
    total = wts.get("TOTAL PACKAGE") or wts.get("total_kg",0)
    hlift = wts.get("Heaviest Single Lift", total)
    hitem = wts.get("Heaviest Lift Item","See breakdown")

    # Top metrics
    c1,c2,c3 = st.columns(3)
    c1.markdown(_metric("Total Package Weight", f"{total} kg", "#58a6ff"), unsafe_allow_html=True)
    c2.markdown(_metric("Heaviest Single Lift",  f"{hlift} kg","#ffa657"), unsafe_allow_html=True)
    c3.markdown(_metric("Crane Required",         crane,        "#3fb950"), unsafe_allow_html=True)

    st.markdown("---")

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">A. Component Weight Breakdown</div>', unsafe_allow_html=True)

        basis = "Actual GA drawing data" if tier=="tier1" else "Empirical estimate ±20%"
        st.caption(f"Basis: {basis}")

        skip = {"Heaviest Single Lift","Heaviest Lift Item","TOTAL PACKAGE","total_kg"}
        items = {k:v for k,v in wts.items() if k not in skip and isinstance(v,(int,float))}
        max_w = max(items.values()) if items else 1

        for comp, kg in items.items():
            pct = int(kg/max_w*100)
            st.markdown(
                f'<div style="margin:4px 0;">'
                f'<div style="display:flex;justify-content:space-between;">'
                f'<span style="color:#8b949e;font-size:12px;">{comp}</span>'
                f'<span style="color:#58a6ff;font-family:IBM Plex Mono;font-size:12px;">{kg} kg</span>'
                f'</div>'
                f'<div class="wt-bar"><div class="wt-fill" '
                f'style="background:#1f6feb;width:{pct}%;"></div></div>'
                f'</div>',
                unsafe_allow_html=True)

        if "TOTAL PACKAGE" in wts:
            st.markdown(
                f'<div style="margin-top:10px;padding:8px;background:#0d1b2a;'
                f'border-radius:5px;border:1px solid #1f6feb;">'
                f'<b style="color:#58a6ff;font-size:13px;">TOTAL: {wts["TOTAL PACKAGE"]} kg</b>'
                f'</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with col_b:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">B. Crane & Lifting Requirements</div>', unsafe_allow_html=True)
        _kv("Heaviest Item to Lift",  hitem)
        _kv("Heaviest Lift Weight",   f"{hlift} kg", blue=True)
        _kv("Rigging Safety Factor",  "1.25× (IS 3938)")
        _kv("Safe Working Load Needed", f"{round(hlift*1.25)} kg minimum", blue=True)
        _kv("Recommended Crane",      crane, blue=True)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">C. Foundation Loads</div>', unsafe_allow_html=True)
        _kv("Static Load (dry weight)", f"{found['static_kg']} kg")
        _kv("Dynamic Load (estimated)", f"±{found['dynamic_kg']} kg", blue=True)
        _kv("Basis", found["note"])
        _kv("Foundation Type", "RCC block with non-shrink grout")
        _kv("Grout",           "Fosroc Conbextra GP2 or equivalent")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    if st.button("← Back to BOM Output", use_container_width=False):
        st.session_state.page="output"; st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 6 — MATERIAL TRACEABILITY
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "moc":
    bom=st.session_state.bom_df; tier=st.session_state.tier
    cs=st.session_state.calc_summary; specs=st.session_state.specs

    if bom is None:
        st.warning("Generate a BOM first.")
        st.stop()

    st.markdown("## 🔬 Material Traceability")
    st.markdown(
        '<p style="color:#8b949e;">Every component MOC is linked to the '
        'fluid-temperature compatibility rule that selected it. '
        'Fully auditable — no black box.</p>',
        unsafe_allow_html=True)

    moc_info    = (cs or {}).get("moc", {})
    fluid_match = (cs or {}).get("fluid_matched","—")
    mat_rule    = (cs or {}).get("material_rule","—")
    seal_plan   = (cs or {}).get("seal_plan","—")
    temp_c      = (specs or {}).get("temp_c","—")
    fluid       = (specs or {}).get("fluid","—")

    # MOC summary card
    if moc_info:
        st.markdown('<div class="card-blue">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">Material Selection Summary</div>', unsafe_allow_html=True)
        c1,c2 = st.columns(2)
        with c1:
            _kv("Fluid Service",     fluid)
            _kv("Fluid Matched To",  fluid_match, blue=True)
            _kv("Operating Temp",    f"{temp_c} °C")
            _kv("Compatibility Rule",mat_rule, blue=True)
        with c2:
            _kv("Casing MOC",        moc_info.get("Casing_MOC","—"))
            _kv("Impeller MOC",      moc_info.get("Impeller_MOC","—"), blue=True)
            _kv("Shaft MOC",         moc_info.get("Shaft_MOC","—"))
            _kv("Seal Plan",         moc_info.get("Seal_Plan","—"), blue=True)
            _kv("Shaft Sleeve MOC",  moc_info.get("Shaft_Sleeve_MOC","—"))
            _kv("Wear Ring MOC",     moc_info.get("Wear_Ring_MOC","—"))
            _kv("Fastener MOC",      moc_info.get("Fastener_MOC","—"))
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown(
            '<div class="card-green"><b style="color:#3fb950">Tier 1 — Database Match</b><br>'
            '<span style="color:#8b949e;font-size:13px;">'
            'MOC from real vendor datasheets. No calculation needed.</span></div>',
            unsafe_allow_html=True)

    # Component-level traceability table
    st.markdown("---")
    st.markdown('<div class="sec-hdr">Component-Level MOC Traceability</div>',
                unsafe_allow_html=True)

    is_t1 = "Component_Name" in bom.columns
    trace_rows = []
    for _, row in bom.iterrows():
        cat  = str(row.get("Category",""))
        name = str(row.get("Component_Name","") or row.get("Description",""))
        mat  = str(row.get("Material_Spec","") or row.get("MOC",""))
        rule = str(row.get("MOC_Rule","") or mat_rule)
        sp   = seal_plan if cat in ("Seal","Mechanical Seal","Gland") else "—"
        trace_rows.append({
            "Component":       name[:50],
            "Category":        cat,
            "Material (MOC)":  mat[:40],
            "Fluid Service":   fluid_match[:30],
            "Temp (°C)":       str(temp_c),
            "Seal Plan":       sp,
            "Traceability Rule": rule,
            "Basis":           "Real vendor data" if is_t1 else "Compatibility matrix",
        })

    trace_df = pd.DataFrame(trace_rows)
    st.dataframe(trace_df, use_container_width=True, height=450, hide_index=True)
    st.download_button("⬇ Download Traceability Report (CSV)",
        trace_df.to_csv(index=False),
        f"MOC_Traceability_{pd.Timestamp.now().strftime('%d%b%Y')}.csv",
        "text/csv")


# ═══════════════════════════════════════════════════════════════════
# PAGE 7 — CONFIRM & LEARN
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "learn":
    bom=st.session_state.bom_df; tier=st.session_state.tier
    cs=st.session_state.calc_summary; mi=st.session_state.match_info
    specs=st.session_state.specs

    st.markdown("## 🧠 Confirm & Learn")
    if bom is None:
        st.warning("Generate a BOM first."); st.stop()

    if st.session_state.confirmed:
        st.markdown(
            '<div class="card-green"><b style="color:#3fb950;font-size:15px;">✓ Session Saved</b><br>'
            '<span style="color:#8b949e;">Learning store updated.</span></div>',
            unsafe_allow_html=True)
        if st.button("← Back to Output"):
            st.session_state.page="output"; st.rerun()
        st.stop()

    st.dataframe(
        bom[["No","Category","Component_Name" if "Component_Name" in bom.columns else "Description",
              "Material_Spec" if "Material_Spec" in bom.columns else "MOC",
              "Weight_kg"]].head(12),
        use_container_width=True, hide_index=True)

    st.markdown("---")

    # Section A — Type
    st.markdown('<div class="card-purple">', unsafe_allow_html=True)
    st.markdown('<span class="badge-lrn">A</span>&nbsp;<b style="color:#d2a8ff">Pump Type Confirmation</b>', unsafe_allow_html=True)
    st.markdown("&nbsp;", unsafe_allow_html=True)
    cur_type=(cs or {}).get("pump_type","") or (mi or {}).get("model","Unknown")
    pump_types=["Horizontal Split Casing","Horizontal Split Casing — High Head",
                "Horizontal Slurry Pump","Vertical Turbine Pump",
                "Vertical Turbine Pump VS6 (Condensate)","Vertical Sump Pump",
                "Vertical Submersible","Multistage Centrifugal (BFW)","Other"]
    if cur_type and cur_type not in pump_types: pump_types.insert(0, cur_type)
    conf_type = st.selectbox("Confirm pump type:",pump_types,
        index=pump_types.index(cur_type) if cur_type in pump_types else 0)
    type_notes= st.text_input("Notes (optional)")
    st.markdown("</div>", unsafe_allow_html=True)

    # Section B — MOC
    st.markdown('<div class="card-purple">', unsafe_allow_html=True)
    st.markdown('<span class="badge-lrn">B</span>&nbsp;<b style="color:#d2a8ff">MOC Confirmation</b>', unsafe_allow_html=True)
    st.markdown("&nbsp;", unsafe_allow_html=True)
    mb=(cs or {}).get("moc",{}) or {}
    bc1,bc2=st.columns(2)
    with bc1:
        c_cas=st.text_input("Casing MOC",    value=str(mb.get("Casing_MOC","ASTM A216 WCB")))
        c_imp=st.text_input("Impeller MOC",  value=str(mb.get("Impeller_MOC","CF8M SS316")))
        c_sha=st.text_input("Shaft MOC",     value=str(mb.get("Shaft_MOC","EN19/SS410")))
        c_sea=st.text_input("Seal Plan",     value=str(mb.get("Seal_Plan","Plan 11")))
    with bc2:
        c_slv=st.text_input("Shaft Sleeve",  value=str(mb.get("Shaft_Sleeve_MOC","SS410")))
        c_wrg=st.text_input("Wear Ring",     value=str(mb.get("Wear_Ring_MOC","A487 CA6M")))
        c_fas=st.text_input("Fasteners",     value=str(mb.get("Fastener_MOC","A193 B7")))
        c_stp=st.text_input("Seal Type",     value=str(mb.get("Seal_Type","Mechanical Seal")))
    conf_moc={"Casing_MOC":c_cas,"Impeller_MOC":c_imp,"Shaft_MOC":c_sha,
              "Shaft_Sleeve_MOC":c_slv,"Wear_Ring_MOC":c_wrg,
              "Seal_Plan":c_sea,"Seal_Type":c_stp,"Fastener_MOC":c_fas}
    st.markdown("</div>", unsafe_allow_html=True)

    # Section C — Weights
    st.markdown('<div class="card-purple">', unsafe_allow_html=True)
    st.markdown('<span class="badge-lrn">C</span>&nbsp;<b style="color:#d2a8ff">Weight Confirmation (calibrates future estimates)</b>', unsafe_allow_html=True)
    st.markdown("&nbsp;", unsafe_allow_html=True)
    wb2=(cs or {}).get("weights",{}) or {}
    wc1,wc2=st.columns(2)
    with wc1:
        wp=st.number_input("Pump Weight (kg)",  value=float(wb2.get("Pump (bare)",0) or 0), min_value=0.0, step=10.0)
        wm=st.number_input("Motor Weight (kg)", value=float(wb2.get("Motor",0) or 0),       min_value=0.0, step=10.0)
    with wc2:
        wbs=st.number_input("Baseplate (kg)",   value=float(wb2.get("Baseplate",0) or 0),   min_value=0.0, step=10.0)
        wt=st.number_input("Total Package (kg)",value=float(wb2.get("TOTAL PACKAGE",0) or wb2.get("total_kg",0) or 0), min_value=0.0, step=10.0)
    conf_wts={"pump_kg":wp or None,"motor_kg":wm or None,
              "baseplate_kg":wbs or None,"total_kg":wt or None}
    st.markdown("</div>", unsafe_allow_html=True)

    # Section D — Parser pattern
    st.markdown('<div class="card-purple">', unsafe_allow_html=True)
    st.markdown('<span class="badge-lrn">D</span>&nbsp;<b style="color:#d2a8ff">Teach Parser (optional)</b>', unsafe_allow_html=True)
    st.markdown('<p style="color:#8b949e;font-size:12px;">If parser missed a value, teach it the pattern.</p>', unsafe_allow_html=True)
    pd1,pd2,pd3=st.columns(3)
    with pd1: p_field=st.selectbox("Field",["—","flow_m3h","head_m","speed_rpm","motor_kw","temp_c","density_kgm3","stages"])
    with pd2: p_snippet=st.text_input("Text snippet", placeholder="BKW 60.4 kW")
    with pd3: p_correct=st.text_input("Correct value",placeholder="60.4")
    p_notes=st.text_input("Notes",placeholder="BKW = shaft power")
    st.markdown("</div>", unsafe_allow_html=True)

    eng_notes=st.text_area("General notes (optional)")
    st.markdown("---")

    col_conf,col_skip=st.columns(2)
    with col_conf:
        if st.button("✅ Confirm & Save",type="primary",use_container_width=True):
            try:
                if p_field!="—" and p_snippet and p_correct:
                    log_pattern(p_field,"",p_correct,p_snippet,p_notes)
                if tier=="tier2" and cs:
                    orig=cs.get("pump_type","")
                    if conf_type!=orig and orig:
                        log_correction(specs, orig, conf_type, type_notes)
                log_feedback(specs,bom,tier,conf_type,conf_moc,conf_wts,eng_notes)
                st.session_state.store=get_store()
                st.session_state.confirmed=True; st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
    with col_skip:
        if st.button("Skip",use_container_width=True):
            st.session_state.page="output"; st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 8 — LEARNING STATS
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "stats":
    st.markdown("## 📊 Learning Statistics")
    store=get_store(); stats=store.get("stats",{})

    c1,c2,c3,c4,c5=st.columns(5)
    for col,(lbl,val,col_c) in zip([c1,c2,c3,c4,c5],[
        ("Sessions",         stats.get("total_sessions",0), "#58a6ff"),
        ("Tier 1 Hits",      stats.get("tier1_hits",0),     "#3fb950"),
        ("Tier 2 Hits",      stats.get("tier2_hits",0),     "#79c0ff"),
        ("Corrections",      stats.get("corrections",0),    "#d2a8ff"),
        ("Patterns Learned", stats.get("patterns_added",0), "#ffa657"),
    ]):
        col.markdown(_metric(lbl,val,col_c), unsafe_allow_html=True)

    st.markdown("---")
    t1,t2,t3,t4=st.tabs(["Feedback History","Corrections","Patterns","Weight Calibration"])

    with t1:
        fb=store.get("feedback",[])
        if not fb:
            st.info("No sessions yet. Confirm a BOM to start learning.")
        else:
            rows=[{
                "Timestamp": f.get("ts","")[:16].replace("T"," "),
                "Pump Type": f.get("pump_type",""),
                "Tier":      f.get("tier",""),
                "Ns":        f.get("ns",""),
                "Flow m³/h": (f.get("specs") or {}).get("flow_m3h",""),
                "Head m":    (f.get("specs") or {}).get("head_m",""),
                "BOM Rows":  f.get("bom_rows",""),
            } for f in reversed(fb[-20:])]
            st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

    with t2:
        corrs=store.get("corrections",[])
        if not corrs: st.info("No corrections logged yet.")
        else:
            rows=[{"Timestamp":c.get("ts","")[:16].replace("T"," "),"Ns":c.get("ns",""),
                   "Fluid":c.get("fluid",""),"Was":c.get("wrong_type",""),
                   "Corrected To":c.get("correct_type",""),"Notes":c.get("notes","")}
                  for c in reversed(corrs)]
            st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
            st.info("Next run with similar Ns ±30% + fluid → system uses corrected type automatically.")

    with t3:
        pats=store.get("patterns",[])
        if not pats: st.info("No patterns added yet.")
        else:
            rows=[{"Timestamp":p.get("ts","")[:16].replace("T"," "),"Field":p.get("field",""),
                   "Snippet":p.get("snippet","")[:60],"Correct":p.get("correct",""),
                   "Notes":p.get("notes","")} for p in reversed(pats)]
            st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)

    with t4:
        calibs=store.get("weight_calibs",{})
        if not calibs: st.info("No weight calibrations yet. Confirm sessions with actual weights.")
        else:
            rows=[{"Pump Type":pt,"Pump Coeff":round(cal.get("pump_coeff",1.0),4),
                   "Motor Coeff":round(cal.get("motor_coeff",1.0),4),
                   "Samples":cal.get("n_samples",0),
                   "Pump Drift":f"{(cal.get('pump_coeff',1)-1)*100:+.1f}%",
                   "Motor Drift":f"{(cal.get('motor_coeff',1)-1)*100:+.1f}%"}
                  for pt,cal in calibs.items()]
            st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
            st.info("Coefficients > 1.0 mean formula under-predicts. Future estimates auto-corrected.")


# ═══════════════════════════════════════════════════════════════════
# PAGE 9 — DATABASE EXPLORER
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "database":
    st.markdown("## 🗄️ Database Explorer")
    db=st.session_state.db
    if not db: st.error("DB not loaded."); st.stop()

    tp,tc,tm,tv = st.tabs([
        f"Pumps ({len(db['pumps'])})",
        f"Components ({len(db['comps'])})",
        f"Materials ({len(db['mats'])})",
        f"Vendors ({len(db['vendors'])})",
    ])

    with tp:
        st.markdown("### Pump Master List")
        srch=st.text_input("Search pumps")
        pumps=db["pumps"].copy()
        if srch:
            mask=pumps.astype(str).apply(lambda c: c.str.contains(srch,case=False,na=False)).any(axis=1)
            pumps=pumps[mask]
        st.dataframe(pumps,use_container_width=True,hide_index=True)
        # Type distribution
        st.markdown("**Type distribution:**")
        for pt,cnt in db["pumps"]["Type"].value_counts().items():
            bw=int(cnt/max(db["pumps"]["Type"].value_counts())*180)
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:8px;margin:2px 0;">'
                f'<span style="color:#8b949e;font-size:12px;width:220px;">{pt}</span>'
                f'<div style="background:#1f6feb;height:12px;width:{bw}px;border-radius:2px;"></div>'
                f'<span style="color:#58a6ff;font-family:IBM Plex Mono;font-size:12px;">{cnt}</span></div>',
                unsafe_allow_html=True)

    with tc:
        st.markdown("### Component Library")
        cs1,cs2=st.columns([2,1])
        with cs1: csrch=st.text_input("Search components")
        with cs2: pf=["All"]+db["pumps"]["Model"].tolist(); pfilt=st.selectbox("Filter by pump",pf)
        comps=db["comps"].copy()
        if csrch:
            mask=comps.astype(str).apply(lambda c: c.str.contains(csrch,case=False,na=False)).any(axis=1)
            comps=comps[mask]
        if pfilt!="All":
            comps=comps[comps["Pump_Model_Compatibility"].str.contains(pfilt,case=False,na=False)]
        st.dataframe(comps[["Component_ID","Component_Name","Category","Material_Spec",
                             "Weight_kg","Vendor_Name","Pump_Model_Compatibility"]],
                     use_container_width=True,hide_index=True,height=420)

    with tm:
        st.markdown("### Material Database")
        st.dataframe(db["mats"],use_container_width=True,hide_index=True)

    with tv:
        st.markdown("### Vendor Database")
        st.dataframe(db["vendors"],use_container_width=True,hide_index=True)



# ═══════════════════════════════════════════════════════════════════
# PAGE — COST ESTIMATION  (Claude API + web search)
# ═══════════════════════════════════════════════════════════════════
if st.session_state.page == "pricing":
    bom   = st.session_state.bom_df
    tier  = st.session_state.tier
    mi    = st.session_state.match_info
    cs    = st.session_state.calc_summary
    specs = st.session_state.specs

    st.markdown("## 💰 BOM Cost Estimation")

    if bom is None:
        st.warning("Generate a BOM first.")
        if st.button("← Generate"): st.session_state.page="generate"; st.rerun()
        st.stop()

    if not PRICING_AVAILABLE:
        st.error("Pricing module not available. Check pricer.py is in the same folder.")
        st.stop()

    # ── Sub-header ───────────────────────────────────────────────
    st.markdown(
        '<p style="color:#8b949e;">'
        'Live market price intelligence — major components priced via '
        'current market search. Rates indexed to India 2025-26 procurement.'
        '</p>', unsafe_allow_html=True)

    st.markdown(
        f'<div class="card-blue">'
        f'<b style="color:#79c0ff;">{len(bom)} components</b> across '
        f'<b style="color:#79c0ff;">{len(group_bom(bom))}</b> sub-assemblies | '
        f'Fluid: <b style="color:#79c0ff;">{(specs or {}).get("fluid","—")}</b> | '
        f'Motor: <b style="color:#79c0ff;">'
        f'{(specs or {}).get("motor_kw") or (cs or {}).get("motor_kw_calc","—")} kW</b>'
        f'</div>', unsafe_allow_html=True)

    # ── Already priced? show results ─────────────────────────────
    if st.session_state.priced_df is not None and st.session_state.cost_summary:
        priced_df   = st.session_state.priced_df
        cost_sum    = st.session_state.cost_summary
        _show_pricing_results(priced_df, cost_sum, specs, tier, mi, cs)

    else:
        # ── Run pricing ──────────────────────────────────────────
        st.markdown("---")
        st.markdown(
            '<div class="card">'
            '<div class="sec-hdr">Market Data Accumulation</div>'
            '<p style="color:#8b949e;font-size:13px;">'
            'High-value components (pump casing, impeller, motor, mechanical seal, baseplate) '
            'are priced using live market intelligence. '
            'Standard components use published material rate indices.</p>'
            '</div>', unsafe_allow_html=True)

        col_run, col_info = st.columns([1, 2])
        with col_run:
            run_btn = st.button("▶  Run Cost Estimation",
                                type="primary", use_container_width=True)
        with col_info:
            high_count = sum(1 for _, r in bom.iterrows()
                            if str(r.get("Category","")) in
                            {"Pump","Casing","Impeller","Motor","Mechanical Seal",
                             "Seal","Baseplate","Enclosure","Rotor"})
            st.markdown(
                f'<div style="padding:8px 0;color:#8b949e;font-size:12px;">'
                f'~{high_count} live lookups + {len(bom)-high_count} formula/table items'
                f'</div>', unsafe_allow_html=True)

        if run_btn:
            prog_bar  = st.progress(0)
            status_ph = st.empty()

            def _update_progress(pct, msg):
                prog_bar.progress(pct)
                status_ph.markdown(
                    f'<p style="color:#8b949e;font-family:IBM Plex Mono;'
                    f'font-size:12px;">◉ {msg}</p>',
                    unsafe_allow_html=True)

            _update_progress(5, "Initialising market data pipeline...")
            time.sleep(0.3)

            try:
                priced_df = price_bom(bom, specs, cs, _update_progress)
                cost_sum  = build_cost_summary(priced_df)

                st.session_state.priced_df   = priced_df
                st.session_state.cost_summary = cost_sum

                prog_bar.progress(100)
                status_ph.markdown(
                    '<p style="color:#3fb950;font-family:IBM Plex Mono;font-size:12px;">'
                    '✓ Market data compilation complete</p>',
                    unsafe_allow_html=True)
                time.sleep(0.4)
                st.rerun()

            except Exception as e:
                import traceback
                st.error(f"Pricing error: {e}")
                st.code(traceback.format_exc())

    st.markdown("---")
    if st.button("← Back to BOM Output"):
        st.session_state.page = "output"; st.rerun()
