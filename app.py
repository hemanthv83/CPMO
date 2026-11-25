"""CPMO – AI-Powered Global Sourcing Intelligence (Streamlit).

Run:  streamlit run app.py
Keys and passwords come only from Streamlit Secrets / environment variables (see README).
"""
from __future__ import annotations

import io
import os
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote

import pandas as pd
import streamlit as st

import engine as E
import store as S

APP, TAGLINE = "CPMO", "AI-Powered Global Sourcing Intelligence"
NAVY = "#1F3864"
st.set_page_config(page_title=f"{APP} – {TAGLINE}", page_icon="🌐", layout="wide")

DEFAULT_SETTINGS = {"searches_per_hour": 20, "daily_api_cap": 400, "max_pages": 40, "time_budget": 90,
                    "company": "Sun Mobility", "sender": "", "location": "Bengaluru, India"}

st.markdown(f"""<style>
[data-testid="stSidebar"] {{background:{NAVY};}}
[data-testid="stSidebar"] * {{color:#fff !important;}}
[data-testid="stSidebar"] [data-baseweb="select"] * {{color:#000 !important;}}
.block-container {{padding-top:1.4rem;}}
.card {{border:1px solid #d6dce8;border-left:5px solid {NAVY};border-radius:8px;padding:12px 16px;margin-bottom:10px;background:#fff;}}
.card h4 {{margin:0 0 4px 0;color:{NAVY};font-size:1.02rem;}}
.muted {{color:#5f6b7a;font-size:.85rem;}}
.badge {{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.75rem;font-weight:600;margin-right:4px;}}
.kpi {{border:1px solid #d6dce8;border-radius:8px;padding:10px 14px;background:#f7f9fc;}}
.kpi .v {{font-size:1.6rem;font-weight:700;color:{NAVY};}}
.kpi .l {{font-size:.78rem;color:#5f6b7a;text-transform:uppercase;letter-spacing:.03em;}}
</style>""", unsafe_allow_html=True)

LEVEL_COL = {"A": "#1e7b34", "B": "#2f6db5", "C": "#b07a00", "D": "#a33", "E": "#777"}
MATCH_COL = {"Exact MPN Match": "#1e7b34", "Same Manufacturer Alternative": "#2f6db5",
             "Technically Compatible Alternative": "#6a4bb5", "Similar Product": "#b07a00", "Not Verified": "#777"}


def badge(text, color):
    return f"<span class='badge' style='background:{color}1a;color:{color};border:1px solid {color}55'>{text}</span>"


def na(v):
    return E.NA if v in (None, "", [], {}) else v


def esc(s):
    return (str(s) if s is not None else "").replace("<", "&lt;").replace(">", "&gt;")


# ============================================================================ secrets
def sec(section, key, default=None):
    env = os.environ.get(f"{section}_{key}".upper())
    if env:
        return env
    try:
        return st.secrets.get(section, {}).get(key, default)
    except Exception:  # noqa: BLE001
        return default


def search_keys():
    return {"tavily": sec("search", "tavily_key"), "brave": sec("search", "brave_key"), "serpapi": sec("search", "serpapi_key")}


def llm_cfg():
    return {"openai_key": sec("openai", "api_key"), "openai_model": sec("openai", "model", "gpt-4o-mini"),
            "anthropic_key": sec("anthropic", "api_key"), "anthropic_model": sec("anthropic", "model", "claude-haiku-4-5-20251001")}


def gh_cfg():
    tok, repo = sec("github", "token"), sec("github", "repo")
    if not tok or not repo:
        return None
    return {"token": tok, "repo": repo, "branch": sec("github", "branch", "data"),
            "path": sec("github", "path", "cpmo_data.json"), "base": sec("github", "base_branch", "main")}


def auth_users():
    try:
        u = st.secrets.get("auth", {}).get("users", {})
        return {k: dict(v) for k, v in u.items()}
    except Exception:  # noqa: BLE001
        return {}


# ============================================================================ shared cache (7 days, all users)
class TTLCache(dict):
    ttl = 7 * 24 * 3600

    def __setitem__(self, k, v):
        super().__setitem__(k, (time.time(), v))

    def __contains__(self, k):
        if not super().__contains__(k):
            return False
        if time.time() - super().__getitem__(k)[0] > self.ttl:
            super().__delitem__(k)
            return False
        return True

    def __getitem__(self, k):
        return super().__getitem__(k)[1]


@st.cache_resource
def shared_cache():
    return TTLCache()


# ============================================================================ data load / save
def load():
    ss = st.session_state
    if "data" in ss:
        return
    ss.sha, ss.source, ss.load_error = None, "memory", None
    cfg, path = gh_cfg(), os.environ.get("CPMO_DATA_FILE")
    data = None
    if cfg:
        try:
            data, ss.sha = S.gh_load(cfg)
            ss.source = "github"
        except Exception as e:  # noqa: BLE001
            ss.load_error = str(e)
    elif path:
        data, ss.source = S.file_load(path), "file"
    ss.data = S.norm(data or S.empty())
    ss.data["settings"] = {**DEFAULT_SETTINGS, **ss.data.get("settings", {})}


def save(msg):
    ss = st.session_state
    try:
        if ss.source == "github" and not ss.load_error:
            ss.sha = S.gh_save(gh_cfg(), ss.data, ss.sha, msg)
        elif ss.source == "file":
            S.file_save(os.environ["CPMO_DATA_FILE"], ss.data)
        return True
    except Exception as e:  # noqa: BLE001
        st.error(f"Not saved: {e}. Your change is kept in this session – download a backup from Settings.")
        return False


def D():
    return st.session_state.data


