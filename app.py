"""
Automated BOM Generation System for Engineered Equipment
Author: Ayush Kamle
Stack: Streamlit + Python + Excel Database
"""

import streamlit as st
import pandas as pd
import json
import time
from engine import (
    load_db, extract_pdf_text, parse_specs,
    generate_bom, export_bom_excel, calc_specific_speed
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
# CUSTOM CSS
# ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}

/* Hide Streamlit branding */
#MainMenu, footer, header { visibility: hidden; }

/* Main background */
.stApp {
    background-color: #0d1117;
    color: #e6edf3;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background-color: #161b22;
    border-right: 1px solid #30363d;
}
[data-testid="stSidebar"] .stMarkdown {
    color: #8b949e;
}

/* Cards */
.bom-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 16px;
}
.bom-card-green {
    background: #0d1f0d;
    border: 1px solid #238636;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 16px;
}
.bom-card-blue {
    background: #0d1b2a;
    border: 1px solid #1f6feb;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 16px;
}
.bom-card-orange {
    background: #1f1200;
    border: 1px solid #d29922;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 16px;
}

/* Metric tiles */
.metric-row {
    display: flex;
    gap: 12px;
    margin-bottom: 16px;
}
.metric-tile {
    flex: 1;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px;
    text-align: center;
}
.metric-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 28px;
    font-weight: 600;
    color: #58a6ff;
}
.metric-label {
    font-size: 11px;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-top: 4px;
}

