"""Storage for CPMO: one JSON file on a GitHub branch (same design as the NPD tracker).

Fallbacks: a local file (env CPMO_DATA_FILE, for running on your PC) or session memory only.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime

import requests

SEARCH_KEEP = 60  # searches kept with full results


def empty():
    return {"version": 1, "suppliers": [], "searches": [], "rfqs": [], "usage": {}, "settings": {}}


def new_id():
    return uuid.uuid4().hex[:10]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def norm(d):
    base = empty()
    if not isinstance(d, dict):
        return base
    for k, v in base.items():
        d.setdefault(k, v)
    d["searches"] = d["searches"][-SEARCH_KEEP:]
    return d


def dumps(d):
    return json.dumps(d, ensure_ascii=False, indent=1, default=str).encode("utf-8")


# ------------------------------------------------------------------ GitHub
def _gh(cfg, method, url, **kw):
    h = {"Authorization": f"Bearer {cfg['token']}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    return requests.request(method, f"https://api.github.com/repos/{cfg['repo']}{url}", headers=h, timeout=25, **kw)


def _msg(r):
    try:
        return r.json().get("message", r.text)[:200]
    except Exception:  # noqa: BLE001
        return r.text[:200]


def gh_ensure_branch(cfg):
    r = _gh(cfg, "GET", f"/git/ref/heads/{cfg['branch']}")
    if r.status_code == 200:
        return
    if r.status_code != 404:
        raise RuntimeError(f"GitHub {r.status_code}: {_msg(r)}")
    base = _gh(cfg, "GET", f"/git/ref/heads/{cfg.get('base', 'main')}")
    if base.status_code != 200:
        raise RuntimeError(f"Cannot read branch {cfg.get('base', 'main')}: {_msg(base)}")
    c = _gh(cfg, "POST", "/git/refs", json={"ref": f"refs/heads/{cfg['branch']}", "sha": base.json()["object"]["sha"]})
    if c.status_code not in (200, 201):
        raise RuntimeError(f"Cannot create branch {cfg['branch']}: {_msg(c)}")


def gh_load(cfg):
    gh_ensure_branch(cfg)
    r = _gh(cfg, "GET", f"/contents/{cfg['path']}", params={"ref": cfg["branch"]})
    if r.status_code == 404:
        return None, None
    if r.status_code != 200:
        raise RuntimeError(f"GitHub {r.status_code}: {_msg(r)}")
    j = r.json()
    if j.get("content"):
        raw = base64.b64decode(j["content"])
    else:
        raw = base64.b64decode(_gh(cfg, "GET", f"/git/blobs/{j['sha']}").json()["content"])
    return norm(json.loads(raw.decode("utf-8"))), j["sha"]


def gh_save(cfg, data, sha, message):
    body = {"message": f"CPMO: {message}", "branch": cfg["branch"], "content": base64.b64encode(dumps(data)).decode()}
    for attempt in (1, 2):
        if sha:
            body["sha"] = sha
        r = _gh(cfg, "PUT", f"/contents/{cfg['path']}", json=body)
        if r.status_code in (200, 201):
            return r.json()["content"]["sha"]
        if r.status_code in (409, 422) and attempt == 1:
            g = _gh(cfg, "GET", f"/contents/{cfg['path']}", params={"ref": cfg["branch"]})
            sha = g.json().get("sha") if g.status_code == 200 else None
            continue
        raise RuntimeError(f"GitHub {r.status_code}: {_msg(r)}")


# ------------------------------------------------------------------ local file
def file_load(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return norm(json.load(f))
    return None


def file_save(path, data):
    tmp = path + ".tmp"
    if os.path.exists(path):  # backup before overwriting
        os.replace(path, path + ".bak")
    with open(tmp, "wb") as f:
        f.write(dumps(data))
    os.replace(tmp, path)


# ------------------------------------------------------------------ supplier upsert (no duplicates)
def root_dom(d):
    parts = (d or "").split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def supplier_key(r):
    """B2B directory pages hold many companies, so key them by company name too."""
    if "(via " in (r.get("company") or ""):
        return (root_dom(r.get("domain")), (r.get("company") or "").lower().strip())
    return (root_dom(r.get("domain")), "")


def upsert_supplier(data, r, part, user):
    key = supplier_key(r)
    sup = next((s for s in data["suppliers"] if supplier_key(s) == key), None)
    product = {"mpn": part.get("mpn"), "manufacturer": part.get("manufacturer"), "description": part.get("description"),
               "match": r.get("match"), "source_url": r.get("product_page") or r.get("source_url"),
               "price": r.get("price"), "moq": r.get("moq"), "lead_time": r.get("lead_time"),
               "warranty": r.get("warranty"), "availability": r.get("availability"), "date": now()}
    fields = ("company", "domain", "website", "type", "country", "address", "sales_person", "emails", "phones",
              "whatsapp", "level", "level_text", "contact_source", "general_contact")
    if sup is None:
        sup = {"id": new_id(), **{k: r.get(k) for k in fields}, "products": [], "notes": "", "edited": False,
               "saved_by": user, "saved_at": now(), "search_date": now()}
        data["suppliers"].append(sup)
        created = True
    else:
        created = False
        if not sup.get("edited"):  # never overwrite manual edits
            better = "ABCDE".index(r.get("level") or "E") < "ABCDE".index(sup.get("level") or "E")
            for k in fields:
                if (better or not sup.get(k)) and r.get(k):
                    sup[k] = r[k]
        sup["search_date"] = now()
    pk = (product["mpn"] or product["description"], product["source_url"])
    sup["products"] = [p for p in sup.get("products", []) if (p.get("mpn") or p.get("description"), p.get("source_url")) != pk]
    sup["products"].append(product)
    return created
