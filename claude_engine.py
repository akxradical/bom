"""
BOM Engine — Free Multi-LLM Edition
════════════════════════════════════
Zero dependency on Claude. Uses FREE LLM APIs for everything:
  - Gemini 2.5 Flash (primary) — best for datasheet reading (huge context)
  - Groq Llama 3.3 70B — fastest, good for JSON generation
  - Cerebras Llama 3.3 70B — highest quota (14,400 req/day)
  - Claude — OPTIONAL paid fallback (only if you have key + all free fail)

Cost per BOM: ₹0 (free tier) vs ₹11 with Claude.

Author: Ayush Kamle
"""

import json, re, time, os
import pandas as pd
from io import BytesIO

# ─────────────────────────────────────────────────────────────────
# MULTI-PROVIDER LLM CLIENT (FREE-FIRST)
# ─────────────────────────────────────────────────────────────────
# For SPEC EXTRACTION (needs big context window):
#   1. Gemini 2.5 Flash — 1M token context, perfect for datasheets
#   2. Groq / Cerebras — 128k context, good enough for most docs
#   3. Claude — only if ANTHROPIC_API_KEY is set and all above fail
#
# For BOM GENERATION (needs good JSON output):
#   1. Gemini 2.5 Flash — great structured output
#   2. Groq Llama 3.3 70B — fast JSON generation
#   3. Cerebras Llama 3.3 70B — reliable fallback
#   4. Claude — paid fallback
#
# For PRICING: should-cost model runs locally (no LLM needed for 90%+)
# ─────────────────────────────────────────────────────────────────

def _get_api_key(key_name):
    """Get API key from Streamlit secrets, return empty string if not set."""
    import streamlit as st
    return st.secrets.get(key_name, "")


