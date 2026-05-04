"""
BOM Engine — 10-Provider Free LLM Router
═════════════════════════════════════════
Tries providers in priority order. If one fails → next one instantly.
With 10 providers, the system almost never fails completely.

Provider priority (best quality + reliability first):
  1. Gemini 2.5 Flash     — 1M context, best for datasheets
  2. Groq (Llama 3.3 70B) — fastest, most reliable free API
  3. Mistral Large 3       — European, high quality, 500K TPM
  4. OpenRouter (free)     — 35+ free models, auto-routes
  5. Cerebras              — ultra-fast, updated model names
  6. GitHub Models         — GPT-4o free, needs GitHub token
  7. NVIDIA NIM            — 100+ models, developer free
  8. Cohere Command A      — reliable, 1000 calls/month
  9. LLM7.io              — no registration needed
 10. Claude (paid)         — optional last resort

Author: Ayush Kamle
"""

import json, re, time, os
import pandas as pd
from io import BytesIO


# ─────────────────────────────────────────────────────────────────
# CORE HELPERS
# ─────────────────────────────────────────────────────────────────

def _get_api_key(key_name):
    """Get API key from Streamlit secrets."""
    import streamlit as st
    return st.secrets.get(key_name, "")


def _http_post(url, headers, body_dict, timeout=90, retries=2):
    """POST with auto-retry on 503/429. Returns parsed JSON."""
    import urllib.request, urllib.error
    data_bytes = json.dumps(body_dict).encode("utf-8")
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data_bytes, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = e.read().decode("utf-8")[:300]
            except Exception:
                body = str(e.reason)
            if status in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(8 * (attempt + 1))
                last_err = Exception(f"HTTP {status}: {body}")
                continue
            raise Exception(f"HTTP {status}: {body}")
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(4)
                continue
            raise
    raise last_err