/* Tier badge */
.tier-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    font-family: 'IBM Plex Mono', monospace;
}
.tier1 { background: #0d4429; color: #3fb950; border: 1px solid #238636; }
.tier2 { background: #0d1b2a; color: #79c0ff; border: 1px solid #1f6feb; }

/* Section headers */
.section-header {
    font-size: 13px;
    font-weight: 600;
    color: #8b949e;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 12px;
    border-bottom: 1px solid #30363d;
    padding-bottom: 8px;
}

/* Spec tag */
.spec-tag {
    display: inline-block;
    background: #1f2937;
    border: 1px solid #374151;
    border-radius: 4px;
    padding: 2px 8px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    color: #60a5fa;
    margin: 2px;
}

/* BOM table styling */
.dataframe {
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 12px !important;
}

/* Buttons */
.stButton > button {
    background: #238636;
    color: white;
    border: none;
    border-radius: 6px;
    font-family: 'IBM Plex Sans', sans-serif;
    font-weight: 500;
    padding: 8px 20px;
    width: 100%;
}
.stButton > button:hover {
    background: #2ea043;
}

/* Upload area */
[data-testid="stFileUploader"] {
    background: #161b22;
    border: 2px dashed #30363d;
    border-radius: 8px;
}

/* Inputs */
.stTextInput input, .stNumberInput input, .stSelectbox select {
    background: #161b22 !important;
    border: 1px solid #30363d !important;
    color: #e6edf3 !important;
    border-radius: 6px !important;
    font-family: 'IBM Plex Mono', monospace !important;
}

/* Progress */
.stProgress > div > div {
    background: #238636 !important;
}

/* Tabs */
.stTabs [data-baseweb="tab"] {
    background: transparent;
    color: #8b949e;
    font-family: 'IBM Plex Sans', sans-serif;
}
.stTabs [aria-selected="true"] {
    color: #58a6ff;
    border-bottom: 2px solid #58a6ff;
}

/* Warning/info boxes */
.stAlert {
    border-radius: 6px;
}

/* Download button */
.stDownloadButton > button {
    background: #1f6feb;
    color: white;
    border: none;
    border-radius: 6px;
    font-weight: 500;
    width: 100%;
}
.stDownloadButton > button:hover {
    background: #388bfd;
}

.logo-text {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 22px;
    font-weight: 700;
    color: #58a6ff;
    letter-spacing: -0.5px;
}
.logo-sub {
    font-size: 11px;
    color: #8b949e;
    letter-spacing: 1.5px;
    text-transform: uppercase;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────
def init_state():
    defaults = {
        "page":         "upload",
        "specs":        {},
        "extracted_text": "",
        "bom_df":       None,
        "tier_used":    None,
        "match_info":   None,
        "calc_summary": None,
        "db":           None,
        "pdf_name":     "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()


# ─────────────────────────────────────────────────────────────────
# LOAD DATABASE (cached)
# ─────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def get_db():
    return load_db()


# ─────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="logo-text">⚙ BOM GEN</div>', unsafe_allow_html=True)
    st.markdown('<div class="logo-sub">Automated Bill of Materials</div>', unsafe_allow_html=True)
    st.markdown("---")

    pages = {
        "upload":   "📄  Upload Datasheet",
        "review":   "🔍  Review Specs",
        "generate": "⚙️   Generate BOM",
        "output":   "📋  BOM Output",
        "database": "🗄️   Database Explorer",
    }
    for page_id, label in pages.items():
        is_active = st.session_state.page == page_id
        if st.button(
            label,
            key=f"nav_{page_id}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            st.session_state.page = page_id
            st.rerun()

    st.markdown("---")

    # DB stats
    try:
        db = get_db()
        st.session_state.db = db
        n_pumps = len(db["pumps"])
        n_comps = len(db["comps"])
        n_mats  = len(db["mats"])
        n_vend  = len(db["vendors"])
        st.markdown(f"""
        <div style="font-size:11px; color:#8b949e; line-height:2;">
        <b style="color:#58a6ff">{n_pumps}</b> pumps in database<br>
        <b style="color:#58a6ff">{n_comps}</b> components catalogued<br>
        <b style="color:#58a6ff">{n_mats}</b> materials indexed<br>
        <b style="color:#58a6ff">{n_vend}</b> vendors registered
        </div>
        """, unsafe_allow_html=True)
    except Exception as e:
        st.error(f"DB load error: {e}")

    st.markdown("---")
    st.markdown(
        '<div style="font-size:10px; color:#484f58; text-align:center;">'
        'Rule-Based Classification Engine<br>'
        'Physics-Backed BOM Generator<br>'
        'v1.0 — April 2026</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────
# PAGE 1 — UPLOAD
# ─────────────────────────────────────────────────────────────────
if st.session_state.page == "upload":

    st.markdown("## 📄 Upload Equipment Datasheet")
    st.markdown(
        '<p style="color:#8b949e;">Upload a pump datasheet PDF. '
        'The system will extract specifications automatically using OCR.</p>',
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([3, 2])

    with col1:
        st.markdown('<div class="bom-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-header">PDF Upload</div>', unsafe_allow_html=True)

        uploaded = st.file_uploader(
            "Drop a pump datasheet PDF here",
            type=["pdf"],
            help="Supports digital PDFs. Scanned drawings may need manual spec entry.",
        )

        if uploaded:
            st.session_state.pdf_name = uploaded.name
            with st.spinner("Extracting text from PDF..."):
                text, err = extract_pdf_text(uploaded.read())

            if err:
                st.error(f"PDF read error: {err}")
            elif not text.strip():
                st.warning(
                    "⚠️ No text extracted — this may be a scanned/image PDF. "
                    "Use Manual Entry below."
                )
            else:
                st.success(f"✅ Extracted {len(text.split())} words from PDF")
                st.session_state.extracted_text = text

                with st.spinner("Parsing specifications..."):
                    specs = parse_specs(text)
                    st.session_state.specs = specs

                st.markdown("**Specs found:**")
                found = {k: v for k, v in specs.items() if v}
                for k, v in found.items():
                    st.markdown(
                        f'<span class="spec-tag">{k}: {v}</span>',
                        unsafe_allow_html=True,
                    )

                if st.button("→ Continue to Review", type="primary"):
                    st.session_state.page = "review"
                    st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="bom-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-header">Manual Entry</div>', unsafe_allow_html=True)
        st.markdown(
            '<p style="color:#8b949e; font-size:13px;">No PDF? Enter specs directly.</p>',
            unsafe_allow_html=True,
        )

        with st.form("manual_entry"):
            flow   = st.number_input("Flow (m³/h)", min_value=0.0, value=0.0, step=1.0)
            head   = st.number_input("Head (m)",    min_value=0.0, value=0.0, step=1.0)
            speed  = st.number_input("Speed (RPM)", min_value=0,   value=1450, step=50)
            motor  = st.number_input("Motor (kW)",  min_value=0.0, value=0.0, step=1.0)
            temp   = st.number_input("Temp (°C)",   min_value=0.0, value=30.0, step=5.0)
            fluid  = st.selectbox("Fluid", [
                "Clear Water", "Caustic Liquor (Alumina)",
                "Live Steam Condensate", "Process Condensate",
                "Slurry", "Dilute Sulphuric Acid",
                "Crude Oil", "Seawater", "Cooling Water",
            ])
            model  = st.text_input("Model (optional)", placeholder="e.g. 300-LNN-600")
            stages = st.number_input("Stages", min_value=1, value=1)

            submit = st.form_submit_button("Use These Specs →")
            if submit:
                specs = {}
                if flow  > 0: specs["flow_m3h"]   = flow
                if head  > 0: specs["head_m"]      = head
                if speed > 0: specs["speed_rpm"]   = speed
                if motor > 0: specs["motor_kw"]    = motor
                if temp  > 0: specs["temp_c"]      = temp
                if model:     specs["model"]        = model
                specs["fluid"]  = fluid
                specs["stages"] = stages
                density_map = {
                    "Clear Water": 1000, "Caustic Liquor (Alumina)": 1244,
                    "Live Steam Condensate": 930, "Process Condensate": 990,
                    "Slurry": 1300, "Dilute Sulphuric Acid": 1050,
                    "Crude Oil": 870, "Seawater": 1025, "Cooling Water": 998,
                }
                specs["density_kgm3"] = density_map.get(fluid, 1000)
                st.session_state.specs = specs
                st.session_state.page  = "review"
                st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────
# PAGE 2 — REVIEW SPECS
# ─────────────────────────────────────────────────────────────────
elif st.session_state.page == "review":

    st.markdown("## 🔍 Review & Confirm Specifications")
    st.markdown(
        '<p style="color:#8b949e;">Verify extracted specs. '
        'Correct any errors before generating the BOM.</p>',
        unsafe_allow_html=True,
    )

    specs = st.session_state.specs
    if not specs:
        st.warning("No specs loaded. Please upload a PDF or use manual entry.")
        if st.button("← Back to Upload"):
            st.session_state.page = "upload"
            st.rerun()
        st.stop()

    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="bom-card-blue">', unsafe_allow_html=True)
        st.markdown('<div class="section-header">Hydraulic Parameters</div>', unsafe_allow_html=True)

        flow  = st.number_input("Flow Rate (m³/h) *",
            value=float(specs.get("flow_m3h") or 0.0), min_value=0.0, step=1.0)
        head  = st.number_input("Total Head (m) *",
            value=float(specs.get("head_m") or 0.0),  min_value=0.0, step=1.0)
        speed = st.number_input("Speed (RPM)",
            value=int(specs.get("speed_rpm") or 1450),  min_value=0, step=50)
        motor = st.number_input("Motor Power (kW)",
            value=float(specs.get("motor_kw") or 0.0), min_value=0.0, step=1.0)

        # Live Ns calculation
        if flow > 0 and head > 0:
            Ns = calc_specific_speed(flow, head, speed or 1450)
            st.markdown(
                f'<div style="margin-top:10px; padding:10px; background:#0d1f0d; '
                f'border-radius:6px; border:1px solid #238636;">'
                f'<span style="color:#3fb950; font-family: IBM Plex Mono; font-size:13px;">'
                f'Ns = {Ns:.0f} (US units)</span><br>'
                f'<span style="color:#8b949e; font-size:11px;">'
                f'{"Radial flow — HSC type" if Ns < 1500 else "Mixed flow — VTP type" if Ns < 4000 else "Axial flow"}'
                f'</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="bom-card-blue">', unsafe_allow_html=True)
        st.markdown('<div class="section-header">Service Parameters</div>', unsafe_allow_html=True)

        fluid_options = [
            "Clear Water", "Caustic Liquor (Alumina)",
            "Live Steam Condensate", "Process Condensate",
            "Slurry", "Dilute Sulphuric Acid", "Crude Oil",
            "Seawater", "Cooling Water", "Other",
        ]
        current_fluid = specs.get("fluid", "Clear Water")
        if current_fluid not in fluid_options:
            fluid_options.insert(0, current_fluid)
        fluid  = st.selectbox("Fluid / Service",
            fluid_options, index=fluid_options.index(current_fluid))
        temp   = st.number_input("Operating Temperature (°C)",
            value=float(specs.get("temp_c") or 30.0), min_value=0.0, step=5.0)
        dens_default = {
            "Clear Water": 1000, "Caustic Liquor (Alumina)": 1244,
            "Live Steam Condensate": 930, "Process Condensate": 990,
            "Slurry": 1300, "Dilute Sulphuric Acid": 1050,
            "Crude Oil": 870, "Seawater": 1025,
        }
        dens   = st.number_input("Fluid Density (kg/m³)",
            value=float(specs.get("density_kgm3") or dens_default.get(fluid, 1000)),
            min_value=500.0, step=10.0)
        stages = st.number_input("No. of Stages",
            value=int(specs.get("stages") or 1), min_value=1)
        model  = st.text_input("Pump Model (optional)",
            value=str(specs.get("model") or ""))

        st.markdown("</div>", unsafe_allow_html=True)

    # Tier 1 preview
    if st.session_state.db:
        db = st.session_state.db
        from engine import tier1_match
        test_specs = {
            "flow_m3h": flow if flow > 0 else None,
            "head_m":   head if head > 0 else None,
            "speed_rpm": speed,
            "model":    model,
            "fluid":    fluid,
        }
        pump_row, score, match_type = tier1_match(test_specs, db)
        if pump_row is not None and score >= 30:
            st.markdown(
                f'<div class="bom-card-green">'
                f'<b style="color:#3fb950;">✓ Database Match Found</b> &nbsp;'
                f'<span class="tier-badge tier1">TIER 1</span><br>'
                f'<span style="color:#8b949e; font-size:13px;">'
                f'Matched: <b style="color:#e6edf3;">{pump_row["Model"]}</b> | '
                f'Score: {score}/100 | Type: {match_type}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="bom-card">'
                f'<b style="color:#79c0ff;">ℹ Physics Calculation Mode</b> &nbsp;'
                f'<span class="tier-badge tier2">TIER 2</span><br>'
                f'<span style="color:#8b949e; font-size:13px;">'
                f'No exact database match — BOM will be calculated from engineering formulas.</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("← Back", use_container_width=True):
            st.session_state.page = "upload"
            st.rerun()
    with col_b:
        if st.button("Generate BOM →", type="primary", use_container_width=True):
            # Save updated specs
            updated = {
                "flow_m3h":      flow  if flow  > 0 else None,
                "head_m":        head  if head  > 0 else None,
                "speed_rpm":     speed if speed > 0 else None,
                "motor_kw":      motor if motor > 0 else None,
                "temp_c":        temp,
                "fluid":         fluid,
                "density_kgm3":  dens,
                "stages":        stages,
                "model":         model or None,
            }
            st.session_state.specs = updated
            st.session_state.page  = "generate"
            st.rerun()


# ─────────────────────────────────────────────────────────────────
# PAGE 3 — GENERATE
# ─────────────────────────────────────────────────────────────────
elif st.session_state.page == "generate":

    st.markdown("## ⚙️ Generating BOM...")

    specs = st.session_state.specs
    db    = st.session_state.db

    if not specs or not db:
        st.error("Missing specs or database.")
        st.stop()

    progress = st.progress(0)
    status   = st.empty()

    steps = [
        (10,  "Loading database..."),
        (25,  "Running Tier 1 — checking database for match..."),
        (50,  "Calculating specific speed and pump classification..."),
        (70,  "Selecting materials from compatibility matrix..."),
        (85,  "Building BOM from templates..."),
        (95,  "Estimating weights..."),
        (100, "Complete ✓"),
    ]

    for pct, msg in steps:
        progress.progress(pct)
        status.markdown(
            f'<p style="color:#8b949e; font-family: IBM Plex Mono; font-size:13px;">{msg}</p>',
            unsafe_allow_html=True,
        )
        time.sleep(0.3)

    try:
        bom_df, tier, match_info, calc_summary = generate_bom(specs, db)
        st.session_state.bom_df       = bom_df
        st.session_state.tier_used    = tier
        st.session_state.match_info   = match_info
        st.session_state.calc_summary = calc_summary

        progress.progress(100)
        st.success(f"✅ BOM generated — {len(bom_df)} components | "
                   f"{'Tier 1 (Database Match)' if tier == 'tier1' else 'Tier 2 (Physics Calculated)'}")
        time.sleep(0.5)
        st.session_state.page = "output"
        st.rerun()

    except Exception as e:
        st.error(f"Generation error: {e}")
        import traceback
        st.code(traceback.format_exc())


# ─────────────────────────────────────────────────────────────────
# PAGE 4 — BOM OUTPUT
# ─────────────────────────────────────────────────────────────────
elif st.session_state.page == "output":

    bom_df       = st.session_state.bom_df
    tier         = st.session_state.tier_used
    match_info   = st.session_state.match_info
    calc_summary = st.session_state.calc_summary
    specs        = st.session_state.specs

    if bom_df is None:
        st.warning("No BOM generated yet.")
        if st.button("← Generate BOM"):
            st.session_state.page = "generate"
            st.rerun()
        st.stop()

    # ── Header ────────────────────────────────────────────────────
    st.markdown("## 📋 Bill of Materials")

    # Tier badge + summary
    if tier == "tier1":
        st.markdown(
            f'<div class="bom-card-green">'
            f'<span class="tier-badge tier1">TIER 1 — DATABASE MATCH</span>&nbsp;&nbsp;'
            f'<span style="color:#3fb950; font-size:14px; font-weight:600;">'
            f'{match_info["model"]}</span><br>'
            f'<span style="color:#8b949e; font-size:12px; margin-top:6px; display:block;">'
            f'Match score: {match_info["score"]}/100 &nbsp;|&nbsp; '
            f'Type: {match_info["match_type"]} &nbsp;|&nbsp; '
            f'Pump ID: {match_info["pump_id"]}'
            f'</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="bom-card-blue">'
            f'<span class="tier-badge tier2">TIER 2 — PHYSICS CALCULATED</span>&nbsp;&nbsp;'
            f'<span style="color:#79c0ff; font-size:14px; font-weight:600;">'
            f'{calc_summary.get("pump_type","")}</span><br>'
            f'<span style="color:#8b949e; font-size:12px; margin-top:6px; display:block;">'
            f'Template: {calc_summary.get("template_used","")} &nbsp;|&nbsp; '
            f'Ns = {calc_summary.get("specific_speed_Ns","")} &nbsp;|&nbsp; '
            f'Seal: {calc_summary.get("seal_plan","")} &nbsp;|&nbsp; '
            f'Fluid matched: {calc_summary.get("fluid_matched","")}'
            f'</span></div>',
            unsafe_allow_html=True,
        )

    # ── Metric tiles ──────────────────────────────────────────────
    weights = calc_summary.get("weights", {}) if calc_summary else {}
    if tier == "tier1":
        wt_col = bom_df["Weight_kg"] if "Weight_kg" in bom_df.columns else pd.Series([])
        total_wt = pd.to_numeric(wt_col, errors="coerce").sum()
        weights_display = {"total_kg": total_wt if total_wt > 0 else "—"}
    else:
        weights_display = weights

    m_cols = st.columns(5)
    metrics = [
        ("Components",   len(bom_df)),
        ("Flow",         f"{specs.get('flow_m3h','—')} m³/h"),
        ("Head",         f"{specs.get('head_m','—')} m"),
        ("Motor",        f"{specs.get('motor_kw') or (calc_summary or {}).get('motor_kw_calc','—')} kW"),
        ("Total Weight", f"{weights_display.get('total_kg','—')} kg"),
    ]
    for col, (label, value) in zip(m_cols, metrics):
        col.markdown(
            f'<div class="metric-tile">'
            f'<div class="metric-value">{value}</div>'
            f'<div class="metric-label">{label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Tabs ──────────────────────────────────────────────────────
    tab1, tab2, tab3 = st.tabs(["📋 BOM Table", "📊 Summary", "🔧 Calculation Details"])

    with tab1:
        # Filter controls
        col_f1, col_f2, col_f3 = st.columns([2, 2, 1])
        with col_f1:
            cats = ["All"] + sorted(bom_df["Category"].dropna().unique().tolist()) \
                if "Category" in bom_df.columns else ["All"]
            cat_filter = st.selectbox("Filter by Category", cats)
        with col_f2:
            req_col = "Req_Type" if "Req_Type" in bom_df.columns else None
            if req_col:
                reqs = ["All"] + sorted(bom_df[req_col].dropna().unique().tolist())
                req_filter = st.selectbox("Filter by Required Type", reqs)
            else:
                req_filter = "All"
        with col_f3:
            st.markdown("<br>", unsafe_allow_html=True)
            show_full = st.checkbox("Show all columns", value=False)

        # Apply filters
        display_df = bom_df.copy()
        if cat_filter != "All" and "Category" in display_df.columns:
            display_df = display_df[display_df["Category"] == cat_filter]
        if req_filter != "All" and req_col:
            display_df = display_df[display_df[req_col] == req_filter]

        if not show_full:
            # Show key columns only
            key_cols = ["No", "Component_ID", "Category", "Description",
                        "MOC", "Material_Spec", "Qty", "Qty_Per_Unit",
                        "Weight_kg", "Vendor_Name", "Req_Type", "Source"]
            show_cols = [c for c in key_cols if c in display_df.columns]
            display_df = display_df[show_cols]

        st.dataframe(
            display_df,
            use_container_width=True,
            height=450,
            hide_index=True,
        )

        st.markdown(
            f'<p style="color:#8b949e; font-size:12px;">'
            f'Showing {len(display_df)} of {len(bom_df)} components</p>',
            unsafe_allow_html=True,
        )

    with tab2:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown('<div class="bom-card">', unsafe_allow_html=True)
            st.markdown('<div class="section-header">Input Specifications</div>', unsafe_allow_html=True)
            spec_display = {
                "Flow": f"{specs.get('flow_m3h','—')} m³/h",
                "Head": f"{specs.get('head_m','—')} m",
                "Speed": f"{specs.get('speed_rpm','—')} rpm",
                "Motor": f"{specs.get('motor_kw','—')} kW",
                "Temperature": f"{specs.get('temp_c','—')} °C",
                "Fluid": specs.get("fluid","—"),
                "Density": f"{specs.get('density_kgm3','—')} kg/m³",
                "Stages": specs.get("stages","—"),
            }
            for k, v in spec_display.items():
                st.markdown(
                    f'<div style="display:flex; justify-content:space-between; '
                    f'padding:6px 0; border-bottom:1px solid #30363d;">'
                    f'<span style="color:#8b949e; font-size:12px;">{k}</span>'
                    f'<span style="color:#e6edf3; font-family:IBM Plex Mono; '
                    f'font-size:12px;">{v}</span></div>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

        with c2:
            st.markdown('<div class="bom-card">', unsafe_allow_html=True)
            st.markdown('<div class="section-header">Weight Breakdown</div>', unsafe_allow_html=True)
            if weights:
                for k, v in weights.items():
                    st.markdown(
                        f'<div style="display:flex; justify-content:space-between; '
                        f'padding:6px 0; border-bottom:1px solid #30363d;">'
                        f'<span style="color:#8b949e; font-size:12px;">{k.replace("_"," ").title()}</span>'
                        f'<span style="color:#58a6ff; font-family:IBM Plex Mono; '
                        f'font-size:12px;">{v} kg</span></div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    '<p style="color:#8b949e; font-size:12px;">'
                    'Weight data from matched pump record.</p>',
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)

    with tab3:
        if tier == "tier2" and calc_summary:
            st.markdown('<div class="bom-card">', unsafe_allow_html=True)
            st.markdown('<div class="section-header">Engineering Calculation Trace</div>', unsafe_allow_html=True)

            calc_display = {
                "Specific Speed (Ns)":      calc_summary.get("specific_speed_Ns","—"),
                "Pump Classification":      calc_summary.get("pump_type","—"),
                "BOM Template Used":        calc_summary.get("template_used","—"),
                "Motor kW (calculated)":    f"{calc_summary.get('motor_kw_calc','—')} kW",
                "Pump Efficiency Assumed":  f"{calc_summary.get('eta_pump_assumed','—')}%",
                "Material Rule Applied":    calc_summary.get("material_rule","—"),
                "Fluid Matched To":         calc_summary.get("fluid_matched","—"),
                "Seal Plan Selected":       calc_summary.get("seal_plan","—"),
            }
            for k, v in calc_display.items():
                st.markdown(
                    f'<div style="display:flex; justify-content:space-between; '
                    f'padding:8px 0; border-bottom:1px solid #30363d;">'
                    f'<span style="color:#8b949e; font-size:12px;">{k}</span>'
                    f'<span style="color:#79c0ff; font-family:IBM Plex Mono; '
                    f'font-size:12px;">{v}</span></div>',
                    unsafe_allow_html=True,
                )

            st.markdown("</div>", unsafe_allow_html=True)

            if calc_summary.get("moc"):
                st.markdown('<div class="bom-card">', unsafe_allow_html=True)
                st.markdown('<div class="section-header">Material Selection Result</div>', unsafe_allow_html=True)
                moc = calc_summary["moc"]
                moc_items = {
                    "Casing":       moc.get("Casing_MOC","—"),
                    "Impeller":     moc.get("Impeller_MOC","—"),
                    "Shaft":        moc.get("Shaft_MOC","—"),
                    "Shaft Sleeve": moc.get("Shaft_Sleeve_MOC","—"),
                    "Wear Ring":    moc.get("Wear_Ring_MOC","—"),
                    "Seal Type":    moc.get("Seal_Type","—"),
                    "Seal Plan":    moc.get("Seal_Plan","—"),
                    "Fasteners":    moc.get("Fastener_MOC","—"),
                }
                for k, v in moc_items.items():
                    st.markdown(
                        f'<div style="display:flex; justify-content:space-between; '
                        f'padding:6px 0; border-bottom:1px solid #30363d;">'
                        f'<span style="color:#8b949e; font-size:12px;">{k}</span>'
                        f'<span style="color:#e6edf3; font-family:IBM Plex Mono; '
                        f'font-size:12px;">{v}</span></div>',
                        unsafe_allow_html=True,
                    )
                st.markdown("</div>", unsafe_allow_html=True)

        else:
            st.markdown(
                '<div class="bom-card-green">'
                '<b style="color:#3fb950;">Tier 1 — Direct database lookup.</b><br>'
                '<span style="color:#8b949e; font-size:13px;">'
                'BOM retrieved directly from Component_Library for matched pump. '
                'No physics calculation was required.</span></div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── Export ────────────────────────────────────────────────────
    col_d1, col_d2, col_d3 = st.columns([2, 2, 1])
    with col_d1:
        try:
            excel_buf = export_bom_excel(
                bom_df, specs, tier, match_info, calc_summary
            )
            filename = f"BOM_{specs.get('model','') or 'generated'}_{pd.Timestamp.now().strftime('%d%b%Y')}.xlsx"
            st.download_button(
                "⬇ Download BOM as Excel",
                data=excel_buf,
                file_name=filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"Export error: {e}")

    with col_d2:
        csv = bom_df.to_csv(index=False)
        st.download_button(
            "⬇ Download BOM as CSV",
            data=csv,
            file_name=f"BOM_{pd.Timestamp.now().strftime('%d%b%Y')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col_d3:
        if st.button("🔄 New BOM", use_container_width=True):
            for key in ["specs","bom_df","tier_used","match_info",
                        "calc_summary","extracted_text","pdf_name"]:
                st.session_state[key] = {} if key == "specs" else None
            st.session_state.page = "upload"
            st.rerun()


# ─────────────────────────────────────────────────────────────────
# PAGE 5 — DATABASE EXPLORER
# ─────────────────────────────────────────────────────────────────
elif st.session_state.page == "database":

    st.markdown("## 🗄️ Database Explorer")

    db = st.session_state.db
    if not db:
        st.error("Database not loaded.")
        st.stop()

    tab_p, tab_c, tab_m, tab_v = st.tabs([
        f"Pumps ({len(db['pumps'])})",
        f"Components ({len(db['comps'])})",
        f"Materials ({len(db['mats'])})",
        f"Vendors ({len(db['vendors'])})",
    ])

    with tab_p:
        st.markdown("### Pump Master List")
        search = st.text_input("Search pumps", placeholder="model, manufacturer, type...")
        pumps = db["pumps"].copy()
        if search:
            mask = pumps.astype(str).apply(
                lambda col: col.str.contains(search, case=False, na=False)
            ).any(axis=1)
            pumps = pumps[mask]
        st.dataframe(pumps, use_container_width=True, hide_index=True)

        # Pump type chart
        type_counts = db["pumps"]["Type"].value_counts()
        st.markdown("**Distribution by Type**")
        for ptype, cnt in type_counts.items():
            bar_w = int(cnt / max(type_counts) * 200)
            st.markdown(
                f'<div style="display:flex; align-items:center; margin:3px 0; gap:8px;">'
                f'<span style="color:#8b949e; font-size:12px; width:200px;">{ptype}</span>'
                f'<div style="background:#1f6feb; height:16px; width:{bar_w}px; border-radius:3px;"></div>'
                f'<span style="color:#58a6ff; font-family:IBM Plex Mono; font-size:12px;">{cnt}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    with tab_c:
        st.markdown("### Component Library")
        col_cs, col_cc = st.columns([2, 1])
        with col_cs:
            csearch = st.text_input("Search components", placeholder="category, material, pump...")
        with col_cc:
            pumps_list = ["All"] + db["pumps"]["Model"].tolist()
            pump_filter = st.selectbox("Filter by pump", pumps_list)

        comps = db["comps"].copy()
        if csearch:
            mask = comps.astype(str).apply(
                lambda col: col.str.contains(csearch, case=False, na=False)
            ).any(axis=1)
            comps = comps[mask]
        if pump_filter != "All":
            comps = comps[comps["Pump_Model_Compatibility"].str.contains(
                pump_filter, case=False, na=False
            )]

        st.dataframe(
            comps[["Component_ID","Component_Name","Category","Material_Spec",
                   "Weight_kg","Vendor_Name","Pump_Model_Compatibility"]],
            use_container_width=True,
            hide_index=True,
            height=400,
        )

    with tab_m:
        st.markdown("### Material Database")
        st.dataframe(db["mats"], use_container_width=True, hide_index=True)

    with tab_v:
        st.markdown("### Vendor Database")
        st.dataframe(db["vendors"], use_container_width=True, hide_index=True)
