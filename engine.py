"""
BOM Generation Engine v2.0
──────────────────────────
Tier 1  : Exact / close match from Pump_Master_List database
Tier 2  : Physics-backed classification + material selection
Learning: Feedback logger, pattern learner, classifier corrector,
          weight calibrator — all persisted to learning_data.json

Author  : Ayush Kamle
Stack   : Pure Python — pandas, openpyxl, re, math, json
No external ML libraries. No API keys.
"""

import os, re, math, json, datetime
import pandas as pd
from io import BytesIO

# ─────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────
_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(_DIR, "Component_Library_COMPLETE.xlsx")
LRN_PATH = os.path.join(_DIR, "learning_data.json")


# ═══════════════════════════════════════════════════════════════════
# SECTION 1 — DATABASE LOADER
# ═══════════════════════════════════════════════════════════════════

def load_db():
    return {
        "pumps":      pd.read_excel(DB_PATH, sheet_name="Pump_Master_List"),
        "comps":      pd.read_excel(DB_PATH, sheet_name="Component_Library"),
        "mats":       pd.read_excel(DB_PATH, sheet_name="Material_Database"),
        "vendors":    pd.read_excel(DB_PATH, sheet_name="Vendor_Database"),
        "bom_tpl":    pd.read_excel(DB_PATH, sheet_name="BOM_Templates",          header=4),
        "physics":    pd.read_excel(DB_PATH, sheet_name="Physics_Parameters",     header=4),
        "mat_compat": pd.read_excel(DB_PATH, sheet_name="Material_Compatibility", header=4),
    }


# ═══════════════════════════════════════════════════════════════════
# SECTION 2 — LEARNING DATA STORE
# ═══════════════════════════════════════════════════════════════════

_DEFAULT = {
    "feedback":      [],
    "patterns":      [],
    "corrections":   [],
    "weight_calibs": {},
    "stats": {
        "total_sessions": 0,
        "tier1_hits":     0,
        "tier2_hits":     0,
        "corrections":    0,
        "patterns_added": 0,
    },
}

def get_store():
    if os.path.exists(LRN_PATH):
        try:
            with open(LRN_PATH) as f:
                data = json.load(f)
            for k, v in _DEFAULT.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            pass
    return {k: (v.copy() if isinstance(v, dict) else list(v))
            for k, v in _DEFAULT.items()}

def _save(store):
    try:
        with open(LRN_PATH, "w") as f:
            json.dump(store, f, indent=2, default=str)
    except Exception:
        pass

# ── Public learning functions ────────────────────────────────────

def log_feedback(specs, bom_df, tier,
                 confirmed_pump_type, confirmed_moc,
                 confirmed_weights, engineer_notes=""):
    """Called when engineer clicks Confirm & Learn."""
    store = get_store()
    ns    = _ns_from_specs(specs)
    store["feedback"].append({
        "ts":        datetime.datetime.now().isoformat(),
        "specs":     {k: v for k, v in specs.items() if v is not None},
        "tier":      tier,
        "pump_type": confirmed_pump_type,
        "moc":       confirmed_moc,
        "weights":   confirmed_weights,
        "bom_rows":  len(bom_df),
        "notes":     engineer_notes,
        "ns":        ns,
    })
    store["stats"]["total_sessions"] += 1
    if tier == "tier1": store["stats"]["tier1_hits"] += 1
    else:               store["stats"]["tier2_hits"] += 1

    _update_weight_calibs(store, confirmed_pump_type,
                          confirmed_weights, specs)
    _save(store)


def log_pattern(field, bad_value, correct_value,
                raw_text_snippet, notes=""):
    """Engineer teaches the parser a new extraction pattern."""
    store = get_store()
    store["patterns"].append({
        "ts":      datetime.datetime.now().isoformat(),
        "field":   field,
        "bad":     str(bad_value),
        "correct": str(correct_value),
        "snippet": raw_text_snippet[:200],
        "notes":   notes,
    })
    store["stats"]["patterns_added"] += 1
    _save(store)


def log_correction(specs, wrong_type, correct_type, notes=""):
    """Engineer overrides Tier 2 pump classification."""
    store = get_store()
    store["corrections"].append({
        "ts":          datetime.datetime.now().isoformat(),
        "ns":          _ns_from_specs(specs),
        "fluid":       (specs.get("fluid") or ""),
        "flow":        specs.get("flow_m3h"),
        "head":        specs.get("head_m"),
        "wrong_type":  wrong_type,
        "correct_type":correct_type,
        "notes":       notes,
    })
    store["stats"]["corrections"] += 1
    _save(store)


def get_learned_correction(Ns, fluid):
    """
    Check if a previous engineer correction covers this Ns + fluid.
    Returns correct_type string or None.
    Match criteria: Ns within ±30%, fluid keyword overlap.
    """
    store   = get_store()
    fl      = (fluid or "").lower()
    for corr in reversed(store.get("corrections", [])):
        c_ns = corr.get("ns")
        c_fl = (corr.get("fluid") or "").lower()
        ns_match = (
            c_ns is None or Ns is None or
            (Ns > 0 and abs(c_ns - Ns) / max(Ns, 1) < 0.30)
        )
        fl_match = any(w in c_fl for w in fl.split()[:3]) or \
                   any(w in fl   for w in c_fl.split()[:3])
        if ns_match and fl_match and corr.get("correct_type"):
            return corr["correct_type"]
    return None


