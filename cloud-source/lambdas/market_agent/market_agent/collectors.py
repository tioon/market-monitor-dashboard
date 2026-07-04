import datetime as dt
import csv
import email.utils
import html
import io
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from .http import get_json, get_text, urlencode


YAHOO_SYMBOLS = {
    "US": {
        "S&P 500": "^GSPC",
        "Nasdaq": "^IXIC",
        "Dow": "^DJI",
        "Russell 2000": "^RUT",
        "VIX": "^VIX",
        "US 10Y Yield": "^TNX",
        "Dollar Index": "DX-Y.NYB",
        "WTI Oil": "CL=F",
        "Gold": "GC=F",
        "Semiconductors ETF": "SOXX",
        "20Y Treasury ETF": "TLT",
    },
    "Korea": {
        "KOSPI": "^KS11",
        "KOSDAQ": "^KQ11",
        "Samsung Electronics": "005930.KS",
        "SK Hynix": "000660.KS",
        "USD/KRW": "KRW=X",
        "Korea ETF EWY": "EWY",
    },
    "China": {
        "Shanghai Composite": "000001.SS",
        "Shenzhen Component": "399001.SZ",
        "CSI 300": "000300.SS",
        "Hang Seng": "^HSI",
        "USD/CNH": "CNH=X",
        "China Large-Cap ETF": "FXI",
        "China Internet ETF": "KWEB",
    },
}


FRED_SERIES = {
    "DGS10": "US 10Y Treasury",
    "DGS2": "US 2Y Treasury",
    "T10Y2Y": "US 10Y-2Y Spread",
    "T10Y3M": "US 10Y-3M Spread",
    "BAMLH0A0HYM2": "US High Yield Spread",
    "DCOILWTICO": "WTI Spot Oil",
}

YAHOO_MACRO_FALLBACKS = {
    "DGS10": ("^TNX", "US 10Y Treasury"),
    "DGS2": ("2YY=F", "US 2Y Treasury proxy"),
    "T10Y3M": ("^IRX", "US 13W Treasury proxy"),
    "DCOILWTICO": ("CL=F", "WTI Spot Oil proxy"),
}

INTERNAL_SYMBOLS = {
    "S&P 500 ETF": "SPY",
    "Equal Weight S&P 500": "RSP",
    "Nasdaq 100 ETF": "QQQ",
    "Small Caps ETF": "IWM",
    "Semiconductors ETF": "SOXX",
    "Regional Banks ETF": "KRE",
    "High Yield Bond ETF": "HYG",
    "Investment Grade Bond ETF": "LQD",
    "Long Treasury ETF": "TLT",
    "Gold ETF": "GLD",
    "Dollar Bullish ETF": "UUP",
    "Emerging Markets ETF": "EEM",
}

TREND_SYMBOLS = {
    "SPY": "S&P 500 ETF",
    "RSP": "Equal Weight S&P 500",
    "IWM": "Small Caps ETF",
    "HYG": "High Yield Bond ETF",
    "LQD": "Investment Grade Bond ETF",
    "TLT": "Long Treasury ETF",
    "SOXX": "Semiconductors ETF",
    "KRE": "Regional Banks ETF",
    "^VIX": "VIX",
    "DX-Y.NYB": "Dollar Index",
    "^TNX": "US 10Y Yield",
}

SECTOR_SYMBOLS = {
    "Technology": "XLK",
    "Semiconductors": "SMH",
    "Software": "IGV",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Financials": "XLF",
    "Regional Banks": "KRE",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Materials": "XLB",
    "Health Care": "XLV",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Gold Miners": "GDX",
}

VALUATION_SITES = {
    "shiller_pe": ("Shiller CAPE", "https://www.multpl.com/shiller-pe"),
    "sp500_pe": ("S&P 500 P/E", "https://www.multpl.com/s-p-500-pe-ratio"),
    "earnings_yield": ("S&P 500 Earnings Yield", "https://www.multpl.com/s-p-500-earnings-yield"),
    "current_market_valuation": ("Current Market Valuation", "https://www.currentmarketvaluation.com/"),
}

NEWS_FEEDS = {
    "marketwatch_marketpulse": {
        "name": "MarketWatch MarketPulse",
        "url": "https://www.marketwatch.com/rss/marketpulse",
    },
    "marketwatch_bulletins": {
        "name": "MarketWatch Bulletins",
        "url": "https://www.marketwatch.com/rss/bulletins",
    },
    "reuters_markets_google_news": {
        "name": "Reuters Markets via Google News",
        "url": (
            "https://news.google.com/rss/search?"
            "q=site%3Areuters.com%2Fmarkets%20when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"
        ),
    },
}