def _openai_compat(url, key, model, prompt, system="", max_tokens=4000):
    """Call any OpenAI-compatible API endpoint."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "messages": messages,
            "max_tokens": max_tokens, "temperature": 0.1}
    data = _http_post(url,
                      {"Content-Type": "application/json",
                       "Authorization": f"Bearer {key}"},
                      body)
    return data["choices"][0]["message"]["content"].strip()


# ─────────────────────────────────────────────────────────────────
# PROVIDER FUNCTIONS  (each raises on failure, returns string on success)
# ─────────────────────────────────────────────────────────────────

def _call_gemini(prompt, system="", max_tokens=8000):
    """Gemini 2.5 Flash — 1M context, 250 RPD free. Best for datasheets."""
    key = _get_api_key("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY not set")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash:generateContent?key={key}")
    data = _http_post(url, {"Content-Type": "application/json"}, body)
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _call_groq(prompt, system="", max_tokens=4000):
    """Groq — Llama 3.3 70B, 14,400 RPD free. Very fast."""
    key = _get_api_key("GROQ_API_KEY")
    if not key:
        raise ValueError("GROQ_API_KEY not set")
    # Try best model first, fall back if quota hit
    for model in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
        try:
            return _openai_compat(
                "https://api.groq.com/openai/v1/chat/completions",
                key, model, prompt, system, max_tokens)
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                continue   # try smaller model
            raise
    raise Exception("Groq: all models rate-limited")


def _call_mistral(prompt, system="", max_tokens=4000):
    """Mistral Large 3 — 500K TPM, ~1 RPS free. European provider."""
    key = _get_api_key("MISTRAL_API_KEY")
    if not key:
        raise ValueError("MISTRAL_API_KEY not set")
    # Try large first, fall back to small
    for model in ["mistral-large-latest", "mistral-small-latest"]:
        try:
            return _openai_compat(
                "https://api.mistral.ai/v1/chat/completions",
                key, model, prompt, system, min(max_tokens, 4000))
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                time.sleep(2)
                continue
            raise
    raise Exception("Mistral: rate limited")


def _call_openrouter(prompt, system="", max_tokens=4000):
    """OpenRouter — 35+ free models, 200 RPD. Auto-routes to best available."""
    key = _get_api_key("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OPENROUTER_API_KEY not set")
    # Best free models for engineering/JSON tasks
    for model in [
        "meta-llama/llama-3.3-70b-instruct:free",
        "deepseek/deepseek-chat-v3-0324:free",
        "qwen/qwen3.6-plus:free",
        "google/gemma-4-31b-it:free",
    ]:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            body = {"model": model, "messages": messages,
                    "max_tokens": max_tokens, "temperature": 0.1}
            data = _http_post(
                "https://openrouter.ai/api/v1/chat/completions",
                {"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "HTTP-Referer": "https://pumpbom.streamlit.app",
                 "X-Title": "BOM Generator"},
                body)
            result = data["choices"][0]["message"]["content"].strip()
            if result and len(result) > 10:
                return result
        except Exception as e:
            if "429" in str(e) or "503" in str(e) or "overloaded" in str(e).lower():
                time.sleep(2)
                continue
            raise
    raise Exception("OpenRouter: all free models failed")


def _call_cerebras(prompt, system="", max_tokens=4000):
    """Cerebras — ultra-fast, 14,400 RPD free. Updated model names."""
    key = _get_api_key("CEREBRAS_API_KEY")
    if not key:
        raise ValueError("CEREBRAS_API_KEY not set")
    # Current models per inference-docs.cerebras.ai/models/overview
    for model in ["gpt-oss-120b", "llama3.1-8b"]:
        try:
            return _openai_compat(
                "https://api.cerebras.ai/v1/chat/completions",
                key, model, prompt, system, min(max_tokens, 8000))
        except Exception as e:
            if "404" in str(e) or "model" in str(e).lower():
                continue
            if "429" in str(e):
                time.sleep(3)
                continue
            raise
    raise Exception("Cerebras: no working model")


def _call_github_models(prompt, system="", max_tokens=4000):
    """GitHub Models — GPT-4o free, 50 RPD. Needs GitHub PAT token."""
    key = _get_api_key("GITHUB_TOKEN")
    if not key:
        raise ValueError("GITHUB_TOKEN not set")
    for model in ["gpt-4o", "Meta-Llama-3.3-70B", "gpt-4.1-mini"]:
        try:
            return _openai_compat(
                "https://models.inference.ai.azure.com/chat/completions",
                key, model, prompt, system, min(max_tokens, 4000))
        except Exception as e:
            if "429" in str(e) or "503" in str(e):
                continue
            raise
    raise Exception("GitHub Models: all models failed")


def _call_nvidia_nim(prompt, system="", max_tokens=4000):
    """NVIDIA NIM — 100+ models, no daily token cap, developer free."""
    key = _get_api_key("NVIDIA_API_KEY")
    if not key:
        raise ValueError("NVIDIA_API_KEY not set")
    for model in ["meta/llama-3.3-70b-instruct",
                  "nvidia/llama-3.1-nemotron-ultra-253b-v1",
                  "qwen/qwen2.5-72b-instruct"]:
        try:
            return _openai_compat(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                key, model, prompt, system, min(max_tokens, 4000))
        except Exception as e:
            if "429" in str(e) or "503" in str(e):
                time.sleep(2)
                continue
            raise
    raise Exception("NVIDIA NIM: rate limited")


def _call_cohere(prompt, system="", max_tokens=4000):
    """Cohere Command A — 1,000 calls/month free. Good JSON output."""
    key = _get_api_key("COHERE_API_KEY")
    if not key:
        raise ValueError("COHERE_API_KEY not set")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": "command-a-03-2025",
            "messages": messages,
            "max_tokens": min(max_tokens, 4000),
            "temperature": 0.1}
    data = _http_post(
        "https://api.cohere.com/v2/chat",
        {"Content-Type": "application/json",
         "Authorization": f"Bearer {key}"},
        body)
    return data["message"]["content"][0]["text"].strip()


def _call_llm7(prompt, system="", max_tokens=4000):
    """LLM7.io — no registration, 30 RPM free. Last free resort."""
    # No key needed for basic access
    for model in ["deepseek-v3-0324", "gemini-2.5-flash-lite",
                  "mistral-small-3.1-24b"]:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            body = {"model": model, "messages": messages,
                    "max_tokens": min(max_tokens, 4000), "temperature": 0.1}
            # Optional token for higher limits
            key = _get_api_key("LLM7_TOKEN")
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            data = _http_post("https://api.llm7.io/v1/chat/completions",
                               headers, body)
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if "429" in str(e) or "503" in str(e):
                time.sleep(3)
                continue
            raise
    raise Exception("LLM7: all models failed")


def _call_claude(prompt, system="", max_tokens=4000):
    """Claude — OPTIONAL paid fallback. Only used if key set + all free fail."""
    key = _get_api_key("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    try:
        import anthropic
    except ImportError:
        raise ValueError("pip install anthropic needed for Claude fallback")
    client = anthropic.Anthropic(api_key=key)
    kwargs = {"model": "claude-sonnet-4-5", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]}
    if system:
        kwargs["system"] = system
    for attempt in range(3):
        try:
            resp = client.messages.create(**kwargs)
            return "\n".join(b.text for b in resp.content if hasattr(b, "text"))
        except Exception as e:
            if "429" in str(e):
                time.sleep(30 * (2 ** attempt))
            else:
                raise
    raise Exception("Claude rate limit exceeded.")


# ─────────────────────────────────────────────────────────────────
# PROVIDER REGISTRY
# Each entry: (name, secret_key_needed, function)
# Ordered by: quality → reliability → quota
# ─────────────────────────────────────────────────────────────────
_PROVIDERS = [
    ("Gemini",        "GEMINI_API_KEY",      _call_gemini),
    ("Groq",          "GROQ_API_KEY",        _call_groq),
    ("Mistral",       "MISTRAL_API_KEY",     _call_mistral),
    ("OpenRouter",    "OPENROUTER_API_KEY",  _call_openrouter),
    ("Cerebras",      "CEREBRAS_API_KEY",    _call_cerebras),
    ("GitHub",        "GITHUB_TOKEN",        _call_github_models),
    ("NVIDIA NIM",    "NVIDIA_API_KEY",      _call_nvidia_nim),
    ("Cohere",        "COHERE_API_KEY",      _call_cohere),
    ("LLM7",          None,                  _call_llm7),   # no key needed
    ("Claude",        "ANTHROPIC_API_KEY",   _call_claude),
]

# Keys that show which providers are configured
_FREE_PROVIDER_KEYS = [
    ("GEMINI_API_KEY",     "Gemini",     "https://aistudio.google.com"),
    ("GROQ_API_KEY",       "Groq",       "https://console.groq.com"),
    ("MISTRAL_API_KEY",    "Mistral",    "https://console.mistral.ai"),
    ("OPENROUTER_API_KEY", "OpenRouter", "https://openrouter.ai/keys"),
    ("CEREBRAS_API_KEY",   "Cerebras",   "https://cloud.cerebras.ai"),
    ("GITHUB_TOKEN",       "GitHub",     "https://github.com/settings/tokens"),
    ("NVIDIA_API_KEY",     "NVIDIA NIM", "https://build.nvidia.com"),
    ("COHERE_API_KEY",     "Cohere",     "https://dashboard.cohere.com"),
]


def _call_llm(prompt, system="", max_tokens=4000):
    """
    Try ALL configured providers in priority order.
    Returns (response_text, provider_name).
    Falls through to next provider on ANY failure.
    LLM7 is tried even without a key (no-registration fallback).
    """
    errors = []
    tried = []

    for name, key_name, fn in _PROVIDERS:
        # Skip unconfigured paid providers (not LLM7 — it needs no key)
        if key_name and not _get_api_key(key_name):
            continue

        tried.append(name)
        try:
            result = fn(prompt, system=system, max_tokens=max_tokens)
            if result and len(result.strip()) > 10:
                return result, name
            errors.append(f"{name}: empty response")
        except Exception as e:
            err = str(e)[:150]
            if "not set" not in err.lower():
                errors.append(f"{name}: {err}")
            continue

    # Nothing worked — build a useful error message
    configured = [n for _, k, _ in _PROVIDERS
                  if k is None or _get_api_key(k)]

    if not configured and name != "LLM7":
        signup_lines = "\n".join(
            f'  {label:<12} = "..."   # FREE — {url}'
            for key, label, url in _FREE_PROVIDER_KEYS
        )
        raise Exception(
            "No LLM API keys configured. Add at least ONE to .streamlit/secrets.toml:\n\n"
            + signup_lines +
            "\n\nAll are free. Gemini or Groq are recommended to start."
        )

    raise Exception(
        f"All {len(tried)} providers failed: {', '.join(tried)}\n\n"
        + "\n".join(errors) +
        "\n\nFixes:\n"
        "  • Add more API keys to secrets.toml (more providers = more reliability)\n"
        "  • Wait 1 minute and retry (rate limits reset)\n"
        "  • Check provider status pages"
    )


def _parse_json(text):
    """Extract JSON from LLM response. Handles:
    - Markdown fences (```json ... ```)
    - Preamble text ("Here is the JSON: ...")
    - Trailing commas  
    - Truncated arrays (max_tokens cut off mid-JSON)
    - Gemini wrapping response in extra explanation
    """
    if not text:
        return None

    # Strip markdown fences — all variants
    clean = text.strip()
    for fence in ["```json", "```JSON", "```", "`"]:
        clean = clean.replace(fence, "")
    clean = clean.strip()

    # Strip common LLM preamble lines before JSON
    # Gemini often says "Here is the extracted JSON:" or "```json\n{..."
    lines = clean.split('\n')
    # Find the first line that starts with { or [
    json_start_line = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('{') or stripped.startswith('['):
            json_start_line = i
            break
    if json_start_line > 0:
        clean = '\n'.join(lines[json_start_line:]).strip()

    # Strategy 1: direct parse
    try:
        return json.loads(clean)
    except Exception:
        pass

    # Strategy 2: find the outermost { } (spec extraction returns a dict)
    result = _bracket_extract(clean, '{', '}')
    if result is not None:
        return result

    # Strategy 3: find the outermost [ ] (BOM generation returns an array)
    result = _bracket_extract(clean, '[', ']')
    if result is not None:
        return result

    # Strategy 4: TRUNCATED ARRAY RECOVERY
    arr_start = clean.find('[')
    if arr_start != -1:
        recovered = _recover_truncated_array(clean[arr_start:])
        if recovered and len(recovered) > 1:
            return recovered

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

    # Save raw response so app.py can show it for debugging if parse fails
    try:
        import streamlit as st
        st.session_state["_last_raw_response"] = raw[:4000] if raw else ""
    except Exception:
        pass

    data = _parse_json(raw)

    # If parse completely failed, build a minimal shell so the app doesn't
    # silently freeze — user will see the debug output in app.py
    if not data or not isinstance(data, dict):
        return None

    # Ensure pumps key always exists as a list
    if "pumps" not in data or not isinstance(data.get("pumps"), list):
        # Sometimes Gemini returns the pump object directly at root level
        if any(k in data for k in ["flow_m3h", "head_m", "motor_kw", "fluid", "type"]):
            data = {"pumps": [data], "document_type": "vendor_datasheet",
                    "multi_pump": False, "manufacturer": data.get("manufacturer")}
        else:
            data["pumps"] = []

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
# STEP 3 — SHOULD-COST ENGINE (Claude + Web Search)
# ═══════════════════════════════════════════════════════════════════
#
# Simple, clean logic for presentation:
#
#  For MANUFACTURED components (casing, impeller, shaft, etc.):
#    Raw Material Cost = weight × live ₹/kg (from SAIL/LME/IndiaMART)
#    + Machining Cost  = operations × shop rate
#    ─────────────────────────────────────────────
#    = Manufacturing Cost (NO overhead, NO margin)
#
#  For BOUGHT-OUT items (motor, bearings, seals):
#    = Current market price from IndiaMART/TradeIndia
#      (what the supplier actually pays to procure)
#
#  Total Should-Cost vs Actual PO Price = Supplier's margin
# ═══════════════════════════════════════════════════════════════════

# ── COMPONENT CLASSIFIER ─────────────────────────────────────────
def _classify_component(component_name, moc="", description=""):
    """Classify as 'manufactured', 'bought_out', or 'compliance'."""
    comp = (component_name or "").upper()

    # BOUGHT-OUT: supplier procures from OEM, doesn't make
    if "MOTOR" in comp and not any(x in comp for x in
            ["BOLT", "MOUNT", "BASE", "PULLEY", "SIDE", "BRACKET", "HOUSING"]):
        return "bought_out", "motor"
    if "BEARING" in comp and "HOUSING" not in comp:
        return "bought_out", "bearing"
    if any(x in comp for x in ["MECHANICAL SEAL", "MECH SEAL", "CARTRIDGE SEAL"]):
        return "bought_out", "seal"
    if any(x in comp for x in ["V-BELT", "V BELT", "VBELT"]):
        return "bought_out", "vbelt"
    if "COMPANION FLANGE" in comp or "COUNTER FLANGE" in comp:
        return "bought_out", "flange_set"
    if "GASKET" in comp:
        return "bought_out", "gasket"
    if "FOUNDATION" in comp and "BOLT" in comp:
        return "bought_out", "fastener"
    if any(x in comp for x in ["FIRST FILL", "GREASE", "LUBRICANT"]):
        return "bought_out", "lubricant"
    if any(x in comp for x in ["RTD", "THERMOCOUPLE", "PRESSURE GAUGE",
                                "VIBRATION SWITCH"]):
        return "bought_out", "instrument"
    if "GUARD" in comp:
        return "bought_out", "guard"
    if "SLD" in comp:
        return "bought_out", "seal_accessory"

    # COMPLIANCE: no separate cost
    if any(x in comp for x in ["COMPLETE ASSEMBLY", "NOISE LEVEL",
                                "VIBRATION LIMIT", "PERFORMANCE TEST",
                                "SURFACE PREP", "FACTORY TEST"]):
        return "compliance", "none"

    # MANUFACTURED: everything else
    return "manufactured", "general"


# ── CLAUDE WEB-SEARCH PRICING ────────────────────────────────────
def _call_claude_with_search(prompt, system="", max_tokens=3000):
    """Call Claude with web_search tool enabled for live price lookup."""
    key = _get_api_key("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    try:
        import anthropic
    except ImportError:
        raise ValueError("pip install anthropic needed")

    client = anthropic.Anthropic(api_key=key)
    kwargs = {
        "model":     "claude-sonnet-4-5",
        "max_tokens": max_tokens,
        "tools":     [{"type": "web_search_20250305", "name": "web_search"}],
        "messages":  [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    for attempt in range(3):
        try:
            resp = client.messages.create(**kwargs)
            parts = [b.text for b in resp.content if hasattr(b, "text")]
            return "\n".join(parts).strip()
        except Exception as e:
            if "429" in str(e) or "rate_limit" in str(e).lower():
                time.sleep(30 * (2 ** attempt))
            else:
                raise
    raise Exception("Claude rate limit exceeded.")


SHOULD_COST_SYSTEM = """You are a procurement cost engineer at an Indian EPC company.