# ── Weight calibration ───────────────────────────────────────────

def _update_weight_calibs(store, pump_type, weights, specs):
    if not weights:
        return
    pt  = (pump_type or "").lower()[:20]
    kw  = float(specs.get("motor_kw") or 30)
    if pt not in store["weight_calibs"]:
        store["weight_calibs"][pt] = {
            "pump_coeff": 1.0, "motor_coeff": 1.0,
            "n_samples": 0,   "last_updated": None,
        }
    cal = store["weight_calibs"][pt]
    n   = cal["n_samples"] + 1

    pred_pump  = _base_pump_wt(pt, kw)
    pred_motor = _base_motor_wt(kw)
    act_pump   = float(weights.get("pump_kg",  0) or 0)
    act_motor  = float(weights.get("motor_kg", 0) or 0)

    if pred_pump  > 0 and act_pump  > 0:
        r = act_pump / pred_pump
        cal["pump_coeff"]  = (cal["pump_coeff"]  * (n-1) + r) / n
    if pred_motor > 0 and act_motor > 0:
        r = act_motor / pred_motor
        cal["motor_coeff"] = (cal["motor_coeff"] * (n-1) + r) / n

    cal["n_samples"]    = n
    cal["last_updated"] = datetime.datetime.now().isoformat()

def _base_pump_wt(pt, kw):
    if "slurry"  in pt: return 1.8  * kw**0.85
    if "turbine" in pt: return 0.45 * kw**0.9 * 6**0.3
    if "sump"    in pt: return 0.9  * kw**0.85
    return 2.1 * kw**0.72

def _base_motor_wt(kw):
    return 6.2 * kw**0.80 if kw > 200 else 8.5 * kw**0.75

def _ns_from_specs(specs):
    Q = specs.get("flow_m3h")
    H = specs.get("head_m")
    n = specs.get("speed_rpm") or 1450
    if Q and H and Q > 0 and H > 0:
        try:   return round(calc_specific_speed(Q, H, n), 1)
        except Exception: pass
    return None


# ═══════════════════════════════════════════════════════════════════
# SECTION 3 — PDF TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════════════

def extract_pdf_text(file_bytes):
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
# SECTION 4 — SPEC PARSER  (with learned patterns)
# ═══════════════════════════════════════════════════════════════════

_PATTERNS = {
    "flow_m3h": [
        r"(?:[Rr]ated\s+)?(?:[Vv]olumetric\s+)?[Ff]low\s*(?:[Rr]ate|[Cc]apacity)?\s*[:\-=]?\s*(\d+\.?\d*)\s*m3/h",
        r"[Cc]apacity\s*[:\-=]?\s*(\d+\.?\d*)\s*m3/h",
        r"[Ff]low\s*[:\-=]?\s*(\d+\.?\d*)\s*m\xb3/h",
        r"\bQ\s*[=:\-]\s*(\d+\.?\d*)\s*m3",
        r"(\d+\.?\d*)\s*m3/hr",
        r"\b(\d{3,5}\.?\d*)\s*LPM\b",
        r"\b(\d{3,5}\.?\d*)\s*L/[Mm]in\b",
    ],
    "head_m": [
        r"[Pp]ump\s+[Rr]ated\s+[Hh]ead\s*[:\-=]?\s*(\d+\.?\d*)\s*m\b",
        r"[Tt]otal\s+[Hh]ead\s*[:\-=]?\s*(\d+\.?\d*)\s*m\b",
        r"[Rr]ated\s+[Hh]ead\s*[:\-=]?\s*(\d+\.?\d*)\s*m\b",
        r"[Bb]owl\s+[Hh]ead\s*[:\-=]?\s*(\d+\.?\d*)\s*m\b",
        r"\bH\s*[=:\-]\s*(\d+\.?\d*)\s*m\b",
        r"[Hh]ead\s*[:\-=]?\s*(\d+\.?\d*)\s*m\b",
        r"(\d{1,3}\.?\d*)\s*[Mm]tr\b",
    ],
    "speed_rpm": [
        r"[Ff]ull\s+[Ll]oad\s+[Ss]peed.*?(\d{3,4})\s*[Rr][Pp][Mm]",
        r"[Rr]ated\s+[Ss]peed.*?(\d{3,4})\s*[Rr][Pp][Mm]",
        r"[Ss]peed\s*[:\-=]?\s*(\d{3,4})\s*[Rr][Pp][Mm]",
        r"(\d{3,4})\s*[Rr][Pp][Mm]",
        r"[Mm]otor\s+[Ss]peed.*?(\d{3,4})",
    ],
    "motor_kw": [
        r"[Mm]otor\s+[Rr]ating\s*[:\-=]?\s*(\d+\.?\d*)\s*[Kk][Ww]",
        r"[Nn]ominal\s+[Pp]ower\s*[:\-=]?\s*(\d+\.?\d*)\s*[Kk][Ww]",
        r"[Rr]ated\s+[Oo]utput.*?(\d+\.?\d*)\s*[Kk][Ww]",
        r"[Pp]rime\s*[Mm]over\s+[Pp]ower.*?(\d+\.?\d*)",
        r"(\d+\.?\d*)\s*[Kk][Ww]\s+(?:motor|Motor|MOTOR|nameplate)",
        r"(?:motor|Motor|MOTOR).*?(\d+\.?\d*)\s*[Kk][Ww]",
        r"(\d+\.?\d*)\s*[Hh][Pp]\b",           # HP — converted below
    ],
    "temp_c": [
        r"[Oo]p(?:erating)?\s+[Tt]emp(?:erature)?\s*[:\-=]?\s*(\d+\.?\d*)\s*[°\xb0]?[Cc]",
        r"[Pp]rocess\s+[Tt]emp(?:erature)?\s*[:\-=]?\s*(\d+\.?\d*)\s*[°\xb0]?[Cc]",
        r"[Tt]emp(?:erature)?\s*[:\-=]?\s*(\d+\.?\d*)\s*[°\xb0][Cc]",
        r"(\d{2,3})\s*[Dd]eg\.?\s*[Cc]\b",
        r"(\d{2,3})\s*[°\xb0][Cc]\b",
    ],
    "density_kgm3": [
        r"[Dd]ensity\s*[:\-=]?\s*(\d{3,4}\.?\d*)\s*kg",
        r"[Ss]pecific\s+[Gg]ravity\s*[:\-=]?\s*(\d\.?\d+)",
        r"\bSG\s*[=:\-]\s*(\d\.?\d+)\b",
        r"\bS\.G\.\s*[=:\-]\s*(\d\.?\d+)\b",
        r"\b\xce\xb1\s*[=:\-]\s*(\d{3,4}\.?\d*)\s*kg",
    ],
    "stages": [
        r"[Nn]o\.?\s+[Oo]f\s+[Ss]tages?\s*[:\-=]?\s*(\d+)",
        r"(\d+)\s*[Ss]tage\s+[Pp]ump",
        r"(\d+)[- ][Ss]tage\b",
    ],
}