MARKET_NEWS_KEYWORDS = (
    "stock", "stocks", "market", "markets", "s&p", "nasdaq", "dow", "russell",
    "fed", "rate", "yield", "treasury", "inflation", "cpi", "jobs", "payroll",
    "dollar", "oil", "gold", "tariff", "trade", "earnings", "recession",
    "volatility", "vix", "credit", "bond", "bonds", "ai", "chip", "semiconductor",
)


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_yahoo_quote(symbol: str, range_: str = "5d", interval: str = "1d") -> Dict[str, Any]:
    query = urlencode({"range": range_, "interval": interval})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
    data = get_json(url)
    result = (data.get("chart", {}).get("result") or [{}])[0]
    meta = result.get("meta", {})
    quote = ((result.get("indicators", {}).get("quote") or [{}])[0])
    closes = [c for c in quote.get("close", []) if c is not None]
    current = _safe_float(meta.get("regularMarketPrice"))
    previous = _safe_float(meta.get("chartPreviousClose") or meta.get("previousClose"))
    if current is None and closes:
        current = _safe_float(closes[-1])
    if previous is None and len(closes) >= 2:
        previous = _safe_float(closes[-2])
    change = current - previous if current is not None and previous is not None else None
    change_pct = (change / previous * 100) if change is not None and previous else None
    return {
        "symbol": symbol,
        "name": meta.get("shortName") or meta.get("longName") or symbol,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "price": current,
        "previous_close": previous,
        "change": change,
        "change_pct": change_pct,
        "market_time": meta.get("regularMarketTime"),
        "source": "Yahoo Finance chart API",
    }


def fetch_yahoo_trend_quote(symbol: str, label: str, range_: str) -> Dict[str, Any]:
    query = urlencode({"range": range_, "interval": "1d"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
    data = get_json(url)
    result = (data.get("chart", {}).get("result") or [{}])[0]
    meta = result.get("meta", {})
    quote = ((result.get("indicators", {}).get("quote") or [{}])[0])
    closes = [c for c in quote.get("close", []) if c is not None]
    current = _safe_float(meta.get("regularMarketPrice"))
    if current is None and closes:
        current = _safe_float(closes[-1])
    start = _safe_float(closes[0]) if closes else None
    trend_change = current - start if current is not None and start is not None else None
    trend_change_pct = (trend_change / start * 100) if trend_change is not None and start else None
    return {
        "label": label,
        "symbol": symbol,
        "range": range_,
        "price": current,
        "start_price": start,
        "change": trend_change,
        "change_pct": trend_change_pct,
        "market_time": meta.get("regularMarketTime"),
        "source": "Yahoo Finance chart API",
    }


def collect_market_quotes() -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    jobs: List[Tuple[str, str, str]] = [
        (region, label, symbol)
        for region, items in YAHOO_SYMBOLS.items()
        for label, symbol in items.items()
    ]

    def load(region: str, label: str, symbol: str) -> Tuple[str, Dict[str, Any]]:
        try:
            quote = fetch_yahoo_quote(symbol)
            quote["label"] = label
            return region, quote
        except Exception as exc:
            return region, {"label": label, "symbol": symbol, "error": str(exc)}

    for region in YAHOO_SYMBOLS:
        output[region] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(load, region, label, symbol) for region, label, symbol in jobs]
        for future in as_completed(futures):
            region, row = future.result()
            output[region].append(row)

    for region, items in YAHOO_SYMBOLS.items():
        order = {label: index for index, label in enumerate(items)}
        output[region].sort(key=lambda row: order.get(row.get("label"), len(order)))
    return output


def collect_trend_indicators() -> Dict[str, Any]:
    items: Dict[str, List[Dict[str, Any]]] = {"1mo": [], "3mo": []}

    def load(range_: str, symbol: str, label: str) -> Dict[str, Any]:
        try:
            return fetch_yahoo_trend_quote(symbol, label, range_)
        except Exception as exc:
            return {"label": label, "symbol": symbol, "range": range_, "error": str(exc)}

    jobs = [(range_, symbol, label) for range_ in ("1mo", "3mo") for symbol, label in TREND_SYMBOLS.items()]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(load, range_, symbol, label) for range_, symbol, label in jobs]
        for future in as_completed(futures):
            row = future.result()
            items[row["range"]].append(row)

    for range_ in items:
        order = {label: index for index, label in enumerate(TREND_SYMBOLS.values())}
        items[range_].sort(key=lambda row: order.get(row.get("label"), len(order)))
    return {"source": "Yahoo Finance chart API", "items": items}