# ============================================================================ auth
def login():
    users = auth_users()
    ss = st.session_state
    if not users:
        ss.user, ss.role = "user", "Admin"
        return True
    if ss.get("user"):
        return True
    st.markdown(f"## 🌐 {APP}\n#### {TAGLINE}")
    with st.form("login"):
        u = st.text_input("User name")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            rec = users.get(u.strip())
            if rec and str(rec.get("password")) == p:
                ss.user, ss.role = u.strip(), rec.get("role", "Viewer")
                st.rerun()
            st.error("Wrong user name or password.")
    return False


def can(action):
    role = st.session_state.get("role", "Viewer")
    return {"view": True, "search": role in ("Admin", "Buyer"), "edit": role in ("Admin", "Buyer"),
            "admin": role == "Admin"}[action]


# ============================================================================ usage / limits
def usage_today():
    return D()["usage"].setdefault(date.today().isoformat(), {})


def record_usage(u: E.Usage):
    t = usage_today()
    for k, v in u.calls.items():
        t[k] = t.get(k, 0) + v


def limit_ok():
    s = D()["settings"]
    calls = sum(usage_today().values())
    if calls >= s["daily_api_cap"]:
        return False, f"Daily API call cap reached ({calls}/{s['daily_api_cap']}). An Admin can raise it in Settings."
    hour_ago = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    mine = [x for x in D()["searches"] if x.get("user") == st.session_state.user and x.get("date", "") >= hour_ago]
    if len(mine) >= s["searches_per_hour"]:
        return False, f"Limit of {s['searches_per_hour']} searches per hour reached. Try again later."
    return True, ""


def engines():
    u = E.Usage()
    return E.Searcher(search_keys(), u, shared_cache()), E.LLM(llm_cfg(), u), u


# ============================================================================ file text extraction (memory only, never stored)
def file_text(f):
    name, data = f.name.lower(), f.getvalue()
    if name.endswith(".pdf"):
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages[:4])[:8000], None
    if name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        _, llm, u = engines()
        if not llm.ready:
            return "", "Image reading needs an AI key (OpenAI or Anthropic). Type the part number instead."
        mime = "image/png" if name.endswith(".png") else ("image/webp" if name.endswith(".webp") else "image/jpeg")
        txt = llm.read_image(data, mime)
        record_usage(u)
        return txt or "", (None if txt else "Could not read text from the image.")
    if name.endswith((".xlsx", ".xls", ".csv")):
        df = pd.read_csv(io.BytesIO(data)) if name.endswith(".csv") else pd.read_excel(io.BytesIO(data))
        return df.head(30).to_string()[:6000], None
    return "", "Unsupported file type."


# ============================================================================ UI helpers
def kpi(col, label, value):
    col.markdown(f"<div class='kpi'><div class='l'>{label}</div><div class='v'>{value}</div></div>", unsafe_allow_html=True)


def header(title, sub=""):
    st.markdown(f"<h2 style='color:{NAVY};margin-bottom:0'>{title}</h2><div class='muted'>{sub}</div>", unsafe_allow_html=True)
    st.write("")


def contact_lines(r):
    out = []
    for e in r.get("emails") or []:
        tag = "sales" if e["type"] == "Sales" else ("general" if e["type"] == "General" else "other")
        fm = " · free-mail" if e.get("free_mail") else ""
        out.append(f"✉️ <a href='mailto:{esc(e['email'])}'>{esc(e['email'])}</a> <span class='muted'>({tag}{fm})</span>")
    for p in r.get("phones") or []:
        out.append(f"📞 {esc(p)}")
    for w in r.get("whatsapp") or []:
        out.append(f"💬 WhatsApp (published on source page): {esc(w)}")
    return out


def supplier_card(r, i, key_prefix):
    lines = contact_lines(r)
    src = r.get("contact_source")
    general = " " + badge("General contact – no sales contact found", "#b07a00") if r.get("general_contact") else ""
    snippet_only = "" if r.get("page_fetched", True) else "<div class='muted'>⚠️ Page could not be opened (blocked or timed out) – classified from the search snippet only.</div>"
    specs = r.get("spec_compare") or {}
    spec_html = ""
    if specs:
        cells = []
        for k, (res, want, got) in specs.items():
            col = {"Match": "#1e7b34", "Differs": "#a33"}.get(res, "#777")
            cells.append(f"{E.SPEC_LABEL.get(k, k)}: <b style='color:{col}'>{res}</b>"
                         + (f" ({esc(got)})" if got and res == 'Differs' else ""))
        spec_html = "<div class='muted'>Specs vs requirement – " + " · ".join(cells) + "</div>"
    comm = " · ".join(f"{k}: {esc(na(r.get(f)))}" for k, f in
                      (("Price", "price"), ("MOQ", "moq"), ("Lead time", "lead_time"), ("Warranty", "warranty"), ("Availability", "availability")))
    st.markdown(
        f"<div class='card'><h4>{i}. {esc(r['company'])}</h4>"
        f"{badge(r['match'], MATCH_COL[r['match']])}{badge('Level ' + r['level'] + ' – ' + r['level_text'], LEVEL_COL[r['level']])}"
        f"{badge(r['type'], '#1F3864')}{general}"
        f"<div class='muted'>🌍 {esc(na(r.get('country')))} · 🔗 <a href='{esc(r['website'])}' target='_blank'>{esc(r['domain'])}</a>"
        f" · 📄 <a href='{esc(r.get('product_page') or r['source_url'])}' target='_blank'>source page</a>"
        + (f" · contact source: <a href='{esc(src)}' target='_blank'>{esc(E.domain_of(src))}</a>" if src else "")
        + "</div>"
        + (f"<div class='muted'>📍 {esc(r['address'])}</div>" if r.get("address") else "")
        + (f"<div class='muted'>👤 Sales contact named on page: {esc(r['sales_person'])}</div>" if r.get("sales_person") else "")
        + f"<div style='margin-top:6px'>{'<br>'.join(lines) if lines else '<span class=muted>Contact: Not found (Level E)</span>'}</div>"
        f"{spec_html}<div class='muted'>{comm}</div>{snippet_only}</div>", unsafe_allow_html=True)