_FLUID_MAP = [
    ("live steam condensate",  "Live Steam Condensate"),
    ("steam condensate",       "Live Steam Condensate"),
    ("process condensate",     "Process Condensate"),
    ("condensate",             "Process Condensate"),
    ("caustic liquor",         "Caustic Liquor (Alumina)"),
    ("alumina liquor",         "Caustic Liquor (Alumina)"),
    ("caustic soda",           "Caustic Soda"),
    ("caustic",                "Caustic Liquor"),
    ("sulphuric acid",         "Dilute Sulphuric Acid"),
    ("sulfuric acid",          "Dilute Sulphuric Acid"),
    ("hydrochloric acid",      "Hydrochloric Acid"),
    ("acid",                   "Dilute Acid"),
    ("slurry",                 "Slurry"),
    ("seawater",               "Seawater"),
    ("sea water",              "Seawater"),
    ("sw-lift",                "Seawater"),
    ("brine",                  "Seawater"),
    ("crude oil",              "Crude Oil"),
    ("boiler feed",            "Boiler Feed Water"),
    ("cooling water",          "Cooling Water"),
    ("clear water",            "Clear Water"),
    ("clean water",            "Clear Water"),
    ("raw water",              "Clear Water"),
    ("drinking water",         "Clear Water"),
    ("water",                  "Clear Water"),
]

_DENSITY_DEFAULTS = {
    "Slurry":                   1300,
    "Caustic Liquor (Alumina)": 1244,
    "Caustic Liquor":           1200,
    "Live Steam Condensate":     930,
    "Process Condensate":        990,
    "Dilute Sulphuric Acid":    1050,
    "Seawater":                 1025,
    "Boiler Feed Water":         950,
    "Crude Oil":                 870,
    "Cooling Water":             998,
    "Clear Water":              1000,
}

_SANITY = {
    "flow_m3h":     (0,    100000),
    "head_m":       (0.5,    2000),
    "speed_rpm":    (200,   10000),
    "motor_kw":     (0.1,    5000),
    "temp_c":       (-10,     600),
    "density_kgm3": (500,    5000),
    "stages":       (1,        30),
}

def parse_specs(text, learned_patterns=None):
    specs = {}
    t  = text.replace("\n", " ").replace("  ", " ")
    tl = t.lower()

    for field, patterns in _PATTERNS.items():
        for p in patterns:
            try:
                m = re.search(p, t)
                if not m:
                    continue
                val = float(m.group(1))
                # Conversions
                if field == "flow_m3h" and ("LPM" in p or "L/[Mm]in" in p):
                    val = round(val / 1000 * 60, 2)
                if field == "motor_kw" and "[Hh][Pp]" in p:
                    val = round(val * 0.7457, 2)
                if field == "density_kgm3" and val < 5:
                    val = round(val * 1000, 1)
                lo, hi = _SANITY.get(field, (None, None))
                if lo is not None and not (lo < val < hi):
                    continue
                specs[field] = val
                break
            except Exception:
                continue

    # Apply learned patterns
    if learned_patterns:
        for lp in learned_patterns:
            fld     = lp.get("field")
            snippet = lp.get("snippet","")
            if fld and snippet and fld not in specs:
                words = snippet.split()[:3]
                key   = re.escape(" ".join(words))
                try:
                    m = re.search(key + r".*?(\d+\.?\d*)", t, re.IGNORECASE)
                    if m:
                        specs[fld] = float(m.group(1))
                except Exception:
                    pass

    # Fluid
    for kw, name in _FLUID_MAP:
        if kw in tl:
            specs["fluid"] = name
            break
    if "fluid" not in specs:
        specs["fluid"] = "Clear Water"

    # Density default
    if "density_kgm3" not in specs:
        specs["density_kgm3"] = _DENSITY_DEFAULTS.get(specs["fluid"], 1000)

    # Model
    for p in [
        r"[Pp]ump\s+[Mm]odel\s*[:\-=]?\s*([A-Za-z0-9][A-Za-z0-9\-\/\. ]{3,25})",
        r"[Mm]odel\s*[:\-\/=]?\s*([A-Z][A-Z0-9\-\/]{3,20})",
    ]:
        m = re.search(p, t)
        if m:
            val = m.group(1).strip().rstrip(".,;")
            if len(val) >= 4:
                specs["model"] = val
                break

    # Manufacturer
    for mfr in ["Flowserve","KSB","Metso","Sulzer","Wilo","Jyoti",
                "Kirloskar","Grundfos","Ebara","Xylem","Andritz","Weir"]:
        if mfr.lower() in tl:
            specs["manufacturer"] = mfr
            break

    return specs


