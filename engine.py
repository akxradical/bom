"""
BOM Generation Engine — Pure Python, no API
Tier 1: Exact/close match from database
Tier 2: Physics-backed calculation
"""

import pandas as pd
import numpy as np
import re
import math
import pdfplumber
from io import BytesIO


# ─────────────────────────────────────────────────────────────────
# DATABASE LOADER
# ─────────────────────────────────────────────────────────────────
import os
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Component_Library_COMPLETE.xlsx")

def load_db():
    return {
        "pumps":    pd.read_excel(DB_PATH, sheet_name="Pump_Master_List"),
        "comps":    pd.read_excel(DB_PATH, sheet_name="Component_Library"),
        "mats":     pd.read_excel(DB_PATH, sheet_name="Material_Database"),
        "vendors":  pd.read_excel(DB_PATH, sheet_name="Vendor_Database"),
        "bom_tpl":  pd.read_excel(DB_PATH, sheet_name="BOM_Templates",          header=4),
        "physics":  pd.read_excel(DB_PATH, sheet_name="Physics_Parameters",     header=4),
        "mat_compat": pd.read_excel(DB_PATH, sheet_name="Material_Compatibility", header=4),
    }


# ─────────────────────────────────────────────────────────────────
# PDF TEXT EXTRACTION
# ─────────────────────────────────────────────────────────────────
def extract_pdf_text(file_bytes):
    """Extract all text from a PDF using pdfplumber."""
    text_all = []
    try:
        with pdfplumber.open(BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_all.append(t)
    except Exception as e:
        return "", str(e)
    return "\n".join(text_all), None


# ─────────────────────────────────────────────────────────────────
# SPEC PARSER — extract Q, H, speed, fluid, temp, power from text
# ─────────────────────────────────────────────────────────────────
def parse_specs(text):
    """
    Returns dict with all found specs. Uses regex patterns
    covering common datasheet formats from our 12 pump database.
    """
    specs = {}
    t = text.replace("\n", " ").replace("  ", " ")

    # ── Flow ──────────────────────────────────────────────────────
    flow_patterns = [
        r"[Ff]low\s*[:\-=]?\s*(\d+\.?\d*)\s*m3/h",
        r"[Cc]apacity\s*[:\-=]?\s*(\d+\.?\d*)\s*m3/h",
        r"[Cc]apacity\s*[:\-=]?\s*(\d+\.?\d*)\s*m³/h",
        r"Q\s*[=:\-]\s*(\d+\.?\d*)\s*m3",
        r"(\d+\.?\d*)\s*m3/hr",
        r"(\d+\.?\d*)\s*m³/hr",
        r"[Ff]low\s*[:\-=]?\s*(\d+\.?\d*)\s*m³/h",
        r"[Vv]olume\s+[Ff]low.*?(\d+\.?\d*)\s*m",
        r"(\d{2,5}\.?\d*)\s*LPM",   # LPM → convert to m3/h
    ]
    for p in flow_patterns:
        m = re.search(p, t)
        if m:
            val = float(m.group(1))
            if "LPM" in p:
                val = round(val / 1000 * 60, 2)
            specs["flow_m3h"] = val
            break

    # ── Head ──────────────────────────────────────────────────────
    head_patterns = [
        r"[Hh]ead\s*[:\-=]?\s*(\d+\.?\d*)\s*[Mm]\b",
        r"[Tt]otal\s+[Hh]ead\s*[:\-=]?\s*(\d+\.?\d*)",
        r"H\s*[=:\-]\s*(\d+\.?\d*)\s*m",
        r"[Bb]owl\s+[Hh]ead\s*[:\-=]?\s*(\d+\.?\d*)",
        r"[Pp]ump\s+[Hh]ead.*?(\d+\.?\d*)\s*m",
        r"(\d+\.?\d*)\s*[Mm]tr\b",
        r"[Hh]ead.*?(\d{2,4}\.?\d*)\s*M\b",
    ]
    for p in head_patterns:
        m = re.search(p, t)
        if m:
            val = float(m.group(1))
            if 0.5 < val < 2000:   # sanity check
                specs["head_m"] = val
                break

    # ── Speed ─────────────────────────────────────────────────────
    speed_patterns = [
        r"[Ss]peed\s*[:\-=]?\s*(\d{3,4})\s*[Rr][Pp][Mm]",
        r"(\d{3,4})\s*[Rr][Pp][Mm]",
        r"[Rr]ated\s+[Ss]peed\s*[:\-=]?\s*(\d{3,4})",
        r"[Mm]otor\s+[Ss]peed\s*[:\-=]?\s*(\d{3,4})",
        r"[Ff]ull\s+[Ll]oad\s+[Ss]peed.*?(\d{3,4})",
    ]
    for p in speed_patterns:
        m = re.search(p, t)
        if m:
            val = int(m.group(1))
            if 400 < val < 4000:
                specs["speed_rpm"] = val
                break

    # ── Motor Power ───────────────────────────────────────────────
    motor_patterns = [
        r"[Mm]otor\s+[Rr]ating\s*[:\-=]?\s*(\d+\.?\d*)\s*[Kk][Ww]",
        r"[Mm]otor\s+[Pp]ower\s*[:\-=]?\s*(\d+\.?\d*)\s*[Kk][Ww]",
        r"[Pp]rime\s*[Mm]over\s+[Pp]ower\s*[:\-=]?\s*(\d+\.?\d*)",
        r"[Nn]ominal\s+[Pp]ower\s*[:\-=]?\s*(\d+\.?\d*)\s*[Kk][Ww]",
        r"[Kk][Ww]\s*[:\-=]?\s*(\d+\.?\d*)\b",
        r"(\d+\.?\d*)\s*[Kk][Ww]\b",
        r"[Oo]utput\s*[:\-=]?\s*(\d+\.?\d*)\s*[Kk][Ww]",
    ]
    for p in motor_patterns:
        m = re.search(p, t)
        if m:
            val = float(m.group(1))
            if 0.1 < val < 5000:
                specs["motor_kw"] = val
                break

    # ── Temperature ───────────────────────────────────────────────
    temp_patterns = [
        r"[Tt]emp(?:erature)?\s*[:\-=]?\s*(\d+\.?\d*)\s*°?[Cc]",
        r"[Oo]p(?:erating)?\s+[Tt]emp.*?(\d+\.?\d*)\s*°?C",
        r"(\d+\.?\d*)\s*°C\b",
        r"(\d+)\s*[Dd]eg\s*C",
        r"[Tt]emp.*?(\d{2,3})\s*C",
    ]
    for p in temp_patterns:
        m = re.search(p, t)
        if m:
            val = float(m.group(1))
            if 0 < val < 500:
                specs["temp_c"] = val
                break

    # ── Fluid ─────────────────────────────────────────────────────
    fluid_keywords = {
        "caustic liquor":   "Caustic Liquor (Alumina)",
        "caustic":          "Caustic Liquor",
        "alumina liquor":   "Caustic Liquor (Alumina)",
        "condensate":       "Condensate",
        "live steam condensate": "Live Steam Condensate",
        "process condensate":   "Process Condensate",
        "slurry":           "Slurry",
        "sulphuric acid":   "Dilute Sulphuric Acid",
        "sulfuric acid":    "Dilute Sulphuric Acid",
        "acid":             "Dilute Acid",
        "clear water":      "Clear Water",
        "clean water":      "Clear Water",
        "raw water":        "Clear Water",
        "drinking water":   "Clear Water",
        "water":            "Clear Water",
        "crude oil":        "Crude Oil",
        "seawater":         "Seawater",
        "sea water":        "Seawater",
        "brine":            "Seawater",
        "cooling water":    "Cooling Water",
    }
    tl = t.lower()
    for keyword, fluid_name in fluid_keywords.items():
        if keyword in tl:
            specs["fluid"] = fluid_name
            break
    if "fluid" not in specs:
        specs["fluid"] = "Clear Water"  # safe default

    # ── Density ───────────────────────────────────────────────────
    dens_patterns = [
        r"[Dd]ensity\s*[:\-=]?\s*(\d{3,4}\.?\d*)\s*kg",
        r"[Ss]p(?:ecific)?\s*[Gg]ravity\s*[:\-=]?\s*(\d\.?\d*)",
        r"ρ\s*[=:\-]?\s*(\d{3,4}\.?\d*)\s*kg",
    ]
    for p in dens_patterns:
        m = re.search(p, t)
        if m:
            val = float(m.group(1))
            if val < 5:
                val = val * 1000  # SG → kg/m3
            if 500 < val < 2500:
                specs["density_kgm3"] = val
                break
    if "density_kgm3" not in specs:
        # Default by fluid
        if "slurry" in specs.get("fluid","").lower():
            specs["density_kgm3"] = 1300
        elif "caustic" in specs.get("fluid","").lower():
            specs["density_kgm3"] = 1244
        elif "condensate" in specs.get("fluid","").lower():
            specs["density_kgm3"] = 930
        elif "acid" in specs.get("fluid","").lower():
            specs["density_kgm3"] = 1050
        else:
            specs["density_kgm3"] = 1000

    # ── Pump Model ────────────────────────────────────────────────
    model_patterns = [
        r"[Pp]ump\s+[Mm]odel\s*[:\-=]?\s*([A-Z0-9\-\/]+)",
        r"[Mm]odel\s+[Nn]o\.?\s*[:\-=]?\s*([A-Z0-9\-\/]+)",
        r"[Mm]odel\s*[:\-=]?\s*([A-Z0-9\-\/]{4,20})",
    ]
    for p in model_patterns:
        m = re.search(p, t)
        if m:
            specs["model"] = m.group(1).strip()
            break

    # ── Manufacturer ──────────────────────────────────────────────
    for mfr in ["Flowserve","KSB","Metso","Wilo","Jyoti","Kirloskar",
                "Grundfos","Sulzer","ITT","Xylem","Ebara"]:
        if mfr.lower() in tl:
            specs["manufacturer"] = mfr
            break

    # ── Stages ────────────────────────────────────────────────────
    stage_m = re.search(r"[Nn]o\.?\s+[Oo]f\s+[Ss]tages?\s*[:\-=]?\s*(\d+)", t)
    if stage_m:
        specs["stages"] = int(stage_m.group(1))

    return specs


# ─────────────────────────────────────────────────────────────────
# TIER 1 — EXACT / CLOSE MATCH
# ─────────────────────────────────────────────────────────────────
def tier1_match(specs, db):
    """
    Try to find matching pump in Pump_Master_List.
    Returns (pump_row, match_score, match_type)
    """
    pumps = db["pumps"].copy()
    pumps = pumps.dropna(subset=["Flow_m3h", "Head_m"])

    Q = specs.get("flow_m3h")
    H = specs.get("head_m")
    model = specs.get("model", "").upper()
    mfr   = specs.get("manufacturer", "").lower()

    if Q is None and H is None and not model:
        return None, 0, "no_specs"

    best_row   = None
    best_score = 0
    best_type  = "none"

    for _, row in pumps.iterrows():
        score = 0

        # Model name match (highest weight)
        db_model = str(row["Model"]).upper()
        if model and model in db_model:
            score += 50
        if model and db_model in model:
            score += 40

        # Manufacturer match
        db_mfr = str(row["Manufacturer"]).lower()
        if mfr and mfr in db_mfr:
            score += 15

        # Flow match (within ±25%)
        if Q and pd.notna(row["Flow_m3h"]):
            pct = abs(Q - row["Flow_m3h"]) / max(row["Flow_m3h"], 1)
            if pct < 0.05:
                score += 30
            elif pct < 0.15:
                score += 20
            elif pct < 0.25:
                score += 10

        # Head match (within ±20%)
        if H and pd.notna(row["Head_m"]):
            pct = abs(H - row["Head_m"]) / max(row["Head_m"], 1)
            if pct < 0.05:
                score += 25
            elif pct < 0.15:
                score += 15
            elif pct < 0.20:
                score += 8

        if score > best_score:
            best_score = score
            best_row   = row
            best_type  = "exact" if score >= 60 else "close" if score >= 30 else "weak"

    return best_row, best_score, best_type


def get_bom_from_match(pump_row, db):
    """Return components from Component_Library for matched pump."""
    model_name = pump_row["Model"]
    comps = db["comps"]
    matched = comps[comps["Pump_Model_Compatibility"].str.contains(
        re.escape(model_name), case=False, na=False, regex=True
    )].copy()
    return matched


# ─────────────────────────────────────────────────────────────────
# TIER 2 — PHYSICS-BACKED CALCULATION
# ─────────────────────────────────────────────────────────────────
IEC_STANDARD_KW = [
    0.18, 0.25, 0.37, 0.55, 0.75, 1.1, 1.5, 2.2, 3.0, 4.0, 5.5,
    7.5, 11, 15, 18.5, 22, 30, 37, 45, 55, 75, 90, 110, 132, 160,
    200, 250, 315, 400, 500, 630, 800, 1000
]

def round_up_iec(kw):
    for s in IEC_STANDARD_KW:
        if s >= kw:
            return s
    return kw

def calc_specific_speed(Q, H, n=1450):
    """Ns in US units: n * sqrt(Q_gpm) / H_ft^0.75"""
    if not Q or not H or Q <= 0 or H <= 0:
        return None
    Q_gpm = Q * 4.403
    H_ft  = H * 3.281
    return n * math.sqrt(Q_gpm) / (H_ft ** 0.75)

def classify_pump_type(Ns, Q, H, fluid, stages=1):
    """Classify pump type from specific speed + duty."""
    fluid_l = fluid.lower() if fluid else ""

    if "slurry" in fluid_l or "abrasive" in fluid_l:
        return "TPL-HSS-01", "Horizontal Slurry Pump"
    if "acid" in fluid_l or "sulphuric" in fluid_l:
        return "TPL-VSP-01", "Vertical Sump Pump"
    if "live steam condensate" in fluid_l:
        return "TPL-VTP-02", "Vertical Turbine Pump VS6 (Condensate)"
    if "condensate" in fluid_l:
        if H and H > 80:
            return "TPL-VTP-02", "Vertical Turbine Pump VS6 (Condensate)"
        return "TPL-VTP-02", "Vertical Turbine Pump VS6 (Condensate)"

    if Ns is None:
        return "TPL-HSC-01", "Horizontal Split Casing (default)"

    if stages and stages > 1:
        if H and H > 150:
            return "TPL-MSC-01", "Multistage Centrifugal"

    if Q and Q < 20 and H and H > 50:
        return "TPL-VRT-01", "Vertical Submersible"

    if Ns < 1500:
        if H and H > 150:
            return "TPL-HSC-02", "Horizontal Split Casing - High Head"
        return "TPL-HSC-01", "Horizontal Split Casing"
    elif Ns < 4000:
        if Q and Q > 100:
            return "TPL-VTP-01", "Vertical Turbine Pump"
        return "TPL-VTP-01", "Vertical Turbine Pump"
    else:
        return "TPL-VTP-01", "Vertical Turbine Pump (Axial)"

def calc_motor_kw(Q, H, density=1000, eta_pump=0.78, eta_motor=0.93, sf=1.10):
    """Calculate motor kW from duty point."""
    if not Q or not H:
        return None
    shaft_kw = (Q * H * density * 9.81) / (eta_pump * 3600 * 1000)
    motor_kw = shaft_kw / eta_motor * sf
    return round_up_iec(motor_kw)

def select_material(fluid, temp_c, pressure_kpa, db):
    """Query Material_Compatibility matrix."""
    mc = db["mat_compat"].copy()
    mc = mc.dropna(subset=["Rule_ID"])
    # Keep only rows that start with MAT- (skip section headers)
    mc = mc[mc["Rule_ID"].astype(str).str.startswith("MAT-", na=False)]

    fluid_l = str(fluid).lower()
    temp    = float(temp_c) if temp_c else 30
    press   = float(pressure_kpa) if pressure_kpa else 500

    # Coerce temp/pressure columns to numeric safely
    mc["Temp_Min_C"]       = pd.to_numeric(mc["Temp_Min_C"],       errors="coerce").fillna(0)
    mc["Temp_Max_C"]       = pd.to_numeric(mc["Temp_Max_C"],       errors="coerce").fillna(500)
    mc["Pressure_Max_kPa"] = pd.to_numeric(mc["Pressure_Max_kPa"], errors="coerce").fillna(99999)

    # Separate defaults before filtering
    defaults = mc[mc["Rule_ID"].str.startswith("MAT-DEFAULT", na=False)]
    mc_rules = mc[~mc["Rule_ID"].str.startswith("MAT-DEFAULT", na=False)]

    # Filter by temp and pressure
    valid = mc_rules[
        (mc_rules["Temp_Min_C"] <= temp) &
        (mc_rules["Temp_Max_C"] >= temp) &
        (mc_rules["Pressure_Max_kPa"] >= press)
    ]

    # Try exact fluid match
    exact = valid[valid["Fluid_Type"].str.lower().str.contains(
        fluid_l[:10], na=False, case=False
    )]
    if not exact.empty:
        row = exact.iloc[0]
    else:
        # Category match
        category_map = {
            "water": "Clean Water", "caustic": "Alkali/Caustic",
            "acid": "Dilute Acid", "slurry": "Abrasive Slurry",
            "condensate": "Condensate", "oil": "Hydrocarbon",
            "seawater": "Seawater", "brine": "Seawater",
        }
        cat = "Clean Water"
        for k, v in category_map.items():
            if k in fluid_l:
                cat = v
                break
        cat_match = valid[valid["Fluid_Category"] == cat]
        if not cat_match.empty:
            row = cat_match.iloc[0]
        else:
            # Fallback to defaults
            if not defaults.empty:
                row = defaults.iloc[0]
            else:
                row = mc.iloc[0]

    cols = ["Casing_MOC","Impeller_MOC","Shaft_MOC","Shaft_Sleeve_MOC",
            "Wear_Ring_MOC","Seal_Type","Seal_Plan","Fastener_MOC"]
    result = {}
    for c in cols:
        result[c] = str(row[c]) if c in row.index and pd.notna(row[c]) else "VTA"
    result["Rule_ID"]       = row["Rule_ID"]
    result["Fluid_Match"]   = row["Fluid_Type"]
    return result

def estimate_weight(pump_type_str, motor_kw, Q=None, H=None):
    """Empirical weight estimates from Physics_Parameters."""
    w = {}
    P = float(motor_kw) if motor_kw else 30

    if "slurry" in pump_type_str.lower():
        w["pump_kg"]     = round(1.8 * P**0.85)
        w["motor_kg"]    = round(6.2 * P**0.80) if P > 200 else round(8.5 * P**0.75)
        w["baseplate_kg"]= round(0.3 * w["pump_kg"])
        w["total_kg"]    = w["pump_kg"] + w["motor_kg"] + w["baseplate_kg"]

    elif "sump" in pump_type_str.lower() or "acid" in pump_type_str.lower():
        depth = 2.0
        w["pump_kg"]     = round(0.9 * P**0.85 + 0.4 * depth)
        w["motor_kg"]    = round(8.5 * P**0.75)
        w["baseplate_kg"]= 20
        w["total_kg"]    = w["pump_kg"] + w["motor_kg"] + w["baseplate_kg"]

    elif "vertical turbine" in pump_type_str.lower() or "vtp" in pump_type_str.lower():
        Nc = 6  # default column sections
        w["pump_kg"]     = round(0.45 * P**0.9 * Nc**0.3)
        w["motor_kg"]    = round(6.2 * P**0.80) if P > 200 else round(8.5 * P**0.75)
        w["baseplate_kg"]= 50
        w["total_kg"]    = w["pump_kg"] + w["motor_kg"] + w["baseplate_kg"]

    else:  # horizontal centrifugal
        w["pump_kg"]     = round(2.1 * P**0.72)
        w["motor_kg"]    = round(6.2 * P**0.80) if P > 200 else round(8.5 * P**0.75)
        w["baseplate_kg"]= round(2.5 * max(P * 0.6, 80))
        w["coupling_kg"] = max(10, round(0.015 * P))
        w["total_kg"]    = w["pump_kg"] + w["motor_kg"] + w["baseplate_kg"] + w["coupling_kg"]

    return w

def get_bom_template(template_id, db):
    """Get component requirements for a template."""
    bt = db["bom_tpl"].copy()
    # Section B starts after template registry
    # Header is at row 4, Section B data has Template_ID in col 0
    req_cols = ["Template_ID","Component_Category","Component_Subcategory",
                "Req_Type","Typical_MOC","Qty_Logic","Notes_for_BOM_Generator"]
    # Find section B rows
    bt_full = pd.read_excel(
        DB_PATH, sheet_name="BOM_Templates", header=None
    )
    # Find row with "Component_Category"
    for i, row in bt_full.iterrows():
        if "Component_Category" in str(list(row)):
            sec_b_header_row = i
            break
    else:
        sec_b_header_row = 14  # fallback

    sec_b = pd.read_excel(DB_PATH, sheet_name="BOM_Templates", header=sec_b_header_row)
    sec_b.columns = [str(c).strip() for c in sec_b.columns]

    # Filter for this template
    if "Template_ID" in sec_b.columns:
        tpl_comps = sec_b[sec_b["Template_ID"].astype(str).str.strip() == template_id.strip()]
        if not tpl_comps.empty:
            return tpl_comps
    # Fallback: return all HSC-01
    return sec_b[sec_b["Template_ID"].astype(str).str.contains("HSC-01", na=False)]


def tier2_generate(specs, db):
    """
    Generate BOM purely from physics + templates.
    Returns (bom_df, calc_summary)
    """
    Q     = specs.get("flow_m3h")
    H     = specs.get("head_m")
    n     = specs.get("speed_rpm", 1450)
    fluid = specs.get("fluid", "Clear Water")
    temp  = specs.get("temp_c", 30)
    rho   = specs.get("density_kgm3", 1000)
    stages= specs.get("stages", 1)
    motor_kw_input = specs.get("motor_kw")

    # Specific speed
    Ns = calc_specific_speed(Q, H, n)

    # Pump type
    tpl_id, pump_type_desc = classify_pump_type(Ns, Q, H, fluid, stages)

    # Motor kW
    eta_pump = 0.75 if (Ns and Ns < 1500) else 0.82
    motor_kw = motor_kw_input or calc_motor_kw(Q, H, rho, eta_pump)

    # Material selection
    moc = select_material(fluid, temp, 500, db)

    # Weights
    weights = estimate_weight(pump_type_desc, motor_kw, Q, H)

    # Build BOM from template
    tpl_comps = get_bom_template(tpl_id, db)

    rows = []
    comp_num = 1
    for _, tc in tpl_comps.iterrows():
        cat  = str(tc.get("Component_Category",""))
        sub  = str(tc.get("Component_Subcategory",""))
        req  = str(tc.get("Req_Type","M"))
        qty_logic = str(tc.get("Qty_Logic","1"))

        # Select MOC based on category
        moc_key_map = {
            "Casing":    "Casing_MOC",
            "Impeller":  "Impeller_MOC",
            "Shaft":     "Shaft_MOC",
            "Sleeve":    "Shaft_Sleeve_MOC",
            "Wear Ring": "Wear_Ring_MOC",
            "Seal":      "Seal_Type",
            "Fasteners": "Fastener_MOC",
        }
        mat = moc.get(moc_key_map.get(cat, "Casing_MOC"), "Per Service")
        if mat in ["nan","VTA","None"]:
            mat = "Engineer to Specify"

        # Weight estimate per component
        wt_map = {
            "Pump":       weights.get("pump_kg"),
            "Motor":      weights.get("motor_kg"),
            "Baseplate":  weights.get("baseplate_kg"),
            "Coupling":   weights.get("coupling_kg"),
        }
        wt = wt_map.get(cat, None)

        rows.append({
            "No":           comp_num,
            "Component_ID": f"CALC-{tpl_id}-{comp_num:03d}",
            "Category":     cat,
            "Description":  f"{sub}" if sub != cat else cat,
            "MOC":          mat,
            "Qty":          qty_logic,
            "Req_Type":     req,
            "Weight_kg":    wt if wt else "",
            "Source":       "Tier 2 — Physics Calculated",
            "Notes":        str(tc.get("Notes_for_BOM_Generator",""))[:80],
        })
        comp_num += 1

    bom_df = pd.DataFrame(rows)

    calc_summary = {
        "specific_speed_Ns": round(Ns, 1) if Ns else "N/A",
        "pump_type":         pump_type_desc,
        "template_used":     tpl_id,
        "motor_kw_calc":     motor_kw,
        "eta_pump_assumed":  round(eta_pump * 100, 1),
        "material_rule":     moc.get("Rule_ID"),
        "fluid_matched":     moc.get("Fluid_Match"),
        "seal_plan":         moc.get("Seal_Plan"),
        "weights":           weights,
        "moc":               moc,
    }
    return bom_df, calc_summary


# ─────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────
def generate_bom(specs, db):
    """
    Try Tier 1 first, fall back to Tier 2.
    Returns (bom_df, tier_used, match_info, calc_summary)
    """
    pump_row, score, match_type = tier1_match(specs, db)

    if pump_row is not None and score >= 30:
        bom = get_bom_from_match(pump_row, db)
        if not bom.empty:
            # Format Tier 1 BOM
            bom_out = bom[["Component_ID","Category","Subcategory",
                           "Component_Name","Material_Spec","Qty_Per_Unit",
                           "Unit","Weight_kg","Vendor_Name","Notes"]].copy()
            bom_out.insert(0, "No", range(1, len(bom_out)+1))
            bom_out.insert(9, "Source", "Tier 1 — Database Match")
            return bom_out, "tier1", {
                "pump_id":    pump_row["Pump_ID"],
                "model":      pump_row["Model"],
                "score":      score,
                "match_type": match_type,
            }, None

    # Tier 2
    bom_df, calc_summary = tier2_generate(specs, db)
    return bom_df, "tier2", None, calc_summary


# ─────────────────────────────────────────────────────────────────
# EXCEL EXPORT
# ─────────────────────────────────────────────────────────────────
def export_bom_excel(bom_df, specs, tier, match_info, calc_summary):
    """Export BOM to Excel with formatting."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from io import BytesIO

    wb = Workbook()
    ws = wb.active
    ws.title = "BOM Output"

    # Title
    ws.merge_cells("A1:L1")
    c = ws["A1"]
    c.value = "AUTOMATED BILL OF MATERIALS — GENERATED BY BOM SYSTEM"
    c.font  = Font(bold=True, size=13, color="FFFFFF")
    c.fill  = PatternFill("solid", start_color="1F4E79")
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 22

    # Project info
    info = [
        ["Pump Model / Description", specs.get("model","Unknown") or "Calculated"],
        ["Flow (m³/h)",   specs.get("flow_m3h","N/A")],
        ["Head (m)",      specs.get("head_m","N/A")],
        ["Fluid",         specs.get("fluid","N/A")],
        ["Temperature",   f"{specs.get('temp_c','N/A')} °C"],
        ["BOM Source",    f"Tier 1 — Database Match ({match_info['model']})" if tier=="tier1"
                          else f"Tier 2 — Physics Calculated ({calc_summary.get('pump_type','')})"],
        ["Generated",     pd.Timestamp.now().strftime("%d-%b-%Y %H:%M")],
    ]
    r = 2
    for label, val in info:
        ws.cell(r, 1, label).font = Font(bold=True, size=9)
        ws.cell(r, 2, str(val)).font = Font(size=9)
        r += 1
    r += 1

    # BOM header
    hdr_fill = PatternFill("solid", start_color="2E75B6")
    hdr_font = Font(bold=True, color="FFFFFF", size=9)
    s = Side(style="thin", color="CCCCCC")
    border = Border(left=s, right=s, top=s, bottom=s)

    for j, col in enumerate(bom_df.columns):
        c = ws.cell(r, j+1, col)
        c.font = hdr_font
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", wrap_text=True)
        c.border = border
    ws.row_dimensions[r].height = 30
    r += 1

    # BOM data
    alt = PatternFill("solid", start_color="F2F2F2")
    for i, (_, row) in enumerate(bom_df.iterrows()):
        for j, val in enumerate(row):
            c = ws.cell(r, j+1, val if pd.notna(val) else "")
            c.font = Font(size=9)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = border
            if i % 2 == 1:
                c.fill = alt
        ws.row_dimensions[r].height = 20
        r += 1

    # Auto column widths
    col_widths = [6, 18, 14, 14, 35, 25, 6, 6, 8, 20, 14, 45]
    for i, w in enumerate(col_widths[:ws.max_column]):
        ws.column_dimensions[get_column_letter(i+1)].width = w

    ws.freeze_panes = f"A{r - len(bom_df)}"

    # Calc summary sheet if Tier 2
    if tier == "tier2" and calc_summary:
        ws2 = wb.create_sheet("Calculation Summary")
        ws2["A1"] = "TIER 2 CALCULATION SUMMARY"
        ws2["A1"].font = Font(bold=True, size=12, color="FFFFFF")
        ws2["A1"].fill = PatternFill("solid", start_color="70AD47")
        r2 = 3
        for k, v in calc_summary.items():
            if k not in ["moc","weights"]:
                ws2.cell(r2, 1, str(k)).font = Font(bold=True, size=9)
                ws2.cell(r2, 2, str(v)).font = Font(size=9)
                r2 += 1
        r2 += 1
        ws2.cell(r2, 1, "WEIGHT ESTIMATES").font = Font(bold=True, size=10)
        r2 += 1
        for k, v in calc_summary.get("weights", {}).items():
            ws2.cell(r2, 1, k).font = Font(bold=True, size=9)
            ws2.cell(r2, 2, str(v) + " kg").font = Font(size=9)
            r2 += 1
        ws2.column_dimensions["A"].width = 30
        ws2.column_dimensions["B"].width = 40

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