Your job: find the RAW MANUFACTURING COST of pump/valve components.
This means ONLY: Raw Material + Machining. NO overhead. NO profit. NO markup.

You MUST use web_search to find CURRENT prices (2025-26) from:
- SAIL, RINL, MSTC for steel/alloy prices
- IndiaMART for castings, forgings, machined parts
- LME India for non-ferrous metals
- TradeIndia for bought-out items

Be specific. Search for the exact grade mentioned in MOC.
Give numbers you can justify from search results.
All amounts in Indian Rupees (INR). Be conservative (use lower end of range)."""


def claude_price_bom(bom_df, pump_specs, progress_callback=None):
    """
    Should-cost engine: Claude searches live prices for each component.
    Returns Raw Material Cost + Machining Cost per component.
    NO overhead, NO margin — just bare manufacturing cost.
    Compare total with actual PO to see supplier's margin.
    """
    if bom_df is None or bom_df.empty:
        return bom_df

    pump = pump_specs if isinstance(pump_specs, dict) else {}
    pump_fluid  = pump.get("fluid", "")
    pump_kw     = pump.get("motor_kw", "")
    pump_type   = pump.get("type", "centrifugal pump")

    if progress_callback:
        progress_callback(8, "Classifying components...")

    # Classify all components
    classified = []
    for _, row in bom_df.iterrows():
        comp = str(row.get("Component", ""))
        moc  = str(row.get("MOC", ""))
        desc = str(row.get("Description", ""))
        cat, sub = _classify_component(comp, moc, desc)
        classified.append({
            "row":    row.to_dict(),
            "no":     row.get("No", 0),
            "comp":   comp,
            "moc":    moc,
            "weight": row.get("Weight_kg"),
            "qty":    str(row.get("Qty", "1")),
            "cat":    cat,
            "sub":    sub,
        })

    mfg_items  = [c for c in classified if c["cat"] == "manufactured"]
    bo_items   = [c for c in classified if c["cat"] == "bought_out"]
    comp_items = [c for c in classified if c["cat"] == "compliance"]

    if progress_callback:
        progress_callback(12, f"{len(mfg_items)} manufactured, "
                              f"{len(bo_items)} bought-out to price...")

    result_rows = []

    # ── PRICE MANUFACTURED ITEMS (batch of 4) ────────────────────
    BATCH = 4
    for i in range(0, len(mfg_items), BATCH):
        batch = mfg_items[i:i + BATCH]
        pct   = 15 + int((i / max(len(mfg_items), 1)) * 50)
        names = ", ".join(c["comp"][:20] for c in batch)
        if progress_callback:
            progress_callback(pct, f"Searching live prices: {names}...")

        items_json = json.dumps([{
            "no":        c["no"],
            "component": c["comp"],
            "moc":       c["moc"],
            "weight_kg": c["weight"],
            "qty":       c["qty"],
        } for c in batch], default=str)

        prompt = f"""Find the RAW MANUFACTURING COST for these {pump_type} components.