def collect_internal_indicators() -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []

    def load(label: str, symbol: str) -> Dict[str, Any]:
        try:
            quote = fetch_yahoo_quote(symbol)
            quote["label"] = label
            return quote
        except Exception as exc:
            return {"label": label, "symbol": symbol, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(load, label, symbol) for label, symbol in INTERNAL_SYMBOLS.items()]
        for future in as_completed(futures):
            rows.append(future.result())

    order = {label: index for index, label in enumerate(INTERNAL_SYMBOLS)}
    rows.sort(key=lambda row: order.get(row.get("label"), len(order)))
    return {"source": "Yahoo Finance chart API", "items": rows}


def collect_sector_indicators() -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []

    def load(label: str, symbol: str) -> Dict[str, Any]:
        try:
            quote = fetch_yahoo_quote(symbol)
            quote["label"] = label
            return quote
        except Exception as exc:
            return {"label": label, "symbol": symbol, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(load, label, symbol) for label, symbol in SECTOR_SYMBOLS.items()]
        for future in as_completed(futures):
            rows.append(future.result())

    order = {label: index for index, label in enumerate(SECTOR_SYMBOLS)}
    rows.sort(key=lambda row: order.get(row.get("label"), len(order)))
    return {"source": "Yahoo Finance chart API", "items": rows}


def _compact_text(raw_html: str, max_chars: int = 1600) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _extract_meta_description(raw_html: str) -> Optional[str]:
    match = re.search(
        r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
        raw_html,
        flags=re.I,
    )
    if not match:
        return None
    return html.unescape(match.group(1)).strip()


def fetch_context_sites() -> Dict[str, Any]:
    sites = {
        "feargreed_kr": "https://feargreed.co.kr/",
        "current_market_valuation": "https://www.currentmarketvaluation.com/",
    }
    snapshots: Dict[str, Any] = {}
    for key, url in sites.items():
        try:
            page = get_text(url, timeout=25)
            snapshots[key] = {
                "url": url,
                "ok": True,
                "excerpt": _compact_text(page),
            }
        except Exception as exc:
            snapshots[key] = {"url": url, "ok": False, "error": str(exc)}
    return snapshots


def fetch_valuation_indicators() -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    for key, (label, url) in VALUATION_SITES.items():
        try:
            page = get_text(url, timeout=15)
            description = _extract_meta_description(page)
            value_match = re.search(r"Current [^.]*? is ([0-9,.]+%?)", description or "")
            value = value_match.group(1).rstrip(",") if value_match else None
            output[key] = {
                "label": label,
                "url": url,
                "ok": True,
                "value": value,
                "description": description or _compact_text(page, max_chars=600),
            }
        except Exception as exc:
            output[key] = {"label": label, "url": url, "ok": False, "error": str(exc)}
    return output


def _parse_rss_items(raw_xml: str, source_name: str, limit: int = 8) -> List[Dict[str, Any]]:
    root = ET.fromstring(raw_xml)
    items = []
    for node in root.findall(".//item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        if "Stock Price" in title or re.search(r"\b[A-Z0-9]{2,5}\.[A-Z]{1,3}\b", title):
            continue
        if not any(keyword in title.lower() for keyword in MARKET_NEWS_KEYWORDS):
            continue
        pub_date = (node.findtext("pubDate") or "").strip()
        parsed_date = None
        parsed_dt = None
        if pub_date:
            try:
                parsed_dt = email.utils.parsedate_to_datetime(pub_date)
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=dt.timezone.utc)
                if parsed_dt < dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3):
                    continue
                parsed_date = parsed_dt.isoformat()
            except (TypeError, ValueError):
                parsed_date = None
        items.append(
            {
                "source": source_name,
                "title": html.unescape(re.sub(r"\s+", " ", title)),
                "published": parsed_date or pub_date,
                "link": (node.findtext("link") or "").strip(),
            }
        )
        if len(items) >= limit:
            break
    return items


