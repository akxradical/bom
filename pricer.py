"""
BOM Pricing Engine — pricer.py
═══════════════════════════════
Adds current market prices to every BOM line item.

Strategy:
  HIGH VALUE   (pump, casing, impeller, motor, seal, baseplate)
               → Claude API + web_search tool
               → Live market intelligence, current year prices
               → 6–8 API calls per BOM

  MEDIUM VALUE (bearing, coupling, shaft, sleeve, wear ring)
               → Rate formula: weight_kg × material_rate_per_kg
               → Rates from published Indian market indices

  LOW VALUE    (fasteners, gaskets, flanges, piping, guard)
               → Fixed rate table, updated quarterly
               → These items are <2% of total BOM value

  MOTOR        → Always API call — single biggest cost item

The progress bar hides API calls behind procurement-language labels.
API key is read from Streamlit secrets. Never hardcoded.

Author: Ayush Kamle
"""

import json, re, time
import pandas as pd

# ─────────────────────────────────────────────────────────────────
# PRICING TIERS
# ─────────────────────────────────────────────────────────────────

# Categories that warrant a live API price lookup
HIGH_VALUE_CATS = {
    "Pump", "Casing", "Impeller", "Motor", "Mechanical Seal",
    "Seal", "Baseplate", "Enclosure", "Rotor",
}

# Medium value — weight × rate
MEDIUM_VALUE_CATS = {
    "Bearing", "Housing", "Coupling", "Shaft", "Sleeve",
    "Wear Ring", "Stool", "Frame", "Guard", "Assembly",
}

# ─── Material rate table (₹ per kg, India 2025-26) ───────────────
# Source: Published rates from MSTC, Indian Steel/Casting indices
MATERIAL_RATES_PER_KG = {
    # Cast iron
    "CI IS 210 FG 260":           185,
    "CI":                          170,
    "Cast Iron":                   170,
    "SG Iron":                     200,
    "SG Iron GR.500/7":            200,
    # Carbon steel castings
    "ASTM A216 WCB":               220,
    "A216 WCB":                    220,
    "CS ASTM A216 Gr WCB":         220,
    "IS 2062":                     110,   # mild steel plate/fabrication
    "MS IS:2062":                  110,
    "Carbon Steel":                175,
    "CS":                          175,
    # Stainless steel castings
    "CF8M SS316":                  720,
    "SS316":                       750,
    "SS304":                       580,
    "SS 2324 (Lean Duplex)":       980,
    "Super Duplex 2507":          2200,
    "Duplex 2205":                1450,
    "SS410":                       620,
    "SS410 ASTM A276":             620,
    "EN19/SS410":                  680,
    "EN24 Alloy Steel":            480,
    "EN19":                        450,
    # Alloy & special
    "ASTM A532 GR.IIIA":          1100,   # High chrome white iron
    "A532-IIIA":                  1100,
    "A487 CA6M":                   820,
    "12% Chrome Steel A487":       820,
    # Bronze / copper alloys
    "Bronze LTB5 IS:318":          700,
    "Bronze LTB5":                 700,
    "Bronze":                      650,
    # Fasteners
    "A193 B7":                     280,   # ₹/kg alloy steel studs
    "MS HDG":                      160,
    "SS316 Fasteners":             750,
    # Default fallback
    "DEFAULT":                     300,
}

# ─── Fixed rate table (₹ per item/set) ───────────────────────────
FIXED_RATES = {
    # Gaskets
    "Gasket":            ("per unit",   450),
    # Flanges (estimate, size-independent rough cut)
    "Flange":            ("per unit",  1800),
    # Foundation bolts (per set)
    "Foundation":        ("per set",   2400),
    "Fasteners":         ("per set",   3200),
    "Fastener":          ("per set",   1600),
    # Instrumentation
    "Instrumentation":   ("per unit",  8500),
    "Thermometer":       ("per unit",  4200),
    # Piping (seal flush piping assembly)
    "Piping":            ("per set",  18000),
    # Guard
    "Guard":             ("per unit",  7500),
    # Pulley / belt
    "Pulley":            ("per unit",  6500),
    "V-Belt":            ("per set",   3800),
    # Misc
    "Shim":              ("per set",    800),
    "Nameplate":         ("per unit",   350),
    "Lubrication":       ("per unit",  2500),
    "Bracket":           ("per unit",  2200),
    "Saddle":            ("per unit",  4500),
    "Cover":             ("per unit",  3200),
}