def results_df(rows):
    return pd.DataFrame([{
        "Company": r["company"], "Type": r["type"], "Country": na(r.get("country")), "Match": r["match"],
        "Sales / contact email": ", ".join(e["email"] for e in r.get("emails") or []) or E.NA,
        "Phone": ", ".join(r.get("phones") or []) or E.NA, "WhatsApp": ", ".join(r.get("whatsapp") or []) or E.NA,
        "Level": f"{r['level']} – {r['level_text']}", "Price": na(r.get("price")), "MOQ": na(r.get("moq")),
        "Lead time": na(r.get("lead_time")), "Warranty": na(r.get("warranty")), "Availability": na(r.get("availability")),
        "Website": r["website"], "Source": r.get("product_page") or r["source_url"],
        "Contact source": r.get("contact_source") or E.NA} for r in rows])


def to_xlsx(sheets: dict) -> bytes:
    b = io.BytesIO()
    with pd.ExcelWriter(b, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name[:31], index=False)
            ws = w.sheets[name[:31]]
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(60, max(10, max(len(str(c.value or "")) for c in col[:50]) + 2))
    return b.getvalue()


LINKCOLS = {"Website": st.column_config.LinkColumn(), "Source": st.column_config.LinkColumn(),
            "Contact source": st.column_config.LinkColumn()}


