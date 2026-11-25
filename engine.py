"""CPMO sourcing engine: search, fetch, extract, classify.

No Streamlit imports here, so the logic can be tested on its own.
Rules enforced in code:
  * nothing is shown without the source URL it came from;
  * every email / phone / WhatsApp must appear verbatim on the fetched page (AI output is re-checked);
  * unknown values stay None and are displayed as "Not Available";
  * no email is ever sent or probed.
"""
from __future__ import annotations

import json
import re
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests

UA = "Mozilla/5.0 (compatible; CPMO-SourcingBot/1.0; +supplier-discovery)"
NA = "Not Available"

# --------------------------------------------------------------------------------------------
# Reference lists
# --------------------------------------------------------------------------------------------
DISTRIBUTORS = {
    "digikey": "Digi-Key", "mouser": "Mouser Electronics", "arrow": "Arrow Electronics", "avnet": "Avnet",
    "farnell": "Farnell", "element14": "element14", "newark": "Newark", "rs-online": "RS Components",
    "rsdelivers": "RS Components", "tme.eu": "TME", "lcsc": "LCSC", "futureelectronics": "Future Electronics",
    "tti.com": "TTI", "ttiinc": "TTI", "verical": "Verical", "rutronik": "Rutronik", "heilind": "Heilind",
    "sager": "Sager Electronics", "alliedelec": "Allied Electronics", "conrad": "Conrad", "reichelt": "Reichelt",
    "distrelec": "Distrelec", "chip1stop": "Chip One Stop", "marutsu": "Marutsu", "devicemart": "DeviceMart",
    "eleparts": "Eleparts", "robu.in": "Robu", "evelta": "Evelta", "kunkune": "Kunkune", "onlinecomponents": "Onlinecomponents",
    "newarkindia": "Newark", "in.element14": "element14 India", "automationdirect": "AutomationDirect",
    "galco": "Galco", "radwell": "Radwell", "octopart": "Octopart (aggregator)", "findchips": "FindChips (aggregator)",
}
B2B = {
    "indiamart": "IndiaMART", "tradeindia": "TradeIndia", "alibaba": "Alibaba", "made-in-china": "Made-in-China",
    "globalsources": "Global Sources", "ec21": "EC21", "exporthub": "ExportHub", "kompass": "Kompass",
    "thomasnet": "Thomasnet", "dhgate": "DHgate", "1688.com": "1688", "tradekey": "TradeKey",
    "exportersindia": "ExportersIndia", "justdial": "Justdial", "europages": "Europages", "go4worldbusiness": "go4WorldBusiness",
    "directindustry": "DirectIndustry", "hktdc": "HKTDC",
}
EXCLUDE_DOMAINS = (
    "wikipedia.", "youtube.", "facebook.", "linkedin.", "instagram.", "twitter.", "x.com", "pinterest.", "reddit.",
    "quora.", "medium.com", "tiktok.", "scribd.", "slideshare.", "alldatasheet", "datasheetspdf", "datasheet4u",
    "manualslib", "ebay.", "amazon.", "flipkart.", "google.", "bing.com", "baidu.", "zhihu.", "news", "blog.",
    "patents.", "github.", "stackexchange", "stackoverflow",
)
DEALER_WORDS = ("car showroom", "test drive", "dealership", "authorised dealer of cars", "new cars", "used cars",
                "bike showroom", "ex-showroom", "two wheeler dealer", "car dealer", "motorcycle dealer", "book a test")
FREE_MAIL = ("gmail.com", "yahoo.", "hotmail.", "outlook.com", "live.com", "qq.com", "163.com", "126.com", "aol.com",
             "rediffmail.com", "icloud.com", "sina.com", "foxmail.com", "yeah.net")
SALES_LOCAL = ("sales", "sale", "enquiry", "enquiries", "inquiry", "inquiries", "export", "order", "orders", "quote",
               "rfq", "purchase", "business", "trade", "marketing", "bd", "biz")
GENERAL_LOCAL = ("info", "contact", "contactus", "support", "service", "hello", "office", "admin", "mail", "customercare",
                 "care", "help")
BAD_EMAIL = ("example.", "domain.", "email.com", "yourcompany", "sentry", "wixpress", "@2x", ".png", ".jpg", ".gif",
             ".svg", ".webp", "noreply", "no-reply", "donotreply", "privacy@", "abuse@", "webmaster@", "jobs@", "careers@",
             "hr@", "recruit")
CONTACT_PATHS = ("/contact", "/contact-us", "/contactus", "/contact.html", "/contact-us.html", "/en/contact",
                 "/about", "/about-us", "/sales")