# ═══════════════════════════════════════════════════════════════════
# SECTION 5 — TIER 1  (database match)
# ═══════════════════════════════════════════════════════════════════

def tier1_match(specs, db):
    pumps = db["pumps"].copy().dropna(subset=["Flow_m3h","Head_m"])
    Q     = specs.get("flow_m3h")
    H     = specs.get("head_m")
    model = (specs.get("model") or "").upper().strip()
    mfr   = (specs.get("manufacturer") or "").lower().strip()

    if not Q and not H and not model:
        return None, 0, "no_specs"

    best_row, best_score, best_type = None, 0, "none"

    for _, row in pumps.iterrows():
        score    = 0
        db_model = str(row["Model"]).upper()
        db_mfr   = str(row["Manufacturer"]).lower()

        if model:
            m1 = re.sub(r"[\s\-\/]","", model)
            m2 = re.sub(r"[\s\-\/]","", db_model)
            if   m1 == m2:              score += 55
            elif m1 in m2:              score += 50
            elif m2 in m1:              score += 45
            elif model[:6] in db_model: score += 25

        if mfr and db_mfr and mfr[:5] in db_mfr:
            score += 15

        if Q and pd.notna(row["Flow_m3h"]):
            pct = abs(Q - row["Flow_m3h"]) / max(row["Flow_m3h"], 1)
            score += 30 if pct < 0.05 else 20 if pct < 0.15 else 10 if pct < 0.25 else 0

        if H and pd.notna(row["Head_m"]):
            pct = abs(H - row["Head_m"]) / max(row["Head_m"], 1)
            score += 25 if pct < 0.05 else 15 if pct < 0.15 else 8 if pct < 0.20 else 0

        if score > best_score:
            best_score = score
            best_row   = row
            best_type  = ("exact" if score >= 65
                          else "close" if score >= 35
                          else "weak")

    return best_row, best_score, best_type


def get_bom_from_match(pump_row, db):
    model   = str(pump_row["Model"])
    matched = db["comps"][db["comps"]["Pump_Model_Compatibility"].str.contains(
        re.escape(model), case=False, na=False, regex=True
    )].copy()
    return matched


# ═══════════════════════════════════════════════════════════════════
# SECTION 6 — TIER 2  (physics engine)
# ═══════════════════════════════════════════════════════════════════

_IEC_KW = [
    0.18,0.25,0.37,0.55,0.75,1.1,1.5,2.2,3.0,4.0,5.5,
    7.5,11,15,18.5,22,30,37,45,55,75,90,110,132,160,
    200,250,315,400,500,630,800,1000,
]

def round_iec(kw):
    for s in _IEC_KW:
        if s >= kw: return s
    return round(kw, 1)

def calc_specific_speed(Q, H, n=1450):
    if not Q or not H or Q <= 0 or H <= 0:
        return None
    return n * math.sqrt(Q * 4.403) / ((H * 3.281) ** 0.75)

def classify_pump_type(Ns, Q, H, fluid, stages=1, learned=None):
    fl = (fluid or "").lower()

    if any(k in fl for k in ["slurry","abrasive"]):
        return "TPL-HSS-01", "Horizontal Slurry Pump"
    if any(k in fl for k in ["sulphuric","sulfuric","hydrochloric","acid"]):
        return "TPL-VSP-01", "Vertical Sump Pump"
    if "live steam condensate" in fl:
        return "TPL-VTP-02", "Vertical Turbine Pump VS6 (Condensate)"
    if "condensate" in fl:
        return "TPL-VTP-02", "Vertical Turbine Pump VS6 (Condensate)"
    if "boiler feed" in fl:
        return "TPL-MSC-01", "Multistage Centrifugal (BFW)"

    if learned:
        return "TPL-LEARNED", learned

    if Ns is None:
        return "TPL-HSC-01", "Horizontal Split Casing (default)"

    if stages and stages > 1 and H and H > 150:
        return "TPL-MSC-01", "Multistage Centrifugal"
    if Q and Q < 20 and H and H > 50:
        return "TPL-VRT-01", "Vertical Submersible"
    if Ns < 1500:
        return ("TPL-HSC-02","Horizontal Split Casing — High Head") \
               if (H and H > 150) else ("TPL-HSC-01","Horizontal Split Casing")
    elif Ns < 4000:
        return "TPL-VTP-01", "Vertical Turbine Pump"
    else:
        return "TPL-VTP-01", "Vertical Turbine Pump (Axial)"