Fluid service: {pump_fluid}. Motor: {pump_kw} kW.

Use web_search to find CURRENT 2025-26 Indian market prices for each material grade.

Components:
{items_json}

For each component calculate:
1. RAW MATERIAL COST:
   - Search: "[MOC grade] price per kg India 2025" (e.g. "SS316 plate price India 2025")
   - Use SAIL/RINL for steel, LME India for Cu/Al, IndiaMART for castings
   - Gross weight = finished weight × 1.35 for castings, × 1.15 for bar/forging
   - Raw material cost = gross weight × ₹/kg × qty

2. MACHINING COST (realistic for the component type):
   - Casting: pattern+moulding ₹15-20/kg gross weight
   - CNC turning/boring: ₹1000-1400/hr, estimate realistic hours
   - Fabrication (welding+cutting): ₹90-120/kg
   - Simple parts (keys, sleeves): ₹400-600/hr conventional

3. NO overhead. NO margin. Just raw material + machining.

Return JSON array ONLY:
[{{
  "no": <item no>,
  "raw_material_rate_inr_per_kg": <₹/kg searched>,
  "material_source": "SAIL/RINL/IndiaMART/etc + search query used",
  "gross_weight_kg": <with allowance>,
  "raw_material_cost_inr": <int>,
  "machining_operations": "brief: op1 Xhr + op2 Xhr",
  "machining_cost_inr": <int>,
  "total_manufacturing_cost_inr": <raw + machining>,
  "confidence": "high|medium|low",
  "notes": "one line basis"
}}]"""

        try:
            raw = _call_claude_with_search(prompt, system=SHOULD_COST_SYSTEM)
            parsed = _parse_json(raw)
            if not isinstance(parsed, list):
                parsed = [parsed] if isinstance(parsed, dict) else []
            price_map = {p["no"]: p for p in parsed
                         if isinstance(p, dict) and "no" in p}

            for c in batch:
                rd  = c["row"].copy()
                p   = price_map.get(c["no"], {})
                raw_cost  = int(p.get("raw_material_cost_inr", 0))
                mach_cost = int(p.get("machining_cost_inr", 0))
                total     = int(p.get("total_manufacturing_cost_inr",
                                      raw_cost + mach_cost))
                gst = int(total * 0.18)

                rd["Raw_Material_INR"]      = raw_cost
                rd["Machining_INR"]         = mach_cost
                rd["Total_Manufacturing_INR"] = total
                rd["Unit_Price_INR"]        = total
                rd["Total_Price_INR"]       = total
                rd["GST_18pct"]             = gst
                rd["Price_With_GST"]        = total + gst
                rd["Price_Confidence"]      = p.get("confidence", "medium")
                rd["Price_Source"]          = p.get("material_source", "")
                rd["Price_Notes"]           = (
                    f"RM: ₹{p.get('raw_material_rate_inr_per_kg',0)}/kg "
                    f"× {p.get('gross_weight_kg',0)}kg | "
                    f"Machining: {p.get('machining_operations','')}"
                )
                rd["Component_Type"]        = "manufactured"
                result_rows.append(rd)

        except Exception as e:
            for c in batch:
                rd = c["row"].copy()
                rd.update(_zero_cost_row("manufactured", f"Error: {str(e)[:80]}"))
                result_rows.append(rd)

        # Small delay between batches
        if i + BATCH < len(mfg_items):
            time.sleep(3)

    # ── PRICE BOUGHT-OUT ITEMS (batch of 6) ──────────────────────
    BO_BATCH = 6
    for i in range(0, len(bo_items), BO_BATCH):
        batch = bo_items[i:i + BO_BATCH]
        pct   = 67 + int((i / max(len(bo_items), 1)) * 22)
        names = ", ".join(c["comp"][:20] for c in batch)
        if progress_callback:
            progress_callback(pct, f"Live market price: {names}...")

        items_json = json.dumps([{
            "no":        c["no"],
            "component": c["comp"],
            "moc":       c["moc"],
            "weight_kg": c["weight"],
            "qty":       c["qty"],
        } for c in batch], default=str)

        prompt = f"""Find the current MARKET PRICE for these bought-out items.
