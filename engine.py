"""
Automated BOM Generation System v2.0
7-page Streamlit app with full learning system
Author: Ayush Kamle
"""

import streamlit as st
import pandas as pd
import time
import json
from engine import (
    load_db, extract_pdf_text, parse_specs,
    generate_bom, export_bom_excel, calc_specific_speed,
    get_store, log_feedback, log_correction, log_pattern,
)

# ─────────────────────────────────────────────────────────────────
# PAGE CONFIG
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

[data-testid="stSidebar"]{
    background:#161b22;
    border-right:1px solid #30363d;
}

/* cards */
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px 24px;margin-bottom:16px;}
.card-green{background:#0d1f0d;border:1px solid #238636;border-radius:8px;padding:20px 24px;margin-bottom:16px;}
.card-blue{background:#0d1b2a;border:1px solid #1f6feb;border-radius:8px;padding:20px 24px;margin-bottom:16px;}
.card-orange{background:#1f1200;border:1px solid #d29922;border-radius:8px;padding:20px 24px;margin-bottom:16px;}
.card-red{background:#1f0d0d;border:1px solid #da3633;border-radius:8px;padding:20px 24px;margin-bottom:16px;}
.card-purple{background:#130d1f;border:1px solid #8957e5;border-radius:8px;padding:20px 24px;margin-bottom:16px;}

/* metric tiles */
.metric-tile{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;text-align:center;}
.metric-value{font-family:'IBM Plex Mono',monospace;font-size:26px;font-weight:600;color:#58a6ff;}
.metric-label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.8px;margin-top:4px;}

/* tier badges */
.badge-t1{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:600;background:#0d4429;color:#3fb950;border:1px solid #238636;font-family:'IBM Plex Mono',monospace;}
.badge-t2{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:600;background:#0d1b2a;color:#79c0ff;border:1px solid #1f6feb;font-family:'IBM Plex Mono',monospace;}
.badge-learn{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:600;background:#130d1f;color:#d2a8ff;border:1px solid #8957e5;font-family:'IBM Plex Mono',monospace;}

.sec-hdr{font-size:12px;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;border-bottom:1px solid #30363d;padding-bottom:6px;}
.spec-tag{display:inline-block;background:#1f2937;border:1px solid #374151;border-radius:4px;padding:2px 8px;font-family:'IBM Plex Mono',monospace;font-size:12px;color:#60a5fa;margin:2px;}
.kv-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #21262d;}
.kv-lbl{color:#8b949e;font-size:12px;}
.kv-val{color:#e6edf3;font-family:'IBM Plex Mono',monospace;font-size:12px;}
.kv-val-blue{color:#58a6ff;font-family:'IBM Plex Mono',monospace;font-size:12px;}

.logo-text{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:700;color:#58a6ff;}
.logo-sub{font-size:10px;color:#8b949e;letter-spacing:1.5px;text-transform:uppercase;}

.stButton>button{background:#238636;color:white;border:none;border-radius:6px;font-weight:500;width:100%;}
.stButton>button:hover{background:#2ea043;}
.stDownloadButton>button{background:#1f6feb;color:white;border:none;border-radius:6px;font-weight:500;width:100%;}

[data-testid="stFileUploader"]{background:#161b22;border:2px dashed #30363d;border-radius:8px;}
.stTextInput input,.stNumberInput input,.stSelectbox select{background:#161b22!important;border:1px solid #30363d!important;color:#e6edf3!important;border-radius:6px!important;font-family:'IBM Plex Mono',monospace!important;}
.stTabs [data-baseweb="tab"]{background:transparent;color:#8b949e;}
.stTabs [aria-selected="true"]{color:#58a6ff;border-bottom:2px solid #58a6ff;}

/* learning bar */
.lrn-bar-bg{background:#21262d;border-radius:4px;height:8px;margin-top:4px;}
.lrn-bar-fill{background:#3fb950;border-radius:4px;height:8px;}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────
def _init():
    D = {
        "page":          "upload",
        "specs":         {},
        "raw_text":      "",
        "pdf_name":      "",
        "bom_df":        None,
        "tier":          None,
        "match_info":    None,
        "calc_summary":  None,
        "db":            None,
        "store":         None,
        "confirmed":     False,
    }
    for k, v in D.items():
        if k not in st.session_state:
            st.session_state[k] = v
_init()


# ─────────────────────────────────────────────────────────────────
# CACHED LOADERS
# ─────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _load_db():
    return load_db()

def _kv(label, val, blue=False):
    cls = "kv-val-blue" if blue else "kv-val"
    st.markdown(
        f'<div class="kv-row"><span class="kv-lbl">{label}</span>'
        f'<span class="{cls}">{val}</span></div>',
        unsafe_allow_html=True,
    )

def _metric(label, value):
    return (
        f'<div class="metric-tile">'
        f'<div class="metric-value">{value}</div>'
        f'<div class="metric-label">{label}</div>'
        f'</div>'
    )


# ─────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="logo-text">⚙ BOM GEN</div>', unsafe_allow_html=True)
    st.markdown('<div class="logo-sub">Automated Bill of Materials v2.0</div>',
                unsafe_allow_html=True)
    st.markdown("---")

    pages = {
        "upload":   "📄  Upload Datasheet",
        "review":   "🔍  Review Specs",
        "generate": "⚙️   Generate BOM",
        "output":   "📋  BOM Output",
        "learn":    "🧠  Confirm & Learn",
        "stats":    "📊  Learning Stats",
        "database": "🗄️   Database Explorer",
    }
    for pid, lbl in pages.items():
        active = st.session_state.page == pid
        if st.button(lbl, key=f"nav_{pid}",
                     use_container_width=True,
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
            f'<b style="color:#58a6ff">{len(db["pumps"])}</b> pumps in database<br>'
            f'<b style="color:#58a6ff">{len(db["comps"])}</b> components catalogued<br>'
            f'<b style="color:#3fb950">{store["stats"]["total_sessions"]}</b> sessions confirmed<br>'
            f'<b style="color:#d2a8ff">{store["stats"]["corrections"]}</b> corrections learned<br>'
            f'<b style="color:#d2a8ff">{store["stats"]["patterns_added"]}</b> patterns added'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.error(f"DB error: {e}")

    st.markdown("---")
    st.markdown(
        '<div style="font-size:10px;color:#484f58;text-align:center;">'
        'Rule-Based Classification Engine<br>'
        'Physics-Backed BOM Generator<br>'
        'Learning System v2.0</div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════
# PAGE 1 — UPLOAD
# ═══════════════════════════════════════════════════════════════════
if st.session_state.page == "upload":
    st.markdown("## 📄 Upload Equipment Datasheet")

    col1, col2 = st.columns([3, 2])

    with col1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">PDF Upload</div>', unsafe_allow_html=True)

        uploaded = st.file_uploader(
            "Drop a pump datasheet or GA drawing PDF",
            type=["pdf"],
            help="Digital PDFs work best. Scanned drawings — use Manual Entry.",
        )
        if uploaded:
            st.session_state.pdf_name = uploaded.name
            with st.spinner("Extracting text from PDF..."):
                text, err = extract_pdf_text(uploaded.read())

            if err:
                st.error(f"PDF read error: {err}")
            elif not text.strip():
                st.warning("No text extracted — scanned PDF. Use Manual Entry →")
            else:
                st.success(f"✅ Extracted {len(text.split())} words")
                st.session_state.raw_text = text

                store = st.session_state.store or get_store()
                learned_pats = store.get("patterns", [])

                with st.spinner("Parsing specifications..."):
                    specs = parse_specs(text, learned_pats)
                st.session_state.specs = specs

                found = {k: v for k, v in specs.items() if v is not None}
                st.markdown("**Extracted specs:**")
                for k, v in found.items():
                    st.markdown(
                        f'<span class="spec-tag">{k}: {v}</span>',
                        unsafe_allow_html=True,
                    )

                if st.button("Continue to Review →", type="primary"):
                    st.session_state.page = "review"
                    st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">Manual Entry</div>', unsafe_allow_html=True)

        with st.form("manual"):
            flow  = st.number_input("Flow (m³/h)",  min_value=0.0, step=1.0)
            head  = st.number_input("Head (m)",      min_value=0.0, step=1.0)
            speed = st.number_input("Speed (RPM)",   min_value=0,   value=1450, step=50)
            motor = st.number_input("Motor (kW)",    min_value=0.0, step=1.0)
            temp  = st.number_input("Temp (°C)",     min_value=0.0, value=30.0, step=5.0)
            fluid = st.selectbox("Fluid", [
                "Clear Water","Caustic Liquor (Alumina)",
                "Live Steam Condensate","Process Condensate",
                "Slurry","Dilute Sulphuric Acid","Crude Oil",
                "Seawater","Cooling Water","Boiler Feed Water",
            ])
            model  = st.text_input("Model (optional)")
            stages = st.number_input("Stages", min_value=1, value=1)
            sub    = st.form_submit_button("Use These Specs →")
            if sub:
                dens = {
                    "Clear Water":1000,"Caustic Liquor (Alumina)":1244,
                    "Live Steam Condensate":930,"Process Condensate":990,
                    "Slurry":1300,"Dilute Sulphuric Acid":1050,
                    "Crude Oil":870,"Seawater":1025,
                    "Cooling Water":998,"Boiler Feed Water":950,
                }.get(fluid,1000)
                st.session_state.specs = {
                    "flow_m3h":    flow  or None,
                    "head_m":      head  or None,
                    "speed_rpm":   speed or None,
                    "motor_kw":    motor or None,
                    "temp_c":      temp,
                    "fluid":       fluid,
                    "density_kgm3":dens,
                    "stages":      stages,
                    "model":       model.strip() or None,
                }
                st.session_state.page = "review"
                st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# PAGE 2 — REVIEW SPECS
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "review":
    st.markdown("## 🔍 Review & Confirm Specifications")

    specs = st.session_state.specs
    if not specs:
        st.warning("No specs loaded.")
        if st.button("← Back to Upload"):
            st.session_state.page = "upload"
            st.rerun()
        st.stop()

    c1, c2 = st.columns(2)

    with c1:
        st.markdown('<div class="card-blue">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">Hydraulic Parameters</div>', unsafe_allow_html=True)
        flow  = st.number_input("Flow Rate (m³/h) *",
                    value=float(specs.get("flow_m3h") or 0.0), min_value=0.0, step=1.0)
        head  = st.number_input("Total Head (m) *",
                    value=float(specs.get("head_m") or 0.0),   min_value=0.0, step=1.0)
        speed = st.number_input("Speed (RPM)",
                    value=int(specs.get("speed_rpm") or 1450),  min_value=0, step=50)
        motor = st.number_input("Motor Power (kW)",
                    value=float(specs.get("motor_kw") or 0.0), min_value=0.0, step=1.0)

        if flow > 0 and head > 0:
            Ns = calc_specific_speed(flow, head, speed or 1450)
            cls_txt = ("Radial — HSC type" if Ns < 1500
                       else "Mixed flow — VTP type" if Ns < 4000
                       else "Axial flow")
            st.markdown(
                f'<div style="margin-top:10px;padding:10px;background:#0d1f0d;'
                f'border-radius:6px;border:1px solid #238636;">'
                f'<span style="color:#3fb950;font-family:IBM Plex Mono;font-size:13px;">'
                f'Ns = {Ns:.0f} (US units)</span><br>'
                f'<span style="color:#8b949e;font-size:11px;">{cls_txt}</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="card-blue">', unsafe_allow_html=True)
        st.markdown('<div class="sec-hdr">Service Parameters</div>', unsafe_allow_html=True)
        fluid_opts = [
            "Clear Water","Caustic Liquor (Alumina)",
            "Live Steam Condensate","Process Condensate",
            "Slurry","Dilute Sulphuric Acid","Crude Oil",
            "Seawater","Cooling Water","Boiler Feed Water","Other",
        ]
        cur_fluid = specs.get("fluid","Clear Water")
        if cur_fluid not in fluid_opts:
            fluid_opts.insert(0, cur_fluid)
        fluid  = st.selectbox("Fluid / Service",
                     fluid_opts, index=fluid_opts.index(cur_fluid))
        temp   = st.number_input("Operating Temperature (°C)",
                     value=float(specs.get("temp_c") or 30.0), min_value=0.0, step=5.0)
        dens_d = {"Clear Water":1000,"Caustic Liquor (Alumina)":1244,
                  "Live Steam Condensate":930,"Process Condensate":990,
                  "Slurry":1300,"Dilute Sulphuric Acid":1050,
                  "Crude Oil":870,"Seawater":1025}
        dens   = st.number_input("Fluid Density (kg/m³)",
                     value=float(specs.get("density_kgm3") or dens_d.get(fluid,1000)),
                     min_value=500.0, step=10.0)
        stages = st.number_input("No. of Stages",
                     value=int(specs.get("stages") or 1), min_value=1)
        model  = st.text_input("Pump Model (optional)",
                     value=str(specs.get("model") or ""))
        st.markdown("</div>", unsafe_allow_html=True)

    # Preview Tier 1 match
    db = st.session_state.db
    if db:
        from engine import tier1_match
        test = {"flow_m3h":flow or None,"head_m":head or None,
                "model":model,"fluid":fluid}
        pr, ps, pt = tier1_match(test, db)
        if pr is not None and ps >= 30:
            st.markdown(
                f'<div class="card-green">'
                f'<span class="badge-t1">TIER 1 — DATABASE MATCH</span>&nbsp;&nbsp;'
                f'<b style="color:#3fb950">{pr["Model"]}</b><br>'
                f'<span style="color:#8b949e;font-size:12px;">'
                f'Score: {ps}/100 | Type: {pt}</span></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="card-blue">'
                '<span class="badge-t2">TIER 2 — PHYSICS MODE</span><br>'
                '<span style="color:#8b949e;font-size:12px;">'
                'No database match — BOM will be calculated from engineering formulas.'
                '</span></div>',
                unsafe_allow_html=True,
            )

    ca, cb = st.columns(2)
    with ca:
        if st.button("← Back", use_container_width=True):
            st.session_state.page = "upload"
            st.rerun()
    with cb:
        if st.button("Generate BOM →", type="primary", use_container_width=True):
            st.session_state.specs = {
                "flow_m3h":    float(flow)  if flow  > 0 else None,
                "head_m":      float(head)  if head  > 0 else None,
                "speed_rpm":   int(speed)   if speed > 0 else None,
                "motor_kw":    float(motor) if motor > 0 else None,
                "temp_c":      float(temp),
                "fluid":       str(fluid),
                "density_kgm3":float(dens),
                "stages":      int(stages),
                "model":       model.strip() or None,
            }
            st.session_state.confirmed = False
            st.session_state.page = "generate"
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 3 — GENERATE
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "generate":
    st.markdown("## ⚙️ Generating BOM...")

    specs = st.session_state.specs
    db    = st.session_state.db
    store = st.session_state.store or get_store()

    if not specs or not db:
        st.error("Missing specs or database.")
        st.stop()

    prog   = st.progress(0)
    status = st.empty()

    steps = [
        (10,  "Loading database..."),
        (25,  "Checking database for exact match..."),
        (50,  "Calculating specific speed & pump type..."),
        (65,  "Checking learned corrections..."),
        (80,  "Selecting materials from compatibility matrix..."),
        (92,  "Building BOM from templates..."),
        (100, "Complete ✓"),
    ]
    for pct, msg in steps:
        prog.progress(pct)
        status.markdown(
            f'<p style="color:#8b949e;font-family:IBM Plex Mono;font-size:13px;">'
            f'{msg}</p>',
            unsafe_allow_html=True,
        )
        time.sleep(0.25)

    try:
        bom, tier, mi, cs = generate_bom(specs, db, store)
        st.session_state.bom_df       = bom
        st.session_state.tier         = tier
        st.session_state.match_info   = mi
        st.session_state.calc_summary = cs
        st.success(
            f"✅ BOM generated — {len(bom)} components | "
            f"{'Tier 1 (Database Match)' if tier=='tier1' else 'Tier 2 (Physics Calculated)'}"
        )
        time.sleep(0.4)
        st.session_state.page = "output"
        st.rerun()
    except Exception as e:
        import traceback
        st.error(f"Generation error: {e}")
        st.code(traceback.format_exc())


# ═══════════════════════════════════════════════════════════════════
# PAGE 4 — BOM OUTPUT
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "output":

    bom  = st.session_state.bom_df
    tier = st.session_state.tier
    mi   = st.session_state.match_info
    cs   = st.session_state.calc_summary
    specs= st.session_state.specs

    if bom is None:
        st.warning("No BOM yet.")
        st.stop()

    st.markdown("## 📋 Bill of Materials")

    if tier == "tier1":
        st.markdown(
            f'<div class="card-green">'
            f'<span class="badge-t1">TIER 1 — DATABASE MATCH</span>&nbsp;&nbsp;'
            f'<b style="color:#3fb950;font-size:14px;">{mi["model"]}</b><br>'
            f'<span style="color:#8b949e;font-size:12px;">'
            f'Score: {mi["score"]}/100 | Type: {mi["match_type"]} | ID: {mi["pump_id"]}'
            f'</span></div>',
            unsafe_allow_html=True,
        )
    else:
        lrn_note = " | <span style='color:#d2a8ff'>⚡ Learned correction applied</span>" \
                   if (cs or {}).get("learned_correction") else ""
        st.markdown(
            f'<div class="card-blue">'
            f'<span class="badge-t2">TIER 2 — PHYSICS CALCULATED</span>&nbsp;&nbsp;'
            f'<b style="color:#79c0ff;font-size:14px;">{(cs or {}).get("pump_type","")}</b><br>'
            f'<span style="color:#8b949e;font-size:12px;">'
            f'Ns = {(cs or {}).get("specific_speed_Ns","")} | '
            f'Template: {(cs or {}).get("template_used","")} | '
            f'Seal: {(cs or {}).get("seal_plan","")}'
            f'{lrn_note}</span></div>',
            unsafe_allow_html=True,
        )

    # Metrics row
    wts  = (cs or {}).get("weights", {})
    mkw  = specs.get("motor_kw") or (cs or {}).get("motor_kw_calc","—")
    tw   = wts.get("total_kg","—") if wts else "—"

    cols = st.columns(5)
    for col, (lbl, val) in zip(cols, [
        ("Components",   len(bom)),
        ("Flow",         f"{specs.get('flow_m3h','—')} m³/h"),
        ("Head",         f"{specs.get('head_m','—')} m"),
        ("Motor",        f"{mkw} kW"),
        ("Total Weight", f"{tw} kg"),
    ]):
        col.markdown(
            f'<div class="metric-tile">'
            f'<div class="metric-value">{val}</div>'
            f'<div class="metric-label">{lbl}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    tab1, tab2, tab3 = st.tabs(["📋 BOM Table", "📊 Summary", "🔧 Calculations"])

    with tab1:
        fc1, fc2 = st.columns([2, 2])
        with fc1:
            cats = ["All"] + sorted(bom["Category"].dropna().unique().tolist()) \
                   if "Category" in bom.columns else ["All"]
            cat_f = st.selectbox("Category", cats)
        with fc2:
            req_col = "Req_Type" if "Req_Type" in bom.columns else None
            if req_col:
                reqs  = ["All"] + sorted(bom[req_col].dropna().unique().tolist())
                req_f = st.selectbox("Required Type", reqs)
            else:
                req_f = "All"

        disp = bom.copy()
        if cat_f != "All" and "Category" in disp.columns:
            disp = disp[disp["Category"] == cat_f]
        if req_f != "All" and req_col:
            disp = disp[disp[req_col] == req_f]

        key_cols = ["No","Component_ID","Category","Description",
                    "MOC","Material_Spec","Qty","Qty_Per_Unit",
                    "Weight_kg","Vendor_Name","Req_Type","Source"]
        show = [c for c in key_cols if c in disp.columns]
        st.dataframe(disp[show], use_container_width=True, height=430, hide_index=True)
        st.caption(f"{len(disp)} of {len(bom)} components shown")

    with tab2:
        sc1, sc2 = st.columns(2)
        with sc1:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown('<div class="sec-hdr">Input Specifications</div>',
                        unsafe_allow_html=True)
            for k, v in {
                "Flow":        f"{specs.get('flow_m3h','—')} m³/h",
                "Head":        f"{specs.get('head_m','—')} m",
                "Speed":       f"{specs.get('speed_rpm','—')} rpm",
                "Motor":       f"{specs.get('motor_kw','—')} kW",
                "Temperature": f"{specs.get('temp_c','—')} °C",
                "Fluid":       specs.get('fluid','—'),
                "Density":     f"{specs.get('density_kgm3','—')} kg/m³",
                "Stages":      specs.get('stages','—'),
            }.items():
                _kv(k, v)
            st.markdown("</div>", unsafe_allow_html=True)

        with sc2:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown('<div class="sec-hdr">Weight Breakdown</div>',
                        unsafe_allow_html=True)
            if wts:
                for k, v in wts.items():
                    _kv(k.replace("_"," ").title(), f"{v} kg", blue=True)
            else:
                st.markdown('<p style="color:#8b949e;font-size:12px;">'
                            'Weight from database record.</p>',
                            unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

    with tab3:
        if tier == "tier2" and cs:
            sc1, sc2 = st.columns(2)
            with sc1:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown('<div class="sec-hdr">Engineering Calculation Trace</div>',
                            unsafe_allow_html=True)
                for k, v in {
                    "Specific Speed (Ns)":    cs.get("specific_speed_Ns","—"),
                    "Pump Classification":    cs.get("pump_type","—"),
                    "Template Used":          cs.get("template_used","—"),
                    "Motor kW (calculated)":  f"{cs.get('motor_kw_calc','—')} kW",
                    "Pump Efficiency":        f"{cs.get('eta_pump_assumed','—')}%",
                    "Learned Correction":     cs.get("learned_correction","None applied"),
                }.items():
                    _kv(k, str(v), blue=True)
                st.markdown("</div>", unsafe_allow_html=True)

            with sc2:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown('<div class="sec-hdr">Material Selection</div>',
                            unsafe_allow_html=True)
                _kv("Material Rule",  cs.get("material_rule","—"))
                _kv("Fluid Matched",  cs.get("fluid_matched","—"))
                moc = cs.get("moc", {})
                for k, v in {
                    "Casing":       moc.get("Casing_MOC","—"),
                    "Impeller":     moc.get("Impeller_MOC","—"),
                    "Shaft":        moc.get("Shaft_MOC","—"),
                    "Shaft Sleeve": moc.get("Shaft_Sleeve_MOC","—"),
                    "Seal Plan":    moc.get("Seal_Plan","—"),
                }.items():
                    _kv(k, str(v))
                st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="card-green">'
                '<b style="color:#3fb950">Tier 1 — Direct database lookup.</b><br>'
                '<span style="color:#8b949e;font-size:13px;">'
                'BOM retrieved from Component_Library. No physics calculation needed.'
                '</span></div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    ec1, ec2, ec3 = st.columns([2, 2, 1])
    with ec1:
        try:
            buf = export_bom_excel(bom, specs, tier, mi, cs)
            fn  = f"BOM_{specs.get('model','') or 'calculated'}_{pd.Timestamp.now().strftime('%d%b%Y')}.xlsx"
            st.download_button("⬇ Download Excel", buf, fn,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)
        except Exception as e:
            st.error(f"Export error: {e}")
    with ec2:
        csv = bom.to_csv(index=False)
        st.download_button("⬇ Download CSV", csv,
            f"BOM_{pd.Timestamp.now().strftime('%d%b%Y')}.csv",
            "text/csv", use_container_width=True)
    with ec3:
        if st.button("🧠 Confirm & Learn", use_container_width=True):
            st.session_state.page = "learn"
            st.rerun()

    if st.button("🔄 New BOM", use_container_width=False):
        for k in ["specs","bom_df","tier","match_info","calc_summary",
                  "raw_text","pdf_name","confirmed"]:
            st.session_state[k] = {} if k == "specs" else None
        st.session_state.page = "upload"
        st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 5 — CONFIRM & LEARN
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "learn":

    bom  = st.session_state.bom_df
    tier = st.session_state.tier
    cs   = st.session_state.calc_summary
    mi   = st.session_state.match_info
    specs= st.session_state.specs

    st.markdown("## 🧠 Confirm & Learn")
    st.markdown(
        '<p style="color:#8b949e;">Review the generated BOM. '
        'Correct anything wrong. The system learns from your corrections '
        'and improves future outputs.</p>',
        unsafe_allow_html=True,
    )

    if bom is None:
        st.warning("Generate a BOM first.")
        st.stop()

    if st.session_state.confirmed:
        st.markdown(
            '<div class="card-green">'
            '<b style="color:#3fb950;font-size:15px;">✓ Session Confirmed & Saved</b><br>'
            '<span style="color:#8b949e;font-size:13px;">'
            'The learning store has been updated. View stats on the Learning Stats page.'
            '</span></div>',
            unsafe_allow_html=True,
        )
        if st.button("← Back to Output"):
            st.session_state.page = "output"
            st.rerun()
        st.stop()

    st.markdown('<div class="sec-hdr">Current BOM Summary</div>', unsafe_allow_html=True)
    st.dataframe(
        bom[["No","Category","Description","MOC","Qty","Weight_kg","Source"]
            if all(c in bom.columns for c in ["Description","MOC"])
            else bom.columns[:7]].head(10),
        use_container_width=True, hide_index=True, height=280,
    )

    st.markdown("---")

    # ── Section A: Confirm pump type ─────────────────────────────
    st.markdown('<div class="card-purple">', unsafe_allow_html=True)
    st.markdown(
        '<span class="badge-learn">SECTION A</span>&nbsp;&nbsp;'
        '<b style="color:#d2a8ff">Pump Type Confirmation</b>',
        unsafe_allow_html=True,
    )
    st.markdown("&nbsp;", unsafe_allow_html=True)

    current_type = ((cs or {}).get("pump_type","") or
                    (mi or {}).get("model","Unknown"))

    pump_types = [
        "Horizontal Split Casing",
        "Horizontal Split Casing — High Head",
        "Horizontal Slurry Pump",
        "Vertical Turbine Pump",
        "Vertical Turbine Pump VS6 (Condensate)",
        "Vertical Sump Pump",
        "Vertical Submersible",
        "Multistage Centrifugal (BFW)",
        "Other",
    ]
    if current_type and current_type not in pump_types:
        pump_types.insert(0, current_type)

    confirmed_type = st.selectbox(
        "Confirm or correct the pump type classification:",
        pump_types,
        index=pump_types.index(current_type) if current_type in pump_types else 0,
    )
    type_notes = st.text_input("Notes on type correction (optional)",
                               placeholder="e.g. Ns was 1600 but client specified HSC due to site layout")
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Section B: MOC Confirmation ──────────────────────────────
    st.markdown('<div class="card-purple">', unsafe_allow_html=True)
    st.markdown(
        '<span class="badge-learn">SECTION B</span>&nbsp;&nbsp;'
        '<b style="color:#d2a8ff">Material of Construction Confirmation</b>',
        unsafe_allow_html=True,
    )
    st.markdown("&nbsp;", unsafe_allow_html=True)

    moc_base = (cs or {}).get("moc", {}) or {}
    bc1, bc2 = st.columns(2)
    with bc1:
        c_casing  = st.text_input("Casing MOC",
            value=str(moc_base.get("Casing_MOC","ASTM A216 WCB")))
        c_imp     = st.text_input("Impeller MOC",
            value=str(moc_base.get("Impeller_MOC","CF8M SS316")))
        c_shaft   = st.text_input("Shaft MOC",
            value=str(moc_base.get("Shaft_MOC","EN19/SS410")))
        c_seal    = st.text_input("Seal Plan",
            value=str(moc_base.get("Seal_Plan","Plan 11")))
    with bc2:
        c_sleeve  = st.text_input("Shaft Sleeve MOC",
            value=str(moc_base.get("Shaft_Sleeve_MOC","SS410")))
        c_wring   = st.text_input("Wear Ring MOC",
            value=str(moc_base.get("Wear_Ring_MOC","A487 CA6M")))
        c_fastener= st.text_input("Fastener MOC",
            value=str(moc_base.get("Fastener_MOC","A193 B7")))
        c_seal_type=st.text_input("Seal Type",
            value=str(moc_base.get("Seal_Type","Mechanical Seal")))

    confirmed_moc = {
        "Casing_MOC": c_casing, "Impeller_MOC": c_imp,
        "Shaft_MOC": c_shaft,   "Shaft_Sleeve_MOC": c_sleeve,
        "Wear_Ring_MOC": c_wring, "Seal_Plan": c_seal,
        "Seal_Type": c_seal_type, "Fastener_MOC": c_fastener,
    }
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Section C: Weight Confirmation ───────────────────────────
    st.markdown('<div class="card-purple">', unsafe_allow_html=True)
    st.markdown(
        '<span class="badge-learn">SECTION C</span>&nbsp;&nbsp;'
        '<b style="color:#d2a8ff">Weight Confirmation (calibrates future predictions)</b>',
        unsafe_allow_html=True,
    )
    st.markdown("&nbsp;", unsafe_allow_html=True)

    wts_base = (cs or {}).get("weights", {}) or {}
    wc1, wc2 = st.columns(2)
    with wc1:
        wp  = st.number_input("Actual Pump Weight (kg)",
                value=float(wts_base.get("pump_kg",0) or 0), min_value=0.0, step=10.0)
        wm  = st.number_input("Actual Motor Weight (kg)",
                value=float(wts_base.get("motor_kg",0) or 0), min_value=0.0, step=10.0)
    with wc2:
        wb  = st.number_input("Actual Baseplate Weight (kg)",
                value=float(wts_base.get("baseplate_kg",0) or 0), min_value=0.0, step=10.0)
        wt  = st.number_input("Actual Total Package Weight (kg)",
                value=float(wts_base.get("total_kg",0) or 0), min_value=0.0, step=10.0)

    confirmed_weights = {
        "pump_kg": wp or None, "motor_kg": wm or None,
        "baseplate_kg": wb or None, "total_kg": wt or None,
    }
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Section D: Parser pattern correction ─────────────────────
    st.markdown('<div class="card-purple">', unsafe_allow_html=True)
    st.markdown(
        '<span class="badge-learn">SECTION D</span>&nbsp;&nbsp;'
        '<b style="color:#d2a8ff">Teach Parser a New Pattern (optional)</b>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p style="color:#8b949e;font-size:12px;">'
        'If the PDF parser missed a value, teach it here. '
        'Example: field=motor_kw, snippet="BKW 60.4", correct=60.4</p>',
        unsafe_allow_html=True,
    )
    pc1, pc2, pc3 = st.columns(3)
    with pc1:
        p_field = st.selectbox("Field that was missed", [
            "—", "flow_m3h","head_m","speed_rpm","motor_kw",
            "temp_c","density_kgm3","stages",
        ])
    with pc2:
        p_snippet = st.text_input("Text snippet from PDF",
            placeholder="BKW 60.4 kW")
    with pc3:
        p_correct = st.text_input("Correct value",
            placeholder="60.4")
    p_notes = st.text_input("Notes", placeholder="BKW = shaft power in this manufacturer's format")
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Engineer notes & confirm ──────────────────────────────────
    eng_notes = st.text_area("General notes / observations (optional)",
                    placeholder="Overall quality of BOM, what was wrong, any special observations...")

    st.markdown("---")

    # Type correction logging
    if tier == "tier2" and cs:
        orig_type = cs.get("pump_type","")
        if confirmed_type != orig_type and orig_type:
            st.markdown(
                f'<div class="card-orange">'
                f'⚠️ <b style="color:#d29922;">Type correction detected:</b><br>'
                f'<span style="color:#8b949e;font-size:12px;">'
                f'System said: <b>{orig_type}</b> → You corrected to: '
                f'<b>{confirmed_type}</b><br>'
                f'This will be remembered for similar duty points.</span></div>',
                unsafe_allow_html=True,
            )

    col_conf, col_skip = st.columns(2)
    with col_conf:
        if st.button("✅ Confirm & Save to Learning Store",
                     type="primary", use_container_width=True):
            try:
                # Save pattern if provided
                if p_field != "—" and p_snippet and p_correct:
                    log_pattern(p_field, "", p_correct, p_snippet, p_notes)

                # Save type correction if changed
                if tier == "tier2" and cs:
                    orig = cs.get("pump_type","")
                    if confirmed_type != orig and orig:
                        log_correction(specs, orig, confirmed_type, type_notes)

                # Save full feedback
                log_feedback(
                    specs, bom, tier,
                    confirmed_type, confirmed_moc,
                    confirmed_weights, eng_notes,
                )

                # Refresh store
                st.session_state.store    = get_store()
                st.session_state.confirmed = True
                st.rerun()

            except Exception as e:
                st.error(f"Error saving: {e}")

    with col_skip:
        if st.button("Skip — Back to Output", use_container_width=True):
            st.session_state.page = "output"
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 6 — LEARNING STATS
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "stats":
    st.markdown("## 📊 Learning Statistics")

    store = get_store()
    stats = store.get("stats", {})

    # Top metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    for col, (lbl, val, color) in zip(
        [m1,m2,m3,m4,m5],
        [
            ("Total Sessions",    stats.get("total_sessions",0), "#58a6ff"),
            ("Tier 1 Hits",       stats.get("tier1_hits",0),     "#3fb950"),
            ("Tier 2 Hits",       stats.get("tier2_hits",0),     "#79c0ff"),
            ("Corrections Made",  stats.get("corrections",0),    "#d2a8ff"),
            ("Patterns Learned",  stats.get("patterns_added",0), "#ffa657"),
        ]
    ):
        col.markdown(
            f'<div class="metric-tile">'
            f'<div class="metric-value" style="color:{color}">{val}</div>'
            f'<div class="metric-label">{lbl}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    tab1, tab2, tab3, tab4 = st.tabs([
        "📝 Feedback History",
        "🔧 Corrections",
        "🔬 Parser Patterns",
        "⚖️ Weight Calibration",
    ])

    with tab1:
        fb = store.get("feedback", [])
        if not fb:
            st.info("No sessions confirmed yet. Generate a BOM and click 'Confirm & Learn'.")
        else:
            rows = []
            for f in reversed(fb[-30:]):
                rows.append({
                    "Timestamp":   f.get("ts","")[:16].replace("T"," "),
                    "Pump Type":   f.get("pump_type",""),
                    "Tier":        f.get("tier",""),
                    "Ns":          f.get("ns",""),
                    "Flow m³/h":   (f.get("specs") or {}).get("flow_m3h",""),
                    "Head m":      (f.get("specs") or {}).get("head_m",""),
                    "BOM Rows":    f.get("bom_rows",""),
                    "Notes":       f.get("notes",""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         hide_index=True, height=350)

            # Tier distribution bar
            t1h = stats.get("tier1_hits",0)
            t2h = stats.get("tier2_hits",0)
            tot = t1h + t2h
            if tot > 0:
                st.markdown("**Tier Distribution**")
                t1pct = int(t1h/tot*100)
                t2pct = 100 - t1pct
                st.markdown(
                    f'<div style="display:flex;gap:8px;align-items:center;margin-top:8px;">'
                    f'<span style="color:#3fb950;font-size:12px;width:80px;">Tier 1: {t1h}</span>'
                    f'<div style="flex:1;background:#21262d;border-radius:4px;height:12px;">'
                    f'<div style="background:#3fb950;width:{t1pct}%;border-radius:4px;height:12px;"></div></div>'
                    f'<span style="color:#3fb950;font-size:12px;">{t1pct}%</span></div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<div style="display:flex;gap:8px;align-items:center;margin-top:4px;">'
                    f'<span style="color:#79c0ff;font-size:12px;width:80px;">Tier 2: {t2h}</span>'
                    f'<div style="flex:1;background:#21262d;border-radius:4px;height:12px;">'
                    f'<div style="background:#1f6feb;width:{t2pct}%;border-radius:4px;height:12px;"></div></div>'
                    f'<span style="color:#79c0ff;font-size:12px;">{t2pct}%</span></div>',
                    unsafe_allow_html=True,
                )

    with tab2:
        corrs = store.get("corrections", [])
        if not corrs:
            st.info("No corrections logged yet.")
        else:
            rows = []
            for c in reversed(corrs):
                rows.append({
                    "Timestamp":    c.get("ts","")[:16].replace("T"," "),
                    "Ns":           c.get("ns",""),
                    "Fluid":        c.get("fluid",""),
                    "Flow m³/h":    c.get("flow",""),
                    "Head m":       c.get("head",""),
                    "Was Wrong":    c.get("wrong_type",""),
                    "Corrected To": c.get("correct_type",""),
                    "Notes":        c.get("notes",""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         hide_index=True, height=300)
            st.markdown(
                '<div class="card-blue">'
                '<b style="color:#79c0ff;">How corrections are used:</b><br>'
                '<span style="color:#8b949e;font-size:12px;">'
                'When a new pump comes in with Ns within ±30% of a corrected case '
                'and same fluid category, the system applies the corrected pump type '
                'instead of the formula-based classification.</span></div>',
                unsafe_allow_html=True,
            )

    with tab3:
        pats = store.get("patterns", [])
        if not pats:
            st.info("No parser patterns added yet. Use 'Confirm & Learn → Section D'.")
        else:
            rows = []
            for p in reversed(pats):
                rows.append({
                    "Timestamp": p.get("ts","")[:16].replace("T"," "),
                    "Field":     p.get("field",""),
                    "Snippet":   p.get("snippet","")[:60],
                    "Correct Value": p.get("correct",""),
                    "Notes":     p.get("notes",""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         hide_index=True, height=250)
            st.markdown(
                '<div class="card-blue">'
                '<b style="color:#79c0ff;">How patterns are used:</b><br>'
                '<span style="color:#8b949e;font-size:12px;">'
                'When a PDF is uploaded, the parser tries these learned patterns '
                'in addition to the built-in regex rules. Useful for manufacturer-specific '
                'terminology like "BKW" (shaft power) or "Q_rated" (flow).</span></div>',
                unsafe_allow_html=True,
            )

    with tab4:
        calibs = store.get("weight_calibs", {})
        if not calibs:
            st.info("No weight calibrations yet. Confirm sessions with actual weights to calibrate.")
        else:
            rows = []
            for pt, cal in calibs.items():
                rows.append({
                    "Pump Type Key":    pt,
                    "Pump Coefficient": round(cal.get("pump_coeff",1.0),4),
                    "Motor Coefficient":round(cal.get("motor_coeff",1.0),4),
                    "Samples":          cal.get("n_samples",0),
                    "Pump Drift %":     f"{(cal.get('pump_coeff',1.0)-1)*100:+.1f}%",
                    "Motor Drift %":    f"{(cal.get('motor_coeff',1.0)-1)*100:+.1f}%",
                    "Last Updated":     (cal.get("last_updated","")[:16] or "").replace("T"," "),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.markdown(
                '<div class="card-blue">'
                '<b style="color:#79c0ff;">How calibration works:</b><br>'
                '<span style="color:#8b949e;font-size:12px;">'
                'Each time you confirm actual weights, the system computes the ratio '
                '(actual ÷ predicted) and updates a running average coefficient per pump type. '
                'A coefficient of 1.08 means the formula consistently under-predicts by 8% '
                'for that type — future predictions are scaled up automatically.</span></div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")
    if st.button("🗑 Reset Learning Store", type="secondary"):
        confirm_reset = st.checkbox("I confirm — reset all learned data")
        if confirm_reset:
            import os
            from engine import LRN_PATH
            if os.path.exists(LRN_PATH):
                os.remove(LRN_PATH)
            st.session_state.store = get_store()
            st.success("Learning store reset.")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 7 — DATABASE EXPLORER
# ═══════════════════════════════════════════════════════════════════
elif st.session_state.page == "database":
    st.markdown("## 🗄️ Database Explorer")

    db = st.session_state.db
    if not db:
        st.error("Database not loaded.")
        st.stop()

    tp, tc, tm, tv = st.tabs([
        f"Pumps ({len(db['pumps'])})",
        f"Components ({len(db['comps'])})",
        f"Materials ({len(db['mats'])})",
        f"Vendors ({len(db['vendors'])})",
    ])

    with tp:
        st.markdown("### Pump Master List")
        srch = st.text_input("Search", placeholder="model, manufacturer, type...")
        pumps = db["pumps"].copy()
        if srch:
            mask = pumps.astype(str).apply(
                lambda col: col.str.contains(srch, case=False, na=False)
            ).any(axis=1)
            pumps = pumps[mask]
        st.dataframe(pumps, use_container_width=True, hide_index=True)

        # Type distribution
        tc_counts = db["pumps"]["Type"].value_counts()
        st.markdown("**Type Distribution**")
        for ptype, cnt in tc_counts.items():
            bw = int(cnt / max(tc_counts) * 200)
            st.markdown(
                f'<div style="display:flex;align-items:center;margin:3px 0;gap:8px;">'
                f'<span style="color:#8b949e;font-size:12px;width:220px;">{ptype}</span>'
                f'<div style="background:#1f6feb;height:14px;width:{bw}px;border-radius:3px;"></div>'
                f'<span style="color:#58a6ff;font-family:IBM Plex Mono;font-size:12px;">{cnt}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    with tc:
        st.markdown("### Component Library")
        cs1, cs2 = st.columns([2,1])
        with cs1:
            csrch = st.text_input("Search components", placeholder="category, material, vendor...")
        with cs2:
            pf = ["All"] + db["pumps"]["Model"].tolist()
            pfilt = st.selectbox("Filter by pump", pf)
        comps = db["comps"].copy()
        if csrch:
            mask = comps.astype(str).apply(
                lambda col: col.str.contains(csrch, case=False, na=False)
            ).any(axis=1)
            comps = comps[mask]
        if pfilt != "All":
            comps = comps[comps["Pump_Model_Compatibility"].str.contains(
                pfilt, case=False, na=False)]
        st.dataframe(
            comps[["Component_ID","Component_Name","Category",
                   "Material_Spec","Weight_kg","Vendor_Name",
                   "Pump_Model_Compatibility"]],
            use_container_width=True, hide_index=True, height=400,
        )

    with tm:
        st.markdown("### Material Database")
        st.dataframe(db["mats"], use_container_width=True, hide_index=True)

    with tv:
        st.markdown("### Vendor Database")
        st.dataframe(db["vendors"], use_container_width=True, hide_index=True)