CATEGORIES = {
    "DC Contactor": ("contactor",),
    "Relay": ("relay",),
    "RCCB": ("rccb", "residual current", "earth leakage", "elcb"),
    "MCB": ("mcb", "miniature circuit breaker"),
    "UPS": ("ups", "uninterruptible"),
    "Ethernet Switch": ("ethernet switch", "network switch", "industrial switch", "managed switch", "unmanaged switch"),
    "Fuse": ("fuse",),
    "Connector": ("connector",),
    "Power Supply": ("power supply", "smps", "psu"),
    "DC Fan": ("dc fan", "axial fan", "cooling fan"),
    "Industrial PC": ("industrial pc", "embedded computer", "box pc", "fanless pc"),
    "Battery Cell": ("cell", "lifepo4", "lfp"),
}
KNOWN_MAKERS = ("BYD", "Schneider", "ABB", "Siemens", "Legrand", "Havells", "L&T", "Lauritz Knudsen", "Eaton", "Hager",
                "Omron", "TE Connectivity", "Panasonic", "Gigavac", "Tyco", "Kilovac", "Hongfa", "Delta", "APC",
                "Vertiv", "Numeric", "Microtek", "Luminous", "Moxa", "Hirschmann", "Phoenix Contact", "Weidmuller",
                "Advantech", "Cisco", "D-Link", "TP-Link", "Planet", "Siemens Scalance", "Mitsubishi", "Fuji Electric",
                "Chint", "Littelfuse", "Bussmann", "Molex", "Amphenol", "Lenovo", "Invendis", "Kyocera", "Song Chuan",
                "Zettler", "Finder", "Allen-Bradley", "Rockwell", "Socomec", "Riello", "Emerson", "Krykard", "Hikvision")

COUNTRY_TLD = {"in": "India", "cn": "China", "jp": "Japan", "kr": "South Korea", "de": "Germany", "uk": "United Kingdom",
               "fr": "France", "it": "Italy", "es": "Spain", "nl": "Netherlands", "tw": "Taiwan", "hk": "Hong Kong",
               "sg": "Singapore", "ae": "UAE", "us": "USA", "ch": "Switzerland", "at": "Austria", "pl": "Poland",
               "se": "Sweden", "cz": "Czech Republic", "tr": "Turkey", "my": "Malaysia", "th": "Thailand", "vn": "Vietnam"}
PHONE_CC = {"+91": "India", "+86": "China", "+81": "Japan", "+82": "South Korea", "+49": "Germany", "+1": "USA/Canada",
            "+44": "United Kingdom", "+33": "France", "+39": "Italy", "+886": "Taiwan", "+852": "Hong Kong",
            "+65": "Singapore", "+971": "UAE", "+31": "Netherlands", "+41": "Switzerland", "+34": "Spain"}
REGIONS = ["India", "China", "Japan", "South Korea", "Germany", "USA", "Europe", "Worldwide"]

MATCH_ORDER = ["Exact MPN Match", "Same Manufacturer Alternative", "Technically Compatible Alternative",
               "Similar Product", "Not Verified"]
LEVELS = {"A": "Officially published contact", "B": "Publicly listed business contact",
          "C": "Third-party listed contact", "D": "Unverified", "E": "Not found"}


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------
def domain_of(url: str) -> str:
    d = urlparse(url if "//" in url else "http://" + url).netloc.lower().split("@")[-1].split(":")[0]
    return d[4:] if d.startswith("www.") else d