This is what the pump vendor PAYS to procure them from OEM.
Pump: {pump_type}, {pump_kw} kW, fluid: {pump_fluid}.

Use web_search to find CURRENT 2025-26 prices from IndiaMART/TradeIndia/IEEMA.

Items:
{items_json}

Search examples:
- Motor: "ABB Siemens {pump_kw}kW HT motor price India 2025"
- Bearing: "SKF TIMKEN taper roller bearing price India"
- Seal: "EagleBurgmann mechanical seal price India"
- Flanges: "ANSI 300 flange price per kg India"

Return JSON array ONLY:
[{{
  "no": <item no>,
  "market_price_inr": <what vendor pays to OEM — integer>,
  "search_source": "IndiaMART/TradeIndia/IEEMA/vendor site",
  "search_query": "exact search query used",
  "confidence": "high|medium|low",
  "notes": "model/size/make referenced"
}}]"""

        try:
            raw = _call_claude_with_search(prompt, system=SHOULD_COST_SYSTEM)
            parsed = _parse_json(raw)
            if not isinstance(parsed, list):
                parsed = [parsed] if isinstance(parsed, dict) else []
            price_map = {p["no"]: p for p in parsed
                         if isinstance(p, dict) and "no" in p}

            for c in batch:
                rd    = c["row"].copy()
                p     = price_map.get(c["no"], {})
                price = int(p.get("market_price_inr", 0))
                gst   = int(price * 0.18)

                rd["Raw_Material_INR"]        = price
                rd["Machining_INR"]           = 0
                rd["Total_Manufacturing_INR"] = price
                rd["Unit_Price_INR"]          = price
                rd["Total_Price_INR"]         = price
                rd["GST_18pct"]               = gst
                rd["Price_With_GST"]          = price + gst
                rd["Price_Confidence"]        = p.get("confidence", "medium")
                rd["Price_Source"]            = p.get("search_source", "")
                rd["Price_Notes"]             = (
                    f"Query: {p.get('search_query','')} | "
                    f"{p.get('notes','')}"
                )
                rd["Component_Type"]          = "bought_out"
                result_rows.append(rd)

        except Exception as e:
            for c in batch:
                rd = c["row"].copy()
                rd.update(_zero_cost_row("bought_out", f"Error: {str(e)[:80]}"))
                result_rows.append(rd)

        if i + BO_BATCH < len(bo_items):
            time.sleep(3)

    # ── COMPLIANCE / ZERO-COST ────────────────────────────────────
    for c in comp_items:
        rd = c["row"].copy()
        rd.update(_zero_cost_row("compliance", "Compliance/assembly item"))
        result_rows.append(rd)

    if progress_callback:
        progress_callback(92, "Compiling should-cost report...")

    return pd.DataFrame(result_rows)


def _zero_cost_row(comp_type, note=""):
    return {
        "Raw_Material_INR": 0, "Machining_INR": 0,
        "Total_Manufacturing_INR": 0,
        "Unit_Price_INR": 0, "Total_Price_INR": 0,
        "GST_18pct": 0, "Price_With_GST": 0,
        "Price_Confidence": "none" if comp_type != "compliance" else "high",
        "Price_Source": note, "Price_Notes": note,
        "Component_Type": comp_type,
    }


def build_cost_summary(priced_df):
    """Build should-cost summary. No overhead/margin — raw manufacturing only."""
    if priced_df is None or priced_df.empty:
        return {}

    def _s(col):
        return int(priced_df[col].sum()) if col in priced_df.columns else 0

    total_raw  = _s("Raw_Material_INR")
    total_mach = _s("Machining_INR")
    total_mfg  = _s("Total_Manufacturing_INR")
    total_gst  = _s("GST_18pct")
    total_inc  = _s("Price_With_GST")

    sub_col    = "Sub_Assembly" if "Sub_Assembly" in priced_df.columns else "Section"
    sub_totals = (priced_df.groupby(sub_col)["Total_Price_INR"]
                  .sum().sort_values(ascending=False).to_dict()
                  if "Total_Price_INR" in priced_df.columns else {})

    top5 = (priced_df.nlargest(5, "Total_Price_INR")
            [["Component", "Total_Price_INR",
              "Raw_Material_INR", "Machining_INR",
              "Price_Confidence", "Price_Source"]]
            .to_dict("records")) if "Total_Price_INR" in priced_df.columns else []

    conf       = priced_df["Price_Confidence"].value_counts().to_dict() \
                 if "Price_Confidence" in priced_df.columns else {}
    type_split = priced_df["Component_Type"].value_counts().to_dict() \
                 if "Component_Type" in priced_df.columns else {}

    return {
        "total_raw_material":  total_raw,
        "total_machining":     total_mach,
        "total_ex_gst":        total_mfg,
        "total_gst":           total_gst,
        "total_incl_gst":      total_inc,
        "sub_totals":          {k: int(v) for k, v in sub_totals.items()},
        "top5_drivers":        top5,
        "confidence":          conf,
        "component_count":     len(priced_df),
        "type_split":          type_split,
        "note": (
            "Should-Cost = Raw Material (live prices) + Machining only. "
            "NO overhead, NO margin. "
            "Difference vs actual PO = supplier's overhead + profit."
        ),
    }
#
#  MANUFACTURED items (supplier makes in his shop):
#  ┌─────────────────────────────────────────────────────────────┐
#  │ 1. Raw Material    = weight × LME/SAIL/market rate (₹/kg)  │
#  │    → LLM searches: "SS316 casting scrap price India today"  │
#  │ 2. Machining       = hours × shop rate (₹/hr)              │
#  │    → LLM searches: "CNC turning rate India per hour 2025"   │
#  │ 3. Welding/FAB     = joints × weld rate or ₹/kg fab        │
#  │ 4. Surface treat   = area/weight × treatment rate           │
#  │ 5. QC/Testing      = % of manufactured cost                 │
#  │ ─────────────────────────────────────────────              │
#  │    Manufactured Cost = sum of above                         │
#  └─────────────────────────────────────────────────────────────┘
#
#  BOUGHT-OUT items (supplier just procures from OEM):
#  ┌─────────────────────────────────────────────────────────────┐
#  │ 1. OEM market price → LLM searches IndiaMART/TradeIndia    │
#  │ 2. Supplier markup  = 5–8% on bought-out                   │
#  │ ─────────────────────────────────────────────              │
#  │    Bought-out cost = OEM price + markup                     │
#  └─────────────────────────────────────────────────────────────┘
#
#  SUPPLIER OVERHEAD on total manufactured cost:
#  ┌─────────────────────────────────────────────────────────────┐
#  │ Factory overhead   = 25–35% of manufactured cost            │
#  │ Profit margin      = 10–15% of (manufactured + overhead)    │
#  │ ─────────────────────────────────────────────              │
#  │ SELLING PRICE = manufactured + overhead + margin            │
#  └─────────────────────────────────────────────────────────────┘
#
# The LLM searches the internet for EACH rate, not the final price.
# This makes the model transparent and verifiable.
# ═══════════════════════════════════════════════════════════════════

# ── CLASSIFICATION: what type is each component ──────────────────
# Determines which cost model to apply

def _classify_component(component_name, moc="", description=""):
    """Classify component as 'manufactured', 'bought_out', or 'compliance'.
    Returns (category, sub_type)."""
    comp = (component_name or "").upper()
    moc_u = (moc or "").upper()

    # ── BOUGHT-OUT: supplier doesn't make, just buys from OEM ─────
    if any(x in comp for x in ["ELECTRIC MOTOR", "SCIM", "SQUIRREL CAGE"]):
        return "bought_out", "motor"
    if "MOTOR" in comp and not any(x in comp for x in
            ["BOLT", "MOUNT", "BASE", "PULLEY", "SIDE", "BRACKET", "HOUSING"]):
        return "bought_out", "motor"
    if "BEARING" in comp and "HOUSING" not in comp and "BRACKET" not in comp:
        return "bought_out", "bearing"
    if any(x in comp for x in ["MECHANICAL SEAL", "MECH SEAL", "CARTRIDGE SEAL",
                                "LIP SEAL", "OIL SEAL"]):
        return "bought_out", "seal"
    if "SLD" in comp and "DEVICE" in comp:
        return "bought_out", "seal_accessory"
    if any(x in comp for x in ["V-BELT", "V BELT", "VBELT", "TIMING BELT"]):
        return "bought_out", "vbelt"
    if "COMPANION FLANGE" in comp or "COUNTER FLANGE" in comp:
        return "bought_out", "flange_set"
    if any(x in comp for x in ["SPIRAL WOUND GASKET", "RING JOINT GASKET",
                                "COMPRESSED FIBRE GASKET"]):
        return "bought_out", "gasket"
    if "FOUNDATION" in comp and "BOLT" in comp:
        return "bought_out", "foundation_bolt"
    if "ANCHOR BOLT" in comp:
        return "bought_out", "foundation_bolt"
    if any(x in comp for x in ["FIRST FILL", "GREASE FILL", "OIL FILL",
                                "LUBRICANT FILL"]):
        return "bought_out", "lubricant"
    if "BELT GUARD" in comp or "COUPLING GUARD" in comp:
        return "bought_out", "guard"
    if any(x in comp for x in ["RTD", "THERMOCOUPLE", "DIAL THERMOMETER",
                                "PRESSURE GAUGE", "VIBRATION SWITCH",
                                "LEVEL SWITCH", "FLOW METER"]):
        return "bought_out", "instrument"
    if "GASKET" in comp:
        return "bought_out", "gasket"

    # ── COMPLIANCE / ZERO COST ────────────────────────────────────
    if any(x in comp for x in ["COMPLETE ASSEMBLY", "COMPLETE PUMP",
                                "NOISE LEVEL", "VIBRATION LIMIT",
                                "SURFACE PREP", "PERFORMANCE TEST",
                                "FACTORY TEST", "HYDRO TEST"]):
        return "compliance", "none"

    # ── MANUFACTURED: supplier makes in his workshop ──────────────
    if "CASING" in comp and "BOLT" not in comp:
        if any(x in comp for x in ["PUMP CASING", "VOLUTE", "SPIRAL"]):
            return "manufactured", "pump_casing"
        if "BEARING" in comp:
            return "manufactured", "bearing_housing"
        return "manufactured", "casing_general"

    if "IMPELLER" in comp and "WEAR" not in comp and "NUT" not in comp:
        return "manufactured", "impeller"
    if "WEAR RING" in comp:
        return "manufactured", "wear_ring"
    if "SHAFT SLEEVE" in comp or "SLEEVE" in comp:
        return "manufactured", "shaft_sleeve"
    if "SHAFT" in comp and "KEY" not in comp and "SLEEVE" not in comp:
        return "manufactured", "shaft"
    if "BEARING HOUSING" in comp or "PEDESTAL" in comp:
        return "manufactured", "bearing_housing"
    if "GLAND" in comp or "SEAL PLATE" in comp or "STUFFING BOX" in comp:
        return "manufactured", "gland_plate"
    if "PULLEY" in comp or "SHEAVE" in comp:
        return "manufactured", "pulley"
    if any(x in comp for x in ["BASE FRAME", "BASEPLATE", "BASE PLATE",
                                "COMMON BASE", "SKID"]):
        return "manufactured", "baseframe"
    if "KEY" in comp or "KEYWAY" in comp:
        return "manufactured", "key"
    if any(x in comp for x in ["BOLT", "NUT", "STUD", "FASTENER",
                                "HARDWARE", "SCREW"]):
        return "manufactured", "fastener"
    if "NOZZLE" in comp:
        return "manufactured", "nozzle"
    if "DIFFUSER" in comp or "RETURN CHANNEL" in comp:
        return "manufactured", "diffuser"
    if "COUPLING HALF" in comp or "HUB" in comp:
        return "manufactured", "coupling_half"
    if "DIAPHRAGM" in comp:
        return "manufactured", "diaphragm"

    # Default: manufactured
    return "manufactured", "general"


# ── SHOULD-COST SYSTEM PROMPT ─────────────────────────────────────
SHOULD_COST_SYSTEM = """You are a senior cost engineer at an Indian EPC company.
Your job is to estimate the MANUFACTURING COST (should-cost) of engineered components.

