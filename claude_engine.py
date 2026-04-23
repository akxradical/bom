"""
Claude-Powered BOM Engine
═════════════════════════
Claude reads the datasheet. Claude generates the BOM.
Claude prices each component with live web search.

The old rule-based engine stays for:
  - Database matching (Tier 1 — 12 known pumps)
  - Weight schedule (REAL_WEIGHTS from dissection sheets)
  - Hierarchy grouping (SECTION_ORDER)

Everything else: Claude handles it.
Author: Ayush Kamle
"""

import json, re, time, os
import pandas as pd
from io import BytesIO

# ─────────────────────────────────────────────────────────────────
# ANTHROPIC CLIENT
# ─────────────────────────────────────────────────────────────────

_client = None

def _get_client():
    global _client
    if _client is None:
        import anthropic
        import streamlit as st
        key = st.secrets.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set in Streamlit secrets")
        _client = anthropic.Anthropic(api_key=key)
    return _client


def _call_claude(prompt, system="", use_search=False, max_tokens=4000):
    """Single Claude API call with automatic retry on rate limits."""
    client = _get_client()
    kwargs = {
        "model":      "claude-sonnet-4-5",
        "max_tokens":  max_tokens,
        "messages":   [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    if use_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    max_retries = 4
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(**kwargs)
            parts = []
            for block in resp.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
            return "\n".join(parts).strip()
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str.lower():
                wait = 30 * (2 ** attempt)  # 30s, 60s, 120s, 240s
                import streamlit as st
                st.toast(f"⏳ Rate limit hit — waiting {wait}s before retry ({attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
    raise Exception("Rate limit exceeded after 4 retries. Try again in a few minutes.")


def _parse_json(text):
    """Extract JSON from Claude response. Handles extra text, fences, trailing commas,
    AND truncated arrays (when max_tokens cuts off mid-JSON)."""
    if not text:
        return None

    # Strip markdown fences
    clean = text.strip()
    clean = clean.replace("```json", "").replace("```", "").strip()

    # Strategy 1: direct parse
    try:
        return json.loads(clean)
    except Exception:
        pass

    # Strategy 2: find the outermost [ ] or { } using bracket counting
    result = _bracket_extract(clean, '[', ']')
    if result is not None:
        return result

    # Strategy 3: TRUNCATED ARRAY RECOVERY
    # If the text starts with [ but has no matching ] (max_tokens cut off),
    # extract every complete {...} object inside it
    arr_start = clean.find('[')
    if arr_start != -1:
        recovered = _recover_truncated_array(clean[arr_start:])
        if recovered and len(recovered) > 1:
            return recovered

    # Strategy 4: single object fallback
    result = _bracket_extract(clean, '{', '}')
    if result is not None:
        return result

    return None


def _recover_truncated_array(text):
    """Extract all complete JSON objects from a truncated array like [{ }, { }, { ..."""
    objects = []
    i = 0
    while i < len(text):
        # Find start of next object
        start = text.find('{', i)
        if start == -1:
            break

        # Use bracket counting to find its end
        depth = 0
        in_string = False
        j = start
        found_end = False
        while j < len(text):
            ch = text[j]
            if ch == '"' and (j == 0 or text[j-1] != '\\'):
                in_string = not in_string
                j += 1
                continue
            if in_string:
                j += 1
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:j+1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            objects.append(obj)
                    except json.JSONDecodeError:
                        fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
                        try:
                            obj = json.loads(fixed)
                            if isinstance(obj, dict):
                                objects.append(obj)
                        except Exception:
                            pass
                    found_end = True
                    i = j + 1
                    break
            j += 1
        if not found_end:
            # This object was truncated — skip it
            break
    return objects


def _bracket_extract(text, opener, closer):
    """Find and parse the first complete JSON structure bounded by opener/closer."""
    start = text.find(opener)
    if start == -1:
        return None

    depth = 0
    in_string = False
    i = start
    while i < len(text):
        ch = text[i]

        # Handle string literals — skip everything inside quotes
        if ch == '"' and (i == 0 or text[i-1] != '\\'):
            in_string = not in_string
            i += 1
            continue

        if in_string:
            i += 1
            continue

        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                candidate = text[start:i+1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
                    try:
                        return json.loads(fixed)
                    except Exception:
                        return None
        i += 1

    return None


# ═══════════════════════════════════════════════════════════════════
# STEP 1 — CLAUDE READS THE PDF
# ═══════════════════════════════════════════════════════════════════

SPEC_SYSTEM = """You are a senior mechanical/procurement engineer specialising in 
rotating equipment (pumps, compressors, turbines) for EPC projects in India.

Your job: Read pump datasheets, GA drawings, vendor technical documents, and 
procurement specifications. Extract every technical parameter accurately.

Critical rules:
- If the document contains multiple pump types (e.g. Hydrant, Spray, Jockey), 
  identify ALL of them and list each separately.
- Convert all units to SI: flow in m³/h, head in metres, power in kW, temp in °C.
- If head is in mWC, mlc, or kPa — convert to metres.
- If flow is in LPM, LPS, USGPM, l/s — convert to m³/h.
- If power is in HP/BHP — convert to kW (×0.7457).
- Extract Material of Construction (MOC) for every component mentioned.
- Identify the pump type: HSC, VTP, Slurry, Sump, Multistage, etc.
- Note manufacturer, model, tag numbers, project details.
- If a field says "VTA" or "Bidder to furnish" → mark as null, don't guess.
- Read EVERY row carefully: vendor response columns often contain the actual 
  values (materials, dimensions, weights) even when the spec column says VTA.
- Pay special attention to:
  * Nozzle sizes and ratings from the nozzle schedule
  * All MOC entries including casing bolts, bearing housing, base plate
  * Weights section (total, heaviest part, transport)
  * Seal type, seal plan, and seal accessories
  * Drive type (direct/belt/gear) and coupling details
  * Motor electrical data (voltage, frequency, poles)
  * NPSHA/NPSHR values
  * Vibration and noise limits"""


def claude_extract_specs(pdf_text):
    """
    Send PDF text to Claude. Returns structured specs dict.
    Handles multi-pump documents automatically.
    """
    prompt = f"""Read this pump technical document and extract ALL pump specifications.
Pay careful attention to the VENDOR RESPONSE column — it often contains the actual
values when the specification column says "VTA" (Vendor to Advise).

DOCUMENT TEXT:
{pdf_text[:15000]}

Respond with ONLY a JSON object (no other text):
{{
  "document_type": "vendor_datasheet | procurement_spec | ga_drawing | manual",
  "project": "project name if mentioned",
  "manufacturer": "manufacturer name or null",
  "multi_pump": true/false,
  
  "pumps": [
    {{
      "pump_label": "descriptive name e.g. Feed Liquor Booster Pump",
      "model": "pump model or null",
      "manufacturer": "name or null",
      "type": "Horizontal Centrifugal | Horizontal Split Casing | Vertical Turbine | Slurry | Sump | Multistage | Other",
      "tag_numbers": "tag nos or null",
      "standard": "API 610 | IS 5120 | ISO 9906 | etc or null",
      "quantity": "total number of pump units or null",
      "configuration": "1W+1S per stream etc or null",
      
      "flow_m3h": number or null,
      "head_m": number or null,
      "speed_rpm": number or null,
      "motor_kw": number or null,
      "shaft_power_kw": number or null,
      "stages": number or null,
      "temp_c": number or null,
      "density_kgm3": number or null,
      "fluid": "fluid name",
      "viscosity": "value with unit or null",
      "npsha_m": number or null,
      "npshr_m": number or null,
      "min_flow_m3h": number or null,
      "shutoff_head_m": number or null,
      "efficiency_pct": number or null,
      "impeller_dia_mm": number or null,
      
      "moc": {{
        "casing": "exact material spec from vendor response, e.g. ASTM A532 Gr.IIIA",
        "impeller": "exact material spec, e.g. 12% Chrome Steel ASTM A487",
        "shaft": "exact material spec, e.g. EN-19 / SS410",
        "shaft_sleeve": "exact material spec, e.g. SS316",
        "wear_ring": "exact material spec or null",
        "bearing": "type and make, e.g. Taper roller / TIMKEN",
        "bearing_housing": "material spec, e.g. Grey Cast Iron",
        "seal_type": "Single mechanical seal with SLD / gland packing / etc",
        "seal_plan": "API Plan 62 / Plan 11 / Plan 53B / etc or null",
        "baseplate": "material spec, e.g. MS (Mild Steel)",
        "fasteners": "material spec, e.g. A197 2H & A193 B7",
        "gland_plate": "material spec or null",
        "coupling_halves": "material spec or null"
      }},
      
      "nozzles": {{
        "suction_size": "DN or inch size, e.g. DN200 or 8 inch",
        "suction_rating": "Class 300 / PN16 / etc",
        "discharge_size": "DN or inch size, e.g. DN150 or 6 inch",
        "discharge_rating": "Class 300 / PN16 / etc",
        "flange_standard": "ANSI B16.5 / IS 6392 / etc"
      }},

      "weights": {{
        "pump_bare_kg": number or null,
        "motor_kg": number or null,
        "baseplate_kg": number or null,
        "total_package_kg": number or null,
        "heaviest_part_kg": number or null,
        "transport_kg": number or null
      }},
      
      "motor": {{
        "type": "Squirrel cage / Slip ring / etc",
        "rating_kw": number or null,
        "voltage_v": "690V / 415V / etc",
        "frequency_hz": 50,
        "poles": 4,
        "speed_rpm": number or null,
        "enclosure": "TEFC / etc or null",
        "mounting": "by pump vendor / by contractor / etc"
      }},
      
      "drive": {{
        "type": "direct coupled | belt driven | gear driven",
        "coupling_type": "disc / tyre / gear / V-belt / etc",
        "belt_guard": true/false
      }},
      
      "vibration_limit": "value with unit or null",
      "noise_limit_dba": number or null,
      "performance_test_std": "ISO 9906:2012 / etc or null",
      "surface_prep_spec": "spec reference or null",
      
      "scope": {{
        "pump": true/false,
        "motor": true/false,
        "mechanical_seal": true/false,
        "baseframe": true/false,
        "coupling_guard": true/false,
        "companion_flanges": true/false,
        "foundation_bolts": true/false,
        "first_fill_lubricants": true/false,
        "suction_strainer": true/false
      }},
      
      "notes": "any critical notes including vendor remarks"
    }}
  ]
}}

Be accurate. If data is missing, use null — never guess."""

    raw = _call_claude(prompt, system=SPEC_SYSTEM, max_tokens=6000)
    data = _parse_json(raw)
    return data


# ═══════════════════════════════════════════════════════════════════
# STEP 2 — CLAUDE GENERATES THE BOM
# ═══════════════════════════════════════════════════════════════════

BOM_SYSTEM = """You are an expert pump procurement engineer in India. 
You generate Bills of Materials for centrifugal pump packages.

Every BOM you generate must:
1. Cover ALL sub-assemblies: pump hydraulics, rotating assembly, bearings, 
   sealing, drive/coupling, motor, structural, piping/nozzles, fasteners, 
   instrumentation, acoustic (if needed), complete assembly.
2. Specify MOC (Material of Construction) for EVERY component — use exact 
   ASTM/EN/IS specifications, not generic terms.
3. Include realistic weights where known.
4. Specify quantity per pump unit.
5. Mark each component as M (Mandatory), C (Conditional), or O (Optional).
6. Be specific — not generic. E.g. "Taper Roller Bearing - TIMKEN" not just "Bearing".
7. Include seal plan piping, coupling guard, foundation bolts, counter flanges.
8. For Indian EPC: include RTDs for pump bearings, dial thermometers.
9. Reference the datasheet wherever possible in notes (e.g. "per DS row 86").
10. For belt-driven pumps: include V-belt set, pulleys, belt guard.
11. For motors mounted by pump vendor: include full motor specs.
"""


def claude_generate_bom(pump_specs):
    """
    Given extracted pump specs, generate a complete BOM.
    Returns a list of component dicts.
    """
    specs_str = json.dumps(pump_specs, indent=2, default=str)

    prompt = f"""Generate a COMPLETE Bill of Materials for this pump:

PUMP SPECIFICATIONS:
{specs_str}

Generate the BOM as a JSON array. Each component:
{{
  "section": "A. PUMP HYDRAULICS | B. ROTATING ASSEMBLY | C. BEARINGS & LUBRICATION | D. SHAFT SEALING | E. DRIVE & COUPLING | F. MOTOR / DRIVER | G. STRUCTURAL | H. PIPING & NOZZLES | I. FASTENERS & GASKETS | J. INSTRUMENTATION | K. ACOUSTIC & SAFETY | L. COMPLETE ASSEMBLY",
  "sub_assembly": "Casing Assembly | Rotor Assembly | Bearing Assembly | Sealing Assembly | Drive Assembly | Motor | Structural | Piping Assembly | Fasteners | Instrumentation | Acoustic Enclosure | Complete Package",
  "component": "specific component name",
  "description": "detailed description with specs where applicable",
  "moc": "exact material specification — ASTM/IS/EN standard",
  "qty": "1 | 2 | 1 set | etc",
  "weight_kg": number or null,
  "req_type": "M | C | O",
  "notes": "any relevant notes"
}}

IMPORTANT:
- Include AT LEAST 25 components for a standard pump
- Include ALL wetted parts with correct MOC for the fluid service
- Motor: specify frame size, kW, voltage, poles
- Seal: specify type, plan, materials
- For belt drive: include V-belt set, motor pulley, pump pulley, belt guard
- For direct coupling: specify type (disc/tyre/spacer), DBSE
- Baseplate: IS 2062 fabricated with drain pan
- Foundation bolts: specify quantity and size
- Counter flanges: both suction and discharge with gaskets and fasteners
- Gaskets: specify type and material
- Instrumentation: RTDs for pump bearings if applicable
- Casing wear rings AND impeller wear rings as separate items
- Shaft sleeve if applicable
- All bearing housing components
- Casing joint bolts/fasteners

Respond with ONLY the JSON array — no preamble, no explanation.
Keep descriptions under 80 characters to avoid truncation."""

    raw = _call_claude(prompt, system=BOM_SYSTEM, max_tokens=8000)
    data = _parse_json(raw)

    # Normalize: always return a list of dicts
    items = _normalize_bom_data(data, raw)
    return items


def _normalize_bom_data(data, raw_text=""):
    """
    Claude can return BOM as:
      - list of dicts (ideal)
      - dict with "components", "bom", or "items" key
      - dict that IS a single component
      - string (if _parse_json failed)
      - None
    Normalize to list of dicts always.
    """
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        for key in ["components", "bom", "items", "bill_of_materials",
                     "BOM", "Components", "data"]:
            if key in data and isinstance(data[key], list):
                return [item for item in data[key] if isinstance(item, dict)]
        if "component" in data or "section" in data or "moc" in data:
            return [data]
        for v in data.values():
            if isinstance(v, list) and len(v) > 3:
                items = [item for item in v if isinstance(item, dict)]
                if items:
                    return items

    if raw_text and isinstance(raw_text, str):
        cleaned = raw_text.replace("```json","").replace("```","")
        arr = _bracket_extract(cleaned, "[", "]")
        if isinstance(arr, list):
            return [item for item in arr if isinstance(item, dict)]
        # Last resort: recover individual objects from truncated array
        recovered = _recover_truncated_array(cleaned)
        if recovered and len(recovered) > 0:
            return recovered

    return []


def bom_to_dataframe(bom_list):
    """Convert Claude BOM output to a clean DataFrame."""
    if not bom_list:
        return pd.DataFrame()

    rows = []
    for i, comp in enumerate(bom_list, 1):
        if not isinstance(comp, dict):
            continue
        rows.append({
            "No":           i,
            "Section":      str(comp.get("section", "")),
            "Sub_Assembly": str(comp.get("sub_assembly", "")),
            "Component":    str(comp.get("component", comp.get("name", comp.get("item", "")))),
            "Description":  str(comp.get("description", comp.get("desc", ""))),
            "MOC":          str(comp.get("moc", comp.get("material", comp.get("MOC", "")))),
            "Qty":          str(comp.get("qty", comp.get("quantity", "1"))),
            "Weight_kg":    comp.get("weight_kg", comp.get("weight", None)),
            "Req_Type":     str(comp.get("req_type", comp.get("type", "M"))),
            "Notes":        str(comp.get("notes", comp.get("note", comp.get("remarks", "")))),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════
# STEP 3 — CLAUDE PRICES EACH COMPONENT (with web search)
# ═══════════════════════════════════════════════════════════════════

PRICE_SYSTEM = """You are a procurement cost estimator for an EPC company in India.
You estimate current 2025-2026 market prices for pump components.

Rules:
- Prices in Indian Rupees (INR)
- Use current Indian market rates
- For castings: price depends on material and weight
- For motors: price depends on kW rating and voltage
- For seals: price depends on type, size, and plan
- Search for current vendor prices when possible
- Be realistic — not textbook prices, actual procurement prices
- Include machining, finishing, and quality testing costs
- For imported items, include customs + freight to India site"""


def claude_price_bom(bom_df, pump_specs, progress_callback=None):
    """
    Price every component in the BOM using Claude + web search.
    Groups similar items to reduce API calls.
    Returns enhanced DataFrame with price columns.
    """
    if bom_df is None or bom_df.empty:
        return bom_df

    specs_str = ""
    if isinstance(pump_specs, dict):
        # Only send essential specs, not the full extraction
        compact = {
            "pump": pump_specs.get("pump_label", ""),
            "type": pump_specs.get("type", ""),
            "flow_m3h": pump_specs.get("flow_m3h"),
            "head_m": pump_specs.get("head_m"),
            "motor_kw": pump_specs.get("motor_kw"),
            "fluid": pump_specs.get("fluid", ""),
            "temp_c": pump_specs.get("temp_c"),
        }
        specs_str = json.dumps(compact, default=str)
    else:
        specs_str = str(pump_specs)[:500]

    components = []
    for _, row in bom_df.iterrows():
        components.append({
            "no":        row.get("No", ""),
            "component": str(row.get("Component", "")),
            "moc":       str(row.get("MOC", "")),
            "qty":       str(row.get("Qty", "1")),
            "weight_kg": row.get("Weight_kg"),
        })

    # Larger batches = fewer API calls = less rate limit pressure
    batch_size = 15
    batches    = [components[i:i+batch_size]
                  for i in range(0, len(components), batch_size)]

    all_prices = []
    progress_labels = [
        "Accumulating pump assembly market data...",
        "Compiling rotating equipment pricing indices...",
        "Gathering bearing and seal vendor rates...",
        "Analysing structural component costs...",
        "Finalising piping and accessories pricing...",
        "Cross-referencing vendor quotations...",
        "Validating price consistency...",
    ]

    for batch_idx, batch in enumerate(batches):
        if progress_callback:
            pct = int(10 + (batch_idx / max(len(batches), 1)) * 80)
            label = progress_labels[min(batch_idx, len(progress_labels)-1)]
            progress_callback(pct, label)

        # Rate limit protection: wait between batches (30k tokens/min limit)
        if batch_idx > 0:
            time.sleep(25)

        batch_str = json.dumps(batch, default=str)
        prompt = f"""Price these pump components at Indian market rates (2025-26).

Pump: {specs_str}

Components:
{batch_str}

Return JSON array, one per component:
[{{"no":<num>,"unit_price_inr":<int>,"total_price_inr":<int>,"confidence":"high|medium|low","source":"brief source","notes":"brief"}}]

Rate guide: Motor ₹4-8k/kW, CS casting ₹200-280/kg, SS316 ₹700-900/kg, High chrome ₹900-1200/kg, Mech seal ₹40k-4L, Bearings ₹2-50k, Baseplate ₹100-140/kg.
JSON only."""

        raw = _call_claude(prompt, system=PRICE_SYSTEM,
                          use_search=True, max_tokens=2000)
        data = _parse_json(raw)

        if isinstance(data, list):
            all_prices.extend(data)
        elif isinstance(data, dict):
            all_prices.append(data)

    if progress_callback:
        progress_callback(92, "Compiling final cost report...")

    price_map = {}
    for p in all_prices:
        if isinstance(p, dict) and "no" in p:
            price_map[p["no"]] = p

    result_rows = []
    for _, row in bom_df.iterrows():
        rd = row.to_dict()
        no = row.get("No", 0)
        pr = price_map.get(no, {})

        unit_p  = int(pr.get("unit_price_inr", 0))
        total_p = int(pr.get("total_price_inr", unit_p))
        gst     = int(total_p * 0.18)

        rd["Unit_Price_INR"]   = unit_p
        rd["Total_Price_INR"]  = total_p
        rd["GST_18pct"]        = gst
        rd["Price_With_GST"]   = total_p + gst
        rd["Price_Confidence"] = str(pr.get("confidence", "low"))
        rd["Price_Source"]     = str(pr.get("source", "estimate"))
        rd["Price_Notes"]      = str(pr.get("notes", ""))
        result_rows.append(rd)

    return pd.DataFrame(result_rows)


def build_cost_summary(priced_df):
    """Build a cost summary from priced BOM."""
    if priced_df is None or priced_df.empty:
        return {}

    total_ex  = int(priced_df["Total_Price_INR"].sum())
    total_gst = int(priced_df["GST_18pct"].sum())
    total_inc = int(priced_df["Price_With_GST"].sum())

    sub_col = "Sub_Assembly" if "Sub_Assembly" in priced_df.columns else "Section"
    sub_totals = (priced_df.groupby(sub_col)["Total_Price_INR"]
                  .sum().sort_values(ascending=False).to_dict())

    top5 = (priced_df.nlargest(5, "Total_Price_INR")
            [["Component","Description","Total_Price_INR","Price_Confidence"]]
            .to_dict("records"))

    conf = priced_df["Price_Confidence"].value_counts().to_dict()

    return {
        "total_ex_gst":   total_ex,
        "total_gst":      total_gst,
        "total_incl_gst": total_inc,
        "sub_totals":     {k: int(v) for k, v in sub_totals.items()},
        "top5_drivers":   top5,
        "confidence":     conf,
        "component_count":len(priced_df),
        "note": ("Indicative market estimate for budget planning. "
                 "Actual prices subject to vendor quotation."),
    }


# ═══════════════════════════════════════════════════════════════════
# GROUPING (for display — uses section from Claude output)
# ═══════════════════════════════════════════════════════════════════

SECTION_ORDER = [
    "A. PUMP HYDRAULICS",
    "B. ROTATING ASSEMBLY",
    "C. BEARINGS & LUBRICATION",
    "D. SHAFT SEALING",
    "E. DRIVE & COUPLING",
    "F. MOTOR / DRIVER",
    "G. STRUCTURAL",
    "H. PIPING & NOZZLES",
    "I. FASTENERS & GASKETS",
    "J. INSTRUMENTATION",
    "K. ACOUSTIC & SAFETY",
    "L. COMPLETE ASSEMBLY",
]

def group_bom(bom_df):
    """Group BOM by section and sub-assembly. Returns [(section, sub, df)]."""
    if bom_df is None or bom_df.empty:
        return []

    sec_col = "Section" if "Section" in bom_df.columns else None
    sub_col = "Sub_Assembly" if "Sub_Assembly" in bom_df.columns else None

    if not sec_col:
        return [("ALL", "All Components", bom_df)]

    sec_order = {s: i for i, s in enumerate(SECTION_ORDER)}
    df = bom_df.copy()
    df["_ord"] = df[sec_col].apply(lambda s: sec_order.get(str(s).strip(), 99))
    df = df.sort_values("_ord").reset_index(drop=True)
    df["No"] = range(1, len(df)+1)

    result = []
    for sec in SECTION_ORDER:
        sec_rows = df[df[sec_col].str.strip() == sec]
        if sec_rows.empty:
            continue
        if sub_col:
            for sub in sec_rows[sub_col].unique():
                sub_rows = sec_rows[sec_rows[sub_col] == sub].copy()
                sub_rows = sub_rows.drop(columns=["_ord"], errors="ignore")
                result.append((sec, str(sub), sub_rows))
        else:
            sec_rows2 = sec_rows.drop(columns=["_ord"], errors="ignore")
            result.append((sec, sec, sec_rows2))

    other = df[~df[sec_col].str.strip().isin(SECTION_ORDER)]
    if not other.empty:
        other = other.drop(columns=["_ord"], errors="ignore")
        result.append(("Z. OTHER", "Other", other))

    return result


# ═══════════════════════════════════════════════════════════════════
# PDF EXTRACTION (still pdfplumber — free, local)
# ═══════════════════════════════════════════════════════════════════

def extract_pdf_text(file_bytes):
    """Extract text from PDF using pdfplumber."""
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(BytesIO(file_bytes)) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t:
                    pages.append(t)
        return "\n".join(pages), None
    except Exception as e:
        return "", str(e)


# ═══════════════════════════════════════════════════════════════════
# EXCEL EXPORT  *** BUG FIX: removed hfont() helper that conflicted
#               *** with openpyxl internals. Now uses Font() directly.
# ═══════════════════════════════════════════════════════════════════

def export_excel(bom_df, pump_specs, priced=False):
    """Export BOM to professional Excel with cover + grouped BOM + optional pricing."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb  = Workbook()
    thin = Side(style="thin", color="CCCCCC")
    bdr  = Border(left=thin, right=thin, top=thin, bottom=thin)

    # ── Cover ─────────────────────────────────────────────────────
    ws0 = wb.active
    ws0.title = "Cover"
    ws0.sheet_view.showGridLines = False
    ws0.column_dimensions["A"].width = 30
    ws0.column_dimensions["B"].width = 55

    ws0.merge_cells("A1:B1")
    title_cell = ws0["A1"]
    title_cell.value = "BILL OF MATERIALS"
    title_cell.font = Font(name="Arial", bold=True, size=18, color="FFFFFF")
    title_cell.fill = PatternFill("solid", fgColor="1F4E79")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws0.row_dimensions[1].height = 36

    specs = pump_specs if isinstance(pump_specs, dict) else {}
    info_items = []
    if isinstance(specs.get("pumps"), list) and specs["pumps"]:
        p = specs["pumps"][0]
        info_items = [
            ("Pump Model",       p.get("model", "—")),
            ("Manufacturer",     p.get("manufacturer", "—")),
            ("Type",             p.get("type", "—")),
            ("Tag Numbers",      p.get("tag_numbers", "—")),
            ("Flow (m³/h)",      p.get("flow_m3h", "—")),
            ("Head (m)",         p.get("head_m", "—")),
            ("Motor (kW)",       p.get("motor_kw", "—")),
            ("Shaft Power (kW)", p.get("shaft_power_kw", "—")),
            ("Speed (RPM)",      p.get("speed_rpm", "—")),
            ("Fluid",            p.get("fluid", "—")),
            ("Temperature (°C)", p.get("temp_c", "—")),
            ("Density (kg/m³)",  p.get("density_kgm3", "—")),
            ("Standard",         p.get("standard", "—")),
            ("Quantity",         p.get("quantity", "—")),
            ("Project",          specs.get("project", "—")),
        ]
    else:
        info_items = [
            ("Pump",    specs.get("pump_label", "—")),
            ("Flow",    specs.get("flow_m3h", "—")),
            ("Head",    specs.get("head_m", "—")),
            ("Motor",   specs.get("motor_kw", "—")),
            ("Fluid",   specs.get("fluid", "—")),
        ]

    r = 3
    for lbl, val in info_items:
        lbl_cell = ws0.cell(r, 1, lbl)
        lbl_cell.font = Font(name="Arial", bold=True, size=10)
        lbl_cell.fill = PatternFill("solid", fgColor="EEF2F7")
        lbl_cell.border = bdr

        val_cell = ws0.cell(r, 2, str(val) if val else "—")
        val_cell.font = Font(name="Arial", size=10)
        val_cell.border = bdr
        r += 1

    gen_cell_lbl = ws0.cell(r + 1, 1, "Generated")
    gen_cell_lbl.font = Font(name="Arial", bold=True, size=10)
    gen_cell_val = ws0.cell(r + 1, 2, pd.Timestamp.now().strftime("%d-%b-%Y %H:%M"))
    gen_cell_val.font = Font(name="Arial", size=10)

    # ── BOM Sheet ─────────────────────────────────────────────────
    ws1 = wb.create_sheet("BOM")
    ws1.sheet_view.showGridLines = False

    if priced and "Total_Price_INR" in bom_df.columns:
        cols = ["No", "Section", "Sub_Assembly", "Component", "Description", "MOC",
                "Qty", "Weight_kg", "Req_Type", "Unit_Price_INR", "Total_Price_INR",
                "GST_18pct", "Price_With_GST", "Price_Confidence", "Notes"]
    else:
        cols = ["No", "Section", "Sub_Assembly", "Component", "Description", "MOC",
                "Qty", "Weight_kg", "Req_Type", "Notes"]
    cols = [c for c in cols if c in bom_df.columns]

    widths = {
        "No": 5, "Section": 22, "Sub_Assembly": 20, "Component": 30,
        "Description": 40, "MOC": 25, "Qty": 8, "Weight_kg": 10,
        "Req_Type": 6, "Unit_Price_INR": 14, "Total_Price_INR": 14,
        "GST_18pct": 12, "Price_With_GST": 14, "Price_Confidence": 10, "Notes": 35,
    }

    # Title row
    ws1.merge_cells(f"A1:{get_column_letter(len(cols))}1")
    bom_title = ws1["A1"]
    bom_title.value = "BILL OF MATERIALS"
    bom_title.font = Font(name="Arial", bold=True, size=12, color="FFFFFF")
    bom_title.fill = PatternFill("solid", fgColor="1F4E79")
    bom_title.alignment = Alignment(horizontal="center")
    ws1.row_dimensions[1].height = 24

    # Header row
    r = 2
    for j, col in enumerate(cols):
        hdr = ws1.cell(r, j + 1, col.replace("_", " "))
        hdr.font = Font(name="Arial", bold=True, size=9, color="FFFFFF")
        hdr.fill = PatternFill("solid", fgColor="2E75B6")
        hdr.alignment = Alignment(horizontal="center", wrap_text=True)
        hdr.border = bdr
        ws1.column_dimensions[get_column_letter(j + 1)].width = widths.get(col, 14)
    ws1.row_dimensions[r].height = 26
    r += 1

    # Data rows
    alt_fill_1 = PatternFill("solid", fgColor="EEF4FB")
    alt_fill_2 = PatternFill("solid", fgColor="FFFFFF")

    for i, (_, row) in enumerate(bom_df.iterrows()):
        row_fill = alt_fill_1 if i % 2 == 0 else alt_fill_2
        for j, col in enumerate(cols):
            val = row.get(col, "")
            if pd.isna(val):
                val = ""
            if col in ("Unit_Price_INR", "Total_Price_INR", "GST_18pct", "Price_With_GST"):
                try:
                    if val and val != "":
                        val = f"₹{int(float(val)):,}"
                except (ValueError, TypeError):
                    pass
            cell = ws1.cell(r, j + 1, val)
            cell.font = Font(name="Arial", size=8)
            cell.fill = row_fill
            cell.border = bdr
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws1.row_dimensions[r].height = 16
        r += 1

    ws1.freeze_panes = "A3"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
