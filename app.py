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
"""

import html
import pandas as pd
import streamlit as st
import pydeck as pdk

from claude_engine import (
    extract_pdf_text, run_agent, bom_to_dataframe, export_excel, _get_key,
    price_manual, SUPPLIER_FACTORS,
)
import geo_cost
try:
    from pricing import _rate_for_material
except Exception:
    def _rate_for_material(m): return (0, "")


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
ss.setdefault("rates", {})        # {component_id: raw ₹/kg}
ss.setdefault("supplier", "Indian")
ss.setdefault("labour", 90)
ss.setdefault("overhead", 0)
ss.setdefault("per_km", 35)
ss.setdefault("supplier_loc", "")
ss.setdefault("site_loc", "")
ss.setdefault("freight", None)    # {km, mode, cost, a:{lat,lon}, b:{lat,lon}}

LIGHT = ss.result is not None   # phase switch: light once a BOM exists

# ═══════════════════════════════════════════════════════════════════
# THEME  (dark during run, light once results are in)
# ═══════════════════════════════════════════════════════════════════
if LIGHT:
    BG, FG, MUT, CARD, BORDER, PRIMARY = "#f4f6f9", "#1a1f29", "#5b6472", "#ffffff", "#e3e8ef", "#0a6ed1"
else:
    BG, FG, MUT, CARD, BORDER, PRIMARY = "#0a0a0f", "#e8e4db", "#8b8b9b", "#12121a", "#23232f", "#e8a020"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Syne:wght@700;800&family=Inter:wght@400;500;600;700&display=swap');
.stApp {{ background:{BG}; color:{FG}; }}
#MainMenu, footer, header {{ visibility:hidden; }}
[data-testid="collapsedControl"] {{ display:none; }}
.block-container {{ max-width:1200px; padding-top:2rem; font-family:'Inter',sans-serif; }}
h1,h2,h3 {{ font-family:'Syne',sans-serif; color:{FG}; }}

.stButton > button {{
    background:{PRIMARY} !important; color:{'#fff' if LIGHT else '#0a0a0f'} !important;
    border:none !important; border-radius:6px !important; font-weight:700 !important;
    font-family:'Inter',sans-serif !important; letter-spacing:0.03em !important; padding:10px 26px !important;
}}
.kpi {{ background:{CARD}; border:1px solid {BORDER}; border-radius:10px; padding:14px 16px; }}
.kpi .v {{ font-family:'Syne',sans-serif; font-size:24px; font-weight:800; color:{PRIMARY}; }}
.kpi .l {{ font-size:10px; letter-spacing:0.12em; text-transform:uppercase; color:{MUT}; margin-top:2px; }}
.sa-head {{ background:{PRIMARY}; color:#fff; padding:8px 14px; border-radius:8px 8px 0 0;
           font-family:'Inter',sans-serif; font-weight:700; font-size:14px; letter-spacing:0.02em; }}
.sa-body {{ background:{CARD}; border:1px solid {BORDER}; border-top:none; border-radius:0 0 8px 8px;
           padding:6px 14px 12px 14px; margin-bottom:14px; }}
.row-c {{ font-size:13px; color:{FG}; }}
.row-m {{ font-size:12px; color:{MUT}; }}
[data-testid="stDataFrame"] {{ font-family:'IBM Plex Mono',monospace; font-size:12px; }}
hr {{ border-color:{BORDER} !important; }}
.stNumberInput input {{ background:{'#fff' if LIGHT else '#1b2030'}; color:{FG}; }}
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
            ss.result = None; ss.agent_lines = []; ss.rates = {}; st.rerun()

# ═══════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════
st.markdown(f"""
<div style="padding:6px 0 18px 0;">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:0.3em;
              color:{PRIMARY};text-transform:uppercase;">Zetwerk · Central Procurement · Category 2</div>
  <div style="font-family:'Syne',sans-serif;font-size:46px;font-weight:800;line-height:1;color:{FG};">
    AGENTIC&nbsp;BOM</div>
  <div style="font-size:14px;color:{MUT};margin-top:8px;">
    Drop any engineered-product datasheet → complete BOM → buyer-driven should-cost.</div>
