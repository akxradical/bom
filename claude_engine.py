"""
BOM Engine v5.0 — Should-Cost Model for Engineered Products
════════════════════════════════════════════════════════════
Flow: PDF → Extract Specs → Generate BOM → Should-Cost Pricing

1. EXTRACT: Claude/LLM reads datasheet PDF, outputs structured JSON specs
2. BOM:     Claude/LLM generates 25-35 component BOM from specs  
3. PRICE:   Claude + web_search finds live rates for each MOC,
            adds machining/labor → gives RAW manufacturing cost
            (no supplier overhead, no margin — just true cost)

Author: Ayush Kamle | Zetwerk CPT Category 2
"""

import json, re, time
import pandas as pd
from io import BytesIO

# ═══════════════════════════════════════════════════════════════════
# LLM PROVIDERS — Claude primary, free LLMs as fallback
# ═══════════════════════════════════════════════════════════════════

def _get_key(name):
    import streamlit as st
    return st.secrets.get(name, "")


def _http_post(url, headers, body, timeout=90, retries=2):
    import urllib.request, urllib.error
    raw = json.dumps(body).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=raw, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < retries - 1:
                time.sleep(10 * (i + 1)); continue
            try: b = e.read().decode()[:300]
            except: b = str(e.reason)
            raise Exception(f"HTTP {e.code}: {b}")
        except Exception as e:
            if i < retries - 1: time.sleep(5); continue
            raise


def _oai(url, key, model, prompt, system="", mt=4000):
    msgs = []
    if system: msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    d = _http_post(url, {"Content-Type": "application/json",
        "Authorization": f"Bearer {key}"},
        {"model": model, "messages": msgs, "max_tokens": mt, "temperature": 0.1})
    return d["choices"][0]["message"]["content"].strip()


def _gemini(p, s="", mt=8000):
    k = _get_key("GEMINI_API_KEY")
    if not k: raise ValueError("no key")
    b = {"contents": [{"parts": [{"text": p}]}],
         "generationConfig": {"maxOutputTokens": mt, "temperature": 0.1}}
    if s: b["systemInstruction"] = {"parts": [{"text": s}]}
    d = _http_post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={k}",
                   {"Content-Type": "application/json"}, b)
    return d["candidates"][0]["content"]["parts"][0]["text"].strip()


def _groq(p, s="", mt=4000):
    k = _get_key("GROQ_API_KEY")
    if not k: raise ValueError("no key")
    for m in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
        try: return _oai("https://api.groq.com/openai/v1/chat/completions", k, m, p, s, mt)
        except: continue
    raise Exception("Groq failed")


def _mistral(p, s="", mt=4000):
    k = _get_key("MISTRAL_API_KEY")
    if not k: raise ValueError("no key")
    return _oai("https://api.mistral.ai/v1/chat/completions", k, "mistral-large-latest", p, s, mt)


def _openrouter(p, s="", mt=4000):
    k = _get_key("OPENROUTER_API_KEY")
    if not k: raise ValueError("no key")
    for m in ["meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-chat-v3-0324:free"]:
        try:
            msgs = []
            if s: msgs.append({"role": "system", "content": s})
            msgs.append({"role": "user", "content": p})
            d = _http_post("https://openrouter.ai/api/v1/chat/completions",
                {"Content-Type": "application/json", "Authorization": f"Bearer {k}",
                 "HTTP-Referer": "https://pumpbom.streamlit.app"},
                {"model": m, "messages": msgs, "max_tokens": mt, "temperature": 0.1})
            r = d["choices"][0]["message"]["content"].strip()
            if r and len(r) > 10: return r
        except: continue
    raise Exception("OpenRouter failed")


def _cerebras(p, s="", mt=4000):
    k = _get_key("CEREBRAS_API_KEY")
    if not k: raise ValueError("no key")
    for m in ["gpt-oss-120b", "llama3.1-8b"]:
        try: return _oai("https://api.cerebras.ai/v1/chat/completions", k, m, p, s, min(mt, 8000))
        except: continue
    raise Exception("Cerebras failed")