def calc_motor_kw(Q, H, rho=1000, eta_pump=0.78, eta_motor=0.93, sf=1.10):
    if not Q or not H or Q <= 0 or H <= 0:
        return None
    shaft = (Q * H * rho * 9.81) / (eta_pump * 3600 * 1000)
    return round_iec(shaft / eta_motor * sf)

def select_material(fluid, temp_c, pressure_kpa, db):
    mc = db["mat_compat"].copy()
    mc = mc.dropna(subset=["Rule_ID"])
    mc = mc[mc["Rule_ID"].astype(str).str.startswith("MAT-", na=False)]

    fl   = str(fluid or "").lower()
    temp = float(temp_c or 30)
    pres = float(pressure_kpa or 500)

    mc["Temp_Min_C"]       = pd.to_numeric(mc["Temp_Min_C"],       errors="coerce").fillna(0)
    mc["Temp_Max_C"]       = pd.to_numeric(mc["Temp_Max_C"],       errors="coerce").fillna(500)
    mc["Pressure_Max_kPa"] = pd.to_numeric(mc["Pressure_Max_kPa"],errors="coerce").fillna(99999)

    defaults = mc[ mc["Rule_ID"].str.startswith("MAT-DEFAULT", na=False)]
    rules    = mc[~mc["Rule_ID"].str.startswith("MAT-DEFAULT", na=False)]

    valid = rules[
        (rules["Temp_Min_C"]       <= temp) &
        (rules["Temp_Max_C"]       >= temp) &
        (rules["Pressure_Max_kPa"] >= pres)
    ]

    exact = valid[valid["Fluid_Type"].str.lower().str.contains(
        fl[:12], na=False, case=False
    )]
    if not exact.empty:
        row = exact.iloc[0]
    else:
        cat_map = {
            "water":      "Clean Water",
            "caustic":    "Alkali/Caustic",
            "acid":       "Dilute Acid",
            "slurry":     "Abrasive Slurry",
            "condensate": "Condensate",
            "oil":        "Hydrocarbon",
            "seawater":   "Seawater",
            "brine":      "Seawater",
            "boiler":     "High Temp",
        }
        cat = "Clean Water"
        for k, v in cat_map.items():
            if k in fl: cat = v; break
        cm = valid[valid["Fluid_Category"] == cat]
        row = cm.iloc[0] if not cm.empty \
              else (defaults.iloc[0] if not defaults.empty else rules.iloc[0])

    cols = ["Casing_MOC","Impeller_MOC","Shaft_MOC","Shaft_Sleeve_MOC",
            "Wear_Ring_MOC","Seal_Type","Seal_Plan","Fastener_MOC"]
    out = {c: (str(row[c]) if c in row.index and pd.notna(row[c]) else "VTA")
           for c in cols}
    out["Rule_ID"]     = str(row.get("Rule_ID",""))
    out["Fluid_Match"] = str(row.get("Fluid_Type",""))
    return out

def estimate_weight(pump_type_str, motor_kw, store=None):
    P  = float(motor_kw or 30)
    pt = (pump_type_str or "").lower()

    # Calibration coefficients from learning
    pump_c = motor_c = 1.0
    if store:
        for key, cal in store.get("weight_calibs", {}).items():
            if key in pt:
                pump_c  = cal.get("pump_coeff",  1.0)
                motor_c = cal.get("motor_coeff", 1.0)
                break

    w = {}
    if "slurry" in pt:
        w["pump_kg"]      = round(_base_pump_wt(pt, P)  * pump_c)
        w["motor_kg"]     = round(_base_motor_wt(P)     * motor_c)
        w["baseplate_kg"] = round(0.30 * w["pump_kg"])
        w["guard_kg"]     = round(0.10 * w["pump_kg"])
    elif "sump" in pt or "acid" in pt:
        w["pump_kg"]      = round(_base_pump_wt(pt, P)  * pump_c)
        w["motor_kg"]     = round(_base_motor_wt(P)     * motor_c)
        w["baseplate_kg"] = 20
    elif "turbine" in pt or "vs6" in pt:
        w["pump_kg"]      = round(_base_pump_wt(pt, P)  * pump_c)
        w["motor_kg"]     = round(_base_motor_wt(P)     * motor_c)
        w["baseplate_kg"] = 60
    else:
        w["pump_kg"]      = round(_base_pump_wt(pt, P)  * pump_c)
        w["motor_kg"]     = round(_base_motor_wt(P)     * motor_c)
        w["baseplate_kg"] = round(max(80, 0.18 * P**1.02))
        w["coupling_kg"]  = max(10, round(0.015 * P))

    w["total_kg"] = sum(w.values())
    return w