def root_domain(d: str) -> str:
    parts = d.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def norm_mpn(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def country_from_domain(d: str):
    tld = d.rsplit(".", 1)[-1]
    if tld in COUNTRY_TLD:
        return COUNTRY_TLD[tld]
    if d.endswith(".co.in"):
        return "India"
    return None


def which(d: str, table: dict):
    for k, v in table.items():
        if k in d:
            return v
    return None


# --------------------------------------------------------------------------------------------
# Usage / rate tracking (the app reads and stores this)
# --------------------------------------------------------------------------------------------
@dataclass
class Usage:
    calls: dict = field(default_factory=dict)  # provider -> count

    def add(self, provider, n=1):
        self.calls[provider] = self.calls.get(provider, 0) + n


# --------------------------------------------------------------------------------------------
# Search providers
# --------------------------------------------------------------------------------------------
class SearchError(RuntimeError):
    pass


def _retry(fn, tries=3, base=1.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
        except SearchError as e:
            last = e
            if "401" in str(e) or "403" in str(e) or "quota" in str(e).lower():
                break  # auth / quota errors: retrying will not help
        time.sleep(base * (2 ** i))
    raise SearchError(str(last))


def tavily_search(q, key, n=8):
    def go():
        r = requests.post("https://api.tavily.com/search", timeout=25,
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          json={"query": q, "max_results": n, "search_depth": "basic", "include_raw_content": False})
        if r.status_code != 200:
            raise SearchError(f"Tavily {r.status_code}: {r.text[:150]}")
        return [{"url": x.get("url"), "title": x.get("title") or "", "snippet": x.get("content") or ""}
                for x in r.json().get("results", []) if x.get("url")]
    return _retry(go)


def brave_search(q, key, n=8):
    def go():
        r = requests.get("https://api.search.brave.com/res/v1/web/search", timeout=25, params={"q": q, "count": n},
                         headers={"X-Subscription-Token": key, "Accept": "application/json"})
        if r.status_code != 200:
            raise SearchError(f"Brave {r.status_code}: {r.text[:150]}")
        return [{"url": x.get("url"), "title": x.get("title") or "", "snippet": re.sub("<[^>]+>", "", x.get("description") or "")}
                for x in r.json().get("web", {}).get("results", []) if x.get("url")]
    return _retry(go)


def serpapi_search(q, key, n=8):
    def go():
        r = requests.get("https://serpapi.com/search.json", timeout=30,
                         params={"q": q, "engine": "google", "num": n, "api_key": key})
        if r.status_code != 200:
            raise SearchError(f"SerpAPI {r.status_code}: {r.text[:150]}")
        j = r.json()
        if j.get("error"):
            raise SearchError(f"SerpAPI: {j['error'][:150]}")
        return [{"url": x.get("link"), "title": x.get("title") or "", "snippet": x.get("snippet") or ""}
                for x in j.get("organic_results", []) if x.get("link")]
    return _retry(go)


PROVIDERS = {"tavily": tavily_search, "brave": brave_search, "serpapi": serpapi_search}


class Searcher:
    """Tries the providers in order; falls back to the next one if a provider fails."""

    def __init__(self, keys: dict, usage: Usage | None = None, cache: dict | None = None):
        self.order = [p for p in ("tavily", "brave", "serpapi") if keys.get(p)]
        self.keys, self.usage = keys, usage or Usage()
        self.cache = cache if cache is not None else {}
        self.errors = []

    @property
    def ready(self):
        return bool(self.order)

    def search(self, q, n=8, refresh=False):
        ck = f"s|{q}|{n}"
        if not refresh and ck in self.cache:
            return self.cache[ck]
        for p in self.order:
            try:
                res = PROVIDERS[p](q, self.keys[p], n)
                self.usage.add(p)
                self.cache[ck] = res
                return res
            except SearchError as e:
                self.errors.append(str(e))
        return []


# --------------------------------------------------------------------------------------------
# LLM (optional)
# --------------------------------------------------------------------------------------------
class LLM:
    def __init__(self, cfg: dict, usage: Usage | None = None):
        self.cfg, self.usage = cfg or {}, usage or Usage()
        self.kind = "openai" if self.cfg.get("openai_key") else ("anthropic" if self.cfg.get("anthropic_key") else None)
        self.errors = []

    @property
    def ready(self):
        return self.kind is not None

    def read_image(self, data: bytes, mime: str) -> str | None:
        """Describe a product photo / nameplate: return the text visible on it plus the product type."""
        import base64
        if not self.kind:
            return None
        b64 = base64.b64encode(data).decode()
        ask = ("Read this product photo or nameplate. Return JSON {\"visible_text\": str (all text printed on the "
               "product or label, verbatim), \"product_type\": str, \"manufacturer\": str|null, \"mpn\": str|null}. "
               "Only report text you can actually read; null if unreadable.")
        try:
            if self.kind == "openai":
                r = requests.post("https://api.openai.com/v1/chat/completions", timeout=60,
                                  headers={"Authorization": f"Bearer {self.cfg['openai_key']}"},
                                  json={"model": self.cfg.get("openai_model", "gpt-4o-mini"), "temperature": 0,
                                        "response_format": {"type": "json_object"}, "max_tokens": 600,
                                        "messages": [{"role": "user", "content": [
                                            {"type": "text", "text": ask},
                                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]})
                if r.status_code != 200:
                    raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:150]}")
                txt = r.json()["choices"][0]["message"]["content"]
            else:
                r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
                                  headers={"x-api-key": self.cfg["anthropic_key"], "anthropic-version": "2023-06-01"},
                                  json={"model": self.cfg.get("anthropic_model", "claude-haiku-4-5-20251001"), "max_tokens": 600,
                                        "messages": [{"role": "user", "content": [
                                            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                                            {"type": "text", "text": ask + " Reply with JSON only."}]}]})
                if r.status_code != 200:
                    raise RuntimeError(f"Anthropic {r.status_code}: {r.text[:150]}")
                txt = "".join(b.get("text", "") for b in r.json().get("content", []))
            self.usage.add(self.kind)
            m = re.search(r"\{.*\}", txt, re.S)
            j = json.loads(m.group(0)) if m else {}
            return " ".join(str(j.get(k) or "") for k in ("manufacturer", "mpn", "product_type", "visible_text")).strip() or None
        except Exception as e:  # noqa: BLE001
            self.errors.append(str(e)[:200])
            return None

    def json(self, system, user, max_tokens=1200):
        if not self.kind:
            return None
        try:
            if self.kind == "openai":
                r = requests.post("https://api.openai.com/v1/chat/completions", timeout=60,
                                  headers={"Authorization": f"Bearer {self.cfg['openai_key']}"},
                                  json={"model": self.cfg.get("openai_model", "gpt-4o-mini"), "temperature": 0,
                                        "response_format": {"type": "json_object"}, "max_tokens": max_tokens,
                                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
                if r.status_code != 200:
                    raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:150]}")
                txt = r.json()["choices"][0]["message"]["content"]
            else:
                r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
                                  headers={"x-api-key": self.cfg["anthropic_key"], "anthropic-version": "2023-06-01"},
                                  json={"model": self.cfg.get("anthropic_model", "claude-haiku-4-5-20251001"),
                                        "max_tokens": max_tokens, "temperature": 0, "system": system + " Reply with JSON only.",
                                        "messages": [{"role": "user", "content": user}]})
                if r.status_code != 200:
                    raise RuntimeError(f"Anthropic {r.status_code}: {r.text[:150]}")
                txt = "".join(b.get("text", "") for b in r.json().get("content", []))
            self.usage.add(self.kind)
            m = re.search(r"\{.*\}", txt, re.S)
            return json.loads(m.group(0)) if m else None
        except Exception as e:  # noqa: BLE001
            self.errors.append(str(e)[:200])
            return None


