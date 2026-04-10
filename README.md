# Automated BOM Generation System
### For Engineered Equipment (Pumps)

**Automated Bill of Materials generation from equipment datasheets using a rule-based classification engine and physics-backed calculation system.**

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://your-app-name.streamlit.app)

---

## What It Does

Upload a pump datasheet PDF → get a complete Bill of Materials in under 10 seconds.

**Two-tier approach:**
- **Tier 1 (Data-Rich)** — If the pump is in the database, returns the exact real-world BOM directly
- **Tier 2 (Physics-Backed)** — If not, classifies the pump using specific speed analysis, selects materials from a compatibility matrix, calculates motor sizing and weights from empirical correlations, and generates a BOM from validated templates

---

## Tech Stack

```
Python 3.10+    — core language
Streamlit       — web interface  
pdfplumber      — PDF text extraction
pandas          — data handling
openpyxl        — Excel database read/write
re, math        — spec parsing + physics calculations
```

**Zero external APIs. Zero cost to run.**

---

## Database (Component_Library_COMPLETE.xlsx)

Built from real vendor documents across 12 pumps:

| Sheet | Contents |
|-------|----------|
| `Pump_Master_List` | 12 real pumps with full performance specs |
| `Component_Library` | 169 components — MOC, weights, vendors |
| `Material_Database` | 28 materials with density, temp limits, standards |
| `Vendor_Database` | 28 vendors across India + global |
| `BOM_Templates` | Mandatory/Optional component rules per pump type |
| `Physics_Parameters` | Ns classification, motor sizing, weight correlations |
| `Material_Compatibility` | 24 rules: fluid × temp × pressure → full MOC selection |

---

## Supported Pump Types

| Type | API Classification | Examples in DB |
|------|--------------------|----------------|
| Horizontal Split Casing | BB1/BB2 | Flowserve 300-LNN-600, Wilo 150-200-GSN |
| Horizontal Slurry | — | Metso HM200 MHC-S C5 |
| Vertical Turbine (Water) | VS1 | Jyoti 400TE3D4, Jyoti 350TE1A4, Wilo MPS-3 |
| Vertical Turbine (Condensate) | VS6 | KSB WKT 80/9, KSB VS6 |
| Vertical Sump | VS4 | KSB STGC 050-160-CC-WDL |
| Vertical Submersible | VS5 | KSB RPH-V 50-360, KSB RPH-V 40-180 |

---

## Local Setup

```bash
git clone https://github.com/YOUR_USERNAME/bom-system.git
cd bom-system
pip install -r requirements.txt
streamlit run app.py
```

---

## Deploy on Streamlit Cloud

1. Fork/push this repo to your GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Click **New app**
4. Select your repo → branch `main` → file `app.py`
5. Click **Deploy**

Free tier is sufficient. No secrets or environment variables needed.

---

## Accuracy

| Scenario | BOM Completeness |
|----------|-----------------|
| Pump already in database | 100% — exact real BOM |
| Clean digital PDF, standard format | 70–80% — engineer review recommended |
| Manual spec entry | 75–85% — full physics path |

Tier 2 output is a calculated starting point. Final BOM should be reviewed by a mechanical engineer before issue.

---

## Project Background

Final year engineering project — Automated BOM Generation System for Engineered Equipment.

**Phase 1 complete:** Database architecture and population from real vendor documents  
**Phase 2 complete:** Classification engine, material selection, weight calculations, BOM generator  
**Phase 3 complete:** Streamlit web interface  
**Phase 4:** Testing against historical projects (in progress)