def get_bom_template(template_id, db):
    sec_b = pd.read_excel(DB_PATH, sheet_name="BOM_Templates", header=16)
    sec_b.columns = [str(c).strip() for c in sec_b.columns]
    if "Template_ID" in sec_b.columns:
        tpl = sec_b[sec_b["Template_ID"].astype(str).str.strip() == template_id.strip()]
        if not tpl.empty:
            return tpl
    return sec_b[sec_b["Template_ID"].astype(str).str.contains("HSC-01", na=False)]

def tier2_generate(specs, db, store=None):
    Q      = specs.get("flow_m3h")
    H      = specs.get("head_m")
    n      = specs.get("speed_rpm")     or 1450
    fluid  = specs.get("fluid")         or "Clear Water"
    temp   = specs.get("temp_c")        or 30
    rho    = specs.get("density_kgm3")  or 1000
    stages = specs.get("stages")        or 1
    motor_input = specs.get("motor_kw")

    Ns  = calc_specific_speed(Q, H, n)
    lrn = get_learned_correction(Ns, fluid) if Ns else None

    tpl_id, pump_type_desc = classify_pump_type(Ns, Q, H, fluid, stages, lrn)

    eta   = 0.75 if (Ns and Ns < 1500) else 0.82
    mkw   = motor_input or calc_motor_kw(Q, H, rho, eta)
    moc   = select_material(fluid, temp, 500, db)
    wts   = estimate_weight(pump_type_desc, mkw, store)

    tpl   = get_bom_template(tpl_id, db)
    moc_map = {
        "Casing":"Casing_MOC","Impeller":"Impeller_MOC",
        "Shaft":"Shaft_MOC","Sleeve":"Shaft_Sleeve_MOC",
        "Wear Ring":"Wear_Ring_MOC","Seal":"Seal_Type",
        "Fasteners":"Fastener_MOC",
    }
    wt_map = {"Pump":"pump_kg","Motor":"motor_kg",
              "Baseplate":"baseplate_kg","Coupling":"coupling_kg"}

    rows = []
    for i, (_, tc) in enumerate(tpl.iterrows(), 1):
        cat = str(tc.get("Component_Category",""))
        sub = str(tc.get("Component_Subcategory",""))
        req = str(tc.get("Req_Type","M"))
        qty = str(tc.get("Qty_Logic","1"))
        mat = moc.get(moc_map.get(cat,"Casing_MOC"),"Per Service")
        if mat in ["nan","VTA","None",""]: mat = "Engineer to Specify"
        wt  = wts.get(wt_map.get(cat,""), "")
        rows.append({
            "No":          i,
            "Component_ID":f"CALC-{tpl_id}-{i:03d}",
            "Category":    cat,
            "Description": sub if sub and sub != cat else cat,
            "MOC":         mat,
            "Qty":         qty,
            "Req_Type":    req,
            "Weight_kg":   wt,
            "Source":      "Tier 2 — Physics",
            "Notes":       str(tc.get("Notes_for_BOM_Generator",""))[:80],
        })

    summary = {
        "specific_speed_Ns":  round(Ns,1) if Ns else "N/A",
        "pump_type":          pump_type_desc,
        "template_used":      tpl_id,
        "motor_kw_calc":      mkw,
        "eta_pump_assumed":   round(eta*100,1),
        "material_rule":      moc.get("Rule_ID",""),
        "fluid_matched":      moc.get("Fluid_Match",""),
        "seal_plan":          moc.get("Seal_Plan",""),
        "weights":            wts,
        "moc":                moc,
        "learned_correction": lrn,
    }
    return pd.DataFrame(rows), summary



# ═══════════════════════════════════════════════════════════════════
# SECTION 7A — BOM GROUPING
# ═══════════════════════════════════════════════════════════════════

# Canonical group order — how a real BOM is structured in EPC
_GROUP_ORDER = [
    # Section 1 — Pump Hydraulics
    ('PUMP HYDRAULICS',
     ['Pump','Casing','Impeller','Rotor','Wear Ring','Liner']),
    # Section 2 — Rotating & Shaft
    ('ROTATING ASSEMBLY',
     ['Shaft','Sleeve','Rotor']),
    # Section 3 — Bearings
    ('BEARINGS & LUBRICATION',
     ['Bearing','Housing','Lubrication','Oiler']),
    # Section 4 — Sealing
    ('SHAFT SEALING',
     ['Seal','Mechanical Seal','Gland','Stuffing Box']),
    # Section 5 — Drive
    ('DRIVE & COUPLING',
     ['Coupling','Guard','V-Belt','Pulley','Belt']),
    # Section 6 — Driver
    ('MOTOR / DRIVER',
     ['Motor']),
    # Section 7 — Structural
    ('STRUCTURAL & BASEPLATE',
     ['Baseplate','Foundation','Stool','Saddle','Frame','Bracket']),
    # Section 8 — Piping & Nozzles
    ('PIPING, NOZZLES & FLANGES',
     ['Flange','Piping','Pipe','Column','Strainer','Nozzle']),
    # Section 9 — Fasteners & Gaskets
    ('FASTENERS & GASKETS',
     ['Fastener','Fasteners','Gasket','Bolt','Stud','Nut']),
    # Section 10 — Instrumentation
    ('INSTRUMENTATION',
     ['Instrumentation','Thermometer','Gauge','Sensor']),
    # Section 11 — Acoustic & Safety
    ('ACOUSTIC & SAFETY',
     ['Enclosure','Acoustic','Guard']),
    # Section 12 — Complete Assembly
    ('COMPLETE ASSEMBLY',
     ['Assembly']),
]