# --------------------------------------------------------------------------------------------
# Spec extraction (rule-based; used for identification and for comparison)
# --------------------------------------------------------------------------------------------
SPEC_RX = {
    "current_A": r"(\d{1,4}(?:\.\d+)?)\s?(?:A|Amp|Amps|ampere)\b",
    "voltage_V": r"(\d{1,4}(?:\.\d+)?)\s?(?:V|VDC|VAC|Vdc|Vac|volt)\b",
    "coil_voltage_V": r"coil(?:\s*voltage)?\s*[:\-]?\s*(\d{1,3}(?:\.\d+)?)\s?V",
    "power_kVA": r"(\d{1,3}(?:\.\d+)?)\s?kVA\b",
    "poles": r"\b(\d)\s?(?:P|pole|poles|-pole)\b",
    "ports": r"\b(\d{1,2})\s?[- ]?(?:port|ports)\b",
    "phase": r"\b(single|three|1|3)[\s-]?phase\b",
    "sensitivity_mA": r"(\d{2,3})\s?mA\b",
    "temp_range": r"(-?\d{1,2}\s?(?:°C|℃|C)?\s?(?:to|~|…|–|-)\s?\+?\d{2,3}\s?(?:°C|℃))",
    "mounting": r"\b(DIN[\s-]?rail|panel[\s-]?mount|PCB[\s-]?mount|chassis[\s-]?mount|rack[\s-]?mount|stud[\s-]?mount)\b",
    "dimensions_mm": r"(\d{2,4}(?:\.\d+)?\s?[x×*]\s?\d{2,4}(?:\.\d+)?(?:\s?[x×*]\s?\d{2,4}(?:\.\d+)?)?\s?mm)",
}
SPEC_LABEL = {"current_A": "Current rating (A)", "voltage_V": "Voltage (V)", "coil_voltage_V": "Coil voltage (V)",
              "power_kVA": "Power (kVA)", "poles": "Poles", "ports": "Ports", "phase": "Phase",
              "sensitivity_mA": "Sensitivity (mA)", "temp_range": "Operating temperature", "mounting": "Mounting",
              "dimensions_mm": "Dimensions"}


def extract_specs(text: str) -> dict:
    t = text or ""
    out = {}
    for k, rx in SPEC_RX.items():
        m = re.search(rx, t, re.I)
        if m:
            v = m.group(1).strip()
            if k == "phase":
                v = {"1": "single", "3": "three"}.get(v.lower(), v.lower())
            if k == "mounting":
                v = re.sub(r"[\s-]", " ", v.lower())
            out[k] = v
    return out


def _num(v):
    try:
        return float(re.sub(r"[^\d.\-]", "", str(v)))
    except ValueError:
        return None


def compare_specs(target: dict, found: dict) -> dict:
    """Per spec: Match / Differs / Not Available."""
    res = {}
    for k in SPEC_LABEL:
        if k not in target:
            continue
        a, b = target.get(k), found.get(k)
        if b is None:
            res[k] = (NA, a, None)
        elif _num(a) is not None and _num(b) is not None and k not in ("temp_range", "dimensions_mm"):
            res[k] = ("Match" if abs(_num(a) - _num(b)) < 1e-6 else "Differs", a, b)
        else:
            res[k] = ("Match" if str(a).lower().replace(" ", "") == str(b).lower().replace(" ", "") else "Differs", a, b)
    return res


def detect_category(text: str):
    t = (text or "").lower()
    for cat, kws in CATEGORIES.items():
        for kw in kws:
            if re.search(r"\b" + re.escape(kw) + r"\b", t):
                return cat
    return None


def detect_maker(text: str):
    t = (text or "").lower()
    for m in KNOWN_MAKERS:
        if re.search(r"\b" + re.escape(m.lower()) + r"\b", t):
            return m
    return None


def detect_mpn(text: str):
    """A token with letters and digits, 4+ chars, that is not just a spec like 50A / 12V / 63A."""
    for tok in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9\-_/\.]{3,}[A-Za-z0-9]\b", text or ""):
        if not (re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok)):
            continue
        if re.fullmatch(r"\d+(\.\d+)?\s?(A|V|VDC|VAC|kVA|KVA|mA|mm|W|kW|Hz|P|Ah)", tok, re.I):
            continue
        return tok.upper()
    return None