_PROVIDERS = [
    ("Gemini", _gemini), ("Groq", _groq), ("Mistral", _mistral),
    ("OpenRouter", _openrouter), ("Cerebras", _cerebras),
]

def _call_llm(prompt, system="", max_tokens=4000):
    """Try free providers in order. Returns (text, name)."""
    for name, fn in _PROVIDERS:
        try:
            r = fn(prompt, system, max_tokens)
            if r and len(r.strip()) > 10: return r, name
        except: continue
    raise Exception("All free LLM providers failed. Add API keys to secrets.toml.")


def _call_claude(prompt, system="", max_tokens=4000, use_search=False):
    """Claude API with optional web search."""
    k = _get_key("ANTHROPIC_API_KEY")
    if not k: raise ValueError("ANTHROPIC_API_KEY not set")
    try: import anthropic
    except ImportError: raise ValueError("pip install anthropic")
    client = anthropic.Anthropic(api_key=k)
    kw = {"model": "claude-sonnet-4-20250514", "max_tokens": min(max_tokens, 4096),
          "messages": [{"role": "user", "content": prompt}]}
    if system: kw["system"] = system
    if use_search: kw["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
    for attempt in range(3):
        try:
            resp = client.messages.create(**kw)
            return "\n".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        except Exception as e:
            if "429" in str(e) or "rate_limit" in str(e).lower():
                time.sleep(25 * (attempt + 1)); continue
            raise
    raise Exception("Claude rate limit after 3 retries")


def _smart_call(prompt, system="", max_tokens=4000):
    """Claude first (if key set), else free LLMs. Returns (text, provider)."""
    if _get_key("ANTHROPIC_API_KEY"):
        try:
            r = _call_claude(prompt, system, max_tokens)
            if r and len(r) > 10: return r, "Claude"
        except: pass
    return _call_llm(prompt, system, max_tokens)


# ═══════════════════════════════════════════════════════════════════
# JSON PARSER — handles fences, preamble, truncated arrays
# ═══════════════════════════════════════════════════════════════════

def _parse_json(text):
    if not text: return None
    c = text.strip()
    for f in ["```json", "```JSON", "```", "`"]: c = c.replace(f, "")
    c = c.strip()
    # Strip preamble
    for i, line in enumerate(c.split('\n')):
        if line.strip().startswith('{') or line.strip().startswith('['):
            c = '\n'.join(c.split('\n')[i:]); break
    # 1. Direct parse
    try: return json.loads(c)
    except: pass
    # 2. Try complete {...} dict first (spec extraction returns dicts)
    r = _bracket_extract(c, '{', '}')
    if r is not None and isinstance(r, dict) and ("pumps" in r or "document_type" in r):
        return r
    # 3. Try complete [...] array (BOM generation returns arrays)
    r = _bracket_extract(c, '[', ']')
    if r is not None: return r
    # 4. TRUNCATED ARRAY — extract every complete {...} from broken [
    idx = c.find('[')
    if idx != -1:
        items = _recover_truncated(c[idx:])
        if items and len(items) >= 1: return items
    # 5. Single dict fallback (last resort)
    r = _bracket_extract(c, '{', '}')
    if r is not None: return r
    return None


def _bracket_extract(text, o, c):
    start = text.find(o)
    if start == -1: return None
    depth = 0; in_str = False; i = start
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i-1] != '\\'): in_str = not in_str; i += 1; continue
        if in_str: i += 1; continue
        if ch == o: depth += 1
        elif ch == c:
            depth -= 1
            if depth == 0:
                s = text[start:i+1]
                try: return json.loads(s)
                except:
                    try: return json.loads(re.sub(r',\s*([}\]])', r'\1', s))
                    except: return None
        i += 1
    return None


