from __future__ import annotations
import os
import time
import datetime as dt
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from . import sources

app = FastAPI(
    title="Egypt Open Data API",
    version="0.1.0",
    description=(
        "Clean JSON for public Egyptian data: official fuel prices, daily EGP reference rates, "
        "and World Bank macro series (inflation, GDP, population, unemployment, remittances). "
        "Every response names its source and license."
    ),
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FREE_DAILY_LIMIT = int(os.getenv("FREE_DAILY_LIMIT", "100"))
PROXY_SECRET = os.getenv("RAPIDAPI_PROXY_SECRET", "")
# Manual paid keys: env API_KEYS = {"<key>": "pro" | "ultra"}, or "pro|YYYY-MM-DD" to expire after that date.
# Added by hand after a confirmed wallet credit.
try:
    API_KEYS = json.loads(os.getenv("API_KEYS", "{}") or "{}")
except Exception:
    API_KEYS = {}
PLAN_DAILY = {"pro": 400, "ultra": 4000}
_key_hits: dict[tuple[str, str], int] = {}
_cache: dict[str, tuple[float, object]] = {}
_hits: dict[tuple[str, str], int] = {}


def cached(key: str, ttl: int, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception as exc:  # serve stale data rather than failing, but say so
        if hit:
            stale = dict(hit[1]) if isinstance(hit[1], dict) else hit[1]
            if isinstance(stale, dict):
                stale["stale"] = True
            return stale
        raise HTTPException(502, f"upstream source unavailable: {type(exc).__name__}")
    _cache[key] = (now, val)
    return val


@app.middleware("http")
async def limit(request: Request, call_next):
    path = request.url.path
    if path.startswith("/v1/"):
        via_rapid = bool(PROXY_SECRET) and request.headers.get("x-rapidapi-proxy-secret") == PROXY_SECRET
        key = request.headers.get("x-api-key")
        if key:
            plan = API_KEYS.get(key)
            if not plan:
                return JSONResponse({"error": "invalid API key"}, status_code=401)
            plan, _, until = str(plan).partition("|")
            if until and dt.date.today().isoformat() > until:
                return JSONResponse({"error": f"API key expired on {until}; renew by a new transfer, see /pricing"}, status_code=401)
            if path.startswith("/v1/fuel"):
                return JSONResponse({"error": "fuel data is not included in paid plans (source redistribution terms unverified)"}, status_code=403)
            day = dt.date.today().isoformat()
            kk = (key, day)
            _key_hits[kk] = _key_hits.get(kk, 0) + 1
            for old in [x for x in _key_hits if x[1] != day]:
                _key_hits.pop(old, None)
            if _key_hits[kk] > PLAN_DAILY.get(plan, 0):
                return JSONResponse({"error": f"daily limit for plan {plan} reached"}, status_code=429)
            return await call_next(request)
        if via_rapid and path.startswith("/v1/fuel") and request.headers.get("x-rapidapi-subscription", "").strip().lower() != "basic":
            return JSONResponse({"error": "fuel data is not included in paid plans (source redistribution terms unverified); available on the free BASIC plan"}, status_code=403)
        if not via_rapid:
            ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
            day = dt.date.today().isoformat()
            k = (ip, day)
            _hits[k] = _hits.get(k, 0) + 1
            for old in [x for x in _hits if x[1] != day]:
                _hits.pop(old, None)
            if _hits[k] > FREE_DAILY_LIMIT:
                return JSONResponse({"error": f"free tier limit of {FREE_DAILY_LIMIT} requests/day reached"}, status_code=429)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return FileResponse(Path(__file__).resolve().parent / "static" / "index.html")


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo():
    return FileResponse(Path(__file__).resolve().parent / "static" / "2-demo.html")


@app.get("/pricing", response_class=HTMLResponse, include_in_schema=False)
def pricing():
    return FileResponse(Path(__file__).resolve().parent / "static" / "pricing.html")


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}


@app.get("/v1/sources", tags=["meta"])
def list_sources():
    """Every dataset with its source, license and refresh cadence."""
    return {"datasets": [
        {"id": "fuel", "source": sources.PETROLEUM_URL, "owner": "Ministry of Petroleum and Mineral Resources (Egypt)",
         "refresh": "cached 6h, snapshot history committed daily", "terms": "Official public prices, cited with source."},
        {"id": "fx", "source": "https://github.com/fawazahmed0/exchange-api", "license": "CC0 1.0",
         "refresh": "cached 6h", "note": "Market reference rates, not the official CBE rate."},
        {"id": "macro", "source": "https://api.worldbank.org", "license": "CC BY 4.0 (World Bank)",
         "refresh": "cached 24h", "indicators": list(sources.WB_INDICATORS)},
    ]}


@app.get("/v1/fuel/latest", tags=["fuel"])
def fuel_latest():
    """Current official pump prices (EGP) for gasoline 80/92/95, diesel, kerosene and LPG cylinder."""
    return cached("fuel", 6 * 3600, sources.fetch_fuel)


@app.get("/v1/fuel/history", tags=["fuel"])
def fuel_history():
    """Daily snapshots we have recorded so far (grows every day)."""
    f = DATA_DIR / "fuel_history.json"
    return {"snapshots": json.loads(f.read_text()) if f.exists() else [],
            "source": {"name": "Ministry of Petroleum and Mineral Resources (Egypt)", "url": sources.PETROLEUM_URL}}


@app.get("/v1/fx/latest", tags=["fx"])
def fx_latest(symbols: str | None = Query(None, description="Comma list, e.g. USD,EUR,SAR")):
    """EGP value of one unit of USD, EUR, GBP, SAR, AED and more."""
    d = dict(cached("fx:latest", 6 * 3600, sources.fetch_fx))
    if symbols:
        want = {s.strip().upper() for s in symbols.split(",")}
        d["rates_egp"] = {k: v for k, v in d["rates_egp"].items() if k in want}
    return d


@app.get("/v1/fx/{date}", tags=["fx"])
def fx_on(date: str, symbols: str | None = Query(None)):
    """Reference rates for a past date (YYYY-MM-DD)."""
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    d = dict(cached(f"fx:{date}", 7 * 24 * 3600, lambda: sources.fetch_fx(date)))
    if symbols:
        want = {x.strip().upper() for x in symbols.split(",")}
        d["rates_egp"] = {k: v for k, v in d["rates_egp"].items() if k in want}
    return d


@app.get("/v1/macro", tags=["macro"])
def macro_list():
    return {"indicators": {k: v[1] for k, v in sources.WB_INDICATORS.items()}}


@app.get("/v1/macro/{indicator}", tags=["macro"])
def macro(indicator: str, from_year: int | None = None, to_year: int | None = None):
    """Yearly series for inflation, gdp_growth, gdp_per_capita, population, unemployment, remittances, official_exchange_rate."""
    if indicator not in sources.WB_INDICATORS:
        raise HTTPException(404, f"unknown indicator, choose one of {list(sources.WB_INDICATORS)}")
    d = dict(cached(f"wb:{indicator}", 24 * 3600, lambda: sources.fetch_wb(indicator)))
    s = d["series"]
    if from_year:
        s = [x for x in s if x["year"] >= from_year]
    if to_year:
        s = [x for x in s if x["year"] <= to_year]
    d["series"] = s
    return d