# --------------------------------------------------------------------------------------------
# Step 1: identify the part
# --------------------------------------------------------------------------------------------
def identify_part(query: str, extra_text: str = "", searcher: Searcher | None = None, llm: LLM | None = None,
                  refresh=False) -> dict:
    text = f"{query}\n{extra_text}".strip()
    part = {"query": query, "mpn": detect_mpn(query) or detect_mpn(extra_text), "manufacturer": detect_maker(text),
            "category": detect_category(text), "key_specs": extract_specs(query) or extract_specs(extra_text),
            "description": query, "confidence": "Low", "source_urls": [], "candidates": [], "method": "rules"}
    hits = []
    if searcher and searcher.ready:
        q = f"{part['mpn']} {part['manufacturer'] or ''} datasheet" if part["mpn"] else f"{query} datasheet specifications"
        hits = searcher.search(q.strip(), n=8, refresh=refresh)
    if llm and llm.ready:
        ctx = "\n".join(f"- {h['title']} | {h['url']} | {h['snippet'][:300]}" for h in hits[:8])
        j = llm.json(
            "You identify industrial/electrical components. Use ONLY the user input and the search snippets given. "
            "Never invent part numbers or specs. If unsure, leave fields null and lower confidence.",
            f"User input: {text[:3000]}\n\nSearch snippets:\n{ctx}\n\nReturn JSON: {{\"mpn\": str|null, "
            "\"manufacturer\": str|null, \"category\": str|null, \"description\": str, \"key_specs\": {"
            "\"current_A\":..., \"voltage_V\":..., \"coil_voltage_V\":..., \"power_kVA\":..., \"poles\":..., "
            "\"ports\":..., \"phase\":..., \"mounting\":..., \"temp_range\":..., \"dimensions_mm\":...} (only keys you "
            "found), \"is_generic\": bool (true if no specific part number), \"confidence\": \"High\"|\"Medium\"|\"Low\", "
            "\"candidates\": [{\"mpn\": str, \"manufacturer\": str, \"why\": str}] (only if several parts could match), "
            "\"source_urls\": [urls from snippets that support this]}")
        if j:
            for k in ("mpn", "manufacturer", "category", "description", "confidence"):
                if j.get(k):
                    part[k] = j[k]
            ks = {k: v for k, v in (j.get("key_specs") or {}).items() if v not in (None, "", "null") and k in SPEC_LABEL}
            part["key_specs"] = {**part["key_specs"], **ks}
            part["candidates"] = j.get("candidates") or []
            hit_urls = {h["url"] for h in hits}
            part["source_urls"] = [u for u in (j.get("source_urls") or []) if u in hit_urls]  # only real URLs
            part["method"] = "AI + search" if hits else "AI"
            # an MPN the AI proposes must appear in the input or in a snippet
            if part["mpn"]:
                corpus = norm_mpn(text + " ".join(h["title"] + h["snippet"] for h in hits))
                if norm_mpn(part["mpn"]) not in corpus:
                    part["mpn"], part["confidence"] = None, "Low"
    else:
        if part["mpn"] and hits:
            sup = [h["url"] for h in hits if norm_mpn(part["mpn"]) in norm_mpn(h["title"] + h["snippet"] + h["url"])]
            part["source_urls"] = sup[:5]
            part["confidence"] = "High" if len(sup) >= 2 else ("Medium" if sup else "Low")
            if not part["manufacturer"]:
                part["manufacturer"] = detect_maker(" ".join(h["title"] + " " + h["snippet"] for h in hits))
        elif not part["mpn"]:
            part["confidence"] = "Medium" if part["category"] and part["key_specs"] else "Low"
    part["is_generic"] = not part["mpn"]
    return part


# --------------------------------------------------------------------------------------------
# Step 2: queries
# --------------------------------------------------------------------------------------------
def build_queries(part: dict, regions=None) -> list[str]:
    regions = regions or REGIONS
    mpn, mk = part.get("mpn"), part.get("manufacturer") or ""
    if mpn:
        base = f"{mpn}"
        q = [f"\"{mpn}\"", f"{mpn} {mk}".strip(), f"{mpn} distributor", f"{mpn} authorized distributor",
             f"{mpn} price stock", f"{mpn} supplier sales contact"]
        if mk:
            q.append(f"{mk} sales contact email")
    else:
        desc = part.get("description") or part.get("query")
        base = desc
        q = [desc, f"{desc} manufacturer", f"{desc} distributor", f"{desc} supplier contact"]
    for r in regions:
        if r == "Worldwide":
            continue
        q.append(f"{base} supplier {r}")
    seen, out = set(), []
    for x in q:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


# --------------------------------------------------------------------------------------------
# Step 3: fetch pages (robots.txt respected)
# --------------------------------------------------------------------------------------------
_ROBOTS = {}


def allowed(url):
    d = urlparse(url)
    base = f"{d.scheme}://{d.netloc}"
    if base not in _ROBOTS:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", timeout=6, headers={"User-Agent": UA})
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:  # noqa: BLE001
            rp.parse([])
        _ROBOTS[base] = rp
    return _ROBOTS[base].can_fetch(UA, url)


def fetch(url, cache=None, refresh=False, timeout=10):
    """Return dict(url, html, text, title) or None."""
    if cache is not None and not refresh and ("f|" + url) in cache:
        return cache["f|" + url]
    out = None
    try:
        if allowed(url):
            r = requests.get(url, timeout=timeout, headers={"User-Agent": UA, "Accept-Language": "en"}, allow_redirects=True)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and "html" in ct:
                html = r.text[:600_000]
                out = {"url": r.url, "html": html, "text": html_text(html), "title": html_title(html)}
    except Exception:  # noqa: BLE001
        out = None
    if cache is not None:
        cache["f|" + url] = out
    return out


def html_text(html):
    try:
        from bs4 import BeautifulSoup
        s = BeautifulSoup(html, "html.parser")
        for t in s(["script", "style", "noscript", "svg"]):
            t.decompose()
        return re.sub(r"\s+", " ", s.get_text(" ")).strip()
    except Exception:  # noqa: BLE001
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def html_title(html):
    m = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else ""


def contact_links(page):
    """Contact/about links on the same domain."""
    links = set()
    d = domain_of(page["url"])
    for href, label in re.findall(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', page["html"], re.I | re.S):
        lab = re.sub("<[^>]+>", "", label).lower()
        if any(w in (href.lower() + " " + lab) for w in ("contact", "sales", "enquiry", "inquiry", "about", "联系")):
            u = urljoin(page["url"], href)
            if domain_of(u) == d and u.startswith("http"):
                links.add(u.split("?")[0])
    return list(links)[:4]


# --------------------------------------------------------------------------------------------
# Step 5: contact extraction
# --------------------------------------------------------------------------------------------
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RX = re.compile(r"(?:(?:\+|00)\d{1,3}[\s\-.]?)?(?:\(?\d{2,5}\)?[\s\-.]?)?\d{3,5}[\s\-.]?\d{3,5}")
WA_RX = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\+?\d{8,15})", re.I)