def _get_group(category):
    """Return group name for a given category string."""
    cat = str(category).strip()
    for group_name, members in _GROUP_ORDER:
        for m in members:
            if m.lower() == cat.lower() or m.lower() in cat.lower():
                return group_name
    return 'OTHER'

def group_bom(bom_df):
    """
    Takes flat BOM DataFrame.
    Returns list of (group_name, sub_df) tuples in correct order.
    Re-numbers items within each group.

    Used by both app display and Excel export.
    """
    if bom_df is None or bom_df.empty:
        return []

    # Assign group to every row
    cat_col = 'Category' if 'Category' in bom_df.columns else bom_df.columns[1]
    bom_df = bom_df.copy()
    bom_df['_group'] = bom_df[cat_col].apply(_get_group)

    # Build ordered groups — only include groups that have rows
    seen_groups = []
    result = []
    for group_name, _ in _GROUP_ORDER:
        rows = bom_df[bom_df['_group'] == group_name].copy()
        if not rows.empty and group_name not in seen_groups:
            seen_groups.append(group_name)
            rows = rows.drop(columns=['_group'])
            result.append((group_name, rows))

    # Any ungrouped rows go to OTHER
    other = bom_df[bom_df['_group'] == 'OTHER'].copy().drop(columns=['_group'])
    if not other.empty:
        result.append(('OTHER', other))

    # Re-number: global sequential No across all groups
    n = 1
    renumbered = []
    for gname, gdf in result:
        gdf = gdf.copy()
        gdf['No'] = range(n, n + len(gdf))
        n += len(gdf)
        renumbered.append((gname, gdf))

    return renumbered

# ═══════════════════════════════════════════════════════════════════
# SECTION 7 — ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════

def _safe(v):
    """Ensure spec values are clean — no None crashing .upper()/.lower()."""
    if v is None: return None
    if isinstance(v, str): return v.strip() or None
    try:
        f = float(v)
        return f if (f == f) else None   # NaN check
    except (TypeError, ValueError):
        return v

def generate_bom(specs, db, store=None):
    specs = {k: _safe(v) for k, v in (specs or {}).items()}

    pump_row, score, mtype = tier1_match(specs, db)
    if pump_row is not None and score >= 30:
        bom = get_bom_from_match(pump_row, db)
        if not bom.empty:
            out = bom[["Component_ID","Category","Subcategory",
                        "Component_Name","Material_Spec","Qty_Per_Unit",
                        "Unit","Weight_kg","Vendor_Name","Notes"]].copy()
            out.insert(0,"No", range(1,len(out)+1))
            out.insert(9,"Source","Tier 1 — Database")
            return out, "tier1", {
                "pump_id":    pump_row["Pump_ID"],
                "model":      pump_row["Model"],
                "score":      score,
                "match_type": mtype,
            }, None

    bom_df, summary = tier2_generate(specs, db, store)
    return bom_df, "tier2", None, summary


# ═══════════════════════════════════════════════════════════════════
# SECTION 8 — EXCEL EXPORT
# ═══════════════════════════════════════════════════════════════════