# ============================================================================ pages
def page_dashboard():
    header("Dashboard", TAGLINE)
    d = D()
    sr = d["searches"]
    found = sum(x.get("n_results", 0) for x in sr)
    exact = sum(x.get("n_exact", 0) for x in sr)
    verified = sum(1 for s in d["suppliers"] if s.get("level") in ("A", "B"))
    c = st.columns(6)
    kpi(c[0], "Total searches", len(sr))
    kpi(c[1], "Suppliers identified", found)
    kpi(c[2], "Saved suppliers", len(d["suppliers"]))
    kpi(c[3], "Verified contacts (A/B)", verified)
    kpi(c[4], "Exact part matches", exact)
    kpi(c[5], "RFQs generated", len(d["rfqs"]))
    st.caption("Verified = contact published on the company's own site (A) or its official B2B profile (B). "
               "No email address is tested for delivery.")
    l, r = st.columns(2)
    with l:
        st.subheader("Recent searches")
        if sr:
            st.dataframe(pd.DataFrame([{"Date": x["date"], "User": x.get("user"), "Search": x["query"],
                                        "Part": x["part"].get("mpn") or "Generic", "Suppliers": x.get("n_results"),
                                        "Exact": x.get("n_exact"), "Partial": "Yes" if x.get("partial") else ""}
                                       for x in reversed(sr[-15:])]), hide_index=True, width="stretch")
        else:
            st.info("No searches yet. Go to **Search** to find suppliers.")
    with r:
        st.subheader("Saved suppliers by country")
        if d["suppliers"]:
            cnt = pd.Series([s.get("country") or "Not Available" for s in d["suppliers"]]).value_counts()
            import plotly.express as px
            fig = px.bar(cnt, orientation="h", labels={"value": "Suppliers", "index": ""}, color_discrete_sequence=[NAVY])
            fig.update_layout(height=320, showlegend=False, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No saved suppliers yet.")
    if d["suppliers"]:
        st.subheader("Recently saved suppliers")
        st.dataframe(pd.DataFrame([{"Company": s["company"], "Country": na(s.get("country")), "Level": s.get("level"),
                                    "Parts": ", ".join(sorted({p.get('mpn') or 'generic' for p in s.get('products', [])})),
                                    "Saved": s.get("saved_at")} for s in d["suppliers"][-10:][::-1]]),
                     hide_index=True, width="stretch")


def status_banner():
    sk, lc = search_keys(), llm_cfg()
    if not any(sk.values()):
        st.error("**Live search is not configured.** Add a Tavily, Brave or SerpAPI key in Streamlit Secrets "
                 "(see README). CPMO never shows sample suppliers.")
        return False
    if not (lc["openai_key"] or lc["anthropic_key"]):
        st.info("AI key not set: part identification and extraction use rules only (no address, weaker identification, no image reading).")
    return True


def page_search():
    header("Component Search", "Step 1 identify the part → Steps 2–7 find suppliers, contacts and sources")
    ss = st.session_state
    ready = status_banner()
    with st.form("q"):
        q = st.text_area("Part number, manufacturer, description or specifications",
                         value=ss.get("prefill", ""), height=80,
                         placeholder="e.g. BYD EVR50CI 50A 12V DC contactor")
        c1, c2 = st.columns([2, 1])
        f = c1.file_uploader("Optional: datasheet (PDF), product photo or nameplate (image)",
                             type=["pdf", "png", "jpg", "jpeg", "webp"])
        refresh = c2.checkbox("Ignore cache (fresh search)")
        go = st.form_submit_button("Step 1 – Identify part", type="primary", disabled=not (ready and can("search")))
    if not can("search"):
        st.caption("Your role is Viewer: searching is disabled.")
    if go:
        if not q.strip() and not f:
            st.warning("Enter a part number/description or upload a file.")
        else:
            ok, why = limit_ok()
            if not ok:
                st.error(why)
            else:
                extra, err = ("", None)
                if f:
                    with st.spinner("Reading file…"):
                        extra, err = file_text(f)
                    if err:
                        st.warning(err)
                searcher, llm, u = engines()
                with st.spinner("Identifying part…"):
                    part = E.identify_part(q.strip() or extra[:200], extra, searcher, llm, refresh)
                record_usage(u)
                ss.part, ss.refresh, ss.run = part, refresh, None
                ss.prefill = q
    part = ss.get("part")
    if not part:
        return
    st.markdown("### Step 1 – Identified part")
    conf_col = {"High": "#1e7b34", "Medium": "#b07a00", "Low": "#a33"}.get(part["confidence"], "#777")
    st.markdown(badge(f"Confidence: {part['confidence']}", conf_col) + badge(f"Method: {part['method']}", NAVY)
                + (badge("Generic requirement – no part number", "#b07a00") if part["is_generic"] else ""), unsafe_allow_html=True)
    if part.get("source_urls"):
        st.caption("Identification sources: " + " · ".join(f"[{E.domain_of(u)}]({u})" for u in part["source_urls"][:5]))
    if part.get("candidates"):
        st.warning("Several parts may match. Choose one, or edit the fields below.")
        opts = ["Keep as identified"] + [f"{c.get('mpn')} – {c.get('manufacturer')} ({c.get('why', '')})" for c in part["candidates"]]
        pick = st.radio("Candidates", opts, index=0)
        if pick != opts[0]:
            c = part["candidates"][opts.index(pick) - 1]
            part["mpn"], part["manufacturer"] = c.get("mpn"), c.get("manufacturer")
    with st.form("confirm"):
        c = st.columns(3)
        mpn = c[0].text_input("MPN", part.get("mpn") or "")
        mk = c[1].text_input("Manufacturer", part.get("manufacturer") or "")
        cat = c[2].text_input("Category", part.get("category") or "")
        desc = st.text_input("Description", part.get("description") or "")
        spec_df = pd.DataFrame([{"Spec": k, "Label": E.SPEC_LABEL.get(k, k), "Value": v} for k, v in part["key_specs"].items()]
                               or [{"Spec": "current_A", "Label": "Current rating (A)", "Value": ""}])
        st.caption("Required specifications (used to compare alternatives). Spec keys: " + ", ".join(E.SPEC_LABEL))
        spec_ed = st.data_editor(spec_df, num_rows="dynamic", hide_index=True, width="stretch", key="spec_ed",
                                 column_config={"Spec": st.column_config.SelectboxColumn(options=list(E.SPEC_LABEL))})
        c = st.columns([2, 1, 1])
        regions = c[0].multiselect("Regions", E.REGIONS, default=E.REGIONS)
        maxp = c[1].number_input("Max websites to check", 10, 80, int(D()["settings"]["max_pages"]), 5)
        tb = c[2].number_input("Time limit (s)", 30, 240, int(D()["settings"]["time_budget"]), 10)
        run = st.form_submit_button("Steps 2–7 – Find suppliers", type="primary", disabled=not (ready and can("search")))
    if run:
        ok, why = limit_ok()
        if not ok:
            st.error(why)
            return
        part.update(mpn=mpn.strip() or None, manufacturer=mk.strip() or None, category=cat.strip() or None,
                    description=desc.strip(), is_generic=not mpn.strip(),
                    key_specs={r["Spec"]: str(r["Value"]).strip() for _, r in spec_ed.iterrows() if str(r.get("Value") or "").strip()})
        searcher, llm, u = engines()
        steps = {2: "Search queries", 3: "Searching manufacturers, distributors and regional suppliers",
                 5: "Opening websites and extracting contacts", 6: "Verifying sources and classifying"}
        with st.status("Searching…", expanded=True) as box:
            def prog(step, msg):
                box.write(f"**Step {step} – {steps.get(step, '')}:** {msg}")
            run_ = E.run_search(part, searcher, llm, regions, int(maxp), int(tb), ss.get("refresh", False), prog)
            box.update(label=f"Done in {run_['seconds']} s – {len(run_['results'])} suppliers"
                       + (" (PARTIAL – time or page limit reached)" if run_["partial"] else ""),
                       state="complete" if run_["results"] else "error", expanded=False)
        record_usage(u)
        rec = {"id": S.new_id(), "date": S.now(), "user": ss.user, "query": ss.get("prefill") or part["description"],
               "part": part, "n_results": len(run_["results"]),
               "n_exact": sum(r["match"] == "Exact MPN Match" for r in run_["results"]),
               "partial": run_["partial"], "results": run_["results"], "excluded": run_["excluded"],
               "queries": run_["queries"], "errors": run_["errors"]}
        D()["searches"].append(rec)
        D()["searches"] = D()["searches"][-S.SEARCH_KEEP:]
        save(f"search {rec['query'][:40]}")
        ss.run = rec
    show_results(ss.get("run"))


def show_results(run):
    if not run:
        return
    ss = st.session_state
    res = run["results"]
    st.markdown("### Results")
    if run.get("partial"):
        st.warning("Partial results: the time or website limit was reached. Run again with a higher limit for more.")
    if run.get("errors"):
        with st.expander(f"⚠️ {len(run['errors'])} errors during search (search continued where possible)"):
            for e in run["errors"]:
                st.text(e)
    if not res:
        st.error("No suppliers found. Check the part number, widen the description, or add a search provider.")
        return
    exact = [r for r in res if r["match"] == "Exact MPN Match"]
    alts = [r for r in res if r["match"] != "Exact MPN Match"]
    c = st.columns(5)
    kpi(c[0], "Suppliers", len(res))
    kpi(c[1], "Exact MPN", len(exact))
    kpi(c[2], "Level A/B contacts", sum(r["level"] in "AB" for r in res))
    kpi(c[3], "Sales contacts", sum(r.get("contact_kind") == "Sales" for r in res))
    kpi(c[4], "Excluded", len(run.get("excluded", [])))
    if run["part"].get("is_generic"):
        st.info("Generic requirement: no part number, so there can be no exact matches. "
                "Results are grouped by how well their specifications match yours.")
    names = [f"{r['company']} · {r['domain']}" for r in res]
    tabs = st.tabs([f"Exact matches ({len(exact)})", f"Alternatives & similar ({len(alts)})", "Table view",
                    f"Excluded ({len(run.get('excluded', []))})", "Queries"])
    with tabs[0]:
        if not exact:
            st.caption("No page showed the exact part number.")
        for i, r in enumerate(exact, 1):
            supplier_card(r, i, "ex")
    with tabs[1]:
        st.caption("An alternative is listed as technically compatible only when every compared spec matches. "
                   "Confirm against the datasheet before treating it as equivalent.")
        for i, r in enumerate(alts, 1):
            supplier_card(r, i, "alt")
    with tabs[2]:
        df = results_df(res)
        st.dataframe(df, hide_index=True, width="stretch", column_config=LINKCOLS)
        st.download_button("⬇ Export results (Excel)", to_xlsx({"Results": df}),
                           f"CPMO_{(run['part'].get('mpn') or 'search')}_{date.today()}.xlsx")
    with tabs[3]:
        if run.get("excluded"):
            st.dataframe(pd.DataFrame([{"Domain": x["domain"], "Reason": x["excluded"], "URL": x["url"]}
                                       for x in run["excluded"]]), hide_index=True, width="stretch",
                         column_config={"URL": st.column_config.LinkColumn()})
    with tabs[4]:
        st.write(run.get("queries"))
    st.markdown("#### Save, compare, RFQ")
    pick = st.multiselect("Select suppliers", names, key=f"pick_{run['id']}")
    c = st.columns(3)
    sel = [res[names.index(n)] for n in pick]
    if c[0].button("💾 Save to supplier database", disabled=not (sel and can("edit"))):
        new = sum(S.upsert_supplier(D(), r, run["part"], ss.user) for r in sel)
        if save(f"save {len(sel)} suppliers"):
            st.success(f"Saved {len(sel)} ({new} new, {len(sel) - new} updated; manual edits kept).")
    if c[1].button("⚖️ Compare selected", disabled=len(sel) < 2):
        ss.compare = {"part": run["part"], "rows": sel}
        ss.goto = "Supplier Comparison"
        st.rerun()
    if c[2].button("✉️ RFQ for first selected", disabled=not sel):
        ss.rfq_ctx = {"part": run["part"], "supplier": sel[0]}
        ss.goto = "RFQ Generator"
        st.rerun()


def page_history():
    header("Search History", "Re-open earlier results without spending API calls")
    sr = D()["searches"]
    if not sr:
        st.info("No searches yet.")
        return
    labels = [f"{x['date']} · {x['query'][:60]} · {x['n_results']} suppliers" for x in reversed(sr)]
    i = st.selectbox("Search", range(len(labels)), format_func=lambda k: labels[k])
    run = list(reversed(sr))[i]
    st.session_state.part = run["part"]
    show_results(run)


def page_suppliers():
    header("Supplier Database", "Saved suppliers – no duplicates (one record per company domain). Edits are kept on re-save.")
    d = D()
    if not d["suppliers"]:
        st.info("Nothing saved yet. Save suppliers from a search result.")
        return
    q = st.text_input("Filter (company, country, part number, email)")
    rows = []
    for s in d["suppliers"]:
        mpns = ", ".join(sorted({p.get("mpn") or (p.get("description") or "")[:30] for p in s.get("products", [])}))
        rows.append({"id": s["id"], "Company": s["company"], "Type": s.get("type"), "Country": s.get("country") or "",
                     "Website": s.get("website"), "Sales email": ", ".join(e["email"] for e in s.get("emails") or []),
                     "Phone": ", ".join(s.get("phones") or []), "WhatsApp": ", ".join(s.get("whatsapp") or []),
                     "Parts / MPN": mpns, "Level": s.get("level"), "Contact source": s.get("contact_source") or "",
                     "Search date": s.get("search_date"), "Notes": s.get("notes", ""),
                     "Status": "Manually edited" if s.get("edited") else "As found"})
    df = pd.DataFrame(rows)
    if q:
        m = df.apply(lambda r: q.lower() in " ".join(map(str, r.values)).lower(), axis=1)
        df = df[m]
    edited = st.data_editor(
        df, hide_index=True, width="stretch", disabled=True if not can("edit") else ["id", "Search date", "Status", "Level", "Parts / MPN"],
        column_config={"id": None, "Website": st.column_config.LinkColumn(), "Contact source": st.column_config.LinkColumn(),
                       "Level": st.column_config.TextColumn(help="A official · B B2B profile · C third party · D unverified · E not found")},
        key="sup_ed")
    c = st.columns(4)
    if c[0].button("💾 Save edits", disabled=not can("edit")):
        by = {s["id"]: s for s in d["suppliers"]}
        n = 0
        for _, r in edited.iterrows():
            s = by.get(r["id"])
            if not s:
                continue
            new = {"company": r["Company"], "type": r["Type"], "country": r["Country"] or None, "website": r["Website"],
                   "notes": r["Notes"]}
            emails = [x.strip() for x in str(r["Sales email"] or "").split(",") if x.strip()]
            phones = [x.strip() for x in str(r["Phone"] or "").split(",") if x.strip()]
            wa = [x.strip() for x in str(r["WhatsApp"] or "").split(",") if x.strip()]
            changed = any(s.get(k) != v for k, v in new.items()) or emails != [e["email"] for e in s.get("emails") or []] \
                or phones != (s.get("phones") or []) or wa != (s.get("whatsapp") or [])
            if changed:
                bad = [e for e in emails if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", e)]
                if bad:
                    st.error(f"Not saved – invalid email for {r['Company']}: {', '.join(bad)}")
                    return
                old = {e["email"]: e for e in s.get("emails") or []}
                s.update(new)
                s["emails"] = [old.get(e, {"email": e, "type": "Manual", "free_mail": False, "same_domain": False}) for e in emails]
                s["phones"], s["whatsapp"], s["edited"] = phones, wa, True
                s["edited_by"], s["edited_at"] = st.session_state.user, S.now()
                n += 1
        if n and save(f"edit {n} suppliers"):
            st.success(f"{n} supplier(s) updated and marked 'Manually edited'.")
    exp = df.drop(columns=["id"])
    prod = pd.DataFrame([{"Company": s["company"], **{k.replace('_', ' ').title(): v for k, v in p.items()}}
                         for s in d["suppliers"] for p in s.get("products", [])])
    c[1].download_button("⬇ Excel", to_xlsx({"Suppliers": exp, "Products": prod}), f"CPMO_suppliers_{date.today()}.xlsx")
    c[2].download_button("⬇ CSV", exp.to_csv(index=False).encode("utf-8-sig"), f"CPMO_suppliers_{date.today()}.csv")
    if can("admin"):
        with c[3].popover("🗑 Delete"):
            names = {f"{s['company']} ({s['domain']})": s["id"] for s in d["suppliers"]}
            dl = st.multiselect("Suppliers to delete", list(names))
            if st.button("Confirm delete", type="primary", disabled=not dl):
                ids = {names[x] for x in dl}
                d["suppliers"] = [s for s in d["suppliers"] if s["id"] not in ids]
                save(f"delete {len(ids)} suppliers")
                st.rerun()


def page_compare():
    header("Supplier Comparison", "Unknown values are shown as 'Not Available' – nothing is estimated")
    ss = st.session_state
    d = D()
    pool = {}
    if ss.get("run"):
        for r in ss.run["results"]:
            pool[f"[Search] {r['company']} · {r['domain']}"] = (r, ss.run["part"])
    for s in d["suppliers"]:
        p = s["products"][-1] if s.get("products") else {}
        r = {**s, **{k: p.get(k) for k in ("price", "moq", "lead_time", "warranty", "availability")},
             "match": p.get("match") or "Not Verified", "product_page": p.get("source_url")}
        pool[f"[Saved] {s['company']} · {s['domain']}"] = (r, {"mpn": p.get("mpn")})
    default = [k for k in pool if ss.get("compare") and any(k.endswith(r["domain"]) and k.startswith("[Search]")
                                                             for r in ss.compare["rows"])]
    pick = st.multiselect("Suppliers to compare", list(pool), default=default)
    if len(pick) < 2:
        st.info("Select at least two suppliers (from the latest search or the database).")
        return
    rows = []
    for k in pick:
        r, part = pool[k]
        e = (r.get("emails") or [{}])[0].get("email")
        rows.append({"Company": r["company"], "Country": na(r.get("country")), "Product match": r.get("match"),
                     "Part": na(part.get("mpn")), "Price": na(r.get("price")), "MOQ": na(r.get("moq")),
                     "Lead time": na(r.get("lead_time")), "Warranty": na(r.get("warranty")),
                     "Contact": na(e or ", ".join(r.get("phones") or [])), "Contact level": na(r.get("level")),
                     "Availability": na(r.get("availability")), "Source": r.get("product_page") or r.get("website")})
    df = pd.DataFrame(rows).set_index("Company").T
    st.dataframe(df, width="stretch")
    st.caption("Price, MOQ, lead time and availability are shown only when printed on the supplier's page. "
               "Use RFQs to get confirmed values.")
    st.download_button("⬇ Export comparison (Excel)", to_xlsx({"Comparison": df.reset_index()}), f"CPMO_comparison_{date.today()}.xlsx")


def page_rfq():
    header("RFQ Generator", "Drafts only – CPMO never sends email. Copy, download or open in your mail app.")
    ss = st.session_state
    d = D()
    ctx = ss.get("rfq_ctx") or {}
    part = dict(ctx.get("part") or ss.get("part") or {})
    sup = ctx.get("supplier") or {}
    suppliers = {f"{s['company']} ({s['domain']})": s for s in d["suppliers"]}
    if sup:
        suppliers = {f"{sup['company']} ({sup['domain']}) – from search": sup, **suppliers}
    s = d["settings"]
    with st.form("rfq"):
        c = st.columns(2)
        sname = c[0].selectbox("Supplier", ["(none – generic)"] + list(suppliers), index=1 if sup else 0)
        mpn = c[1].text_input("Part number", part.get("mpn") or "")
        c = st.columns(2)
        mk = c[0].text_input("Manufacturer", part.get("manufacturer") or "")
        desc = c[1].text_input("Description", part.get("description") or "")
        specs = st.text_area("Technical specifications (one per line, 'Label: value')",
                             "\n".join(f"{E.SPEC_LABEL.get(k, k)}: {v}" for k, v in (part.get("key_specs") or {}).items()))
        c = st.columns(4)
        qty = c[0].text_input("Quantity", "")
        loc = c[1].text_input("Delivery location", s.get("location", "Bengaluru, India"))
        sched = c[2].text_input("Required delivery", "")
        war = c[3].text_input("Warranty requirement", "")
        c = st.columns(3)
        reply = c[0].date_input("Reply by", date.today() + timedelta(days=7))
        sender = c[1].text_input("Your name", s.get("sender") or st.session_state.user)
        comp = c[2].text_input("Company", s.get("company", "Sun Mobility"))
        gen = st.form_submit_button("Generate RFQ", type="primary")
    if gen:
        miss = [n for n, v in (("Quantity", qty), ("Delivery location", loc), ("Required delivery", sched), ("Warranty", war)) if not v.strip()]
        if miss:
            st.error("Please fill: " + ", ".join(miss))
            return
        p = {"mpn": mpn or None, "manufacturer": mk or None, "description": desc,
             "key_specs": {ln.split(":", 1)[0].strip(): ln.split(":", 1)[1].strip() for ln in specs.splitlines() if ":" in ln}}
        sp = suppliers.get(sname, {})
        subj, body = E.rfq_text(p, sp, {"qty": qty, "location": loc, "schedule": sched, "warranty": war,
                                        "reply_by": reply.strftime("%d %b %Y"), "sender": sender, "company": comp})
        to = next((e["email"] for e in sp.get("emails") or [] if e["type"] == "Sales"), None) or \
            next((e["email"] for e in sp.get("emails") or []), "")
        ss.rfq_out = {"id": S.new_id(), "date": S.now(), "user": ss.user, "to": to, "subject": subj, "body": body,
                      "supplier": sp.get("company"), "mpn": mpn, "qty": qty}
        d["rfqs"].append(ss.rfq_out)
        save(f"RFQ {mpn or desc[:30]}")
    out = ss.get("rfq_out")
    if out:
        to = st.text_input("To", out["to"])
        subj = st.text_input("Subject", out["subject"])
        body = st.text_area("Body (edit as needed)", out["body"], height=380)
        st.caption("Copy: use the copy icon on the box below.")
        st.code(f"Subject: {subj}\n\n{body}", language=None)
        c = st.columns(2)
        c[0].download_button("⬇ Download .txt", f"To: {to}\nSubject: {subj}\n\n{body}".encode("utf-8"), f"RFQ_{out['mpn'] or 'item'}.txt")
        c[1].link_button("✉️ Open draft in mail app", f"mailto:{quote(to)}?subject={quote(subj)}&body={quote(body[:1800])}")
    if d["rfqs"]:
        with st.expander(f"RFQ history ({len(d['rfqs'])})"):
            st.dataframe(pd.DataFrame([{k: r.get(k) for k in ("date", "user", "supplier", "mpn", "qty", "to", "subject")}
                                       for r in reversed(d["rfqs"])]), hide_index=True, width="stretch")


BOM_SYN = {"mpn": ("mpn", "part no", "part number", "manufacturer part", "mfr part", "p/n", "pn"),
           "manufacturer": ("manufacturer", "make", "mfr", "brand"),
           "description": ("description", "desc", "part name", "item"), "qty": ("qty", "quantity")}


def page_files():
    header("Files & BOM", "Files are processed in memory and not stored. Only the extracted text you search is saved.")
    f = st.file_uploader("Upload Excel/CSV BOM, PDF datasheet or image", type=["xlsx", "xls", "csv", "pdf", "png", "jpg", "jpeg", "webp"])
    if not f:
        return
    name = f.name.lower()
    if name.endswith((".xlsx", ".xls", ".csv")):
        df = pd.read_csv(f) if name.endswith(".csv") else pd.read_excel(f)
        df.columns = [str(c).strip() for c in df.columns]
        cols = {}
        for k, syn in BOM_SYN.items():
            cols[k] = next((c for c in df.columns if any(s == c.lower() or s in c.lower() for s in syn)), None)
        st.caption("Detected columns: " + ", ".join(f"{k} → {v or 'not found'}" for k, v in cols.items()))
        lines = []
        for _, r in df.iterrows():
            txt = " ".join(str(r[cols[k]]) for k in ("manufacturer", "mpn", "description") if cols[k] and pd.notna(r[cols[k]]))
            if txt.strip():
                lines.append({"Search text": txt.strip(), "Qty": r[cols["qty"]] if cols["qty"] else None})
        bom = pd.DataFrame(lines)
        st.dataframe(bom, hide_index=True, width="stretch")
        st.caption(f"{len(bom)} lines. Pick one to search (each search uses roughly 10–15 search API calls).")
        if len(bom):
            i = st.selectbox("Line", range(len(bom)), format_func=lambda k: bom.iloc[k]["Search text"][:90])
            if st.button("Search this line", type="primary", disabled=not can("search")):
                st.session_state.prefill = bom.iloc[i]["Search text"]
                st.session_state.part = None
                st.session_state.goto = "Search"
                st.rerun()
    else:
        with st.spinner("Extracting…"):
            txt, err = file_text(f)
        if err:
            st.warning(err)
        if txt:
            st.text_area("Extracted text", txt[:4000], height=220)
            specs = E.extract_specs(txt)
            st.write({"MPN (detected)": E.detect_mpn(txt) or E.NA, "Manufacturer": E.detect_maker(txt) or E.NA,
                      "Category": E.detect_category(txt) or E.NA, **{E.SPEC_LABEL[k]: v for k, v in specs.items()}})
            if st.button("Use for search", type="primary", disabled=not can("search")):
                st.session_state.prefill = " ".join(x for x in (E.detect_maker(txt), E.detect_mpn(txt), E.detect_category(txt)) if x) or txt[:200]
                st.session_state.part = None
                st.session_state.goto = "Search"
                st.rerun()


def page_settings():
    header("Settings & Usage")
    d, ss = D(), st.session_state
    sk, lc = search_keys(), llm_cfg()
    st.subheader("Integrations")
    st.dataframe(pd.DataFrame([
        {"Integration": "Tavily search", "Status": "✅ key set" if sk["tavily"] else "—"},
        {"Integration": "Brave search", "Status": "✅ key set" if sk["brave"] else "—"},
        {"Integration": "SerpAPI search", "Status": "✅ key set" if sk["serpapi"] else "—"},
        {"Integration": "OpenAI", "Status": f"✅ {lc['openai_model']}" if lc["openai_key"] else "—"},
        {"Integration": "Anthropic", "Status": f"✅ {lc['anthropic_model']}" if lc["anthropic_key"] else "—"},
        {"Integration": "Storage", "Status": {"github": "✅ GitHub (permanent)", "file": "✅ Local file",
                                               "memory": "⚠️ Session only – data lost on restart"}[ss.source]
         + (f" – load error: {ss.load_error}" if ss.load_error else "")},
        {"Integration": "Login", "Status": "✅ users configured" if auth_users() else "⚠️ open – restrict viewers in Streamlit sharing settings"},
    ]), hide_index=True, width="stretch")
    st.caption("Keys are read from Streamlit Secrets only and are never stored in the data file or shown here.")
    st.subheader("API usage")
    if d["usage"]:
        u = pd.DataFrame(d["usage"]).T.fillna(0).astype(int).sort_index(ascending=False)
        st.dataframe(u.head(30), width="stretch")
    else:
        st.caption("No API calls yet.")
    if not can("admin"):
        return
    st.subheader("Limits and defaults")
    s = d["settings"]
    with st.form("set"):
        c = st.columns(4)
        s1 = c[0].number_input("Searches per user per hour", 1, 200, int(s["searches_per_hour"]))
        s2 = c[1].number_input("Daily API call cap", 10, 10000, int(s["daily_api_cap"]))
        s3 = c[2].number_input("Websites checked per search", 10, 80, int(s["max_pages"]))
        s4 = c[3].number_input("Time limit per search (s)", 30, 240, int(s["time_budget"]))
        c = st.columns(3)
        s5 = c[0].text_input("Company (RFQ)", s["company"])
        s6 = c[1].text_input("Sender name (RFQ)", s["sender"])
        s7 = c[2].text_input("Default delivery location", s["location"])
        if st.form_submit_button("Save settings", type="primary"):
            s.update(searches_per_hour=s1, daily_api_cap=s2, max_pages=s3, time_budget=s4, company=s5, sender=s6, location=s7)
            if save("settings"):
                st.success("Saved.")
    st.subheader("Data")
    c = st.columns(3)
    c[0].download_button("⬇ Backup (JSON)", S.dumps(d), f"CPMO_backup_{date.today()}.json")
    if c[1].button("Clear search cache"):
        shared_cache().clear()
        st.success("Cache cleared.")
    with c[2].popover("Restore backup"):
        up = st.file_uploader("CPMO backup JSON", type=["json"])
        if up and st.button("Replace all data with this backup", type="primary"):
            import json
            ss.data = S.norm(json.loads(up.getvalue()))
            ss.data["settings"] = {**DEFAULT_SETTINGS, **ss.data.get("settings", {})}
            save("restore backup")
            st.rerun()


# ============================================================================ main
PAGES = {"Dashboard": page_dashboard, "Search": page_search, "Search History": page_history,
         "Supplier Database": page_suppliers, "Supplier Comparison": page_compare, "RFQ Generator": page_rfq,
         "Files & BOM": page_files, "Settings & Usage": page_settings}
ICONS = {"Dashboard": "📊", "Search": "🔎", "Search History": "🕘", "Supplier Database": "🏭",
         "Supplier Comparison": "⚖️", "RFQ Generator": "✉️", "Files & BOM": "📁", "Settings & Usage": "⚙️"}


def main():
    if not login():
        return
    load()
    ss = st.session_state
    with st.sidebar:
        st.markdown(f"## 🌐 {APP}\n<span style='font-size:.85rem'>{TAGLINE}</span>", unsafe_allow_html=True)
        st.write("")
        if ss.get("nav") not in PAGES:
            ss.nav = "Dashboard"
        if ss.get("goto"):  # programmatic redirect: new widget key so the radio shows the new page
            ss.nav = ss.pop("goto")
            ss.navv = ss.get("navv", 0) + 1
        opts = list(PAGES)
        ss.nav = st.radio("Navigation", opts, index=opts.index(ss.nav), key=f"navr{ss.get('navv', 0)}",
                          format_func=lambda p: f"{ICONS[p]}  {p}", label_visibility="collapsed")
        st.write("")
        st.caption(f"Signed in: {ss.user} ({ss.role})")
        st.caption({"github": "🟢 Saving to GitHub", "file": "🟢 Saving to local file",
                    "memory": "🟠 Not saving permanently"}[ss.source])
        if auth_users() and st.button("Sign out"):
            for k in ("user", "role", "data"):
                ss.pop(k, None)
            st.rerun()
    if ss.load_error:
        st.error(f"Could not load saved data from GitHub: {ss.load_error}. Working in session-only mode; nothing will be saved.")
    PAGES[ss.nav]()


main()