def fetch_market_news() -> Dict[str, Any]:
    feeds: Dict[str, Any] = {}
    combined: List[Dict[str, Any]] = []
    for key, feed in NEWS_FEEDS.items():
        try:
            raw = get_text(feed["url"], timeout=15)
            items = _parse_rss_items(raw, feed["name"])
            feeds[key] = {"ok": True, "name": feed["name"], "url": feed["url"], "items": items}
            combined.extend(items)
        except Exception as exc:
            feeds[key] = {"ok": False, "name": feed["name"], "url": feed["url"], "error": str(exc)}
    return {"feeds": feeds, "items": combined[:12]}


def fetch_fear_greed_api() -> Dict[str, Any]:
    url = "https://feargree-api.vercel.app/api"
    try:
        data = get_json(url, timeout=25)
        return {"url": url, "ok": True, "data": data}
    except Exception as exc:
        return {"url": url, "ok": False, "error": str(exc)}


def fetch_fred_series(series_id: str) -> Dict[str, Any]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    raw = get_text(url, timeout=7, retries=0)
    rows = list(csv.DictReader(io.StringIO(raw)))
    for row in reversed(rows):
        value = row.get(series_id)
        if value and value != ".":
            return {
                "series_id": series_id,
                "date": row.get("observation_date"),
                "value": _safe_float(value),
                "source": "FRED",
            }
    raise RuntimeError(f"No observation found for {series_id}")


def fetch_macro_series(series_id: str, label: str) -> Dict[str, Any]:
    try:
        item = fetch_fred_series(series_id)
        item["label"] = label
        return item
    except Exception as fred_exc:
        fallback = YAHOO_MACRO_FALLBACKS.get(series_id)
        if not fallback:
            return {"label": label, "series_id": series_id, "error": str(fred_exc)}
        symbol, fallback_label = fallback
        try:
            quote = fetch_yahoo_quote(symbol)
            return {
                "series_id": series_id,
                "label": label,
                "date": None,
                "value": quote.get("price"),
                "source": f"Yahoo Finance proxy ({fallback_label}, {symbol})",
                "fallback_reason": str(fred_exc),
            }
        except Exception as yahoo_exc:
            return {
                "label": label,
                "series_id": series_id,
                "error": f"FRED failed: {fred_exc}; Yahoo fallback failed: {yahoo_exc}",
            }


def collect_macro_indicators() -> Dict[str, Any]:
    output: Dict[str, Any] = {}

    def load(series_id: str, label: str) -> Dict[str, Any]:
        return fetch_macro_series(series_id, label)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(load, series_id, label): series_id
            for series_id, label in FRED_SERIES.items()
        }
        for future in as_completed(futures):
            output[futures[future]] = future.result()
    _fill_computed_spreads(output)
    return output


def _fill_computed_spreads(output: Dict[str, Any]) -> None:
    dgs10 = output.get("DGS10", {}).get("value")
    dgs2 = output.get("DGS2", {}).get("value")
    t3m = output.get("T10Y3M", {}).get("value")
    if (
        output.get("T10Y2Y", {}).get("error")
        and isinstance(dgs10, (int, float))
        and isinstance(dgs2, (int, float))
    ):
        output["T10Y2Y"] = {
            "series_id": "T10Y2Y",
            "label": FRED_SERIES["T10Y2Y"],
            "date": None,
            "value": dgs10 - dgs2,
            "source": "Computed from Yahoo Finance proxies",
        }
    if (
        output.get("T10Y3M", {}).get("source", "").startswith("Yahoo")
        and isinstance(dgs10, (int, float))
        and isinstance(t3m, (int, float))
    ):
        output["T10Y3M"] = {
            "series_id": "T10Y3M",
            "label": FRED_SERIES["T10Y3M"],
            "date": None,
            "value": dgs10 - t3m,
            "source": "Computed from Yahoo Finance proxies",
        }


def collect_all() -> Dict[str, Any]:
    from .config import REPORT_TIMEZONE

    now = dt.datetime.now(REPORT_TIMEZONE)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "markets": collect_market_quotes(),
        "internals": collect_internal_indicators(),
        "trend": collect_trend_indicators(),
        "sectors": collect_sector_indicators(),
        "macro": collect_macro_indicators(),
        "valuation": fetch_valuation_indicators(),
        "fear_greed": fetch_fear_greed_api(),
        "news": fetch_market_news(),
        "context_sites": fetch_context_sites(),
    }