def _clean_email(e):
    e = e.strip(".,;:()[]<>").lower()
    if any(b in e for b in BAD_EMAIL) or len(e) > 60:
        return None
    return e


def classify_email(e, site_dom):
    local, dom = e.split("@", 1)
    kind = "Sales" if any(local.startswith(w) or local == w for w in SALES_LOCAL) else (
        "General" if any(local.startswith(w) for w in GENERAL_LOCAL) else "Named/other")
    free = any(dom.endswith(f) or f in dom for f in FREE_MAIL)
    same = root_domain(dom) == root_domain(site_dom)
    return kind, free, same


def extract_phones(text, html):
    """Phones only from tel: links or next to a phone label — avoids picking up part numbers and prices."""
    found = []
    for t in re.findall(r'href=["\']tel:([^"\']+)', html, re.I):
        found.append(t)
    for m in re.finditer(r"(?:tel|phone|mobile|ph|call|fax|电话|手机)\s*[.:#]?\s*((?:\+|00)?[\d\s\-().]{8,22}\d)", text, re.I):
        ctx = text[max(0, m.start() - 4):m.start() + 4].lower()
        if "fax" in text[m.start():m.start() + 5].lower():
            continue
        found.append(m.group(1))
    out = []
    for p in found:
        p = re.sub(r"[^\d+]", "", p.replace("00", "+", 1) if p.strip().startswith("00") else p)
        digits = re.sub(r"\D", "", p)
        if 8 <= len(digits) <= 15 and p not in out:
            out.append(p)
    return out[:4]


def extract_contacts(page, company_domain):
    text, html = page["text"], page["html"]
    emails = []
    for m in EMAIL_RX.findall(html.replace("&#64;", "@").replace("[at]", "@")):
        e = _clean_email(m)
        if e and e not in [x["email"] for x in emails]:
            kind, free, same = classify_email(e, company_domain)
            emails.append({"email": e, "type": kind, "free_mail": free, "same_domain": same})
    emails.sort(key=lambda x: ({"Sales": 0, "General": 1}.get(x["type"], 2), not x["same_domain"]))
    wa = []
    for n in WA_RX.findall(html):
        n = "+" + n.lstrip("+")
        if n not in wa:
            wa.append(n)
    return {"emails": emails[:6], "phones": extract_phones(text, html), "whatsapp": wa[:2]}


def ai_contacts(llm: LLM, page, company_domain):
    """AI reads the page; every returned value must appear verbatim in the page or it is dropped."""
    if not (llm and llm.ready):
        return {}
    j = llm.json(
        "Extract business contact details ONLY if they literally appear in the page text. Never guess. "
        "Do not include personal mobile numbers unless the page presents them as the company's sales line.",
        f"Company domain: {company_domain}\nPage URL: {page['url']}\nPage text:\n{page['text'][:7000]}\n\n"
        "Return JSON: {\"company_name\": str|null, \"country\": str|null, \"address\": str|null, "
        "\"sales_person\": str|null (only if named as sales contact on this page), \"price\": str|null, "
        "\"moq\": str|null, \"lead_time\": str|null, \"stock\": str|null, \"warranty\": str|null}")
    if not j:
        return {}
    flat = re.sub(r"\s+", " ", page["text"]).lower()
    keep = {}
    for k, v in j.items():
        if not v or not isinstance(v, str):
            continue
        probe = re.sub(r"\s+", " ", v).lower()[:40]
        if k in ("country",) or probe in flat:  # country may be inferred from the address
            keep[k] = v.strip()
    return keep


def rx_commercials(text):
    out = {}
    m = re.search(r"\bMOQ\b\s*[:\-]?\s*([\d,]+\s*\w*)", text, re.I) or re.search(r"minimum order(?: quantity)?\s*[:\-]?\s*([\d,]+\s*\w*)", text, re.I)
    if m:
        out["moq"] = m.group(1).strip()
    m = re.search(r"lead[\s-]?time\s*[:\-]?\s*([\d\-–~ to]+\s*(?:weeks?|days?|wks?))", text, re.I)
    if m:
        out["lead_time"] = m.group(1).strip()
    m = re.search(r"(?:in stock|stock)\s*[:\-]?\s*([\d,]{1,9})\b", text, re.I)
    if m:
        out["stock"] = m.group(1)
    m = re.search(r"((?:₹|Rs\.?|INR|US\$|USD|\$|€|EUR|¥|RMB|CNY)\s?[\d,]+(?:\.\d{1,2})?)", text)
    if m:
        out["price"] = m.group(1)
    m = re.search(r"warranty\s*[:\-]?\s*(\d{1,2}\s*(?:years?|months?|yrs?))", text, re.I)
    if m:
        out["warranty"] = m.group(1)
    return out


