"""
AGENTIC BOM — Streamlit UI
═══════════════════════════════════════════════════════════════════
Two phases:
  • RUN phase  → dark "command-center" terminal while the agent works (live log
                 + live token/₹ counter)
  • DATA phase → once the BOM is ready, the page transforms to a clean LIGHT,
                 SAP-style costing layout where the buyer fills raw-material
                 rates, sets labour, and picks supplier — proper cards, not a grid.

Backend: claude_engine.run_agent(price=False) + price_manual()
History:  every generated BOM is saved to bom_history.db (SQLite) and can be
          reloaded from the sidebar.
"""

import os
import html
import hashlib
import json as _json
import sqlite3 as _sql
from datetime import datetime as _dt
from pathlib import Path as _Path
import pandas as pd
import streamlit as st
import pydeck as pdk

from claude_engine import (
    extract_pdf_text, run_agent, bom_to_dataframe, export_excel, _get_key,
    price_manual, SUPPLIER_FACTORS,
    supplier_should_cost, SUPPLIER_BOOK_DEFAULTS,
)
import geo_cost
try:
    from pricing import _rate_for_material
except Exception:
    def _rate_for_material(m): return (0, "")


# ═══════════════════════════════════════════════════════════════════
# BOM HISTORY  — SQLite, one file on disk (bom_history.db next to app.py)
# Every generated BOM is saved here so it can be reloaded later from the
# sidebar. On Streamlit Cloud the container filesystem resets on redeploy;
# set env var BOM_HISTORY_DB to a persistent volume path to keep history.
# ═══════════════════════════════════════════════════════════════════
_HIST_DB = _Path(os.environ.get("BOM_HISTORY_DB",
                                _Path(__file__).parent / "bom_history.db"))