def _recover_truncated(text):
    objects = []; i = 0
    while i < len(text):
        s = text.find('{', i)
        if s == -1: break
        depth = 0; in_str = False; j = s; found = False
        while j < len(text):
            ch = text[j]
            if ch == '"' and (j == 0 or text[j-1] != '\\'): in_str = not in_str; j += 1; continue
            if in_str: j += 1; continue
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try: obj = json.loads(text[s:j+1])
                    except:
                        try: obj = json.loads(re.sub(r',\s*([}\]])', r'\1', text[s:j+1]))
                        except: obj = None
                    if isinstance(obj, dict): objects.append(obj)
                    found = True; i = j + 1; break
            j += 1
        if not found: break
    return objects


# ═══════════════════════════════════════════════════════════════════
# STEP 1 — EXTRACT SPECS FROM PDF
# ═══════════════════════════════════════════════════════════════════

def extract_pdf_text(file_bytes):
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(BytesIO(file_bytes)) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t: pages.append(t)
        return "\n".join(pages), None
    except Exception as e: return "", str(e)


SPEC_SYS = """You are a senior procurement engineer for Indian EPC projects.
Read any technical document (pump datasheet, motor spec, valve spec, GA drawing).
Extract every parameter accurately. Use null for missing. Never guess.
If no flow_m3h and head_m data → set is_pump_document: false."""


def claude_extract_specs(pdf_text):
    prompt = f"""Read this document and extract ALL specs as structured JSON.

TEXT:
{pdf_text[:15000]}

Return ONLY this JSON object:
{{
  "document_type": "pump_datasheet | motor_datasheet | valve_datasheet | other",
  "is_pump_document": true or false,
  "document_warning": "null if pump, else 'Motor-only datasheet' etc",
  "manufacturer": "name or null",
  "project": "name or null",
  "multi_pump": false,
  "pumps": [
    {{
      "pump_label": "descriptive name",
      "model": "model or null",
      "manufacturer": "name or null",
      "type": "type or null",
      "tag_numbers": "tags or null",
      "standard": "standard or null",
      "quantity": 1,
      "flow_m3h": null,
      "head_m": null,
      "speed_rpm": null,
      "motor_kw": null,
      "stages": null,
      "temp_c": null,
      "density_kgm3": null,
      "fluid": "fluid or null",
      "efficiency_pct": null,
      "impeller_dia_mm": null,
      "moc": {{
        "casing": "mat or null", "impeller": "mat or null",
        "shaft": "mat or null", "shaft_sleeve": "mat or null",
        "wear_ring": "mat or null", "bearing": "type or null",
        "bearing_housing": "mat or null", "seal_type": "type or null",
        "seal_plan": "plan or null", "baseplate": "mat or null",
        "fasteners": "mat or null"
      }},
      "nozzles": {{
        "suction_size": "size or null", "discharge_size": "size or null",
        "flange_standard": "std or null"
      }},
      "weights": {{
        "total_package_kg": null, "motor_kg": null, "pump_bare_kg": null
      }},
      "motor": {{
        "type": "type or null", "rating_kw": null, "voltage_v": "v or null",
        "frequency_hz": null, "poles": null, "speed_rpm": null,
        "enclosure": "ip or null", "mounting": "mount or null"
      }},
      "drive": {{
        "type": "direct coupled | belt driven | null",
        "coupling_type": "type or null"
      }},
      "vibration_limit": "value or null",
      "noise_limit_dba": null,
      "performance_test_std": "std or null",
      "notes": "critical notes or null"
    }}
  ]
}}"""

    raw, provider = _smart_call(prompt, SPEC_SYS, 4000)
    try:
        import streamlit as st
        st.session_state["_last_raw_response"] = (raw or "")[:4000]
    except: pass
    
    data = _parse_json(raw)
    if not data or not isinstance(data, dict): return None
    
    if "pumps" not in data or not isinstance(data.get("pumps"), list):
        if any(k in data for k in ["flow_m3h", "head_m", "motor_kw"]):
            data = {"pumps": [data], "document_type": "pump_datasheet",
                    "is_pump_document": True, "manufacturer": data.get("manufacturer")}
        else: data["pumps"] = []
    
    if "is_pump_document" not in data:
        data["is_pump_document"] = any(
            p.get("flow_m3h") or p.get("head_m")
            for p in data.get("pumps", []) if isinstance(p, dict))
    
    data["_llm_provider"] = provider
    return data