# GST rates by category
GST_RATES = {
    "Motor":      0.18,
    "Seal":       0.18,
    "Mechanical Seal": 0.18,
    "Bearing":    0.18,
    "Coupling":   0.18,
    "Pump":       0.18,
    "Casing":     0.18,
    "Impeller":   0.18,
    "Baseplate":  0.18,
    "Enclosure":  0.18,
    "DEFAULT":    0.18,
}

# ─────────────────────────────────────────────────────────────────
# ANTHROPIC CLIENT  (lazy — only initialised when needed)
# ─────────────────────────────────────────────────────────────────

_client = None

def _get_client():
    global _client
    if _client is None:
        try:
            import anthropic
            import streamlit as st
            api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not found in Streamlit secrets.")
            _client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            raise ImportError("anthropic package not installed. Run: pip install anthropic")
    return _client


# ─────────────────────────────────────────────────────────────────
# CORE PRICE LOOKUP — Claude + web search
# ─────────────────────────────────────────────────────────────────

def _price_via_api(component_name, category, moc, weight_kg, motor_kw=None,
                   fluid=None, qty=1):
    """
    Single API call to get current market price for a high-value component.
    Uses Claude with web_search tool for live market intelligence.
    Returns dict: {unit_price_inr, price_basis, confidence, source, notes}
    """
    client = _get_client()

    # Build a targeted, specific prompt
    weight_str = f"{weight_kg} kg, " if weight_kg and str(weight_kg).strip() else ""
    motor_str  = f"{motor_kw} kW motor, " if motor_kw else ""
    fluid_str  = f"for {fluid} service, " if fluid else ""

    if category == "Motor":
        search_hint = (f"Search for current 2026 Indian market price of "
                       f"induction motor {motor_kw}kW LT/HT, IE3 efficiency, "
                       f"from manufacturers like CGL, Siemens, ABB, KECL, WEG India.")
    elif category in ("Casing","Impeller","Pump","Rotor"):
        search_hint = (f"Search for current 2026 Indian market price of "
                       f"{moc} pump casting/machined component, "
                       f"{weight_str}pump procurement India.")
    elif category in ("Seal","Mechanical Seal"):
        search_hint = (f"Search for current 2026 price of cartridge mechanical seal "
                       f"{fluid_str}from Flowserve Sanmar, John Crane, or Roten India.")
    elif category == "Baseplate":
        search_hint = (f"Search for current 2026 fabricated MS baseplate price "
                       f"{weight_str}India, IS 2062 material, pump baseplate.")
    elif category == "Enclosure":
        search_hint = (f"Search for acoustic enclosure pump motor price India 2026, "
                       f"{weight_str}modular steel panels.")
    else:
        search_hint = (f"Search for current 2026 Indian market price of "
                       f"{component_name} {moc} {weight_str}pump component India procurement.")

    prompt = f"""You are a senior procurement engineer at an EPC company in India.

{search_hint}

Component details:
- Name: {component_name}
- Category: {category}
- Material (MOC): {moc}
- Weight: {weight_str or 'not specified'}
- Quantity needed: {qty}
- Service fluid: {fluid or 'general'}

Search for current market prices and respond with ONLY a JSON object, no other text:
{{
  "unit_price_inr": <integer price in Indian Rupees>,
  "price_basis": "<per unit / per kg / per set>",
  "confidence": "<high / medium / low>",
  "source": "<vendor name or market source>",
  "price_range_low": <lower estimate>,
  "price_range_high": <upper estimate>,
  "notes": "<material grade, size assumptions, or caveats>"
}}

Important: Give realistic 2026 Indian market prices. If you cannot find exact prices, give a well-reasoned estimate based on material costs and manufacturing."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )

        # Extract text from response (may have tool_use blocks too)
        text_parts = [
            block.text for block in response.content
            if block.type == "text" and hasattr(block, "text")
        ]
        raw = " ".join(text_parts).strip()

        # Parse JSON
        raw_clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        # Find JSON object
        m = re.search(r"\{.*\}", raw_clean, re.DOTALL)
        if m:
            data = json.loads(m.group())
            return {
                "unit_price_inr":   int(data.get("unit_price_inr", 0)),
                "price_basis":      str(data.get("price_basis", "per unit")),
                "confidence":       str(data.get("confidence", "medium")),
                "source":           str(data.get("source", "Market estimate")),
                "price_range_low":  int(data.get("price_range_low", 0)),
                "price_range_high": int(data.get("price_range_high", 0)),
                "notes":            str(data.get("notes", "")),
            }
    except Exception as e:
        pass

    return None


def _price_formula(category, moc, weight_kg):
    """
    Weight-based rate calculation for medium-value components.
    Returns (unit_price_inr, basis, confidence)
    """
    if not weight_kg or str(weight_kg).strip() in ("", "nan", "None"):
        wt = 25.0   # fallback estimate
    else:
        try:
            wt = float(weight_kg)
        except (ValueError, TypeError):
            wt = 25.0

    # Find rate
    rate = None
    moc_str = str(moc)
    for key, r in MATERIAL_RATES_PER_KG.items():
        if key.lower() in moc_str.lower() or moc_str.lower() in key.lower():
            rate = r
            break
    if rate is None:
        rate = MATERIAL_RATES_PER_KG["DEFAULT"]

    price = int(wt * rate * 1.25)   # +25% for machining/overhead
    return price, "per unit (formula)", "medium"


def _price_fixed(category):
    """Fixed rate for low-value items."""
    if category in FIXED_RATES:
        basis, price = FIXED_RATES[category]
        return int(price), basis, "medium"
    return 2500, "per unit (estimate)", "low"


# ─────────────────────────────────────────────────────────────────
# MAIN PRICING FUNCTION
# ─────────────────────────────────────────────────────────────────

def price_bom(bom_df, specs, calc_summary, progress_callback=None):
    """
    Adds pricing to every row in bom_df.
    progress_callback(pct, message) — for UI progress bar.

    Returns bom_df with added columns:
      Unit_Price_INR, Price_Basis, Qty_Num, Total_Price_INR,
      GST_Rate, GST_Amount, Price_Confidence, Price_Source, Price_Notes
    """
    is_t1   = "Component_Name" in bom_df.columns
    fluid   = (specs or {}).get("fluid", "")
    motor_kw= (specs or {}).get("motor_kw") or (calc_summary or {}).get("motor_kw_calc")

    result_rows = []
    n = len(bom_df)

    # Identify which components need API calls
    api_rows = [i for i, row in bom_df.iterrows()
                if str(row.get("Category","")) in HIGH_VALUE_CATS]
    total_api = len(api_rows)
    api_done  = 0

    for idx, (_, row) in enumerate(bom_df.iterrows()):
        cat   = str(row.get("Category",""))
        desc  = str(row.get("Component_Name","") or row.get("Description",""))
        moc   = str(row.get("Material_Spec","") or row.get("MOC",""))
        wt    = row.get("Weight_kg","")
        qty_s = str(row.get("Qty_Per_Unit","") or row.get("Qty","1"))

        # Parse qty
        try:
            qty_num = float(re.findall(r"\d+\.?\d*", qty_s)[0]) if re.findall(r"\d+\.?\d*", qty_s) else 1.0
        except (ValueError, IndexError):
            qty_num = 1.0

        unit_price   = 0
        price_basis  = "estimate"
        confidence   = "low"
        source       = "—"
        price_low    = 0
        price_high   = 0
        price_notes  = ""

        # ── HIGH VALUE: API call ──────────────────────────────────
        if cat in HIGH_VALUE_CATS:
            api_done += 1
            pct = int(10 + (api_done / max(total_api, 1)) * 70)
            if progress_callback:
                labels = {
                    "Pump":          "Analysing pump assembly specifications...",
                    "Casing":        "Accumulating casting market data...",
                    "Impeller":      "Retrieving impeller pricing indices...",
                    "Motor":         "Compiling motor market rates...",
                    "Seal":          "Gathering mechanical seal market data...",
                    "Mechanical Seal":"Gathering mechanical seal market data...",
                    "Baseplate":     "Calculating structural fabrication costs...",
                    "Enclosure":     "Accumulating acoustic enclosure rates...",
                    "Rotor":         "Retrieving rotating assembly pricing...",
                }
                msg = labels.get(cat, f"Accumulating market data for {cat}...")
                progress_callback(pct, msg)

            result = _price_via_api(desc, cat, moc, wt, motor_kw, fluid, qty_num)
            if result:
                unit_price  = result["unit_price_inr"]
                price_basis = result["price_basis"]
                confidence  = result["confidence"]
                source      = result["source"]
                price_low   = result.get("price_range_low", 0)
                price_high  = result.get("price_range_high", 0)
                price_notes = result.get("notes", "")
            else:
                # Fallback to formula if API fails
                unit_price, price_basis, confidence = _price_formula(cat, moc, wt)
                source      = "Formula fallback"
                price_notes = "API unavailable — rate formula used"

        # ── MEDIUM VALUE: formula ─────────────────────────────────
        elif cat in MEDIUM_VALUE_CATS:
            if progress_callback:
                pct2 = int(80 + (idx/n)*15)
                progress_callback(pct2, "Computing component rates...")
            unit_price, price_basis, confidence = _price_formula(cat, moc, wt)
            source      = "Material rate index (India 2025-26)"
            price_notes = f"Rate: ₹{MATERIAL_RATES_PER_KG.get(moc, MATERIAL_RATES_PER_KG['DEFAULT'])}/kg + machining"

        # ── LOW VALUE: fixed table ────────────────────────────────
        else:
            unit_price, price_basis, confidence = _price_fixed(cat)
            source      = "Fixed rate table"
            price_notes = "Standard item — price stable"

        # Total and GST
        total    = int(unit_price * qty_num)
        gst_rate = GST_RATES.get(cat, GST_RATES["DEFAULT"])
        gst_amt  = int(total * gst_rate)

        row_dict = row.to_dict()
        row_dict.update({
            "Unit_Price_INR":   unit_price,
            "Price_Basis":      price_basis,
            "Qty_Num":          qty_num,
            "Total_Price_INR":  total,
            "GST_Rate_%":       int(gst_rate * 100),
            "GST_Amount_INR":   gst_amt,
            "Price_With_GST":   total + gst_amt,
            "Price_Confidence": confidence,
            "Price_Source":     source,
            "Price_Range":      f"₹{price_low:,}–{price_high:,}" if price_low else "—",
            "Price_Notes":      price_notes,
        })
        result_rows.append(row_dict)

    if progress_callback:
        progress_callback(95, "Compiling final cost summary...")

    return pd.DataFrame(result_rows)


def build_cost_summary(priced_df):
    """
    Returns a summary dict for display.
    Groups by sub-assembly.
    """
    if priced_df is None or priced_df.empty:
        return {}

    total_ex_gst = priced_df["Total_Price_INR"].sum()
    total_gst    = priced_df["GST_Amount_INR"].sum()
    total_incl   = priced_df["Price_With_GST"].sum()

    # Sub-assembly breakdown
    sub_col = "Sub_Assembly" if "Sub_Assembly" in priced_df.columns else "Category"
    sub_totals = (priced_df.groupby(sub_col)["Total_Price_INR"]
                  .sum().sort_values(ascending=False).to_dict())

    # Confidence breakdown
    conf_counts = priced_df["Price_Confidence"].value_counts().to_dict()

    # Top 5 cost drivers
    top5 = (priced_df.nlargest(5, "Total_Price_INR")
            [["Category", "Component_Name" if "Component_Name" in priced_df.columns
              else "Description", "Total_Price_INR", "Price_Confidence"]]
            .to_dict("records"))

    return {
        "total_ex_gst":  int(total_ex_gst),
        "total_gst":     int(total_gst),
        "total_incl_gst":int(total_incl),
        "sub_totals":    {k: int(v) for k, v in sub_totals.items()},
        "confidence":    conf_counts,
        "top5_drivers":  top5,
        "component_count": len(priced_df),
        "api_calls_used":  int(priced_df["Price_Source"].str.contains("Market|vendor|Vendor", na=False).sum()),
        "note": (
            "Prices are indicative market estimates for budget planning. "
            "Actual prices subject to vendor quotation, quantity, delivery terms, and date."
        ),
    }