def _hist_conn():
    c = _sql.connect(_HIST_DB, check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS bom_runs(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts            TEXT NOT NULL,
        product       TEXT,
        source_pdf    TEXT,
        n_components  INTEGER,
        content_hash  TEXT,
        payload       TEXT NOT NULL)""")
    # migrate older DBs that predate the content_hash column
    try:
        c.execute("ALTER TABLE bom_runs ADD COLUMN content_hash TEXT")
    except Exception:
        pass
    return c


def datasheet_hash(pdf_text: str) -> str:
    """Stable fingerprint of a datasheet's content (whitespace-normalised),
    so the same datasheet always maps to the same key even if re-saved."""
    norm = " ".join((pdf_text or "").split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def history_find_by_hash(content_hash: str):
    """Return the id of the most recent BOM generated for this exact datasheet,
    or None if we've never costed it before."""
    if not content_hash:
        return None
    try:
        with _hist_conn() as c:
            row = c.execute("SELECT id FROM bom_runs WHERE content_hash=? "
                            "ORDER BY id DESC LIMIT 1", (content_hash,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def history_save(result: dict, source_pdf: str = "", content_hash: str = ""):
    """Persist a completed BOM run (the dict returned by run_agent)."""
    if not result:
        return
    bom = result.get("bom") or []
    product = str(result.get("equipment_type") or "Unnamed")
    try:
        with _hist_conn() as c:
            c.execute("INSERT INTO bom_runs(ts,product,source_pdf,n_components,content_hash,payload) "
                      "VALUES(?,?,?,?,?,?)",
                      (_dt.utcnow().isoformat(timespec="seconds"),
                       product, source_pdf, len(bom), content_hash,
                       _json.dumps(result, default=str)))
    except Exception as e:
        st.warning(f"Could not save to history: {e}")


def history_list(limit: int = 50):
    try:
        with _hist_conn() as c:
            return c.execute(
                "SELECT id, ts, product, source_pdf, n_components "
                "FROM bom_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except Exception:
        return []


def history_load(row_id: int):
    with _hist_conn() as c:
        row = c.execute("SELECT payload FROM bom_runs WHERE id=?", (row_id,)).fetchone()
    return _json.loads(row[0]) if row else None


def history_delete(row_id: int):
    with _hist_conn() as c:
        c.execute("DELETE FROM bom_runs WHERE id=?", (row_id,))


@st.cache_data(show_spinner=False)
def _geo(place):
    return geo_cost.geocode(place)


@st.cache_data(show_spinner=False)
def _road(a, b, c, d):
    return geo_cost.road_distance_km(a, b, c, d)

st.set_page_config(page_title="Agentic BOM", page_icon="◈", layout="wide",
                   initial_sidebar_state="collapsed")

# ═══════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════
ss = st.session_state
ss.setdefault("result", None)
ss.setdefault("agent_lines", [])
ss.setdefault("rates", {})        # {component_id: raw ₹/kg (mfd) or ₹/unit (bought-out)}
ss.setdefault("pcts", {})         # {component_id: manufacturing % for THAT row}
ss.setdefault("supplier", "Indian")
ss.setdefault("mfg_pct", 80)      # default COGS % (conversion+consumables) seeded into new rows
ss.setdefault("overhead", 0)
ss.setdefault("supplier_loc", "")
ss.setdefault("site_loc", "")
ss.setdefault("freight", None)    # {km, mode, cost, a:{lat,lon}, b:{lat,lon}}
# supplier-book (margin stack on top of COGS) — benchmark defaults, buyer-editable
ss.setdefault("sga_pct", SUPPLIER_BOOK_DEFAULTS["sga_pct"])
ss.setdefault("depr_pct", SUPPLIER_BOOK_DEFAULTS["depr_pct"])
ss.setdefault("amort_inr", SUPPLIER_BOOK_DEFAULTS["amort_inr"])
ss.setdefault("ebitda_pct", SUPPLIER_BOOK_DEFAULTS["ebitda_pct"])
ss.setdefault("package_cbm", 0.0)     # total shipment volume (m³) for freight
ss.setdefault("freight_rate", 3.5)    # ₹ per chargeable-tonne per km

LIGHT = ss.result is not None   # phase flag: results exist → show data layout

# ═══════════════════════════════════════════════════════════════════
# THEME — golden dark "command center" while the agent runs, light
# SAP-style once results exist. RULE: every override pairs background
# AND text color, so nothing can dissolve into the page regardless of
# the base theme in .streamlit/config.toml.
# ═══════════════════════════════════════════════════════════════════
if LIGHT:
    BG, FG, MUT, CARD, BORDER, PRIMARY = "#f4f6f9", "#1a1f29", "#5b6472", "#ffffff", "#e3e8ef", "#0a6ed1"
else:
    BG, FG, MUT, CARD, BORDER, PRIMARY = "#0a0a0f", "#e8e4db", "#9a96a8", "#12121a", "#26262f", "#e8a020"

# Phase-specific widget overrides. Streamlit's base theme is LIGHT (dark text),
# so in the dark phase every text element must be explicitly forced light —
# and every forced-light element sits on an explicitly dark surface.
if LIGHT:
    PHASE_CSS = f"""
/* theme-proof: force paired colors even if config.toml theme is dark */
[data-testid="stMain"] h1, [data-testid="stMain"] h2, [data-testid="stMain"] h3,
[data-testid="stMain"] h4, [data-testid="stMain"] h5 {{ color:{FG} !important; }}
[data-testid="stMain"] p, [data-testid="stMain"] span, [data-testid="stMain"] label,
[data-testid="stMain"] li, [data-testid="stMain"] [data-testid="stMarkdownContainer"] {{ color:{FG}; }}
[data-testid="stWidgetLabel"] p {{ color:{FG} !important; }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p,
[data-testid="stCaptionContainer"] span {{ color:{MUT} !important; }}
.stTabs [data-baseweb="tab"] p {{ color:{MUT} !important; }}
.stTabs [aria-selected="true"] p {{ color:{PRIMARY} !important; }}
.stTabs [data-baseweb="tab-highlight"] {{ background:{PRIMARY} !important; }}
.stNumberInput input, .stTextInput input,
[data-baseweb="input"], [data-baseweb="input"] input {{
    background:#ffffff !important; color:{FG} !important; }}
.stNumberInput button {{ background:#ffffff !important; color:{FG} !important; }}
[data-baseweb="select"] > div {{ background:#ffffff !important; color:{FG} !important; }}
[data-testid="stExpander"] summary {{ background:{CARD}; color:{FG} !important; }}
[data-testid="stExpander"] summary p {{ color:{FG} !important; }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-color:{BORDER} !important;
    background:{CARD}; }}
[data-testid="stSidebar"] {{ background:#ffffff; color:{FG}; }}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span {{ color:{FG}; }}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] * {{ color:{MUT} !important; }}
"""
else:
    PHASE_CSS = f"""
[data-testid="stMain"] p, [data-testid="stMain"] span, [data-testid="stMain"] label,
[data-testid="stMain"] li, [data-testid="stMain"] [data-testid="stMarkdownContainer"] {{ color:{FG}; }}
[data-testid="stMain"] [data-testid="stCaptionContainer"],
[data-testid="stMain"] [data-testid="stCaptionContainer"] p,
[data-testid="stMain"] small {{ color:{MUT} !important; }}
[data-testid="stFileUploaderDropzone"] {{
    background:{CARD} !important; border:1px dashed rgba(232,160,32,0.45) !important; }}
[data-testid="stFileUploaderDropzone"] span, [data-testid="stFileUploaderDropzone"] small,
[data-testid="stFileUploaderDropzone"] div,
[data-testid="stFileUploaderDropzoneInstructions"] * {{ color:{FG} !important; }}
[data-testid="stFileUploaderDropzone"] svg {{ fill:{MUT} !important; }}
[data-testid="stFileUploaderDropzone"] button {{
    background:{PRIMARY} !important; color:#0a0a0f !important; border:none !important; }}
[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] * {{ color:{FG} !important; }}
[data-testid="stSidebar"] {{ background:#0e0e14; border-right:1px solid {BORDER}; }}
[data-testid="stSidebar"] * {{ color:{FG}; }}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ color:{MUT} !important; }}
[data-testid="stAlert"] * {{ color:#1a1f29; }}
"""

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Syne:wght@700;800&family=Inter:wght@400;500;600;700&display=swap');
.stApp {{ background:{BG}; color:{FG}; }}
#MainMenu, footer, header {{ visibility:hidden; }}
[data-testid="collapsedControl"] {{ display:none; }}
.block-container {{ max-width:1200px; padding-top:2rem; font-family:'Inter',sans-serif; }}
h1,h2,h3,h4 {{ font-family:'Syne',sans-serif; color:{FG}; }}

.stButton > button {{
    background:{PRIMARY} !important; color:{'#fff' if LIGHT else '#0a0a0f'} !important;
    border:none !important; border-radius:6px !important; font-weight:700 !important;
    font-family:'Inter',sans-serif !important; letter-spacing:0.03em !important; padding:10px 26px !important;
}}
.kpi {{ background:{CARD}; border:1px solid {BORDER}; border-radius:10px; padding:14px 16px; }}
.kpi .v {{ font-family:'Syne',sans-serif; font-size:24px; font-weight:800; color:{PRIMARY}; }}
.kpi .l {{ font-size:10px; letter-spacing:0.12em; text-transform:uppercase; color:{MUT}; margin-top:2px; }}
[data-testid="stDataFrame"] {{ font-family:'IBM Plex Mono',monospace; font-size:12px; }}
hr {{ border-color:{BORDER} !important; }}
{PHASE_CSS}
</style>
""", unsafe_allow_html=True)


def terminal(lines):
    body = "\n".join(html.escape(str(l)) for l in lines) if lines else "Waiting for input..."
    return (f'<div style="background:#06060a;border:1px solid rgba(232,160,32,0.25);'
            f'border-radius:8px;padding:20px 24px;font-family:IBM Plex Mono,monospace;'
            f'font-size:12px;line-height:1.9;color:#e8a020;min-height:160px;max-height:340px;'
            f'overflow-y:auto;white-space:pre-wrap;">{body}</div>')


# ═══════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("### ◈ AGENTIC BOM")
    st.caption("v3 · Zetwerk CPT")
    st.divider()
    st.markdown("**Providers**")
    for nm, k in [("Claude", "ANTHROPIC_API_KEY"), ("Gemini", "GEMINI_API_KEY"),
                  ("Groq", "GROQ_API_KEY"), ("Cerebras", "CEREBRAS_API_KEY")]:
        st.caption(f"{'🟢' if _get_key(k) else '⚪'} {nm}")
    model = _get_key("CLAUDE_MODEL") or "auto"
    st.caption(f"Model: `{model}`")

    if ss.result:
        st.divider()
        if st.button("↺ New BOM"):
            ss.result = None; ss.agent_lines = []; ss.rates = {}; ss.pcts = {}; st.rerun()

    # ── History: every generated BOM, click to reload ───────────────
    st.divider()
    st.markdown("**History**")
    _hist_rows = history_list(limit=50)
    st.caption(f"{len(_hist_rows)} saved" if _hist_rows else "No saved BOMs yet")
    for _rid, _ts, _prod, _pdf, _n in _hist_rows:
        try:
            _when = _dt.fromisoformat(_ts).strftime("%d %b · %H:%M")
        except Exception:
            _when = str(_ts)
        _label = f"{str(_prod)[:24]}  ·  {_n} parts\n{_when}"
        _hc1, _hc2 = st.columns([5, 1])
        if _hc1.button(_label, key=f"hist_load_{_rid}", use_container_width=True):
            _r = history_load(_rid)
            if _r:
                ss.result = _r
                ss.rates = {}; ss.pcts = {}; ss.agent_lines = []
                st.rerun()
        if _hc2.button("×", key=f"hist_del_{_rid}", help="Delete this saved BOM"):
            history_delete(_rid)
            st.rerun()

# ═══════════════════════════════════════════════════════════════════
# HEADER  — ignition animation plays on first render of each session
# (i.e. every page load / refresh), then the diamond sits still on
# subsequent reruns so button clicks don't retrigger the glow.
# ═══════════════════════════════════════════════════════════════════
_first_load = "_logo_played" not in ss
ss["_logo_played"] = True
ig_class = "bom-ig" if _first_load else "bom-still"
tt_class = "bom-tt" if _first_load else ""
st.markdown(f"""
<style>
@keyframes bom-ignite {{
  0%   {{ opacity:0; transform:scale(.45) rotate(-8deg);
         text-shadow:0 0 0 rgba(232,160,32,0); }}
  55%  {{ opacity:1; transform:scale(1.08) rotate(0);
         text-shadow:0 0 30px rgba(232,160,32,.9),0 0 80px rgba(232,160,32,.55); }}
  100% {{ opacity:1; transform:scale(1) rotate(0);
         text-shadow:0 0 14px rgba(232,160,32,.55),0 0 42px rgba(232,160,32,.28); }}
}}
@keyframes bom-title {{
  from {{ opacity:0; transform:translateY(10px); }}
  to   {{ opacity:1; transform:none; }}
}}
.bom-ig {{ animation:bom-ignite 1.8s cubic-bezier(.16,1,.3,1) both; }}
.bom-tt {{ animation:bom-title  1.2s ease .6s both; }}
.bom-still {{ opacity:1;
  text-shadow:0 0 14px rgba(232,160,32,.55),0 0 42px rgba(232,160,32,.28); }}
</style>
<div style="padding:6px 0 18px 0;display:flex;align-items:center;gap:22px;">
  <div class="{ig_class}" style="font-family:'Syne',sans-serif;font-size:64px;line-height:1;color:#e8a020;
              flex-shrink:0;">◈</div>
  <div class="{tt_class}">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:0.3em;
                color:{PRIMARY};text-transform:uppercase;">Zetwerk · Central Procurement · Category 2</div>
    <div style="font-family:'Syne',sans-serif;font-size:46px;font-weight:800;line-height:1;color:{FG};">
      AGENTIC&nbsp;BOM</div>
    <div style="font-size:14px;color:{MUT};margin-top:8px;">
      Drop any engineered-product datasheet → complete BOM → buyer-driven should-cost.</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════
# RUN PHASE  (only show uploader/terminal until a result exists)
# ═══════════════════════════════════════════════════════════════════
if not LIGHT:
    uploaded = st.file_uploader("Datasheet PDF", type=["pdf"], label_visibility="collapsed")
    st.caption("PDF datasheets, GA drawings, spec sheets — any engineered product")
    rc1, rc2 = st.columns([1, 2])
    run = rc1.button("◈ RUN AGENT", disabled=uploaded is None)
    force_fresh = rc2.checkbox(
        "Re-generate even if this datasheet was costed before",
        value=False,
        help="Off (default): the same datasheet always returns the same saved BOM — "
             "consistent and free. On: run the agent again from scratch.")

    term = st.empty()
    if ss.agent_lines:
        term.markdown(terminal(ss.agent_lines), unsafe_allow_html=True)

    def on_progress(line, agent_log):
        ss.agent_lines.append(line)
        term.markdown(terminal(ss.agent_lines), unsafe_allow_html=True)

    if run and uploaded:
        pdf_text, err = extract_pdf_text(uploaded.read())
        if err or len(pdf_text.strip()) < 100:
            st.error(f"Could not read PDF. {err or 'Try a text-based PDF.'}"); st.stop()

        # DETERMINISTIC REUSE: identical datasheet → identical BOM (unless forced)
        chash = datasheet_hash(pdf_text)
        cached_id = None if force_fresh else history_find_by_hash(chash)
        if cached_id:
            cached = history_load(cached_id)
            if cached:
                ss.result = cached
                ss.rates = {}; ss.pcts = {}; ss.agent_lines = []
                ss["_reused_from"] = uploaded.name
                st.rerun()

        ss.agent_lines = ["[00:00] ◈ AGENT      Starting..."]
        term.markdown(terminal(ss.agent_lines), unsafe_allow_html=True)
        try:
            ss.result = run_agent(pdf_text, progress_callback=on_progress, price=False)
            ss.rates = {}
            ss.pop("_reused_from", None)
            history_save(ss.result, source_pdf=uploaded.name, content_hash=chash)
            st.rerun()
        except Exception as e:
            import traceback
            st.error(f"Agent error: {e}"); st.code(traceback.format_exc())

# ═══════════════════════════════════════════════════════════════════
# DATA PHASE  (light SAP-style costing layout)
# ═══════════════════════════════════════════════════════════════════
else:
    result = ss.result
    bom = result.get("bom", [])
    schema = result.get("schema", [])
    usage = result.get("usage", {})
    conf_pct = int(round(result.get("confidence", 0) * 100))

    if ss.get("_reused_from"):
        st.info(f"↺ Reused the saved BOM for **{ss['_reused_from']}** — this datasheet "
                f"was costed before, so you get the same result (no new AI run). "
                f"Tick **Re-generate** on the upload screen to force a fresh run.")

    # initialise rate + mfg-% stores, then SYNC from widget state BEFORE pricing
    # (widget session values update at rerun start; syncing here keeps every
    # displayed cost live with the latest keystroke — no one-edit lag)
    for c in bom:
        cid = str(c.get("id"))
        if cid not in ss.rates:
            mat = c.get("material", "")
            default = geo_cost.suggested_rate(mat)
            if not default:
                default = float(_rate_for_material(mat)[0] or 0)
            ss.rates[cid] = float(default)
        if cid not in ss.pcts:
            ss.pcts[cid] = float(ss.mfg_pct)
        if f"rate_{cid}" in st.session_state:
            ss.rates[cid] = float(st.session_state[f"rate_{cid}"] or 0)
        if f"pct_{cid}" in st.session_state:
            ss.pcts[cid] = float(st.session_state[f"pct_{cid}"] or 0)

    # ── controls
    cc1, cc2, cc3 = st.columns([1, 1.2, 0.9])
    ss.supplier = cc1.radio("Supplier", list(SUPPLIER_FACTORS.keys()),
                            index=list(SUPPLIER_FACTORS).index(ss.supplier), horizontal=True)
    ss.mfg_pct = cc2.slider("Default manufacturing % (labour + machining)", 0, 200,
                            ss.mfg_pct, 5,
                            help="Starting % for new rows. Each row has its own % "
                                 "you can edit. Not applied to bought-out items.")
    cc3.markdown("<div style='height:26px'></div>", unsafe_allow_html=True)
    if cc3.button("Apply % to all rows"):
        for c in bom:
            cid = str(c.get("id"))
            ss.pcts[cid] = float(ss.mfg_pct)
            st.session_state[f"pct_{cid}"] = float(ss.mfg_pct)

    # ── price with current inputs
    priced, sc = price_manual([dict(c) for c in bom], ss.rates, pct_map=ss.pcts,
                              mfg_pct=ss.mfg_pct, supplier=ss.supplier)
    ss.result["bom"] = priced
    ss.result["should_cost"] = sc

    # ── KPI row (incl. live token/cost)
    cols = st.columns(6)
    kpis = [
        (result.get("equipment_type", "—")[:22], "Equipment"),
        (len(bom), "Components"),
        (len(schema), "Sub-assemblies"),
        (f"₹{sc.get('total_ex_gst',0):,}", "Should-Cost ex-GST"),
        (f"{usage.get('total_tokens',0):,}", "Tokens used"),
        (f"₹{usage.get('est_cost_inr',0)}", "AI cost"),
    ]
    for col, (v, l) in zip(cols, kpis):
        col.markdown(f'<div class="kpi"><div class="v">{v}</div><div class="l">{l}</div></div>',
                     unsafe_allow_html=True)

    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    tab_cost, tab_bom, tab_log, tab_exp = st.tabs(
        ["💰 COSTING", "📋 BOM", "🧠 AGENT LOG", "⬇ EXPORT"])

    # ── COSTING: SAP-style form, grouped by sub-assembly ──────────
    with tab_cost:
        st.markdown(f"**Manufactured**: enter **raw-material ₹/kg** and the row's "
                    f"**COGS %** → Raw = kg × rate; Conversion (labour + machining + "
                    f"consumables) = COGS % × Raw; **Component COGS = Raw + Conversion**. "
                    f"**Bought-out** 🛒: enter **purchase ₹/unit**. "
                    f"Supplier **{ss.supplier}** (×{SUPPLIER_FACTORS[ss.supplier]}). "
                    f"COGS is ₹0 until you enter a value. The supplier's margin "
                    f"(SG&A · depreciation · amortization · EBITDA) is added below.")
        by_sub = {}
        for c in priced:
            by_sub.setdefault((c.get("sub_assembly_id"), c.get("sub_assembly_name")), []).append(c)

        COLW = [2.1, 1.15, 0.55, 0.45, 1.1, 0.75, 1.0, 1.0, 1.1]
        for s in schema:
            items = by_sub.get((s["id"], s["name"]), [])
            if not items:
                continue
            sub_total = sum(int(x.get("total_cost_inr", 0)) for x in items)
            with st.container(border=True):
                st.markdown(f"#### {s['id']}. {s['name']}  ·  ₹{sub_total:,}")
                h = st.columns(COLW)
                for col, t in zip(h, ["Component", "Material", "kg", "Qty",
                                      "Rate ₹/kg • ₹/unit", "COGS %",
                                      "Raw ₹", "Conversion ₹", "COGS ₹"]):
                    col.caption(t)
                for c in items:
                    cid = str(c.get("id"))
                    bo = str(c.get("component_type", c.get("type", ""))).lower() == "bought_out"
                    # seed widget state once, then instantiate without value=
                    if f"rate_{cid}" not in st.session_state:
                        st.session_state[f"rate_{cid}"] = float(ss.rates.get(cid, 0) or 0)
                    if not bo and f"pct_{cid}" not in st.session_state:
                        st.session_state[f"pct_{cid}"] = float(ss.pcts.get(cid, ss.mfg_pct))
                    r = st.columns(COLW)
                    r[0].markdown(f"**{html.escape(str(c.get('description','')))}**"
                                  + ("  🛒" if bo else ""))
                    r[1].caption(html.escape(str(c.get("material", "") or "—")))
                    r[2].markdown(str(c.get("weight_kg", 0)))
                    r[3].markdown(str(c.get("qty", "1")))
                    ss.rates[cid] = r[4].number_input(
                        ("purchase ₹/unit" if bo else "raw ₹/kg"),
                        min_value=0.0, step=(1000.0 if bo else 10.0),
                        key=f"rate_{cid}", label_visibility="collapsed")
                    if bo:
                        r[5].markdown("—")
                    else:
                        ss.pcts[cid] = r[5].number_input(
                            "cogs %", min_value=0.0, max_value=500.0, step=5.0,
                            key=f"pct_{cid}", label_visibility="collapsed")
                    r[6].markdown(f"₹{int(c.get('raw_material_inr', 0)):,}")
                    r[7].markdown("—" if bo else f"₹{int(c.get('machining_inr', 0)):,}")
                    r[8].markdown(f"**₹{int(c.get('total_cost_inr', 0)):,}**")

        # save entered rates to the persistent database
        if st.button("💾 Save rates to database"):
            n = 0
            for c in priced:
                rt = float(ss.rates.get(str(c.get("id")), 0) or 0)
                if rt > 0:
                    geo_cost.record_rate(c.get("material", ""), rt, c.get("description", ""))
                    n += 1
            st.success(f"Saved {n} raw-material rates to the database.")

        cogs_ex = int(sc.get("total_ex_gst", 0))    # Σ component COGS (raw + conversion)
        total_wt_kg = sum(float(c.get("total_kg", 0) or 0) for c in priced)

        # ── SUPPLIER'S BOOK — margin stack added on top of COGS ────
        st.markdown("### 📗 Supplier's book — margin on top of COGS")
        st.caption("What the supplier adds above COGS to cover its P&L. These are "
                   "benchmark defaults for Indian fabrication — override with the "
                   "supplier's real figures (MCA / Tofler filing, annual report) when "
                   "you have them. All values are estimates until you do.")
        sb1, sb2, sb3, sb4 = st.columns(4)
        ss.sga_pct = sb1.number_input("SG&A % of COGS", min_value=0.0, max_value=100.0,
                                      value=float(ss.sga_pct), step=1.0,
                                      help="Selling, general & admin + fixed-cost absorption")
        ss.depr_pct = sb2.number_input("Depreciation % of COGS", min_value=0.0, max_value=100.0,
                                       value=float(ss.depr_pct), step=0.5,
                                       help="Plant & machinery depreciation")
        ss.amort_inr = sb3.number_input("Amortization ₹", min_value=0, step=1000,
                                        value=int(ss.amort_inr),
                                        help="Tooling / NRE ÷ order quantity (absolute ₹)")
        ss.ebitda_pct = sb4.number_input("EBITDA %", min_value=0.0, max_value=100.0,
                                         value=float(ss.ebitda_pct), step=1.0,
                                         help="Supplier operating margin — what they keep")

        # ── FREIGHT — chargeable weight × ₹/tonne-km × distance ────
        st.markdown("### 🚚 Freight — weight × distance")
        st.caption("Freight = chargeable weight × ₹/tonne·km × road distance. "
                   "Chargeable weight = max(actual weight, volume × 250 kg/m³).")
        fc1, fc2 = st.columns(2)
        ss.supplier_loc = fc1.text_input("Supplier location", ss.supplier_loc,
                                         placeholder="e.g. KSB Pimpri, Pune")
        ss.site_loc = fc2.text_input("Delivery / site location", ss.site_loc,
                                     placeholder="e.g. Hindustan Zinc, Udaipur")
        fr1, fr2 = st.columns(2)
        ss.package_cbm = fr1.number_input("Total package volume (m³ / CBM)", min_value=0.0,
                                          value=float(ss.package_cbm), step=0.5,
                                          help="Total crate / shipment volume. Leave 0 to bill "
                                               "on actual weight only.")
        ss.freight_rate = fr2.number_input("Freight rate ₹ per tonne-km", min_value=0.0,
                                           value=float(ss.freight_rate), step=0.5,
                                           help="Default ₹3.5/tonne·km for Indian road PTL/FTL.")
        if st.button("📍 Calculate distance"):
            a = _geo(ss.supplier_loc); b = _geo(ss.site_loc)
            if not a or not b:
                st.error("Could not locate one of the addresses. Be more specific (add city/state).")
                ss.freight = None
            else:
                km, mode = _road(a["lat"], a["lon"], b["lat"], b["lon"])
                ss.freight = {"km": km, "mode": mode, "a": a, "b": b}

        freight_cost = 0
        freight_detail = {}
        if ss.freight:
            km = ss.freight["km"]
            freight_cost, freight_detail = geo_cost.freight_cost(
                total_wt_kg, km, volume_cbm=ss.package_cbm,
                rate_per_tonne_km=ss.freight_rate)
            badge = "road" if ss.freight["mode"] == "road" else "estimated (×1.3)"
            vol_note = (f" · volumetric {freight_detail['volumetric_kg']:,.0f} kg"
                        if ss.package_cbm else "")
            st.markdown(
                f"**Distance {km:,} km** ({badge}) · actual "
                f"{freight_detail['actual_kg']:,.0f} kg{vol_note} → "
                f"**chargeable {freight_detail['chargeable_kg']:,.0f} kg**  \n"
                f"**Freight = {freight_detail['formula']} = ₹{freight_cost:,}**")
            a, b = ss.freight["a"], ss.freight["b"]
            mid = [(a["lat"] + b["lat"]) / 2, (a["lon"] + b["lon"]) / 2]
            pts = [{"name": "Supplier", "lat": a["lat"], "lon": a["lon"]},
                   {"name": "Site", "lat": b["lat"], "lon": b["lon"]}]
            line = [{"from": [a["lon"], a["lat"]], "to": [b["lon"], b["lat"]]}]
            st.pydeck_chart(pdk.Deck(
                map_style=None,
                initial_view_state=pdk.ViewState(latitude=mid[0], longitude=mid[1],
                                                 zoom=5, pitch=0),
                layers=[
                    pdk.Layer("LineLayer", line, get_source_position="from",
                              get_target_position="to", get_width=4,
                              get_color=[10, 110, 209]),
                    pdk.Layer("ScatterplotLayer", pts, get_position="[lon, lat]",
                              get_radius=18000, get_fill_color=[232, 160, 32], pickable=True),
                ],
                tooltip={"text": "{name}"}))

        # ── OVERHEAD (optional) ────────────────────────────────────
        st.markdown("### 🧾 Overhead (optional)")
        ss.overhead = st.number_input("Overhead charges (₹)", min_value=0, step=1000,
                                      value=int(ss.overhead),
                                      help="Any extra charges — testing, packing, documentation, etc.")

        # ── SHOULD-COST LADDER  (COGS → supplier margin → landed) ──
        ladder = supplier_should_cost(
            cogs_ex, sga_pct=ss.sga_pct, depr_pct=ss.depr_pct,
            amort_inr=ss.amort_inr, ebitda_pct=ss.ebitda_pct,
            freight_inr=int(freight_cost), overhead_inr=int(ss.overhead), gst=0.18)

        st.markdown("---")
        gcols = st.columns(5)
        milestones = [
            (ladder["cogs"], "Total COGS"),
            (ladder["ex_works_price"], "Ex-Works (should-cost)"),
            (ladder["freight"], "Freight"),
            (ladder["gst"], f"GST {ladder['gst_pct']}%"),
            (ladder["landed_price"], "Landed Price"),
        ]
        for col, (v, l) in zip(gcols, milestones):
            col.markdown(f'<div class="kpi"><div class="v">₹{v:,}</div>'
                         f'<div class="l">{l}</div></div>', unsafe_allow_html=True)

        ladder_rows = [
            ("Total COGS  (raw material + conversion)", ladder["cogs"]),
            (f"+ SG&A  ({ladder['sga_pct']:.0f}% of COGS)", ladder["sga"]),
            (f"+ Depreciation  ({ladder['depr_pct']:.0f}% of COGS)", ladder["depreciation"]),
            ("+ Amortization  (tooling / NRE)", ladder["amortization"]),
            ("= Supplier total cost", ladder["supplier_total_cost"]),
            (f"+ EBITDA  ({ladder['ebitda_pct']:.0f}% margin)", ladder["ebitda"]),
            ("= Ex-Works price  (reconstructed should-cost)", ladder["ex_works_price"]),
            ("+ Freight", ladder["freight"]),
            ("+ Overhead", ladder["overhead"]),
            ("= Total ex-GST", ladder["total_ex_gst"]),
            (f"+ GST  ({ladder['gst_pct']}%)", ladder["gst"]),
            ("= LANDED PRICE", ladder["landed_price"]),
        ]
        with st.expander("🧮 Full should-cost ladder (COGS → supplier margin → landed)", expanded=True):
            st.dataframe(
                pd.DataFrame([{"Line item": k, "₹": f"{v:,}"} for k, v in ladder_rows]),
                width='stretch', hide_index=True)
            st.caption("Ex-Works price is the reconstructed should-cost — compare it "
                       "against the supplier's actual quote; the gap is your negotiation room.")

        # store the full ladder for export
        ss.result["grand_total"] = {
            "cogs": ladder["cogs"],
            "supplier_total_cost": ladder["supplier_total_cost"],
            "ex_works_price": ladder["ex_works_price"],
            "freight": ladder["freight"], "overhead": ladder["overhead"],
            "total_ex_gst": ladder["total_ex_gst"], "gst": ladder["gst"],
            "landed_price": ladder["landed_price"],
            "book": {"sga_pct": ss.sga_pct, "depr_pct": ss.depr_pct,
                     "amort_inr": int(ss.amort_inr), "ebitda_pct": ss.ebitda_pct},
            "freight_detail": freight_detail,
        }

        # ── reference panels ───────────────────────────────────────
        with st.expander("📚 Rate database (saved buyer rates)"):
            rows = geo_cost.rate_db_table()
            if rows:
                st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
                st.caption("Defaults for new BOMs are auto-suggested from this history. "
                           "Note: resets on app redeploy unless RATE_DB_PATH is a persistent volume.")
            else:
                st.caption("No rates saved yet — fill rates and click 'Save rates to database'.")

    # ── BOM table ─────────────────────────────────────────────────
    with tab_bom:
        df = bom_to_dataframe(priced)
        opts = ["All"] + [f"{s['id']}. {s['name']}" for s in schema]
        pick = st.selectbox("Filter sub-assembly", opts)
        view = df if pick == "All" else df[df["Sub_Assembly"] == pick]
        st.dataframe(view, width='stretch', hide_index=True)

    # ── Agent log ─────────────────────────────────────────────────
    with tab_log:
        log = result.get("agent_log", [])
        lines = [f"{e.get('t','')} {'✓' if e.get('result') else '◈'} "
                 f"{str(e.get('step','')):<10} {e.get('result') or e.get('action','')}" for e in log]
        st.markdown(terminal(lines), unsafe_allow_html=True)
        st.caption(f"{usage.get('calls',0)} LLM calls · {usage.get('total_tokens',0):,} tokens · "
                   f"~₹{usage.get('est_cost_inr',0)} · iterations {result.get('iterations',0)}")

    # ── Export ────────────────────────────────────────────────────
    with tab_exp:
        try:
            xls = export_excel(ss.result)
            fname = str(result.get("equipment_type", "BOM")).replace(" ", "_")[:40]
            st.download_button("⬇ DOWNLOAD EXCEL", data=xls.getvalue(),
                               file_name=f"agentic_bom_{fname}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            st.caption("4 sheets: Agent Summary · BOM · Should-Cost · Agent Log")
        except Exception as e:
            st.error(f"Export error: {e}")