# ═══════════════════════════════════════════════════════════════════
# STEP 2 — GENERATE BOM (25-35 components)
# ═══════════════════════════════════════════════════════════════════

BOM_SYS = """Expert rotating equipment engineer, India. Generate 25-35 component BOMs.
Include ALL sub-assemblies. Exact MOC (ASTM/IS/EN). Realistic weights.
Descriptions MAX 40 chars. Return ONLY JSON array. No text before/after."""


def claude_generate_bom(pump_specs):
    specs_str = json.dumps(pump_specs, indent=1, default=str)
    prompt = f"""BOM for:
{specs_str}

JSON array ONLY:
[{{"section":"A-L","sub_assembly":"group","component":"name",
"description":"max 40 chars","moc":"ASTM/IS spec","qty":"1",
"weight_kg":0,"req_type":"M","notes":""}}]

Sections: A.PUMP HYDRAULICS B.ROTATING ASSEMBLY C.BEARINGS D.SHAFT SEALING
E.DRIVE/COUPLING F.MOTOR G.STRUCTURAL H.PIPING/NOZZLES I.FASTENERS J.INSTRUMENTATION
K.ACOUSTIC L.COMPLETE ASSEMBLY

MUST include ≥25 items: casing, impeller, wear_rings×2, shaft, sleeve, 
bearings×2, brg_housing, seal, gland, coupling/drive, motor, baseplate,
fdn_bolts, counter_flanges×2, gaskets×2, casing_bolts, RTD, painting.
JSON array ONLY. No explanation."""

    raw, provider = _smart_call(prompt, BOM_SYS, 4096)
    data = _parse_json(raw)
    
    # Normalize to list of dicts
    if isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        for k in ["components", "bom", "items", "BOM", "data"]:
            if k in data and isinstance(data[k], list):
                items = [x for x in data[k] if isinstance(x, dict)]; break
        else:
            items = [data] if "component" in data else []
    else:
        items = []
    
    # If too few items, try harder with truncated recovery
    if len(items) < 5 and raw:
        cleaned = raw.replace("```json", "").replace("```", "")
        # Try bracket extract for complete array
        r = _bracket_extract(cleaned, "[", "]")
        if isinstance(r, list) and len(r) > len(items):
            items = [x for x in r if isinstance(x, dict)]
        # Try recovering individual objects from truncated array
        if len(items) < 5:
            recovered = _recover_truncated(cleaned)
            if len(recovered) > len(items):
                items = recovered
    
    return items