# --------------------------------------------------------------------------------------------
# Step 4/6/7: classification
# --------------------------------------------------------------------------------------------
def company_type(dom, page_text, part):
    if any(x in dom for x in EXCLUDE_DOMAINS):
        return "Excluded", "Not a supplier site (social media, encyclopaedia, marketplace, datasheet host or news)"
    low = (page_text or "").lower()
    if any(w in low for w in DEALER_WORDS) and not detect_category(low[:5000]):
        return "Excluded", "Automobile dealership / showroom"
    b2b = which(dom, B2B)
    if b2b:
        return "B2B directory listing", b2b
    dist = which(dom, DISTRIBUTORS)
    mk = (part.get("manufacturer") or "").lower()
    if mk and mk.split()[0] in dom:
        return "Manufacturer", "Manufacturer's own domain"
    if dist:
        if mk and re.search(r"authori[sz]ed\s+distributor", low) and mk in low:
            return "Authorised distributor (claimed on page)", dist
        return "Distributor", dist
    if mk and re.search(r"authori[sz]ed\s+(?:distributor|dealer|channel partner)", low) and mk in low:
        return "Authorised distributor (claimed on page)", "Page states authorised distributor"
    if re.search(r"\b(?:we (?:are )?(?:a )?(?:leading )?manufactur|our factory|manufacturer of|factory direct|OEM)\b", low):
        return "Manufacturer", "Page describes itself as a manufacturer"
    if re.search(r"\b(?:distributor|stockist|supplier|trader|dealer|exporter|wholesal)\w*", low):
        return "Distributor / trader", "Page describes itself as supplier/trader"
    return "Unclassified", ""


def match_class(part, page_text, title=""):
    blob = f"{title} {page_text}"
    nb = norm_mpn(blob)
    found_specs = extract_specs(blob)
    cmp = compare_specs(part.get("key_specs") or {}, found_specs)
    mpn = part.get("mpn")
    if mpn and len(norm_mpn(mpn)) >= 4 and norm_mpn(mpn) in nb:
        return "Exact MPN Match", cmp, found_specs
    same_mk = part.get("manufacturer") and part["manufacturer"].lower() in blob.lower()
    cat = part.get("category")
    cat_hit = cat and detect_category(blob) == cat
    matched = [k for k, v in cmp.items() if v[0] == "Match"]
    differs = [k for k, v in cmp.items() if v[0] == "Differs"]
    if mpn and same_mk and cat_hit:
        return "Same Manufacturer Alternative", cmp, found_specs
    if cat_hit and len(matched) >= max(2, len(cmp) - 1) and not differs and len(cmp) >= 2:
        return "Technically Compatible Alternative", cmp, found_specs
    if cat_hit:
        return "Similar Product", cmp, found_specs
    return "Not Verified", cmp, found_specs


def contact_level(src_dom, company_dom, ctype, contacts):
    if not (contacts.get("emails") or contacts.get("phones") or contacts.get("whatsapp")):
        return "E"
    e0 = contacts["emails"][0] if contacts.get("emails") else None
    if which(src_dom, B2B):
        return "B"
    if root_domain(src_dom) == root_domain(company_dom):
        if e0 and not e0["same_domain"] and not e0["free_mail"]:
            return "D"  # email domain inconsistent with the site
        return "A"  # published on the company's own website
    return "C"


