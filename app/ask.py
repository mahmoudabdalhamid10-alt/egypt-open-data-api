"""اسأل أرقام مصر: Arabic questions about Egypt's economy, answered from real fetched data.

No language model. A question is matched to a dataset by Arabic keywords, the real series is
fetched (World Bank / FX reference rates), and the paragraph is built from the numbers returned.
Anything we cannot match is refused instead of guessed.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from . import sources

router = APIRouter()

FREE_PER_DAY = int(os.getenv("ASK_FREE_DAILY", "3"))
try:
    ASK_KEYS = json.loads(os.getenv("ASK_KEYS", "{}") or "{}")  # {"<code>": "YYYY-MM-DD"}
except Exception:
    ASK_KEYS = {}
_used: dict[tuple[str, str], int] = {}
_cache: dict[str, tuple[float, dict]] = {}


def _cached(key: str, ttl: int, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        if hit:
            return hit[1]
        raise
    _cache[key] = (now, val)
    return val


def _norm(s: str) -> str:
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = re.sub("[\u064b-\u065f\u0640]", "", s)
    s = re.sub("[إأآٱ]", "ا", s)
    s = s.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return s.lower()


# indicator key -> (Arabic name, unit text, kind, divisor, decimals)
IND = {
    "inflation": ("معدل التضخم", "%", "pct", 1, 1),
    "gdp_growth": ("نمو الناتج المحلي الإجمالي", "%", "pct", 1, 1),
    "gdp_per_capita": ("نصيب الفرد من الناتج المحلي", "دولار", "level", 1, 0),
    "population": ("عدد السكان", "مليون نسمة", "level", 1e6, 1),
    "unemployment": ("معدل البطالة", "%", "pct", 1, 1),
    "remittances": ("تحويلات المصريين بالخارج", "مليار دولار", "level", 1e9, 1),
    "official_exchange_rate": ("سعر الدولار الرسمي (متوسط السنة)", "جنيه", "level", 1, 2),
}

CURRENCIES = {
    "USD": ("الدولار", ["دولار", "usd", "dollar"]),
    "EUR": ("اليورو", ["يورو", "eur"]),
    "GBP": ("الجنيه الاسترليني", ["استرليني", "gbp", "باوند", "الجنيه الانجليزي"]),
    "SAR": ("الريال السعودي", ["ريال", "سعودي", "sar"]),
    "AED": ("الدرهم الإماراتي", ["درهم", "اماراتي", "aed"]),
    "KWD": ("الدينار الكويتي", ["كويتي", "kwd"]),
}


def _has(q: str, words) -> bool:
    return any(_norm(w) in q for w in words)


def detect(q: str):
    n = _norm(q)
    if _has(n, ["تضخم", "غلاء", "ارتفاع الاسعار", "زيادة الاسعار", "inflation"]):
        return "inflation"
    if _has(n, ["بطاله", "عاطل", "unemployment"]):
        return "unemployment"
    if _has(n, ["تحويلات", "المغتربين", "مصريين بالخارج", "المصريين في الخارج", "remittance"]):
        return "remittances"
    if _has(n, ["سكان", "تعداد", "population"]):
        return "population"
    if _has(n, ["دخل الفرد", "نصيب الفرد", "للفرد", "متوسط الدخل", "per capita"]):
        return "gdp_per_capita"
    if _has(n, ["نمو", "الناتج المحلي", "gdp"]):
        return "gdp_growth"
    if _has(n, ["الرسمي", "متوسط سعر الدولار", "من 20", "من 19"]) and _has(n, ["دولار", "صرف"]):
        return "official_exchange_rate"
    if _has(n, ["دولار", "يورو", "ريال", "درهم", "استرليني", "صرف", "عمله", "عملات", "كويتي"]):
        # a year range on the dollar means the World Bank annual series
        if re.search(r"(19|20)\d\d", n) or _has(n, ["اخر", "سنين", "سنوات", "من زمان"]):
            return "official_exchange_rate" if "usd" not in n and not _has(n, ["يورو", "ريال", "درهم"]) else "fx"
        return "fx"
    return None


def years_from(q: str, last_year: int):
    n = _norm(q)
    ys = [int(x) for x in re.findall(r"\b((?:19|20)\d\d)\b", n)]
    m = re.search(r"اخر\s*(\d{1,2})\s*(?:سنين|سنوات|سنه|عام|اعوام)", n)
    if m:
        k = max(2, min(40, int(m.group(1))))
        return last_year - k + 1, last_year, "range"
    if _has(n, ["اخر سنتين"]):
        return last_year - 1, last_year, "range"
    if len(ys) >= 2:
        return min(ys), max(ys), "range"
    if len(ys) == 1:
        y = ys[0]
        i = n.find(str(y))
        before = n[max(0, i - 8):i]
        if "من" in before or "منذ" in before or "بعد" in before:
            return y, last_year, "range"
        return y, y, "single"
    return last_year - 14, last_year, "default"


def fnum(v: float, dec: int) -> str:
    return f"{v:,.{dec}f}"


def U(u: str) -> str:
    return "%" if u == "%" else " " + u


def build_series(key: str, q: str):
    name, unit, kind, div, dec = IND[key]
    d = _cached(f"wb:{key}", 24 * 3600, lambda: sources.fetch_wb(key))
    allrows = [r for r in d["series"] if r["value"] is not None]
    if not allrows:
        raise RuntimeError("no data")
    first_av, last_av = allrows[0]["year"], allrows[-1]["year"]
    y0, y1, mode = years_from(q, last_av)
    note = ""
    if y1 > last_av:
        note = f"آخر سنة متاحة عند البنك الدولي هي {last_av}، فاتعرض لحد {last_av}."
        y1 = last_av
    if y0 < first_av:
        note = (note + " " if note else "") + f"أقدم سنة متاحة هي {first_av}."
        y0 = first_av
    if y0 > y1:
        y0 = max(first_av, y1 - 9)
    shown = [r for r in allrows if y0 <= r["year"] <= y1]
    focus = None
    if mode == "single":
        focus = y0
        shown = [r for r in allrows if max(first_av, y0 - 9) <= r["year"] <= y0]
        if not any(r["year"] == y0 for r in shown):
            raise LookupError(f"مفيش رقم منشور لسنة {y0} في بيانات البنك الدولي (المتاح من {first_av} لحد {last_av}).")
    if len(shown) < 2:
        raise LookupError(f"الفترة اللي طلبتها مفيهاش بيانات كفاية. المتاح من {first_av} لحد {last_av}.")
    vals = [r["value"] / div for r in shown]
    yrs = [r["year"] for r in shown]
    a, b = vals[0], vals[-1]
    imax, imin = vals.index(max(vals)), vals.index(min(vals))
    u = unit
    parts = []
    if mode == "single":
        v = vals[-1]
        parts.append(f"في {yrs[-1]} كان {name} {fnum(v, dec)}{U(u)}.")
        p = vals[-2]
        if kind == "pct":
            parts.append(f"ده {'أعلى' if v > p else 'أقل'} من {yrs[-2]} ({fnum(p, dec)}%) بحوالي {fnum(abs(v - p), dec)} نقطة مئوية.")
        else:
            parts.append(f"وده {'أعلى' if v > p else 'أقل'} من {yrs[-2]} ({fnum(p, dec)}{U(u)}) بنسبة {fnum(abs(v / p - 1) * 100, 1)}%.")
    else:
        parts.append(f"من {yrs[0]} لحد {yrs[-1]} {name} اتحرك من {fnum(a, dec)}{U(u)} لـ {fnum(b, dec)}{U(u)}.")
        if kind == "pct":
            ch = b - a
            parts.append(f"يعني {'زاد' if ch > 0 else 'قل'} بمقدار {fnum(abs(ch), dec)} نقطة مئوية.")
        elif a:
            ch = (b / a - 1) * 100
            parts.append(f"يعني {'زاد' if ch > 0 else 'قل'} بنسبة {fnum(abs(ch), 1)}% خلال الفترة.")
        parts.append(f"أعلى قيمة كانت {fnum(vals[imax], dec)}{U(u)} في {yrs[imax]}، وأقل قيمة {fnum(vals[imin], dec)}{U(u)} في {yrs[imin]}.")
    if note:
        parts.append(note)
    if key == "official_exchange_rate":
        parts.append("ده متوسط السعر الرسمي على مدار السنة مش سعر يوم معين.")
    src = d["source"]
    return {
        "status": "ok",
        "title": f"{name} ({u})",
        "answer": " ".join(parts),
        "chart": {"type": "line", "labels": yrs, "values": [round(v, 4) for v in vals], "unit": u, "dec": dec, "focus": focus},
        "source": {"name": "البنك الدولي (World Bank Open Data)", "detail": d["label"], "url": src["url"],
                   "license": "CC BY 4.0", "years": f"{yrs[0]}–{yrs[-1]}", "last_updated": d.get("last_updated")},
    }


def build_fx(q: str):
    d = _cached("fx:latest", 6 * 3600, sources.fetch_fx)
    n = _norm(q)
    want = [c for c, (_, ws) in CURRENCIES.items() if _has(n, ws)]
    if not want:
        want = ["USD", "EUR", "SAR", "AED", "GBP"]
    rates = d["rates_egp"]
    want = [c for c in want if c in rates]
    if not want:
        raise RuntimeError("no fx")
    asked = list(want)
    for c in ["USD", "EUR", "SAR", "AED", "GBP"]:
        if len(want) < 4 and c in rates and c not in want:
            want.append(c)
    items = [(CURRENCIES[c][0], rates[c]) for c in want]
    want = asked
    if len(want) == 1:
        c = want[0]
        text = f"{CURRENCIES[c][0]} بـ {fnum(rates[c], 2)} جنيه مصري بتاريخ {d['date']}."
    else:
        text = "بتاريخ " + d["date"] + ": " + "، ".join(f"{nm} بـ {fnum(v, 2)} جنيه" for nm, v in items) + "."
    text += " ده سعر مرجعي متوسط من السوق، مش سعر البنك المركزي الرسمي ولا سعر البيع والشراء في البنوك."
    return {
        "status": "ok",
        "title": "سعر العملة مقابل الجنيه المصري (جنيه)",
        "answer": text,
        "chart": {"type": "bar", "labels": [i[0] for i in items], "values": [i[1] for i in items], "unit": "جنيه", "dec": 2, "focus": CURRENCIES[asked[0]][0] if len(asked) == 1 else None},
        "source": {"name": "أسعار مرجعية مجمّعة (fawazahmed0/exchange-api)", "detail": "mid-market reference rates", "url": d["source"]["url"],
                   "license": "CC0 1.0", "years": d["date"], "last_updated": d["date"]},
    }


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()


def _quota(request: Request):
    day = dt.date.today().isoformat()
    code = (request.headers.get("x-ask-key") or "").strip()
    if code and code in ASK_KEYS:
        until = str(ASK_KEYS[code])
        if day <= until:
            return None, "unlimited", day
        return None, "expired", day
    return (_ip(request), day), "free", day


@router.get("/api/ask/status")
def ask_status(request: Request):
    k, mode, day = _quota(request)
    used = _used.get(k, 0) if k else 0
    return {"mode": mode, "limit": FREE_PER_DAY, "remaining": None if mode == "unlimited" else max(0, FREE_PER_DAY - used)}


@router.post("/api/ask")
async def ask(request: Request):
    try:
        body = await request.json()
        q = str(body.get("q", "")).strip()[:300]
    except Exception:
        q = ""
    if len(q) < 3:
        return JSONResponse({"status": "bad", "message": "اكتب سؤالك الأول."}, status_code=400)
    k, mode, day = _quota(request)
    if mode == "expired":
        return JSONResponse({"status": "expired", "message": "الكود ده انتهى. شيله وكمل بالنسخة المجانية."}, status_code=401)
    if k:
        for old in [x for x in _used if x[1] != day]:
            _used.pop(old, None)
        if _used.get(k, 0) >= FREE_PER_DAY:
            return JSONResponse({"status": "limit", "remaining": 0, "limit": FREE_PER_DAY,
                                 "message": f"خلصت أسئلتك المجانية النهارده ({FREE_PER_DAY}). ارجع بكره، أو النسخة غير المحدودة هتتفتح قريب."}, status_code=429)
    key = detect(q)
    if key is None:
        return {"status": "unsupported", "remaining": None if not k else max(0, FREE_PER_DAY - _used.get(k, 0)),
                "message": "مقدرتش أفهم السؤال ده من البيانات اللي عندي. بجاوب عن: التضخم، البطالة، نمو الاقتصاد، عدد السكان، دخل الفرد، تحويلات المصريين بالخارج، وسعر الدولار والعملات. السؤال ده ماتحسبش من أسئلتك."}
    try:
        res = build_fx(q) if key == "fx" else build_series(key, q)
    except LookupError as e:
        return {"status": "unsupported", "remaining": None if not k else max(0, FREE_PER_DAY - _used.get(k, 0)), "message": str(e) + " ماتحسبش من أسئلتك."}
    except Exception:
        return JSONResponse({"status": "error", "message": "المصدر مش متاح دلوقتي. جرب بعد شوية. ماتحسبش من أسئلتك."}, status_code=502)
    if k:
        _used[k] = _used.get(k, 0) + 1
    res["remaining"] = None if not k else max(0, FREE_PER_DAY - _used[k])
    res["limit"] = FREE_PER_DAY
    res["mode"] = mode
    return res
