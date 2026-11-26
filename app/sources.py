"""Fetchers for each dataset. Every fetcher returns plain dicts and names its source."""
from __future__ import annotations
import datetime as dt
import re
import httpx
from bs4 import BeautifulSoup

UA = "egypt-open-data-api/0.1 (+https://github.com/mahmoudabdalhamid10-alt/egypt-open-data-api)"
TIMEOUT = 25

PETROLEUM_URL = "https://www.petroleum.gov.eg/en/Pages/HomePage.aspx"
FX_URLS = [
    "https://{ver}.currency-api.pages.dev/v1/currencies/usd.json",
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{ver}/v1/currencies/usd.json",
]
WB_URL = "https://api.worldbank.org/v2/country/EGY/indicator/{code}?format=json&per_page=80"

FX_SYMBOLS = ["usd", "eur", "gbp", "sar", "aed", "kwd", "qar", "jpy", "cny", "chf", "cad", "try"]

WB_INDICATORS = {
    "inflation": ("FP.CPI.TOTL.ZG", "Inflation, consumer prices (annual %)"),
    "gdp_growth": ("NY.GDP.MKTP.KD.ZG", "GDP growth (annual %)"),
    "gdp_per_capita": ("NY.GDP.PCAP.CD", "GDP per capita (current US$)"),
    "population": ("SP.POP.TOTL", "Population, total"),
    "unemployment": ("SL.UEM.TOTL.ZS", "Unemployment, total (% of labor force, modeled ILO estimate)"),
    "remittances": ("BX.TRF.PWKR.CD.DT", "Personal remittances, received (current US$)"),
    "official_exchange_rate": ("PA.NUS.FCRF", "Official exchange rate (EGP per US$, period average)"),
}


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True)


def fetch_fuel() -> dict:
    """Official pump prices from the Ministry of Petroleum and Mineral Resources home page."""
    with _client() as c:
        r = c.get(PETROLEUM_URL)
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    wanted = {
        "gasoline 80": "gasoline_80", "gasoline 92": "gasoline_92", "gasoline 95": "gasoline_95",
        "solar": "diesel", "diesel": "diesel", "kerosene": "kerosene", "gas stove": "lpg_cylinder",
    }
    prices: dict[str, dict] = {}
    for row in soup.find_all("tr"):
        cells = [x.get_text(" ", strip=True) for x in row.find_all(["td", "th"])]
        cells = [x for x in cells if x]
        if len(cells) < 3:
            continue
        key = wanted.get(cells[0].lower())
        m = re.match(r"^\d+(\.\d+)?$", cells[1])
        if not key or not m:
            continue
        value = float(cells[1])
        unit = "EGP/cylinder" if key == "lpg_cylinder" else "EGP/liter"
        # The ministry page labels Gasoline 80 as "piaster/liter" although the value is in pounds.
        prices[key] = {"price": value, "unit": unit, "source_label": f"{cells[2]}"}
    if len(prices) < 5:
        raise RuntimeError("fuel page layout changed: found %d products" % len(prices))
    return {
        "as_of": dt.date.today().isoformat(),
        "currency": "EGP",
        "prices": prices,
        "source": {"name": "Ministry of Petroleum and Mineral Resources (Egypt)", "url": PETROLEUM_URL},
        "note": "Gasoline 80 is labeled 'piaster/liter' on the source page but the value is in EGP per liter; normalized here.",
    }


def fetch_fx(date: str | None = None) -> dict:
    """Daily reference rates. Returns how many EGP one unit of each currency buys."""
    ver = date if date else "latest"
    j = None
    with _client() as c:
        for tpl in FX_URLS:
            try:
                r = c.get(tpl.format(ver=ver))
                r.raise_for_status()
                j = r.json()
                break
            except Exception:
                continue
    if j is None:
        raise RuntimeError("fx source unavailable")
    usd = j["usd"]
    egp_per_usd = usd["egp"]
    # EGP per 1 unit of X = (EGP per USD) / (X per USD)
    rates = {s.upper(): round(egp_per_usd / usd[s], 4) for s in FX_SYMBOLS if usd.get(s)}
    return {
        "date": j["date"],
        "base_note": "Each value is how many EGP one unit of the currency is worth (mid-market reference rate).",
        "rates_egp": rates,
        "source": {
            "name": "fawazahmed0/exchange-api (CC0), aggregated market reference rates",
            "url": "https://github.com/fawazahmed0/exchange-api",
        },
        "disclaimer": "Market reference rates, NOT the official Central Bank of Egypt rate and not a bank buy/sell quote.",
    }


def fetch_wb(key: str) -> dict:
    code, label = WB_INDICATORS[key]
    with _client() as c:
        r = c.get(WB_URL.format(code=code))
        r.raise_for_status()
    j = r.json()
    rows = [
        {"year": int(x["date"]), "value": x["value"]}
        for x in (j[1] or []) if x.get("value") is not None
    ]
    rows.sort(key=lambda x: x["year"])
    return {
        "indicator": key,
        "code": code,
        "label": label,
        "country": "Egypt, Arab Rep.",
        "series": rows,
        "latest": rows[-1] if rows else None,
        "last_updated": j[0].get("lastupdated"),
        "source": {"name": "World Bank Open Data (CC BY 4.0)", "url": "https://data.worldbank.org/country/egypt-arab-rep"},
        "attribution": "Data: World Bank, licensed CC BY 4.0. Served unmodified except for JSON reshaping.",
    }