def _call_openai_compatible(url, key, model, prompt, system="", max_tokens=4000):
    """Generic caller for OpenAI-compatible APIs (Groq, Cerebras, etc.)."""
    import urllib.request
    body = {
        "model": model,
        "messages": [],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    if system:
        body["messages"].append({"role": "system", "content": system})
    body["messages"].append({"role": "user", "content": prompt})

    data_bytes = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def _call_gemini(prompt, system="", max_tokens=8000):
    """Google Gemini 2.5 Flash. Free tier, 1M context window."""
    import urllib.request
    key = _get_api_key("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY not set")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    data_bytes = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data_bytes,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _call_groq(prompt, system="", max_tokens=4000):
    """Groq — Llama 3.3 70B. Free, very fast."""
    key = _get_api_key("GROQ_API_KEY")
    if not key:
        raise ValueError("GROQ_API_KEY not set")
    return _call_openai_compatible(
        "https://api.groq.com/openai/v1/chat/completions",
        key, "llama-3.3-70b-versatile", prompt, system, max_tokens)


def _call_cerebras(prompt, system="", max_tokens=4000):
    """Cerebras — Llama 3.3 70B. Free, 14400 req/day."""
    key = _get_api_key("CEREBRAS_API_KEY")
    if not key:
        raise ValueError("CEREBRAS_API_KEY not set")
    return _call_openai_compatible(
        "https://api.cerebras.ai/v1/chat/completions",
        key, "llama-3.3-70b", prompt, system, max_tokens)


def _call_claude(prompt, system="", max_tokens=4000):
    """Claude — OPTIONAL paid fallback. Only used if key exists + all free fail."""
    key = _get_api_key("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set — skipping Claude")

    import anthropic
    client = anthropic.Anthropic(api_key=key)
    kwargs = {
        "model":      "claude-sonnet-4-5",
        "max_tokens":  max_tokens,
        "messages":   [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(**kwargs)
            parts = []
            for block in resp.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
            return "\n".join(parts).strip()
        except Exception as e:
            if "429" in str(e) or "rate_limit" in str(e).lower():
                time.sleep(30 * (2 ** attempt))
            else:
                raise
    raise Exception("Claude rate limit exceeded.")


def _call_llm(prompt, system="", max_tokens=4000):
    """Universal LLM caller. Tries ALL providers, free-first.
    Returns (response_text, provider_name).
    
    Priority: Gemini → Groq → Cerebras → Claude (paid, optional)
    """
    providers = [
        ("Gemini",   _call_gemini),
        ("Groq",     _call_groq),
        ("Cerebras", _call_cerebras),
        ("Claude",   _call_claude),
    ]

    errors = []
    for name, fn in providers:
        try:
            result = fn(prompt, system=system, max_tokens=max_tokens)
            if result and len(result.strip()) > 10:
                return result, name
        except Exception as e:
            err_msg = str(e)[:120]
            # Don't log "not set" as an error — it's expected
            if "not set" not in err_msg.lower():
                errors.append(f"{name}: {err_msg}")
            continue

    # Build helpful error message
    configured = []
    for key_name, label in [("GEMINI_API_KEY","Gemini"), ("GROQ_API_KEY","Groq"),
                             ("CEREBRAS_API_KEY","Cerebras"), ("ANTHROPIC_API_KEY","Claude")]:
        if _get_api_key(key_name):
            configured.append(label)

    if not configured:
        raise Exception(
            "No LLM API keys configured!\n"
            "Add at least ONE to .streamlit/secrets.toml:\n"
            "  GEMINI_API_KEY = \"AIza...\"    (FREE — https://aistudio.google.com)\n"
            "  GROQ_API_KEY = \"gsk_...\"      (FREE — https://console.groq.com)\n"
            "  CEREBRAS_API_KEY = \"csk-...\"  (FREE — https://cloud.cerebras.ai)\n"
            "  ANTHROPIC_API_KEY = \"sk-...\"  (PAID — https://console.anthropic.com)"
        )
    else:
        raise Exception(
            f"All configured providers failed ({', '.join(configured)}):\n" +
            "\n".join(errors) +
            "\nTry again in a minute, or add more API keys."
        )


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

    raw, provider = _call_llm(prompt, system=SPEC_SYSTEM, max_tokens=6000)
    data = _parse_json(raw)
    if data and isinstance(data, dict):
        data["_llm_provider"] = provider
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

    raw, provider = _call_llm(prompt, system=BOM_SYSTEM, max_tokens=8000)
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
# STEP 3 — SHOULD-COST MODEL
# ═══════════════════════════════════════════════════════════════════
# Purpose: Reverse-engineer the SUPPLIER'S manufacturing cost.
# This is NOT "what we pay" — it's "what it costs HIM to make it."
#
# Cost structure for every component:
#   Raw Material Cost  = net weight × material ₹/kg (grade-specific)
#   Machining Cost     = complexity factor × raw material cost
#   Surface Treatment  = painting / plating / heat treatment
#   ───────────────────────────────────────────
#   Manufactured Cost  = raw + machining + surface
#
#   Bought-out items   = market price (web lookup) + supplier markup 5-6%
#   (motors, bearings, seals, instruments — supplier doesn't make these)
#
# This model works for ANY engineered product: pumps, valves, 
# heat exchangers, gearboxes, vessels, skids, etc.
# ═══════════════════════════════════════════════════════════════════

# ── RAW MATERIAL RATES (₹/kg) ────────────────────────────────────
# These are RAW MATERIAL costs — what the foundry/forge pays for metal.
# NOT finished price. Machining is added separately.
# Sources: Metal Bulletin India, LME India, SAIL price lists, IndiaMart
RAW_MATERIAL_RATES = {
    # Carbon Steel
    "A216 WCB":       130,   # CS casting raw
    "WCB":            130,
    "SA 216":         130,
    "IS 2062":        72,    # Structural plate/section
    "MS":             72,    # Mild steel plate
    "MILD STEEL":     72,
    "CARBON STEEL":   85,
    "A105":           95,    # CS forging
    # Alloy Steel
    "EN-19":          180,   # Alloy steel bar (Cr-Mo)
    "EN19":           180,
    "EN-24":          200,   # Ni-Cr-Mo steel bar
    "EN24":           200,
    "SS410":          210,   # Martensitic SS bar
    "4140":           175,
    "4340":           210,
    # Stainless Steel
    "SS304":          280,   # Austenitic SS
    "CF8":            320,   # SS304 casting
    "SS316":          340,   # SS316 bar/plate
    "CF8M":           380,   # SS316 casting
    "SS316L":         350,
    "DUPLEX":         520,   # Duplex SS (2205)
    "CD4MCU":         550,   # Duplex casting
    "SUPER DUPLEX":   680,
    # High Alloy
    "MONEL":          2200,
    "INCONEL":        3200,
    "HASTELLOY":      3800,
    "TITANIUM":       3500,
    # Chrome Iron
    "A532":           280,   # High chrome white iron casting (raw)
    "HIGH CHROME":    280,
    "ASTM A487":      220,   # 12% chrome steel casting
    "12% CHROME":     220,
    # Cast Iron
    "GREY CAST IRON": 65,    # GCI raw
    "CAST IRON":      65,
    "CI":             65,
    "SG IRON":        85,    # Spheroidal graphite (ductile)
    "DUCTILE IRON":   85,
    # Special
    "A193 B7":        250,   # High-strength alloy bolting bar
    "A197":           150,   # Nut material
    "RUBBER":         180,   # Lined/moulded rubber
    "PTFE":           800,
    "EPDM":           350,
}

# ── MACHINING COMPLEXITY MULTIPLIERS ─────────────────────────────
# Applied on top of raw material cost.
# Factor = additional cost as % of raw material cost.
# E.g. 0.8 means machining costs 80% of raw material cost.
MACHINING_FACTORS = {
    # Heavy castings — pattern + moulding + rough machining + finish
    "pump_casing":       0.90,   # Complex casting, internal passages, flange faces
    "impeller":          1.20,   # Complex curved vanes, balancing, close tolerances
    "wear_ring":         0.60,   # Simple turned ring, interference fit
    "volute_liner":      0.70,
    "diffuser":          1.00,
    # Rotating parts — forging/bar + turning + grinding
    "shaft":             0.85,   # Multi-diameter turning, keyways, threading
    "shaft_sleeve":      0.50,   # Simple OD/ID turning
    "coupling_half":     0.55,
    "impeller_nut":      0.30,
    # Housings — casting + boring + face machining
    "bearing_housing":   0.65,   # Bore machining, face finishing, oil channels
    "seal_housing":      0.70,
    "gland_plate":       0.45,   # Flat plate, bolt holes, bore
    "stuffing_box":      0.60,
    # Fabrication — cutting + welding + machining
    "baseplate":         0.40,   # Cut, weld, drill, machine pads
    "base_frame":        0.40,
    "skid":              0.35,
    # Simple machined parts
    "key":               0.25,
    "spacer":            0.20,
    "bolt":              0.30,
    "nut":               0.20,
    "flange":            0.45,   # Forging + drilling + face machining
    "gasket":            0.15,   # Die cut or spiral wound
    # Default
    "default_casting":   0.70,
    "default_forging":   0.55,
    "default_machined":  0.50,
    "default_fabricated":0.35,
    "default":           0.50,
}

# ── SURFACE TREATMENT COSTS (₹/kg) ──────────────────────────────
SURFACE_TREATMENT = {
    "painting":          8,     # Standard industrial primer + topcoat
    "hot_dip_galv":      18,    # Hot-dip galvanizing
    "electroplating":    35,    # Chrome/nickel plating
    "heat_treatment":    25,    # Normalizing/stress relieving
    "shot_blasting":     5,     # Surface prep
    "none":              0,
}

# ── BOUGHT-OUT ITEM REFERENCE PRICES (₹) ────────────────────────
# These are MARKET prices for items the supplier doesn't manufacture.
# The supplier just buys these and adds 5-6% margin.
# Sources: IndiaMart, TradeIndia, IEEMA motor price index
BOUGHT_OUT_PRICES = {
    # Motors — ₹/kW (OEM price to pump vendor)
    "motor_per_kw_ht_690v":    3800,    # HT/MV motor ≥200kW
    "motor_per_kw_lt_415v":    4800,    # LT motor <200kW
    "motor_per_kw_lt_small":   6500,    # Small LT motor <30kW (higher ₹/kW)
    # Bearings (OEM price)
    "skf_6200_series":         800,     # Small deep groove
    "skf_6300_series":         1800,    # Medium deep groove
    "skf_7300_series":         3500,    # Angular contact medium
    "timken_taper_medium":     5500,    # Taper roller 80-120mm bore
    "timken_taper_large":      9000,    # Taper roller >120mm bore
    "skf_spherical_large":     15000,   # Spherical roller >150mm
    # Mechanical Seals (OEM price, supplier buys from EagleBurgmann/John Crane)
    "mech_seal_single_small":  22000,   # Single, <50mm shaft
    "mech_seal_single_medium": 55000,   # Single, 50-80mm shaft
    "mech_seal_single_large":  110000,  # Single, >80mm shaft
    "mech_seal_double":        180000,  # Double with barrier fluid
    "mech_seal_cartridge":     140000,  # Cartridge type
    "seal_sld_device":         25000,   # Synthetic Lubricating Device add-on
    # Coupling (OEM price)
    "coupling_disc_small":     8000,
    "coupling_disc_large":     28000,
    "coupling_gear":           45000,
    # V-belts
    "vbelt_set_small":         4000,    # <50kW
    "vbelt_set_medium":        8000,    # 50-200kW
    "vbelt_set_large":         14000,   # >200kW
    "vbelt_guard":             3500,
    # Pulleys
    "pulley_small":            3000,
    "pulley_large":            8000,
    # Instrumentation
    "rtd_pt100":               1200,
    "dial_thermometer":        800,
    "pressure_gauge":          1500,
    "vibration_switch":        4500,
    # Flanges & Gaskets (bought-out)
    "companion_flange_4inch":  1200,
    "companion_flange_6inch":  2200,
    "companion_flange_8inch":  3500,
    "companion_flange_10inch": 5500,
    "companion_flange_12inch": 7500,
    "gasket_spiral_wound_4":   400,
    "gasket_spiral_wound_6":   550,
    "gasket_spiral_wound_8":   700,
    "gasket_spiral_wound_10":  900,
    "gasket_compressed":       200,
    # Foundation hardware
    "foundation_bolt_m24":     180,
    "foundation_bolt_m30":     280,
    "foundation_bolt_m36":     400,
    # First fill
    "grease_first_fill_kg":    350,     # per kg
    "oil_first_fill_litre":    250,     # per litre
}

# Supplier markup on bought-out items
SUPPLIER_MARKUP = 0.06  # 6%

# ── REFERENCE URLS for price verification ────────────────────────
PRICE_REFERENCE_URLS = {
    "motors":    "https://www.indiamart.com/search.mp?ss=industrial+electric+motor",
    "bearings":  "https://www.indiamart.com/search.mp?ss=skf+timken+bearing",
    "seals":     "https://www.indiamart.com/search.mp?ss=mechanical+seal+pump",
    "castings":  "https://www.indiamart.com/search.mp?ss=steel+casting+price+per+kg",
    "flanges":   "https://www.indiamart.com/search.mp?ss=ansi+flanges",
    "metals":    "https://www.metalmarket.in",
    "steel":     "https://www.steelmint.com",
    "lme":       "https://www.westmetall.com/en/markdaten.php",
}


def _classify_component(component_name, moc="", description=""):
    """Classify a component as MANUFACTURED or BOUGHT-OUT.
    Returns (category, machining_key, surface_treatment)."""
    comp = (component_name or "").upper()
    desc = (description or "").upper()
    moc_u = (moc or "").upper()

    # ── BOUGHT-OUT (supplier doesn't make, just procures) ─────────
    if any(x in comp for x in ["MOTOR", "ELECTRIC MOTOR", "SCIM", "SQUIRREL CAGE"]):
        if not any(x in comp for x in ["BOLT", "MOUNT", "BASE", "PULLEY", "SIDE", "BRACKET"]):
            return "bought_out", "motor", "none"

    if any(x in comp for x in ["BEARING"]) and "HOUSING" not in comp:
        return "bought_out", "bearing", "none"

    if any(x in comp for x in ["MECHANICAL SEAL", "MECH SEAL", "CARTRIDGE SEAL"]):
        return "bought_out", "seal", "none"

    if "SLD" in comp and ("DEVICE" in comp or "SYNTHETIC" in comp):
        return "bought_out", "seal_accessory", "none"

    if any(x in comp for x in ["RTD", "THERMOCOUPLE", "THERMOMETER", "PRESSURE GAUGE",
                                "VIBRATION SWITCH", "TRANSMITTER"]):
        return "bought_out", "instrument", "none"

    if "V-BELT" in comp or "V BELT" in comp or "VBELT" in comp:
        return "bought_out", "vbelt", "none"

    if "COMPANION FLANGE" in comp or "COUNTER FLANGE" in comp:
        return "bought_out", "flange_set", "none"

    if "GASKET" in comp:
        return "bought_out", "gasket", "none"

    if "FOUNDATION" in comp and "BOLT" in comp:
        return "bought_out", "foundation_bolt", "none"

    if "GREASE" in comp or "LUBRICANT" in comp or "FIRST FILL" in comp:
        return "bought_out", "lubricant", "none"

    if "GUARD" in comp and ("BELT" in comp or "COUPLING" in comp):
        return "bought_out", "guard", "none"

    # ── MANUFACTURED (supplier makes in his shop) ─────────────────
    if "CASING" in comp and "BOLT" not in comp:
        return "manufactured", "pump_casing", "painting"

    if "IMPELLER" in comp and "WEAR" not in comp and "NUT" not in comp:
        return "manufactured", "impeller", "none"

    if "WEAR RING" in comp:
        return "manufactured", "wear_ring", "none"

    if "SHAFT" in comp and "SLEEVE" not in comp and "KEY" not in comp:
        return "manufactured", "shaft", "none"

    if "SLEEVE" in comp:
        return "manufactured", "shaft_sleeve", "none"

    if "GLAND" in comp or "SEAL PLATE" in comp:
        return "manufactured", "gland_plate", "none"

    if "BEARING HOUSING" in comp or "PEDESTAL" in comp:
        return "manufactured", "bearing_housing", "painting"

    if "BASE" in comp or "BASEPLATE" in comp or "FRAME" in comp or "SKID" in comp:
        return "manufactured", "baseplate", "painting"

    if "PULLEY" in comp or "SHEAVE" in comp:
        return "manufactured", "coupling_half", "painting"

    if "KEY" in comp:
        return "manufactured", "key", "none"

    if "BOLT" in comp or "NUT" in comp or "STUD" in comp or "FASTENER" in comp:
        return "manufactured", "bolt", "none"

    if "NOZZLE" in comp:
        return "manufactured", "flange", "painting"  # integral, priced as casting

    if "ANCHOR" in comp:
        return "bought_out", "foundation_bolt", "hot_dip_galv"

    # ── COMPLIANCE / ASSEMBLY (no separate cost) ──────────────────
    if any(x in comp for x in ["COMPLETE ASSEMBLY", "NOISE LEVEL", "VIBRATION",
                                "COMPLIANCE", "PROVISION", "SURFACE PREP",
                                "PERFORMANCE TEST"]):
        return "compliance", "none", "none"

    # Default: manufactured
    return "manufactured", "default", "painting"


def _get_raw_material_rate(moc):
    """Find the ₹/kg rate for a given MOC string."""
    moc_upper = (moc or "").upper()
    for mat_key, rate in RAW_MATERIAL_RATES.items():
        if mat_key in moc_upper:
            return rate, mat_key
    return None, None


def _price_manufactured(comp_name, moc, weight_kg, qty_str, machining_key, surface_key):
    """Should-cost for a manufactured component.
    Returns dict with cost breakdown."""
    result = {
        "raw_material_cost": 0,
        "machining_cost": 0,
        "surface_cost": 0,
        "total_cost": 0,
        "confidence": "low",
        "breakdown": "",
    }

    if not weight_kg or weight_kg <= 0:
        return result

    # Parse qty
    try:
        q = int(re.search(r'\d+', str(qty_str)).group()) if re.search(r'\d+', str(qty_str)) else 1
    except Exception:
        q = 1

    # Gross weight = net weight × 1.08 (8% machining allowance on castings)
    is_casting = machining_key in ("pump_casing", "impeller", "wear_ring", "bearing_housing",
                                     "gland_plate", "default_casting")
    gross_weight = weight_kg * 1.08 if is_casting else weight_kg

    # Raw material cost
    rate, matched_mat = _get_raw_material_rate(moc)
    if rate is None:
        rate = 100  # conservative fallback
        matched_mat = "unknown (₹100/kg default)"
        result["confidence"] = "low"
    else:
        result["confidence"] = "high"

    raw_cost = rate * gross_weight * q
    result["raw_material_cost"] = int(raw_cost)

    # Machining cost
    mach_factor = MACHINING_FACTORS.get(machining_key, MACHINING_FACTORS["default"])
    mach_cost = raw_cost * mach_factor
    result["machining_cost"] = int(mach_cost)

    # Surface treatment
    surf_rate = SURFACE_TREATMENT.get(surface_key, 0)
    surf_cost = surf_rate * weight_kg * q
    result["surface_cost"] = int(surf_cost)

    # Total
    total = raw_cost + mach_cost + surf_cost
    result["total_cost"] = int(total)
    result["breakdown"] = (
        f"Raw: ₹{rate}/kg × {gross_weight:.0f}kg = ₹{int(raw_cost):,} ({matched_mat}) | "
        f"Machining: ×{mach_factor} = ₹{int(mach_cost):,} | "
        f"Surface: ₹{int(surf_cost):,}"
    )
    return result


def _price_bought_out(comp_name, moc, weight_kg, qty_str, sub_type, pump_specs):
    """Should-cost for bought-out items.
    Returns dict with cost breakdown."""
    comp = (comp_name or "").upper()
    pump = pump_specs if isinstance(pump_specs, dict) else {}
    result = {
        "raw_material_cost": 0,    # = market price for bought-out
        "machining_cost": 0,       # = 0 for bought-out
        "surface_cost": 0,         # = supplier markup
        "total_cost": 0,
        "confidence": "medium",
        "breakdown": "",
    }

    try:
        q = int(re.search(r'\d+', str(qty_str)).group()) if re.search(r'\d+', str(qty_str)) else 1
    except Exception:
        q = 1

    market_price = 0
    source = ""

    # ── MOTOR ──
    if sub_type == "motor":
        kw = pump.get("motor_kw") or 0
        try:
            kw = float(kw)
        except (ValueError, TypeError):
            kw = 0
        voltage = str((pump.get("motor", {}) or {}).get("voltage_v", "415V"))

        if kw >= 200 or "690" in voltage:
            rate = BOUGHT_OUT_PRICES["motor_per_kw_ht_690v"]
        elif kw >= 30:
            rate = BOUGHT_OUT_PRICES["motor_per_kw_lt_415v"]
        else:
            rate = BOUGHT_OUT_PRICES["motor_per_kw_lt_small"]

        market_price = int(kw * rate)
        source = f"₹{rate}/kW × {kw}kW ({voltage})"
        result["confidence"] = "high"

    # ── BEARING ──
    elif sub_type == "bearing":
        if "TAPER" in comp or "TAPERED" in comp:
            if weight_kg and weight_kg > 5:
                market_price = BOUGHT_OUT_PRICES["timken_taper_large"]
            else:
                market_price = BOUGHT_OUT_PRICES["timken_taper_medium"]
        elif "SPHERICAL" in comp:
            market_price = BOUGHT_OUT_PRICES["skf_spherical_large"]
        elif "ANGULAR" in comp:
            market_price = BOUGHT_OUT_PRICES["skf_7300_series"]
        else:
            market_price = BOUGHT_OUT_PRICES["skf_6300_series"]
        source = "OEM bearing price"

    # ── MECHANICAL SEAL ──
    elif sub_type == "seal":
        if "DOUBLE" in comp:
            market_price = BOUGHT_OUT_PRICES["mech_seal_double"]
        elif "CARTRIDGE" in comp:
            market_price = BOUGHT_OUT_PRICES["mech_seal_cartridge"]
        elif any(x in comp for x in ["SLD", "PLAN 62", "LARGE"]):
            market_price = BOUGHT_OUT_PRICES["mech_seal_single_large"]
        elif "MEDIUM" in comp or (weight_kg and weight_kg > 10):
            market_price = BOUGHT_OUT_PRICES["mech_seal_single_medium"]
        else:
            market_price = BOUGHT_OUT_PRICES["mech_seal_single_small"]
        source = "OEM seal price (EagleBurgmann/John Crane class)"

    elif sub_type == "seal_accessory":
        market_price = BOUGHT_OUT_PRICES["seal_sld_device"]
        source = "SLD device"

    # ── V-BELT ──
    elif sub_type == "vbelt":
        kw = pump.get("motor_kw") or 0
        try:
            kw = float(kw)
        except Exception:
            kw = 100
        if kw > 200:
            market_price = BOUGHT_OUT_PRICES["vbelt_set_large"]
        elif kw > 50:
            market_price = BOUGHT_OUT_PRICES["vbelt_set_medium"]
        else:
            market_price = BOUGHT_OUT_PRICES["vbelt_set_small"]
        source = f"V-belt set for {kw}kW"

    # ── COMPANION FLANGE SET ──
    elif sub_type == "flange_set":
        if "200" in comp or "8" in comp or "SUCTION" in comp:
            market_price = BOUGHT_OUT_PRICES["companion_flange_8inch"]
        elif "150" in comp or "6" in comp or "DISCHARGE" in comp:
            market_price = BOUGHT_OUT_PRICES["companion_flange_6inch"]
        elif "250" in comp or "10" in comp:
            market_price = BOUGHT_OUT_PRICES["companion_flange_10inch"]
        else:
            market_price = BOUGHT_OUT_PRICES["companion_flange_6inch"]
        source = "Flange + gasket + bolt set"

    # ── GASKET ──
    elif sub_type == "gasket":
        if "SPIRAL" in comp:
            market_price = BOUGHT_OUT_PRICES["gasket_spiral_wound_8"]
        else:
            market_price = BOUGHT_OUT_PRICES["gasket_compressed"]
        source = "Standard gasket"

    # ── FOUNDATION BOLT ──
    elif sub_type == "foundation_bolt":
        count = 8  # typical
        if q > 1:
            count = q
        market_price = BOUGHT_OUT_PRICES["foundation_bolt_m30"] * count
        source = f"{count}× M30 foundation bolts"

    # ── INSTRUMENT ──
    elif sub_type == "instrument":
        if "RTD" in comp:
            market_price = BOUGHT_OUT_PRICES["rtd_pt100"]
        elif "THERMO" in comp:
            market_price = BOUGHT_OUT_PRICES["dial_thermometer"]
        elif "PRESSURE" in comp:
            market_price = BOUGHT_OUT_PRICES["pressure_gauge"]
        elif "VIBRATION" in comp:
            market_price = BOUGHT_OUT_PRICES["vibration_switch"]
        else:
            market_price = 2000
        source = "Standard instrument"

    # ── LUBRICANT ──
    elif sub_type == "lubricant":
        market_price = BOUGHT_OUT_PRICES["grease_first_fill_kg"] * max(weight_kg or 2, 2)
        source = "First fill grease/oil"

    # ── GUARD ──
    elif sub_type == "guard":
        market_price = BOUGHT_OUT_PRICES["vbelt_guard"]
        source = "Belt/coupling guard"

    else:
        market_price = 5000
        result["confidence"] = "low"
        source = "Generic bought-out estimate"

    market_price = market_price * q
    supplier_markup_amt = int(market_price * SUPPLIER_MARKUP)

    result["raw_material_cost"] = int(market_price)  # "raw" = OEM market price
    result["surface_cost"] = supplier_markup_amt      # "surface" = supplier's margin
    result["total_cost"] = int(market_price + supplier_markup_amt)
    result["breakdown"] = f"Market: ₹{int(market_price):,} + Supplier markup {SUPPLIER_MARKUP*100:.0f}%: ₹{supplier_markup_amt:,} | {source}"

    return result


def claude_price_bom(bom_df, pump_specs, progress_callback=None):
    """
    SHOULD-COST MODEL: Reverse-engineer the supplier's manufacturing cost.
    
    For manufactured items:  Raw Material + Machining + Surface Treatment
    For bought-out items:    Market Price + Supplier Markup (5-6%)
    
    Uses free LLMs only for items that can't be classified locally.
    Claude is the absolute last fallback.
    """
    if bom_df is None or bom_df.empty:
        return bom_df

    pump = pump_specs if isinstance(pump_specs, dict) else {}

    if progress_callback:
        progress_callback(10, "Classifying components: manufactured vs bought-out...")

    result_rows = []
    unpriced = []

    for _, row in bom_df.iterrows():
        rd = row.to_dict()
        comp_name = str(row.get("Component", ""))
        moc = str(row.get("MOC", ""))
        desc = str(row.get("Description", ""))
        weight = row.get("Weight_kg")
        qty_str = str(row.get("Qty", "1"))
        no = row.get("No", 0)

        category, sub_type, surface = _classify_component(comp_name, moc, desc)

        if category == "manufactured":
            cost = _price_manufactured(comp_name, moc, weight, qty_str, sub_type, surface)
        elif category == "bought_out":
            cost = _price_bought_out(comp_name, moc, weight, qty_str, sub_type, pump)
        elif category == "compliance":
            cost = {
                "raw_material_cost": 0, "machining_cost": 0, "surface_cost": 0,
                "total_cost": 0, "confidence": "high",
                "breakdown": "Compliance/assembly item — no separate cost",
            }
        else:
            cost = None

        if cost and (cost["total_cost"] > 0 or cost["confidence"] == "high"):
            total = cost["total_cost"]
            gst = int(total * 0.18)
            rd["Raw_Material_INR"]   = cost["raw_material_cost"]
            rd["Machining_INR"]      = cost["machining_cost"]
            rd["Surface_INR"]        = cost["surface_cost"]
            rd["Unit_Price_INR"]     = total
            rd["Total_Price_INR"]    = total
            rd["GST_18pct"]          = gst
            rd["Price_With_GST"]     = total + gst
            rd["Price_Confidence"]   = cost["confidence"]
            rd["Price_Source"]       = cost["breakdown"]
            rd["Price_Notes"]        = f"Category: {category}"
            rd["Component_Type"]     = category
            result_rows.append(rd)
        elif cost and cost["total_cost"] == 0 and category == "compliance":
            rd["Raw_Material_INR"]   = 0
            rd["Machining_INR"]      = 0
            rd["Surface_INR"]        = 0
            rd["Unit_Price_INR"]     = 0
            rd["Total_Price_INR"]    = 0
            rd["GST_18pct"]          = 0
            rd["Price_With_GST"]     = 0
            rd["Price_Confidence"]   = "high"
            rd["Price_Source"]       = cost["breakdown"]
            rd["Price_Notes"]        = "No separate cost"
            rd["Component_Type"]     = "compliance"
            result_rows.append(rd)
        else:
            unpriced.append((rd, no))

    if progress_callback:
        n_priced = len(result_rows)
        progress_callback(60, f"Should-costed {n_priced}/{len(bom_df)} locally. LLM for {len(unpriced)} remaining...")

    # Use free LLM for remaining unpriced
    if unpriced:
        unpriced_list = [{"no": no, "component": rd.get("Component",""),
                          "moc": rd.get("MOC",""), "qty": rd.get("Qty","1"),
                          "weight_kg": rd.get("Weight_kg")}
                         for rd, no in unpriced]

        prompt = f"""Estimate the MANUFACTURING COST (not selling price) for these components.
Break down each into: raw_material_cost (₹/kg × weight), machining_cost, total.
Indian rates 2025-26. Conservative estimates.

Components: {json.dumps(unpriced_list, default=str)}

Return JSON array:
[{{"no":<n>,"raw_material_cost":<int>,"machining_cost":<int>,"total_cost":<int>,"confidence":"medium","breakdown":"brief"}}]
JSON only."""

        try:
            raw, provider = _call_llm(prompt, max_tokens=2000)
            if progress_callback:
                progress_callback(80, f"Got costs from {provider}...")
            llm_prices = _parse_json(raw)
            price_map = {}
            if isinstance(llm_prices, list):
                for p in llm_prices:
                    if isinstance(p, dict) and "no" in p:
                        price_map[p["no"]] = p

            for rd, no in unpriced:
                pr = price_map.get(no, {})
                raw_mat = int(pr.get("raw_material_cost", 0))
                mach    = int(pr.get("machining_cost", 0))
                total   = int(pr.get("total_cost", raw_mat + mach))
                gst     = int(total * 0.18)
                rd["Raw_Material_INR"]  = raw_mat
                rd["Machining_INR"]     = mach
                rd["Surface_INR"]       = 0
                rd["Unit_Price_INR"]    = total
                rd["Total_Price_INR"]   = total
                rd["GST_18pct"]         = gst
                rd["Price_With_GST"]    = total + gst
                rd["Price_Confidence"]  = str(pr.get("confidence", "low"))
                rd["Price_Source"]      = f"{provider}: {str(pr.get('breakdown', ''))}"
                rd["Price_Notes"]       = "LLM estimate"
                rd["Component_Type"]    = "unknown"
                result_rows.append(rd)
        except Exception as e:
            for rd, no in unpriced:
                rd.update({"Raw_Material_INR":0,"Machining_INR":0,"Surface_INR":0,
                           "Unit_Price_INR":0,"Total_Price_INR":0,"GST_18pct":0,
                           "Price_With_GST":0,"Price_Confidence":"none",
                           "Price_Source":f"Failed: {str(e)[:50]}",
                           "Price_Notes":"Manual costing required","Component_Type":"unknown"})
                result_rows.append(rd)

    if progress_callback:
        progress_callback(92, "Compiling should-cost report...")

    return pd.DataFrame(result_rows)


def build_cost_summary(priced_df):
    """Build a should-cost summary from priced BOM."""
    if priced_df is None or priced_df.empty:
        return {}

    total_raw  = int(priced_df.get("Raw_Material_INR", pd.Series([0])).sum())
    total_mach = int(priced_df.get("Machining_INR", pd.Series([0])).sum())
    total_surf = int(priced_df.get("Surface_INR", pd.Series([0])).sum())
    total_ex   = int(priced_df["Total_Price_INR"].sum())
    total_gst  = int(priced_df["GST_18pct"].sum())
    total_inc  = int(priced_df["Price_With_GST"].sum())

    sub_col = "Sub_Assembly" if "Sub_Assembly" in priced_df.columns else "Section"
    sub_totals = (priced_df.groupby(sub_col)["Total_Price_INR"]
                  .sum().sort_values(ascending=False).to_dict())

    top5 = (priced_df.nlargest(5, "Total_Price_INR")
            [["Component","Description","Total_Price_INR","Price_Confidence"]]
            .to_dict("records"))

    conf = priced_df["Price_Confidence"].value_counts().to_dict()

    # Count manufactured vs bought-out
    type_col = "Component_Type" if "Component_Type" in priced_df.columns else None
    type_split = priced_df[type_col].value_counts().to_dict() if type_col else {}

    return {
        "total_raw_material": total_raw,
        "total_machining":    total_mach,
        "total_surface":      total_surf,
        "total_ex_gst":       total_ex,
        "total_gst":          total_gst,
        "total_incl_gst":     total_inc,
        "sub_totals":         {k: int(v) for k, v in sub_totals.items()},
        "top5_drivers":       top5,
        "confidence":         conf,
        "component_count":    len(priced_df),
        "type_split":         type_split,
        "note": ("SHOULD-COST estimate: supplier's manufacturing cost, not selling price. "
                 "Use for procurement negotiation. Verify with vendor quotation."),
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