# --------------------------------------------------------------------------------------------
# Full pipeline
# --------------------------------------------------------------------------------------------
def run_search(part, searcher: Searcher, llm: LLM | None = None, regions=None, max_pages=40, time_budget=90,
               refresh=False, progress=lambda step, msg: None, cache=None):
    """Steps 2-7. Returns dict(results, excluded, queries, partial, errors)."""
    t0 = time.time()
    cache = cache if cache is not None else searcher.cache
    queries = build_queries(part, regions)
    progress(2, f"{len(queries)} search queries prepared")
    hits = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(searcher.search, q, 8, refresh): q for q in queries}
        for f in as_completed(futs):
            for h in f.result() or []:
                h["query"] = futs[f]
                hits.append(h)
    progress(3, f"{len(hits)} search hits from {len(queries)} queries")
    # group by domain, keep best hit per domain (prefer one mentioning the MPN)
    by_dom = {}
    nm = norm_mpn(part.get("mpn") or "")
    for h in hits:
        d = domain_of(h["url"])
        score = (2 if nm and nm in norm_mpn(h["title"] + h["snippet"] + h["url"]) else 0) + (1 if which(d, DISTRIBUTORS) else 0)
        if d not in by_dom or score > by_dom[d]["_score"]:
            by_dom[d] = {**h, "_score": score, "_queries": by_dom.get(d, {}).get("_queries", set()) | {h["query"]}}
        else:
            by_dom[d]["_queries"].add(h["query"])
    doms = sorted(by_dom, key=lambda d: -by_dom[d]["_score"])
    results, excluded, partial, pages_used = [], [], False, 0
    budget_pages = max_pages

    def work(d):
        h = by_dom[d]
        ctype0, why0 = company_type(d, h["title"] + " " + h["snippet"], part)
        if ctype0 == "Excluded":
            return {"domain": d, "excluded": why0, "url": h["url"]}
        page = fetch(h["url"], cache, refresh)
        pages = [page] if page else []
        if page and not which(d, B2B) and not which(d, DISTRIBUTORS):
            for u in contact_links(page)[:2]:
                p2 = fetch(u, cache, refresh)
                if p2:
                    pages.append(p2)
        if not page:
            # fall back to the snippet only (clearly marked)
            page = {"url": h["url"], "html": "", "text": h["title"] + " " + h["snippet"], "title": h["title"]}
            pages = [page]
            fetched = False
        else:
            fetched = True
        alltext = " ".join(p["text"] for p in pages)
        ctype, why = company_type(d, alltext[:20000], part)
        if ctype == "Excluded":
            return {"domain": d, "excluded": why, "url": h["url"]}
        mclass, cmp, fspecs = match_class(part, page["text"][:60000], page["title"])
        # contacts: take the page that yields the strongest contact
        best = None
        for p in pages:
            c = extract_contacts(p, d)
            lvl = contact_level(domain_of(p["url"]), d, ctype, c)
            if best is None or "ABCDE".index(lvl) < "ABCDE".index(best[1]):
                best = (c, lvl, p["url"])
        contacts, level, csrc = best
        info = rx_commercials(page["text"][:60000])
        ai = ai_contacts(llm, pages[-1] if len(pages) > 1 else page, d) if (llm and llm.ready and fetched) else {}
        for k in ("price", "moq", "lead_time", "stock", "warranty"):
            if ai.get(k) and not info.get(k):
                info[k] = ai[k]
        phones = contacts.get("phones") or []
        country = ai.get("country") or country_from_domain(d) or next(
            (PHONE_CC[c] for p in phones for c in sorted(PHONE_CC, key=len, reverse=True) if p.startswith(c)), None)
        title_name = re.split(r"\s[|\-–]\s", page["title"] or "")[0].strip() if page["title"] else ""
        name = ai.get("company_name") or which(d, DISTRIBUTORS) or title_name or d
        if which(d, B2B) and not ai.get("company_name"):
            name = f"{title_name or d} (via {which(d, B2B)})"
        e0 = contacts["emails"][0] if contacts.get("emails") else None
        return {
            "domain": d, "company": name[:80], "website": f"https://{d}", "type": ctype, "type_reason": why,
            "country": country, "address": ai.get("address"), "sales_person": ai.get("sales_person"),
            "match": mclass, "spec_compare": cmp, "found_specs": fspecs,
            "emails": contacts.get("emails", []), "phones": phones, "whatsapp": contacts.get("whatsapp", []),
            "contact_kind": (e0["type"] if e0 else ("Phone only" if phones else None)),
            "general_contact": bool(e0 and e0["type"] != "Sales"),
            "level": level, "level_text": LEVELS[level], "contact_source": csrc if level != "E" else None,
            "source_url": h["url"], "product_page": page["url"], "page_fetched": fetched,
            "price": info.get("price"), "moq": info.get("moq"), "lead_time": info.get("lead_time"),
            "availability": (f"Stock {info['stock']}" if info.get("stock") else None), "warranty": info.get("warranty"),
            "queries": sorted(by_dom[d]["_queries"]), "snippet": h["snippet"][:300],
        }

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        for d in doms:
            if pages_used >= budget_pages:
                partial = True
                break
            futs[ex.submit(work, d)] = d
            pages_used += 1
        done = 0
        for f in as_completed(futs):
            done += 1
            try:
                r = f.result(timeout=max(1, time_budget - (time.time() - t0)))
            except Exception as e:  # noqa: BLE001
                partial = True
                searcher.errors.append(f"{futs[f]}: {str(e)[:120]}")
                continue
            if r.get("excluded"):
                excluded.append(r)
            else:
                results.append(r)
            if done % 5 == 0:
                progress(5, f"Checked {done} of {len(futs)} websites")
            if time.time() - t0 > time_budget:
                partial = True
                break
    tord = ("Manufacturer", "Authorised", "Distributor", "B2B", "Distributor / trader", "Unclassified")
    results.sort(key=lambda r: (MATCH_ORDER.index(r["match"]), "ABCDE".index(r["level"]),
                                next((i for i, t in enumerate(tord) if r["type"].startswith(t)), 9)))
    progress(6, f"{len(results)} suppliers kept, {len(excluded)} excluded")
    return {"results": results, "excluded": excluded, "queries": queries, "partial": partial,
            "errors": list(dict.fromkeys(searcher.errors + (llm.errors if llm else [])))[:20],
            "seconds": round(time.time() - t0, 1)}


# --------------------------------------------------------------------------------------------
# RFQ
# --------------------------------------------------------------------------------------------
def rfq_text(part, supplier, f):
    specs = "\n".join(f"  - {SPEC_LABEL.get(k, k)}: {v}" for k, v in (part.get("key_specs") or {}).items()) or "  - As per attached datasheet"
    to = supplier.get("company") if supplier else "Supplier"
    subj = f"RFQ – {part.get('mpn') or part.get('description', '')[:60]} – Qty {f['qty']}"
    body = f"""Dear {to} Team,

We request your quotation for the item below.

Part number: {part.get('mpn') or 'Not specified (generic requirement)'}
Manufacturer: {part.get('manufacturer') or 'Open'}
Description: {part.get('description') or ''}
Technical specifications:
{specs}

Quantity: {f['qty']}
Delivery location: {f['location']}
Required delivery: {f['schedule']}
Warranty requirement: {f['warranty']}

Please quote:
1. Unit price, currency and Incoterms
2. MOQ
3. Lead time
4. Warranty terms
5. Datasheet and country of origin
6. Quotation validity

Please reply by {f['reply_by']}.

Regards,
{f['sender']}
{f['company']}
"""
    return subj, body
