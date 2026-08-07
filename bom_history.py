"""
BOM history — SQLite-backed, one file on disk (bom_history.db).

Every successful agent run is saved with its full result payload so
the buyer can reload any past BOM straight back into the app.

API:
    history_save(result, source_pdf="")   -> int id
    history_list(limit=50)                -> [(id, ts, product, source_pdf, n_components), ...]
    history_load(row_id)                  -> the result dict (or None)
    history_delete(row_id)                -> None
    render_history_sidebar(ss, on_load)   -> paints the sidebar UI

Persistence caveat (also flagged in the launch deck):
    On Streamlit Community Cloud the container filesystem is ephemeral
    and resets on redeploys / cold starts. For durable storage mount a
    persistent volume, or point _DB_PATH at one via the BOM_HISTORY_DB
    environment variable.
"""
from __future__ import annotations
import json, os, sqlite3
from datetime import datetime
from pathlib import Path
import streamlit as st

_DB_PATH = Path(os.environ.get("BOM_HISTORY_DB",
                               Path(__file__).parent / "bom_history.db"))


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS bom_runs(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ts            TEXT    NOT NULL,
        product       TEXT,
        source_pdf    TEXT,
        n_components  INTEGER,
        payload       TEXT    NOT NULL)""")
    return c


def history_save(result: dict, source_pdf: str = "") -> int | None:
    if not result:
        return None
    bom = result.get("bom") or []
    product = (result.get("product") or {}).get("name") or "Unnamed"
    try:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO bom_runs(ts,product,source_pdf,n_components,payload) "
                "VALUES(?,?,?,?,?)",
                (datetime.utcnow().isoformat(timespec="seconds"),
                 product, source_pdf, len(bom),
                 json.dumps(result, default=str)))
            return cur.lastrowid
    except Exception as e:
        st.warning(f"Could not save to history: {e}")
        return None


def history_list(limit: int = 50):
    try:
        with _conn() as c:
            return c.execute(
                "SELECT id, ts, product, source_pdf, n_components "
                "FROM bom_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    except Exception:
        return []


def history_load(row_id: int):
    with _conn() as c:
        row = c.execute("SELECT payload FROM bom_runs WHERE id=?", (row_id,)).fetchone()
    return json.loads(row[0]) if row else None


def history_delete(row_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM bom_runs WHERE id=?", (row_id,))


def render_history_sidebar(ss, on_load):
    """Paint the History section in the sidebar.

    on_load(result_dict) is called when the buyer clicks a saved row.
    Typically:  lambda r: (ss.update(result=r, rates={}, pcts={}), st.rerun())
    """
    rows = history_list(limit=50)
    st.markdown("**History**")
    st.caption(f"{len(rows)} saved" if rows else "No saved BOMs yet")

    if not rows:
        return

    for row_id, ts, product, source_pdf, n in rows:
        try:
            when = datetime.fromisoformat(ts).strftime("%d %b · %H:%M")
        except Exception:
            when = ts
        label = product[:26]
        sub   = f"{when} · {n} parts"
        if source_pdf:
            sub += f"\n{source_pdf[:34]}"

        c1, c2 = st.columns([5, 1])
        if c1.button(f"{label}\n\n{sub}", key=f"hist_load_{row_id}",
                     use_container_width=True):
            r = history_load(row_id)
            if r:
                on_load(r)
        if c2.button("×", key=f"hist_del_{row_id}",
                     help="Delete this saved BOM"):
            history_delete(row_id)
            st.rerun()