</div>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════
# RUN PHASE  (only show uploader/terminal until a result exists)
# ═══════════════════════════════════════════════════════════════════
if not LIGHT:
    uploaded = st.file_uploader("Datasheet PDF", type=["pdf"], label_visibility="collapsed")
    st.caption("PDF datasheets, GA drawings, spec sheets — any engineered product")
    run = st.button("◈ RUN AGENT", disabled=uploaded is None)

    term = st.empty()
    if ss.agent_lines:
        term.markdown(terminal(ss.agent_lines), unsafe_allow_html=True)

    def on_progress(line, agent_log):
        ss.agent_lines.append(line)
        term.markdown(terminal(ss.agent_lines), unsafe_allow_html=True)

    if run and uploaded:
        ss.agent_lines = ["[00:00] ◈ AGENT      Starting..."]
        term.markdown(terminal(ss.agent_lines), unsafe_allow_html=True)
        pdf_text, err = extract_pdf_text(uploaded.read())
        if err or len(pdf_text.strip()) < 100:
            st.error(f"Could not read PDF. {err or 'Try a text-based PDF.'}"); st.stop()
        try:
            ss.result = run_agent(pdf_text, progress_callback=on_progress, price=False)
            ss.rates = {}
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

    # initialise rate store — prefill from DB history, else standard rate table
    for c in bom:
        cid = str(c.get("id"))
        if cid not in ss.rates:
            mat = c.get("material", "")
            default = geo_cost.suggested_rate(mat)
            if not default:
                default = float(_rate_for_material(mat)[0] or 0)
            ss.rates[cid] = float(default)

    # ── controls
    cc1, cc2, cc3 = st.columns([1.2, 1.4, 1])
    ss.supplier = cc1.radio("Supplier", list(SUPPLIER_FACTORS.keys()),
                            index=list(SUPPLIER_FACTORS).index(ss.supplier), horizontal=True)
    ss.labour = cc2.slider("Labour / mfg (₹/kg)", 0, 500, ss.labour, 10)
    cc3.markdown(" ")

    # ── price with current inputs
    priced, sc = price_manual([dict(c) for c in bom], ss.rates,
                              labour_rate_per_kg=ss.labour, supplier=ss.supplier)
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
        st.markdown(f"Fill the **raw-material rate (₹/kg)** per component. "
                    f"Supplier **{ss.supplier}** (×{SUPPLIER_FACTORS[ss.supplier]}), "
                    f"labour **₹{ss.labour}/kg**. Totals update live.")
        by_sub = {}
        for c in priced:
            by_sub.setdefault((c.get("sub_assembly_id"), c.get("sub_assembly_name")), []).append(c)

        for s in schema:
            key = (s["id"], s["name"])
            items = by_sub.get(key, [])
            if not items:
                continue
            sub_total = sum(int(x.get("total_cost_inr", 0)) for x in items)
            st.markdown(f'<div class="sa-head">{s["id"]}. {s["name"]} '
                        f'&nbsp;·&nbsp; ₹{sub_total:,}</div>', unsafe_allow_html=True)
            st.markdown('<div class="sa-body">', unsafe_allow_html=True)
            # column headers
            h = st.columns([3, 1.6, 0.8, 1.2, 1.2])
            for col, t in zip(h, ["Component", "Material", "Wt kg", "Rate ₹/kg", "Cost ₹"]):
                col.markdown(f"<span class='row-m'>{t}</span>", unsafe_allow_html=True)
            for c in items:
                r = st.columns([3, 1.6, 0.8, 1.2, 1.2])
                r[0].markdown(f"<span class='row-c'>{html.escape(str(c.get('description','')))}</span>",
                              unsafe_allow_html=True)
                r[1].markdown(f"<span class='row-m'>{html.escape(str(c.get('material','') or '—'))}</span>",
                              unsafe_allow_html=True)
                r[2].markdown(f"<span class='row-m'>{c.get('weight_kg',0)}</span>", unsafe_allow_html=True)
                cid = str(c.get("id"))
                ss.rates[cid] = r[3].number_input(
                    "rate", min_value=0.0, step=10.0, value=float(ss.rates.get(cid, 0) or 0),
                    key=f"rate_{cid}", label_visibility="collapsed")
                r[4].markdown(f"<span class='row-c'><b>₹{int(c.get('total_cost_inr',0)):,}</b></span>",
                              unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        # save entered rates to the persistent database
        if st.button("💾 Save rates to database"):
            n = 0
            for c in priced:
                rt = float(ss.rates.get(str(c.get("id")), 0) or 0)
                if rt > 0:
                    geo_cost.record_rate(c.get("material", ""), rt, c.get("description", ""))
                    n += 1
            st.success(f"Saved {n} raw-material rates to the database.")

        comp_ex = int(sc.get("total_ex_gst", 0))

        # ── FREIGHT ────────────────────────────────────────────────
        st.markdown("### 🚚 Freight")
        fc1, fc2 = st.columns(2)
        ss.supplier_loc = fc1.text_input("Supplier location", ss.supplier_loc,
                                         placeholder="e.g. KSB Pimpri, Pune")
        ss.site_loc = fc2.text_input("Delivery / site location", ss.site_loc,
                                     placeholder="e.g. Hindustan Zinc, Udaipur")
        fc3, fc4 = st.columns([1, 1])
        ss.per_km = fc3.number_input("Freight rate (₹ per km)", min_value=0, step=5,
                                     value=int(ss.per_km))
        if fc4.button("📍 Calculate distance"):
            a = _geo(ss.supplier_loc); b = _geo(ss.site_loc)
            if not a or not b:
                st.error("Could not locate one of the addresses. Be more specific (add city/state).")
                ss.freight = None
            else:
                km, mode = _road(a["lat"], a["lon"], b["lat"], b["lon"])
                ss.freight = {"km": km, "mode": mode, "a": a, "b": b}

        freight_cost = 0
        if ss.freight:
            km = ss.freight["km"]
            freight_cost = geo_cost.freight_cost(km, ss.per_km)
            badge = "road" if ss.freight["mode"] == "road" else "estimated (×1.3)"
            st.markdown(f"**Distance: {km:,} km** ({badge}) &nbsp;·&nbsp; "
                        f"**Freight = {km:,} × ₹{ss.per_km} = ₹{freight_cost:,}**")
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
            fuel = geo_cost.get_fuel_prices()
            st.caption(f"Fuel reference (editable defaults): diesel ₹{fuel['diesel']}/L · "
                       f"petrol ₹{fuel['petrol']}/L. A guaranteed-free live India fuel feed "
                       "isn't available — set DIESEL_PRICE in secrets to override.")

        # ── OVERHEAD (optional) ────────────────────────────────────
        st.markdown("### 🧾 Overhead (optional)")
        ss.overhead = st.number_input("Overhead charges (₹)", min_value=0, step=1000,
                                      value=int(ss.overhead),
                                      help="Any extra charges — testing, packing, documentation, etc.")

        # ── GRAND TOTAL ────────────────────────────────────────────
        grand_ex = comp_ex + int(freight_cost) + int(ss.overhead)
        gst = int(grand_ex * 0.18)
        grand_incl = grand_ex + gst
        st.markdown("---")
        g1, g2, g3, g4, g5 = st.columns(5)
        g1.markdown(f'<div class="kpi"><div class="v">₹{comp_ex:,}</div>'
                    f'<div class="l">Components (RM+Mfg)</div></div>', unsafe_allow_html=True)
        g2.markdown(f'<div class="kpi"><div class="v">₹{int(freight_cost):,}</div>'
                    f'<div class="l">Freight</div></div>', unsafe_allow_html=True)
        g3.markdown(f'<div class="kpi"><div class="v">₹{int(ss.overhead):,}</div>'
                    f'<div class="l">Overhead</div></div>', unsafe_allow_html=True)
        g4.markdown(f'<div class="kpi"><div class="v">₹{grand_ex:,}</div>'
                    f'<div class="l">Total ex-GST</div></div>', unsafe_allow_html=True)
        g5.markdown(f'<div class="kpi"><div class="v">₹{grand_incl:,}</div>'
                    f'<div class="l">Grand Total (incl 18% GST)</div></div>', unsafe_allow_html=True)

        # store grand total for export
        ss.result["grand_total"] = {"components_ex_gst": comp_ex, "freight": int(freight_cost),
                                    "overhead": int(ss.overhead), "total_ex_gst": grand_ex,
                                    "gst": gst, "total_incl_gst": grand_incl,
                                    "freight_detail": ss.freight, "per_km": ss.per_km}

        # ── reference panels ───────────────────────────────────────
        with st.expander("📚 Rate database (saved buyer rates)"):
            rows = geo_cost.rate_db_table()
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
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
        st.dataframe(view, use_container_width=True, hide_index=True)

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