def export_bom_excel(bom_df, specs, tier, match_info, calc_summary):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    thin = Side(style="thin", color="CCCCCC")
    bdr  = Border(left=thin,right=thin,top=thin,bottom=thin)

    # ── Title ──────────────────────────────────────────────────
    MAX_COL = 10
    ws.merge_cells(f"A1:{get_column_letter(MAX_COL)}1")
    c = ws["A1"]
    c.value = "AUTOMATED BILL OF MATERIALS"
    c.font  = Font(bold=True,size=13,color="FFFFFF")
    c.fill  = PatternFill("solid",start_color="1F4E79")
    c.alignment = Alignment(horizontal="center",vertical="center")
    ws.row_dimensions[1].height = 22

    # ── Project info block ─────────────────────────────────────
    info = [
        ("Pump Model",   (specs or {}).get("model","—") or "Physics Calculated"),
        ("Flow (m³/h)",  (specs or {}).get("flow_m3h","—")),
        ("Head (m)",     (specs or {}).get("head_m","—")),
        ("Fluid",        (specs or {}).get("fluid","—")),
        ("Temperature",  f"{(specs or {}).get('temp_c','—')} °C"),
        ("Motor (kW)",   (specs or {}).get("motor_kw") or (calc_summary or {}).get("motor_kw_calc","—")),
        ("BOM Method",   f"Tier 1 — {(match_info or {}).get('model','')}"
                          if tier=="tier1"
                          else f"Tier 2 — {(calc_summary or {}).get('pump_type','')}"),
        ("Generated",    pd.Timestamp.now().strftime("%d-%b-%Y %H:%M")),
    ]
    r = 2
    for lbl, val in info:
        c1 = ws.cell(r,1,lbl);         c1.font=Font(bold=True,size=9); c1.fill=PatternFill("solid",start_color="F0F5FB")
        c2 = ws.cell(r,2,str(val));    c2.font=Font(size=9)
        ws.merge_cells(f"B{r}:{get_column_letter(MAX_COL)}{r}")
        r += 1

    # ── Column headers ─────────────────────────────────────────
    r += 1
    HDR_ROW = r
    headers = ["No","Component ID","Category","Description / Name",
               "Material (MOC)","Qty","Unit","Weight (kg)","Vendor","Notes"]
    hf    = PatternFill("solid",start_color="1F4E79")
    hfont = Font(bold=True,color="FFFFFF",size=9)
    for j,h in enumerate(headers):
        c = ws.cell(r,j+1,h)
        c.font=hfont; c.fill=hf
        c.alignment=Alignment(horizontal="center",wrap_text=True)
        c.border=bdr
    ws.row_dimensions[r].height=28
    r += 1

    # ── Group colours (section header fill) ───────────────────
    GROUP_COLORS = {
        "PUMP HYDRAULICS":           "1A3A5C",
        "ROTATING ASSEMBLY":         "1A3A5C",
        "BEARINGS & LUBRICATION":    "2E5984",
        "SHAFT SEALING":             "2E5984",
        "DRIVE & COUPLING":          "366092",
        "MOTOR / DRIVER":            "17375E",
        "STRUCTURAL & BASEPLATE":    "4F6228",
        "PIPING, NOZZLES & FLANGES": "4F6228",
        "FASTENERS & GASKETS":       "7F7F7F",
        "INSTRUMENTATION":           "7F7F7F",
        "ACOUSTIC & SAFETY":         "7F7F7F",
        "COMPLETE ASSEMBLY":         "1F4E79",
        "OTHER":                     "595959",
    }
    alt = PatternFill("solid",start_color="F2F7FC")
    alt2= PatternFill("solid",start_color="FFFFFF")

    # ── Write grouped BOM ─────────────────────────────────────
    groups = group_bom(bom_df)

    # Determine display columns
    t1_cols = ["No","Component_ID","Category","Component_Name",
               "Material_Spec","Qty_Per_Unit","Unit","Weight_kg",
               "Vendor_Name","Notes"]
    t2_cols = ["No","Component_ID","Category","Description",
               "MOC","Qty","","Weight_kg","","Notes"]

    def _cell_val(row_series, col_name, fallback=""):
        if col_name and col_name in row_series.index:
            v = row_series[col_name]
            return "" if pd.isna(v) else v
        return fallback

    for gname, gdf in groups:
        # Section header row
        grp_color = GROUP_COLORS.get(gname, "595959")
        ws.merge_cells(f"A{r}:{get_column_letter(MAX_COL)}{r}")
        gc = ws.cell(r,1, f"  {gname}")
        gc.font  = Font(bold=True,size=9,color="FFFFFF")
        gc.fill  = PatternFill("solid",start_color=grp_color)
        gc.alignment = Alignment(vertical="center")
        ws.row_dimensions[r].height = 16
        r += 1

        # Component rows
        is_t1 = "Component_Name" in gdf.columns
        cols  = t1_cols if is_t1 else t2_cols

        for i,(_, row_s) in enumerate(gdf.iterrows()):
            row_fill = alt if i%2==0 else alt2
            vals = [
                _cell_val(row_s,"No"),
                _cell_val(row_s,"Component_ID"),
                _cell_val(row_s,"Category"),
                _cell_val(row_s,"Component_Name") or _cell_val(row_s,"Description"),
                _cell_val(row_s,"Material_Spec")  or _cell_val(row_s,"MOC"),
                _cell_val(row_s,"Qty_Per_Unit")   or _cell_val(row_s,"Qty"),
                _cell_val(row_s,"Unit"),
                _cell_val(row_s,"Weight_kg"),
                _cell_val(row_s,"Vendor_Name"),
                _cell_val(row_s,"Notes"),
            ]
            for j,val in enumerate(vals):
                c = ws.cell(r,j+1, val)
                c.font      = Font(size=9)
                c.alignment = Alignment(wrap_text=True,vertical="top")
                c.border    = bdr
                c.fill      = row_fill
            ws.row_dimensions[r].height = 18
            r += 1

    # ── Column widths ──────────────────────────────────────────
    col_widths = [5,22,16,38,28,6,6,10,22,42]
    for i,w in enumerate(col_widths):
        ws.column_dimensions[get_column_letter(i+1)].width = w
    ws.freeze_panes = f"A{HDR_ROW+1}"

    if tier=="tier2" and calc_summary:
        ws2 = wb.create_sheet("Calculation")
        ws2["A1"] = "TIER 2 CALCULATION SUMMARY"
        ws2["A1"].font = Font(bold=True,size=12,color="FFFFFF")
        ws2["A1"].fill = PatternFill("solid",start_color="70AD47")
        r2 = 3
        for k,v in calc_summary.items():
            if k in ("moc","weights"): continue
            ws2.cell(r2,1,str(k)).font = Font(bold=True,size=9)
            ws2.cell(r2,2,str(v)).font = Font(size=9)
            r2 += 1
        r2 += 1
        ws2.cell(r2,1,"WEIGHTS").font=Font(bold=True)
        r2 += 1
        for k,v in calc_summary.get("weights",{}).items():
            ws2.cell(r2,1,k).font=Font(bold=True,size=9)
            ws2.cell(r2,2,f"{v} kg").font=Font(size=9)
            r2 += 1
        ws2.column_dimensions["A"].width=30
        ws2.column_dimensions["B"].width=45

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