This is NOT the market selling price. This is what it actually COSTS the supplier to make.

For each component you MUST break down:
1. Raw Material Cost: actual grade × weight × current Indian market rate (₹/kg)
   - Search "SS316 scrap price India 2025" or "IS 2062 steel plate price"
   - Use SAIL, RINL, LME India, or Metal Bulletin rates
   - This is raw stock cost, NOT finished price

2. Machining/Processing Cost: based on actual operations needed
   - Turning/milling: ₹800-1500/hr for CNC, ₹400-600/hr for conventional
   - Welding: ₹500-900/hr MIG/TIG, ₹300-500/hr arc
   - Pattern+moulding (castings): ₹15-25/kg of casting weight
   - Heat treatment: ₹8-15/kg
   - Estimate hours realistically from complexity

3. Surface Treatment: painting, plating, shot blast
   - Industrial primer+topcoat: ₹25-40/sqm
   - Shot blasting: ₹8-12/sqm

4. Quality/Testing: 2-4% of manufacturing cost for routine, 5-8% for critical

The LLM should SEARCH for current rates, not guess from memory.
Give actual numbers with source. Be conservative (lower bound of range).
All amounts in Indian Rupees (INR)."""


def _build_should_cost_prompt(component, moc, weight_kg, qty, sub_type,
                               pump_specs, category):
    """Build a targeted search+calculate prompt for one component or batch."""

    pump_fluid = (pump_specs or {}).get("fluid", "water")
    pump_kw    = (pump_specs or {}).get("motor_kw", "")

    if category == "bought_out":
        return f"""Find the current MARKET PRICE (what the pump vendor PAYS to procure this):