def bom_to_dataframe(bom_list):
    if not bom_list: return pd.DataFrame()
    rows = []
    for i, c in enumerate(bom_list, 1):
        if not isinstance(c, dict): continue
        rows.append({
            "No": i,
            "Section": str(c.get("section", "")),
            "Sub_Assembly": str(c.get("sub_assembly", "")),
            "Component": str(c.get("component", c.get("name", ""))),
            "Description": str(c.get("description", "")),
            "MOC": str(c.get("moc", c.get("material", ""))),
            "Qty": str(c.get("qty", "1")),
            "Weight_kg": c.get("weight_kg", c.get("weight", None)),
            "Req_Type": str(c.get("req_type", "M")),
            "Notes": str(c.get("notes", "")),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════
# STEP 3 — SHOULD-COST PRICING (Claude + Web Search)
# Raw Material (live price) + Machining = True Cost. No overhead.
# ═══════════════════════════════════════════════════════════════════

COST_SYS = """You are a cost engineer at an Indian EPC company.
Find RAW MANUFACTURING COST — NOT selling price.
Use web_search for CURRENT 2025-26 Indian prices from SAIL/RINL/LME/IndiaMART.

For MANUFACTURED: raw material (₹/kg × gross weight) + machining cost
For BOUGHT-OUT: OEM procurement price from IndiaMART/TradeIndia
NO overhead. NO margin. Conservative estimates. All in INR."""


def _classify(comp, moc=""):
    c = (comp or "").upper()
    if "MOTOR" in c and not any(x in c for x in ["BOLT","PULLEY","SIDE","BRACKET","HOUSING","MOUNT"]): return "bought_out"
    if "BEARING" in c and "HOUSING" not in c: return "bought_out"
    if any(x in c for x in ["MECHANICAL SEAL","MECH SEAL"]): return "bought_out"
    if any(x in c for x in ["V-BELT","V BELT","VBELT"]): return "bought_out"
    if "COMPANION FLANGE" in c or "COUNTER FLANGE" in c: return "bought_out"
    if "GASKET" in c: return "bought_out"
    if "FOUNDATION" in c and "BOLT" in c: return "bought_out"
    if any(x in c for x in ["RTD","THERMOMETER","PRESSURE GAUGE","VIBRATION SWITCH"]): return "bought_out"
    if any(x in c for x in ["FIRST FILL","GREASE","LUBRICANT"]): return "bought_out"
    if "GUARD" in c: return "bought_out"
    if "SLD" in c: return "bought_out"
    if any(x in c for x in ["COMPLETE ASSEMBLY","NOISE LEVEL","VIBRATION LIMIT","PERFORMANCE TEST","SURFACE PREP"]): return "compliance"
    return "manufactured"


def claude_price_bom(bom_df, pump_specs, progress_callback=None):
    if bom_df is None or bom_df.empty: return bom_df
    pump = pump_specs if isinstance(pump_specs, dict) else {}
    
    if not _get_key("ANTHROPIC_API_KEY"):
        raise Exception("ANTHROPIC_API_KEY required for should-cost pricing (web search).")
    
    if progress_callback: progress_callback(8, "Classifying components...")
    
    items = []
    for _, row in bom_df.iterrows():
        comp = str(row.get("Component", ""))
        items.append({"row": row.to_dict(), "no": row.get("No", 0),
                      "comp": comp, "moc": str(row.get("MOC", "")),
                      "weight": row.get("Weight_kg"), "qty": str(row.get("Qty", "1")),
                      "cat": _classify(comp, str(row.get("MOC", "")))})
    
    mfg = [x for x in items if x["cat"] == "manufactured"]
    bo = [x for x in items if x["cat"] == "bought_out"]
    cmp = [x for x in items if x["cat"] == "compliance"]
    
    if progress_callback:
        progress_callback(12, f"{len(mfg)} manufactured, {len(bo)} bought-out...")
    
    result = []
    
    # Price manufactured (batch 3, 35s between to stay under 30k TPM)
    for i in range(0, len(mfg), 3):
        batch = mfg[i:i+3]
        pct = 15 + int((i / max(len(mfg), 1)) * 50)
        if progress_callback:
            progress_callback(pct, f"Searching: {', '.join(c['comp'][:18] for c in batch)}...")
        
        items_j = json.dumps([{"no":c["no"],"component":c["comp"],"moc":c["moc"],
                               "weight_kg":c["weight"],"qty":c["qty"]} for c in batch], default=str)
        prompt = f"""Find RAW MANUFACTURING COST. Pump: {pump.get('type','')}, {pump.get('motor_kw','')}kW, fluid: {pump.get('fluid','')}

Components:
{items_j}

For each: web_search "[MOC] price per kg India 2025", calculate:
- raw_material_cost = gross_weight (finished×1.35 castings, ×1.15 bar) × ₹/kg
- machining_cost = hours × rate (CNC ₹1000-1500/hr, pattern+mould ₹18-22/kg)
- total = raw + machining. NO overhead.

Return JSON array ONLY:
[{{"no":<n>,"raw_material_rate_per_kg":<int>,"gross_weight_kg":<num>,
"raw_material_cost_inr":<int>,"machining_cost_inr":<int>,
"total_cost_inr":<int>,"material_source":"source",
"confidence":"high|medium|low","notes":"brief"}}]"""
        
        try:
            raw_resp = _call_claude(prompt, COST_SYS, 2000, use_search=True)
            parsed = _parse_json(raw_resp)
            if not isinstance(parsed, list): parsed = [parsed] if isinstance(parsed, dict) else []
            pm = {p["no"]: p for p in parsed if isinstance(p, dict) and "no" in p}
            for c in batch:
                rd = c["row"].copy()
                p = pm.get(c["no"], {})
                raw_c = int(p.get("raw_material_cost_inr", 0))
                mach = int(p.get("machining_cost_inr", 0))
                total = int(p.get("total_cost_inr", raw_c + mach))
                rd.update({"Raw_Material_INR": raw_c, "Machining_INR": mach,
                          "Total_Price_INR": total, "Unit_Price_INR": total,
                          "GST_18pct": int(total*0.18), "Price_With_GST": int(total*1.18),
                          "Price_Confidence": p.get("confidence","medium"),
                          "Price_Source": p.get("material_source",""),
                          "Price_Notes": f"₹{p.get('raw_material_rate_per_kg',0)}/kg × {p.get('gross_weight_kg',0)}kg | {p.get('notes','')}",
                          "Component_Type": "manufactured"})
                result.append(rd)
        except Exception as e:
            for c in batch:
                rd = c["row"].copy()
                rd.update({"Raw_Material_INR":0,"Machining_INR":0,"Total_Price_INR":0,
                          "Unit_Price_INR":0,"GST_18pct":0,"Price_With_GST":0,
                          "Price_Confidence":"error","Price_Source":str(e)[:80],
                          "Price_Notes":"","Component_Type":"manufactured"})
                result.append(rd)
        if i + 3 < len(mfg): time.sleep(35)
    
    # Price bought-out (batch 4, 35s between)
    for i in range(0, len(bo), 4):
        batch = bo[i:i+4]
        pct = 67 + int((i / max(len(bo), 1)) * 22)
        if progress_callback:
            progress_callback(pct, f"Market pricing: {', '.join(c['comp'][:18] for c in batch)}...")
        
        items_j = json.dumps([{"no":c["no"],"component":c["comp"],"moc":c["moc"],
                               "qty":c["qty"]} for c in batch], default=str)
        prompt = f"""Find OEM MARKET PRICE. Pump: {pump.get('motor_kw','')}kW, fluid: {pump.get('fluid','')}

Items:
{items_j}

web_search IndiaMART/TradeIndia for each. Return what vendor PAYS to OEM.

Return JSON array ONLY:
[{{"no":<n>,"market_price_inr":<int>,"source":"src","confidence":"high|medium|low","notes":"brief"}}]"""
        
        try:
            raw_resp = _call_claude(prompt, COST_SYS, 2000, use_search=True)
            parsed = _parse_json(raw_resp)
            if not isinstance(parsed, list): parsed = [parsed] if isinstance(parsed, dict) else []
            pm = {p["no"]: p for p in parsed if isinstance(p, dict) and "no" in p}
            for c in batch:
                rd = c["row"].copy()
                p = pm.get(c["no"], {})
                price = int(p.get("market_price_inr", 0))
                rd.update({"Raw_Material_INR": price, "Machining_INR": 0,
                          "Total_Price_INR": price, "Unit_Price_INR": price,
                          "GST_18pct": int(price*0.18), "Price_With_GST": int(price*1.18),
                          "Price_Confidence": p.get("confidence","medium"),
                          "Price_Source": p.get("source",""),
                          "Price_Notes": p.get("notes",""),
                          "Component_Type": "bought_out"})
                result.append(rd)
        except Exception as e:
            for c in batch:
                rd = c["row"].copy()
                rd.update({"Raw_Material_INR":0,"Machining_INR":0,"Total_Price_INR":0,
                          "Unit_Price_INR":0,"GST_18pct":0,"Price_With_GST":0,
                          "Price_Confidence":"error","Price_Source":str(e)[:80],
                          "Price_Notes":"","Component_Type":"bought_out"})
                result.append(rd)
        if i + 4 < len(bo): time.sleep(35)
    
    # Compliance (zero cost)
    for c in cmp:
        rd = c["row"].copy()
        rd.update({"Raw_Material_INR":0,"Machining_INR":0,"Total_Price_INR":0,
                  "Unit_Price_INR":0,"GST_18pct":0,"Price_With_GST":0,
                  "Price_Confidence":"high","Price_Source":"No separate cost",
                  "Price_Notes":"Assembly item","Component_Type":"compliance"})
        result.append(rd)
    
    if progress_callback: progress_callback(92, "Building report...")
    return pd.DataFrame(result)


def build_cost_summary(priced_df):
    if priced_df is None or priced_df.empty: return {}
    def _s(c): return int(priced_df[c].sum()) if c in priced_df.columns else 0
    sub_col = "Sub_Assembly" if "Sub_Assembly" in priced_df.columns else "Section"
    sub_totals = (priced_df.groupby(sub_col)["Total_Price_INR"]
                  .sum().sort_values(ascending=False).to_dict()
                  if "Total_Price_INR" in priced_df.columns else {})
    top5 = (priced_df.nlargest(5, "Total_Price_INR")
            [["Component","Total_Price_INR","Raw_Material_INR","Machining_INR","Price_Confidence","Price_Source"]]
            .to_dict("records")) if "Total_Price_INR" in priced_df.columns else []
    return {
        "total_raw_material": _s("Raw_Material_INR"), "total_machining": _s("Machining_INR"),
        "total_ex_gst": _s("Total_Price_INR"), "total_gst": _s("GST_18pct"),
        "total_incl_gst": _s("Price_With_GST"),
        "sub_totals": {k: int(v) for k, v in sub_totals.items()},
        "top5_drivers": top5,
        "confidence": priced_df["Price_Confidence"].value_counts().to_dict() if "Price_Confidence" in priced_df.columns else {},
        "component_count": len(priced_df),
        "type_split": priced_df["Component_Type"].value_counts().to_dict() if "Component_Type" in priced_df.columns else {},
        "note": "Should-Cost = Raw Material (live web prices) + Machining. NO overhead, NO margin. Difference vs PO = supplier's profit.",
    }


# ═══════════════════════════════════════════════════════════════════
# GROUPING & EXCEL EXPORT
# ═══════════════════════════════════════════════════════════════════

SECTION_ORDER = [
    "A. PUMP HYDRAULICS","B. ROTATING ASSEMBLY","C. BEARINGS & LUBRICATION",
    "D. SHAFT SEALING","E. DRIVE & COUPLING","F. MOTOR / DRIVER",
    "G. STRUCTURAL","H. PIPING & NOZZLES","I. FASTENERS & GASKETS",
    "J. INSTRUMENTATION","K. ACOUSTIC & SAFETY","L. COMPLETE ASSEMBLY",
]

def group_bom(bom_df):
    if bom_df is None or bom_df.empty: return []
    if "Section" not in bom_df.columns: return [("ALL","All",bom_df)]
    sec_order = {s:i for i,s in enumerate(SECTION_ORDER)}
    df = bom_df.copy()
    df["_ord"] = df["Section"].apply(lambda s: sec_order.get(str(s).strip(), 99))
    df = df.sort_values("_ord").reset_index(drop=True)
    df["No"] = range(1, len(df)+1)
    result = []
    sub_col = "Sub_Assembly" if "Sub_Assembly" in df.columns else None
    for sec in SECTION_ORDER:
        rows = df[df["Section"].str.strip() == sec]
        if rows.empty: continue
        if sub_col:
            for sub in rows[sub_col].unique():
                result.append((sec, str(sub), rows[rows[sub_col]==sub].drop(columns=["_ord"], errors="ignore")))
        else:
            result.append((sec, sec, rows.drop(columns=["_ord"], errors="ignore")))
    other = df[~df["Section"].str.strip().isin(SECTION_ORDER)]
    if not other.empty:
        result.append(("Z. OTHER","Other",other.drop(columns=["_ord"], errors="ignore")))
    return result


def export_excel(bom_df, pump_specs, priced=False):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    thin = Side(style="thin", color="CCCCCC")
    bdr = Border(left=thin, right=thin, top=thin, bottom=thin)
    
    # Cover
    ws0 = wb.active; ws0.title = "Cover"
    ws0.sheet_view.showGridLines = False
    ws0.column_dimensions["A"].width = 30; ws0.column_dimensions["B"].width = 55
    ws0.merge_cells("A1:B1")
    c = ws0["A1"]; c.value = "BILL OF MATERIALS"
    c.font = Font(name="Arial", bold=True, size=18, color="FFFFFF")
    c.fill = PatternFill("solid", fgColor="1F4E79")
    c.alignment = Alignment(horizontal="center", vertical="center")
    specs = pump_specs if isinstance(pump_specs, dict) else {}
    info = []
    if isinstance(specs.get("pumps"), list) and specs["pumps"]:
        p = specs["pumps"][0]
        for k, l in [("model","Model"),("manufacturer","Mfr"),("type","Type"),
                     ("flow_m3h","Flow"),("head_m","Head"),("motor_kw","Motor kW"),
                     ("fluid","Fluid"),("standard","Standard")]:
            v = p.get(k)
            if v: info.append((l, str(v)))
    r = 3
    for lbl, val in info:
        ws0.cell(r,1,lbl).font = Font(name="Arial",bold=True,size=10)
        ws0.cell(r,1).fill = PatternFill("solid",fgColor="EEF2F7"); ws0.cell(r,1).border = bdr
        ws0.cell(r,2,val).font = Font(name="Arial",size=10); ws0.cell(r,2).border = bdr
        r += 1
    
    # BOM
    ws1 = wb.create_sheet("BOM"); ws1.sheet_view.showGridLines = False
    if priced and "Total_Price_INR" in bom_df.columns:
        cols = ["No","Section","Sub_Assembly","Component","MOC","Qty","Weight_kg",
                "Raw_Material_INR","Machining_INR","Total_Price_INR","Price_Confidence","Price_Notes"]
    else:
        cols = ["No","Section","Sub_Assembly","Component","Description","MOC","Qty","Weight_kg","Req_Type","Notes"]
    cols = [c for c in cols if c in bom_df.columns]
    widths = {"No":5,"Section":20,"Sub_Assembly":18,"Component":28,"Description":30,"MOC":22,
              "Qty":6,"Weight_kg":10,"Req_Type":6,"Notes":25,"Raw_Material_INR":13,
              "Machining_INR":13,"Total_Price_INR":13,"Price_Confidence":10,"Price_Notes":30}
    
    ws1.merge_cells(f"A1:{get_column_letter(len(cols))}1")
    t = ws1["A1"]; t.value = "BILL OF MATERIALS"
    t.font = Font(name="Arial",bold=True,size=12,color="FFFFFF")
    t.fill = PatternFill("solid",fgColor="1F4E79")
    r = 2
    for j, col in enumerate(cols):
        h = ws1.cell(r,j+1,col.replace("_"," "))
        h.font = Font(name="Arial",bold=True,size=9,color="FFFFFF")
        h.fill = PatternFill("solid",fgColor="2E75B6")
        h.alignment = Alignment(horizontal="center",wrap_text=True); h.border = bdr
        ws1.column_dimensions[get_column_letter(j+1)].width = widths.get(col,14)
    r += 1
    f1, f2 = PatternFill("solid",fgColor="EEF4FB"), PatternFill("solid",fgColor="FFFFFF")
    for i, (_, row) in enumerate(bom_df.iterrows()):
        for j, col in enumerate(cols):
            v = row.get(col, "")
            if pd.isna(v): v = ""
            if col in ("Raw_Material_INR","Machining_INR","Total_Price_INR"):
                try: v = f"₹{int(float(v)):,}" if v else ""
                except: pass
            cell = ws1.cell(r,j+1,v)
            cell.font = Font(name="Arial",size=8); cell.fill = f1 if i%2==0 else f2
            cell.border = bdr; cell.alignment = Alignment(wrap_text=True,vertical="top")
        r += 1
    ws1.freeze_panes = "A3"
    buf = BytesIO(); wb.save(buf); buf.seek(0); return buf
