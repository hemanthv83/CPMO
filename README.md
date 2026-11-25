# CPMO – AI-Powered Global Sourcing Intelligence

Streamlit application that identifies an industrial component and finds manufacturers, distributors and suppliers worldwide.
It shows each one's published business contacts, with a source link for every fact.

## What it does

| Step | What happens |
|---|---|
| 1 Identify | MPN / description / specs / datasheet PDF / product photo → part number, manufacturer, category, key specs, confidence, sources. If several parts match, you choose. You can edit everything before searching. |
| 2 Queries | 10–15 queries: exact MPN, MPN + manufacturer, distributor, authorised distributor, price/stock, sales contact, plus India, China, Japan, South Korea, Germany, USA, Europe. |
| 3 Search | Queries run in parallel on the configured search API. If one provider fails, the next is used. Results are cached for 7 days. |
| 4 Classify | Manufacturer / Authorised distributor (claimed on page) / Distributor / B2B directory listing / Trader. Car showrooms, social media, Wikipedia, marketplaces and datasheet sites are **excluded**, with the reason shown. |
| 5 Contacts | The app opens the supplier's page plus its Contact/About pages (robots.txt respected) and extracts emails, phones (only from `tel:` links or labelled numbers) and WhatsApp (only from `wa.me` links on the page). |
| 6 Verify | **A** on the company's own website · **B** on its B2B-platform profile · **C** third-party page · **D** email domain doesn't match the site · **E** not found. If there is no sales contact, the general contact is shown and marked. |
| 7 Match | Exact MPN Match / Same Manufacturer Alternative / Technically Compatible Alternative (every compared spec matches) / Similar Product / Not Verified, with a spec-by-spec table (Match / Differs / Not Available). |

Other pages:
- **Dashboard**: KPIs and recent searches.
- **Search History**: reopen results without new API calls.
- **Supplier Database**: save, edit, delete, export; no duplicates (one record per company domain); manual edits are never overwritten.
- **Supplier Comparison**: side-by-side table, exportable.
- **RFQ Generator**: copy, download or open in your mail app; **never sends**.
- **Files & BOM**: pick a BOM line to search; read MPN/specs from a PDF or image.
- **Settings & Usage**: integrations, API calls per day, limits, backup/restore.

## Rules built into the code
- Nothing is invented. Missing values show **"Not Available"**.
- No sample or demo data. Without a search key the app says search is not configured.
- Every email, phone and WhatsApp number must appear on the fetched page. AI answers are re-checked against the page text, and anything not found word-for-word is dropped. A part number the AI suggests is dropped if it isn't in your input or the search results.
- No email is ever sent or tested. Level A means "published on the company's own site", not "deliverable".
- Personal mobile numbers are not collected unless the company publishes them as a sales line. A named salesperson is shown only if the page names them as the sales contact.
- Uploaded files are processed in memory and **not stored**.

## Secrets (Streamlit Cloud → app → ⋮ → Settings → Secrets)

Add at least one search key. Every other section is optional.

```toml
[search]                 # one or more; tried in this order
tavily_key  = "tvly-..."         # https://tavily.com  (free tier ≈1,000 searches/month)
brave_key   = "BSA..."           # https://brave.com/search/api
serpapi_key = "..."              # https://serpapi.com

[openai]                 # optional – better identification, addresses, image reading
api_key = "sk-..."
model   = "gpt-4o-mini"

[anthropic]              # optional alternative to OpenAI (used only if no OpenAI key)
api_key = "sk-ant-..."
model   = "claude-haiku-4-5-20251001"

[github]                 # permanent storage (same set-up as the NPD tracker)
token  = "github_pat_..."        # fine-grained token, this repo only, Contents: Read and write
repo   = "hemanthv83/CPMO"
branch = "data"
path   = "cpmo_data.json"

[auth.users.rajanish]    # optional login; repeat the block for each user
password = "choose-a-strong-password"
role     = "Admin"               # Admin | Buyer | Viewer

[auth.users.hemanth]
password = "another-password"
role     = "Buyer"
```

Roles:
- **Viewer**: read-only.
- **Buyer**: search, save, edit, RFQ.
- **Admin**: all of the above, plus delete, limits and restore.

Without `[auth]` the app is open to anyone who can reach it. In that case, restrict viewers under Streamlit **Settings → Sharing**.

## Cost control
- Each search uses about 11–15 search-API calls, plus 1 more when the part is identified.
- With an AI key, add about 1 AI call per supplier page.
- **Settings & Usage** sets the searches per user per hour (default 20) and a daily API call cap (default 400). It also shows calls per provider per day.

## Deploy (GitHub + Streamlit Cloud)
1. On GitHub, create a **private** repository, e.g. `CPMO`.
2. Upload `app.py`, `engine.py`, `store.py`, `requirements.txt`, `README.md` and `.gitignore`. Create `.streamlit/config.toml` with **Add file → Create new file** and paste in its contents.
3. Go to share.streamlit.io → **Create app** → repository `…/CPMO`, branch `main`, main file `app.py` → **Deploy**.
4. Add the Secrets above. The sidebar should show **🟢 Saving to GitHub**.
5. Run the acceptance tests below and keep a note of the results.

## Acceptance tests (run after deployment, with live keys)

| Test | Expected |
|---|---|
| `BYD EVR50CI 50A 12V DC contactor` | MPN EVR50CI, BYD identified; exact matches listed first; BYD car dealerships in the *Excluded* tab |
| `Industrial RCCB 4 pole 63A` | Generic requirement (no exact matches); RCCBs with 4P + 63A listed as Technically Compatible; others as Similar |
| `Industrial UPS 1 kVA single phase` | Category UPS, specs 1 kVA + single phase |
| `Industrial Ethernet switch 8 port` | Category Ethernet Switch, spec 8 ports |

For each test, open 5 contact-source links and confirm each contact appears on that page.

## Known limits
- Sites that block bots or need JavaScript can't be opened. Those suppliers are classified from the search snippet only, and the card says so.
- "Authorised distributor" means the supplier's own page claims it. Confirm on the manufacturer's distributor list before ordering.
- Price, MOQ, lead time and stock are shown only when printed on the page. Use the RFQ to get confirmed figures.
- Without an AI key, addresses are not extracted and photos can't be read.

## Run on your PC
```bash
pip install -r requirements.txt
set SEARCH_TAVILY_KEY=tvly-...           # Windows (macOS/Linux: export ...)
set CPMO_DATA_FILE=C:\CPMO\cpmo_data.json   # optional local storage
streamlit run app.py
```
