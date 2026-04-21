"""
Automated BOM Generation System v4.0
Same UI — Claude-powered core
"""

import streamlit as st
import pandas as pd
import time

from claude_engine import (
    extract_pdf_text, claude_extract_specs, claude_generate_bom,
    bom_to_dataframe, claude_price_bom, build_cost_summary,
    group_bom, export_excel, SECTION_ORDER,
)

# ─────────────────────────────────────────────────────────────────
st.set_page_config(page_title="BOM Generator", page_icon="⚙️",
                   layout="wide", initial_sidebar_state="expanded")

# ─────────────────────────────────────────────────────────────────
# CSS (same dark theme)
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

.sec-hdr{ font-size:11px; font-weight:600; color:#8b949e; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:10px; border-bottom:1px solid #21262d; padding-bottom:6px; }
.kv-row { display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid #21262d; }
.kv-lbl { color:#8b949e; font-size:12px; }
.kv-val { color:#e6edf3; font-family:'IBM Plex Mono',monospace; font-size:12px; }
.kv-blue{ color:#58a6ff; font-family:'IBM Plex Mono',monospace; font-size:12px; }
.badge-t1{ display:inline-block; padding:2px 10px; border-radius:20px; font-size:11px; font-weight:600; font-family:'IBM Plex Mono',monospace; background:#0d4429; color:#3fb950; border:1px solid #238636; }
.badge-t2{ display:inline-block; padding:2px 10px; border-radius:20px; font-size:11px; font-weight:600; font-family:'IBM Plex Mono',monospace; background:#0d1b2a; color:#79c0ff; border:1px solid #1f6feb; }
.metric-tile{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px; text-align:center; }
.metric-value{ font-family:'IBM Plex Mono',monospace; font-size:22px; font-weight:600; color:#58a6ff; }
.metric-label{ font-size:10px; color:#8b949e; text-transform:uppercase; letter-spacing:.8px; margin-top:4px; }
.bom-section{ padding:6px 14px; border-radius:5px; margin-top:12px; margin-bottom:3px; display:flex; justify-content:space-between; align-items:center; }
.bom-sub    { background:#1c2d40; padding:4px 14px; border-radius:4px; margin-bottom:3px; }
.spec-tag{ display:inline-block; background:#1f2937; border:1px solid #374151; border-radius:4px; padding:2px 8px; font-family:'IBM Plex Mono',monospace; font-size:11px; color:#60a5fa; margin:2px; }
.wt-bar{ background:#21262d; border-radius:3px; height:8px; margin:2px 0; }
.wt-fill{ border-radius:3px; height:8px; }
.logo-text{ font-family:'IBM Plex Mono',monospace; font-size:19px; font-weight:700; color:#58a6ff; }
.logo-sub { font-size:10px; color:#8b949e; letter-spacing:1.5px; text-transform:uppercase; }
.stButton>button{ background:#238636; color:white; border:none; border-radius:6px; font-weight:500; width:100%%; }
.stDownloadButton>button{ background:#1f6feb; color:white; border:none; border-radius:6px; font-weight:500; width:100%%; }
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
for k, v in {
    "page": "upload", "raw_text": "", "pdf_name": "",
    "extracted_specs": None, "selected_pump_idx": 0,
    "bom_df": None, "bom_raw": None, "pump_specs": None,
    "priced_df": None, "cost_summary": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ─── helpers ─────────────────────────────────────────────────────
def _metric(label, value, color="#58a6ff"):
    return (f'<div class="metric-tile"><div class="metric-value" '
            f'style="color:{color}">{value}</div>'
            f'<div class="metric-label">{label}</div></div>')

def _kv(label, val, blue=False):
    cls = "kv-blue" if blue else "kv-val"
    st.markdown(
        f'<div class="kv-row"><span class="kv-lbl">{label}</span>'
        f'<span class="{cls}">{val}</span></div>',
        unsafe_allow_html=True)

SECTION_COLORS = {
    "A. PUMP HYDRAULICS":"#1a3a5c","B. ROTATING ASSEMBLY":"#1a3a5c",
    "C. BEARINGS & LUBRICATION":"#2e5984","D. SHAFT SEALING":"#2e5984",
    "E. DRIVE & COUPLING":"#366092","F. MOTOR / DRIVER":"#17375e",
    "G. STRUCTURAL":"#4f6228","H. PIPING & NOZZLES":"#4f6228",
    "I. FASTENERS & GASKETS":"#595959","J. INSTRUMENTATION":"#595959",
    "K. ACOUSTIC & SAFETY":"#7f7f7f","L. COMPLETE ASSEMBLY":"#1f4e79",
}


# ─────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="logo-text">⚙ BOM GEN</div>', unsafe_allow_html=True)
    st.markdown('<div class="logo-sub">v4.0 — Intelligent BOM Engine</div>',
                unsafe_allow_html=True)
    st.markdown("---")

    pages = {
        "upload":   "📄  Upload Datasheet",
        "specs":    "🔍  Review Specs",
        "bom":      "📋  BOM Output",
        "pricing":  "💰  Cost Estimation",
    }
    for pid, lbl in pages.items():
        if st.button(lbl, key=f"nav_{pid}", use_container_width=True,
                     type="primary" if st.session_state.page == pid else "secondary"):
            st.session_state.page = pid; st.rerun()

    st.markdown("---")
    st.markdown(
        '<div style="font-size:9px;color:#484f58;text-align:center;line-height:1.8;">'
        'Intelligent document reading<br>'
        'Component-level BOM generation<br>'
        'Live market price estimation<br>'
        'Sub-assembly grouped output'
        '</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# PAGE 1 — UPLOAD
# ═══════════════════════════════════════════════════════════════════
if st.session_state.page == "upload":
    st.markdown("## 📄 Upload Equipment Datasheet")

    st.markdown(
        '<div class="card">'
        '<div class="sec-hdr">Upload a Pump Datasheet, GA Drawing, or Procurement Specification</div>'
        '<p style="color:#8b949e;font-size:13px;">'
        'Supports: vendor datasheets, GA drawings, procurement specs, BHEL/NTPC format specs. '
        'Digital PDFs work best. Multiple pump types in one document are handled automatically.'
        '</p></div>', unsafe_allow_html=True)

    uploaded = st.file_uploader("Drop PDF here", type=["pdf"])

    if uploaded:
        st.session_state.pdf_name = uploaded.name
        with st.spinner("Extracting text from PDF..."):
            text, err = extract_pdf_text(uploaded.read())

        if err:
            st.error(f"PDF error: {err}")
        elif not text.strip():
            st.warning("No text extracted — scanned PDF. Try a digital version.")
        else:
            st.session_state.raw_text = text
            st.success(f"✅ {len(text.split())} words extracted from {uploaded.name}")

            # ── Claude reads the document ─────────────────────────
            prog = st.progress(0)
            stat = st.empty()

            steps = [
                (10,  "Reading document structure..."),
                (30,  "Identifying pump specifications..."),
                (55,  "Extracting performance parameters..."),
                (75,  "Analysing material of construction..."),
                (90,  "Compiling extracted data..."),
                (100, "Document analysis complete ✓"),
            ]
            for pct, msg in steps:
                prog.progress(pct)
                stat.markdown(
                    f'<p style="color:#8b949e;font-family:IBM Plex Mono;'
                    f'font-size:12px;">◉ {msg}</p>',
                    unsafe_allow_html=True)
                if pct == 30:
                    try:
                        specs_data = claude_extract_specs(text)
                        st.session_state.extracted_specs = specs_data
                    except Exception as e:
                        st.error(f"Analysis error: {e}")
                        import traceback; st.code(traceback.format_exc())
                        st.stop()
                elif pct < 100:
                    time.sleep(0.15)

            if st.session_state.extracted_specs:
                st.session_state.page = "specs"
                st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 2 — REVIEW SPECS
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "specs":
    data = st.session_state.extracted_specs
    if not data:
        st.warning("No specs extracted. Upload a datasheet first.")
        if st.button("← Upload"): st.session_state.page = "upload"; st.rerun()
        st.stop()

    st.markdown("## 🔍 Extracted Specifications")

    # Document type badge
    doc_type = data.get("document_type", "unknown")
    project  = data.get("project", "—")
    st.markdown(
        f'<div class="card-blue">'
        f'<b style="color:#79c0ff;">Document:</b> {doc_type} | '
        f'<b style="color:#79c0ff;">Project:</b> {project}'
        f'</div>', unsafe_allow_html=True)

    pumps = data.get("pumps", [])
    if not pumps:
        st.error("No pump specifications found in this document.")
        if st.button("← Try another file"): st.session_state.page = "upload"; st.rerun()
        st.stop()

    # Multi-pump selector
    if len(pumps) > 1:
        st.markdown(
            f'<div class="card-amber">'
            f'⚠️  <b style="color:#d29922;">{len(pumps)} pump specifications found</b>'
            f'</div>', unsafe_allow_html=True)

        labels = [f"{p.get('pump_label','Pump')} — {p.get('flow_m3h','?')} m³/h, "
                  f"{p.get('head_m','?')} m" for p in pumps]
        chosen = st.selectbox("Select pump to generate BOM for:", labels)
        st.session_state.selected_pump_idx = labels.index(chosen)

    pump_idx = st.session_state.selected_pump_idx
    pump     = pumps[min(pump_idx, len(pumps)-1)]

    # Display specs
    c1, c2 = st.columns(2)

    with c1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">Performance</div>', unsafe_allow_html=True)
        for k, lbl in [
            ("pump_label",  "Pump"),
            ("model",       "Model"),
            ("manufacturer","Manufacturer"),
            ("type",        "Type"),
            ("standard",    "Standard"),
            ("flow_m3h",    "Flow (m³/h)"),
            ("head_m",      "Head (m)"),
            ("speed_rpm",   "Speed (RPM)"),
            ("motor_kw",    "Motor (kW)"),
            ("stages",      "Stages"),
            ("fluid",       "Fluid"),
            ("temp_c",      "Temperature (°C)"),
            ("density_kgm3","Density (kg/m³)"),
            ("npsha_m",     "NPSHA (m)"),
        ]:
            val = pump.get(k)
            if val is not None:
                _kv(lbl, str(val), blue=k in ("flow_m3h","head_m","motor_kw"))
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">Material of Construction</div>',
                    unsafe_allow_html=True)
        moc = pump.get("moc", {}) or {}
        for k, lbl in [
            ("casing",       "Casing"),
            ("impeller",     "Impeller"),
            ("shaft",        "Shaft"),
            ("shaft_sleeve", "Shaft Sleeve"),
            ("wear_ring",    "Wear Ring"),
            ("bearing",      "Bearing"),
            ("seal_type",    "Seal Type"),
            ("seal_plan",    "Seal Plan"),
            ("baseplate",    "Baseplate"),
            ("fasteners",    "Fasteners"),
        ]:
            val = moc.get(k)
            if val:
                _kv(lbl, str(val))
        st.markdown("</div>", unsafe_allow_html=True)

        # Weights if available
        wts = pump.get("weights", {}) or {}
        if any(wts.values()):
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown('<div class="sec-hdr">Weights</div>', unsafe_allow_html=True)
            for k, lbl in [("pump_bare_kg","Pump"), ("motor_kg","Motor"),
                           ("baseplate_kg","Baseplate"), ("total_package_kg","Total")]:
                if wts.get(k):
                    _kv(lbl, f"{wts[k]} kg", blue=True)
            st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    ca, cb = st.columns(2)
    with ca:
        if st.button("← Re-upload", use_container_width=True):
            st.session_state.page = "upload"; st.rerun()
    with cb:
        if st.button("Generate BOM →", type="primary", use_container_width=True):
            st.session_state.pump_specs = pump
            st.session_state.bom_df = None
            st.session_state.priced_df = None
            st.session_state.cost_summary = None

            # Generate BOM
            with st.spinner(""):
                prog = st.progress(0)
                stat = st.empty()

                gen_steps = [
                    (10,  "Analysing pump type and duty conditions..."),
                    (25,  "Selecting materials for fluid compatibility..."),
                    (45,  "Building sub-assembly component structure..."),
                    (65,  "Specifying wetted parts and MOC..."),
                    (80,  "Adding instrumentation and accessories..."),
                    (92,  "Compiling complete Bill of Materials..."),
                    (100, "BOM generation complete ✓"),
                ]
                for pct, msg in gen_steps:
                    prog.progress(pct)
                    stat.markdown(
                        f'<p style="color:#8b949e;font-family:IBM Plex Mono;'
                        f'font-size:12px;">◉ {msg}</p>',
                        unsafe_allow_html=True)
                    if pct == 25:
                        try:
                            bom_raw = claude_generate_bom(pump)
                            bom_df  = bom_to_dataframe(bom_raw)
                            st.session_state.bom_raw = bom_raw
                            st.session_state.bom_df  = bom_df
                        except Exception as e:
                            st.error(f"BOM generation error: {e}")
                            import traceback; st.code(traceback.format_exc())
                            st.stop()
                    elif pct < 100:
                        time.sleep(0.15)

            st.session_state.page = "bom"
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 3 — BOM OUTPUT
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "bom":
    bom   = st.session_state.bom_df
    pump  = st.session_state.pump_specs

    if bom is None or bom.empty:
        st.warning("No BOM generated yet.")
        if st.button("← Upload"): st.session_state.page = "upload"; st.rerun()
        st.stop()

    st.markdown("## 📋 Bill of Materials — Sub-Assembly View")

    pump_label = (pump or {}).get("pump_label", "Generated BOM")
    pump_model = (pump or {}).get("model", "")
    pump_type  = (pump or {}).get("type", "")
    st.markdown(
        f'<div class="card-green">'
        f'<b style="color:#3fb950;font-size:14px;">{pump_label}</b>'
        f'{" — " + pump_model if pump_model else ""}<br>'
        f'<span style="color:#8b949e;font-size:12px;">'
        f'Type: {pump_type} | '
        f'Flow: {(pump or {}).get("flow_m3h","—")} m³/h | '
        f'Head: {(pump or {}).get("head_m","—")} m | '
        f'Motor: {(pump or {}).get("motor_kw","—")} kW'
        f'</span></div>', unsafe_allow_html=True)

    # Metrics
    groups = group_bom(bom)
    cols = st.columns(4)
    for col, (lbl, val, clr) in zip(cols, [
        ("Components",     len(bom),    "#58a6ff"),
        ("Sub-Assemblies", len(groups), "#79c0ff"),
        ("Fluid",          (pump or {}).get("fluid","—"), "#ffa657"),
        ("Motor",          f"{(pump or {}).get('motor_kw','—')} kW", "#3fb950"),
    ]):
        col.markdown(_metric(lbl, val, clr), unsafe_allow_html=True)

    st.markdown("---")

    tab1, tab2 = st.tabs(["🔩 Sub-Assembly Groups", "📋 Full Table"])

    with tab1:
        current_sec = None
        for sec, sub, gdf in groups:
            if sec != current_sec:
                current_sec = sec
                color = SECTION_COLORS.get(sec, "#444444")
                cnt   = sum(len(g) for s2,sb,g in groups if s2 == sec)
                st.markdown(
                    f'<div class="bom-section" style="background:{color};">'
                    f'<span style="color:#fff;font-weight:700;font-size:11px;'
                    f'letter-spacing:1px;">{sec}</span>'
                    f'<span style="color:#ffffffaa;font-size:11px;">{cnt} items</span></div>',
                    unsafe_allow_html=True)
            st.markdown(
                f'<div class="bom-sub">'
                f'<span style="color:#8b949e;font-size:11px;">▶ {sub}</span></div>',
                unsafe_allow_html=True)
            show = [c for c in ["No","Component","Description","MOC","Qty",
                                "Weight_kg","Req_Type","Notes"]
                    if c in gdf.columns]
            st.dataframe(gdf[show], use_container_width=True,
                         hide_index=True, height=min(35*len(gdf)+40, 280))
        st.caption(f"{len(bom)} components across {len(groups)} sub-assemblies")

    with tab2:
        st.dataframe(bom, use_container_width=True, height=500, hide_index=True)

    st.markdown("---")

    ec1, ec2, ec3, ec4 = st.columns([2, 2, 1, 1])
    with ec1:
        try:
            buf = export_excel(bom, st.session_state.extracted_specs or {})
            fn  = f"BOM_{pump_model or 'generated'}_{pd.Timestamp.now().strftime('%d%b%Y')}.xlsx"
            st.download_button("⬇ Excel", buf, fn,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)
        except Exception as e:
            st.error(f"Export: {e}")
    with ec2:
        st.download_button("⬇ CSV", bom.to_csv(index=False),
            f"BOM_{pd.Timestamp.now().strftime('%d%b%Y')}.csv",
            "text/csv", use_container_width=True)
    with ec3:
        if st.button("💰 Price It", use_container_width=True):
            st.session_state.page = "pricing"; st.rerun()
    with ec4:
        if st.button("🔄 New", use_container_width=True):
            for k in ["raw_text","extracted_specs","bom_df","bom_raw",
                      "pump_specs","priced_df","cost_summary"]:
                st.session_state[k] = None
            st.session_state.page = "upload"; st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 4 — PRICING
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "pricing":
    bom  = st.session_state.bom_df
    pump = st.session_state.pump_specs

    if bom is None or bom.empty:
        st.warning("Generate a BOM first.")
        if st.button("← Upload"): st.session_state.page = "upload"; st.rerun()
        st.stop()

    st.markdown("## 💰 BOM Cost Estimation")
    st.markdown(
        '<p style="color:#8b949e;">'
        'Live market price intelligence — components priced using current Indian market data.'
        '</p>', unsafe_allow_html=True)

    # ── Already priced? show results ──────────────────────────────
    if st.session_state.priced_df is not None and st.session_state.cost_summary:
        priced = st.session_state.priced_df
        cs     = st.session_state.cost_summary

        c1,c2,c3 = st.columns(3)
        for col,(lbl,val,clr) in zip([c1,c2,c3],[
            ("Total (Ex-GST)",  f"₹{cs['total_ex_gst']:,.0f}",   "#58a6ff"),
            ("GST (18%)",       f"₹{cs['total_gst']:,.0f}",      "#ffa657"),
            ("Total (Incl GST)",f"₹{cs['total_incl_gst']:,.0f}", "#3fb950"),
        ]):
            col.markdown(_metric(lbl,val,clr), unsafe_allow_html=True)

        st.markdown("---")
        tab1, tab2, tab3 = st.tabs(["📊 Breakdown", "📋 Line Items", "⬇ Export"])

        with tab1:
            ca, cb = st.columns([3,2])
            with ca:
                st.markdown('<div class="sec-hdr">Cost by Sub-Assembly</div>',
                            unsafe_allow_html=True)
                sub_t = cs.get("sub_totals",{})
                max_v = max(sub_t.values()) if sub_t else 1
                for sub, val in sub_t.items():
                    pct = int(val / max_v * 100)
                    pct_t = val / max(cs["total_ex_gst"],1) * 100
                    st.markdown(
                        f'<div style="margin:5px 0;">'
                        f'<div style="display:flex;justify-content:space-between;">'
                        f'<span style="color:#8b949e;font-size:12px;">{sub[:35]}</span>'
                        f'<span style="color:#58a6ff;font-family:IBM Plex Mono;font-size:12px;">'
                        f'₹{val:,.0f} ({pct_t:.1f}%)</span></div>'
                        f'<div class="wt-bar"><div class="wt-fill" '
                        f'style="background:#1f6feb;width:{pct}%;"></div></div></div>',
                        unsafe_allow_html=True)

            with cb:
                st.markdown('<div class="sec-hdr">Top Cost Drivers</div>',
                            unsafe_allow_html=True)
                for item in cs.get("top5_drivers",[]):
                    name = str(item.get("Component",""))[:35]
                    val2 = item.get("Total_Price_INR",0)
                    conf = item.get("Price_Confidence","—")
                    clr2 = "#3fb950" if conf=="high" else "#ffa657" if conf=="medium" else "#8b949e"
                    st.markdown(
                        f'<div style="padding:6px 0;border-bottom:1px solid #21262d;">'
                        f'<span style="color:#e6edf3;font-size:12px;">{name}</span><br>'
                        f'<span style="color:#58a6ff;font-family:IBM Plex Mono;font-size:12px;">'
                        f'₹{val2:,.0f}</span> '
                        f'<span style="color:{clr2};font-size:10px;">{conf}</span></div>',
                        unsafe_allow_html=True)

            st.markdown(
                f'<div class="card-amber" style="margin-top:16px;">'
                f'⚠️ <b style="color:#d29922;">Indicative Estimate</b><br>'
                f'<span style="color:#8b949e;font-size:12px;">{cs.get("note","")}</span>'
                f'</div>', unsafe_allow_html=True)

        with tab2:
            show = [c for c in ["No","Component","Description","MOC","Qty",
                                "Weight_kg","Unit_Price_INR","Total_Price_INR",
                                "GST_18pct","Price_With_GST",
                                "Price_Confidence","Price_Source"]
                    if c in priced.columns]
            disp = priced[show].copy()
            for pc in ["Unit_Price_INR","Total_Price_INR","GST_18pct","Price_With_GST"]:
                if pc in disp.columns:
                    disp[pc] = disp[pc].apply(
                        lambda x: f"₹{int(x):,}" if pd.notna(x) and x else "—")
            st.dataframe(disp, use_container_width=True, height=500, hide_index=True)
            st.caption(
                f"Total: ₹{cs['total_ex_gst']:,} + GST ₹{cs['total_gst']:,} "
                f"= ₹{cs['total_incl_gst']:,}")

        with tab3:
            st.download_button("⬇ Priced BOM (CSV)",
                priced.to_csv(index=False),
                f"Priced_BOM_{pd.Timestamp.now().strftime('%d%b%Y')}.csv",
                "text/csv", use_container_width=False)
            try:
                buf = export_excel(priced, st.session_state.extracted_specs or {}, priced=True)
                st.download_button("⬇ Priced BOM (Excel)", buf,
                    f"Priced_BOM_{pd.Timestamp.now().strftime('%d%b%Y')}.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=False)
            except Exception as e:
                st.error(f"Export: {e}")

        st.markdown("---")
        c_r1, c_r2 = st.columns(2)
        with c_r1:
            if st.button("🔄 Re-run Pricing", use_container_width=True):
                st.session_state.priced_df = None
                st.session_state.cost_summary = None; st.rerun()
        with c_r2:
            if st.button("← Back to BOM", use_container_width=True):
                st.session_state.page = "bom"; st.rerun()

    else:
        # ── Run pricing ──────────────────────────────────────────
        st.markdown(
            f'<div class="card">'
            f'<div class="sec-hdr">Market Price Accumulation</div>'
            f'<p style="color:#8b949e;font-size:13px;">'
            f'{len(bom)} components will be priced using current market intelligence. '
            f'High-value items (pump, motor, seal) use live market search. '
            f'Standard items use published rate indices.</p></div>',
            unsafe_allow_html=True)

        if st.button("▶  Run Cost Estimation", type="primary", use_container_width=False):
            prog = st.progress(0)
            stat = st.empty()

            def _prog(pct, msg):
                prog.progress(pct)
                stat.markdown(
                    f'<p style="color:#8b949e;font-family:IBM Plex Mono;'
                    f'font-size:12px;">◉ {msg}</p>',
                    unsafe_allow_html=True)

            _prog(5, "Initialising market data pipeline...")
            try:
                priced = claude_price_bom(bom, pump, _prog)
                cs     = build_cost_summary(priced)
                st.session_state.priced_df   = priced
                st.session_state.cost_summary = cs
                prog.progress(100)
                stat.markdown(
                    '<p style="color:#3fb950;font-family:IBM Plex Mono;font-size:12px;">'
                    '✓ Market data compilation complete</p>',
                    unsafe_allow_html=True)
                time.sleep(0.4); st.rerun()
            except Exception as e:
                import traceback
                st.error(f"Pricing error: {e}")
                st.code(traceback.format_exc())

        st.markdown("---")
        if st.button("← Back to BOM"):
            st.session_state.page = "bom"; st.rerun()