Component: {component}
MOC/Spec:  {moc}
Qty:       {qty}
Pump fluid: {pump_fluid}
Pump size:  {pump_kw} kW

Search IndiaMART / TradeIndia for current Indian market price.
The vendor adds 5-8% markup on top when selling to us.

Return JSON:
{{
  "component": "{component}",
  "oem_market_price_inr": <integer, what vendor pays to OEM>,
  "vendor_markup_pct": <5 to 8>,
  "vendor_cost_inr": <oem_price + markup>,
  "search_source": "IndiaMART / TradeIndia / IEEMA / etc",
  "basis": "brief explanation of how you arrived at this price"
}}"""

    else:  # manufactured
        return f"""Calculate the MANUFACTURING COST (should-cost) for this component.
Break down every cost layer. Search for current Indian rates.

Component:   {component}
Material:    {moc}
Weight:      {weight_kg} kg (finished)
Qty:         {qty}
Sub-type:    {sub_type}
Pump fluid:  {pump_fluid}

Step 1 — Search current raw material rate for "{moc}" in India (₹/kg):
  Examples: SAIL HR coil, RINL billet, SS316 scrap, A532 high chrome castings
  Gross weight for castings = finished weight × 1.35 (allowance for risers/gates)
  Gross weight for forgings/bar = finished weight × 1.15

Step 2 — List machining operations needed and estimate hours:
  e.g. rough turning 2hr, finish bore 1hr, face milling 0.5hr, heat treat...

Step 3 — Any surface treatment? (painting, plating, shot blast)

Step 4 — QC/testing cost (% of manufacturing)

Return JSON:
{{
  "component": "{component}",
  "raw_material_rate_per_kg": <current ₹/kg from search>,
  "gross_weight_kg": <with allowance>,
  "raw_material_cost_inr": <rate × gross_weight × qty>,
  "machining_operations": ["op1 X hrs", "op2 Y hrs"],
  "machining_cost_inr": <total machining>,
  "surface_treatment_cost_inr": <0 if none>,
  "qc_cost_inr": <2-5% of above total>,
  "total_manufacturing_cost_inr": <sum of all above>,
  "material_source": "SAIL/RINL/LME/IndiaMART/etc",
  "basis": "brief calculation summary"
}}"""


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
