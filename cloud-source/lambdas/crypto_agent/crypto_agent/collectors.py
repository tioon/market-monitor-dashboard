import datetime as dt
import csv
import io
import email.utils
import html
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from .http import get_json, get_text, urlencode


COINGECKO_IDS = {
    "bitcoin": "Bitcoin",
    "ethereum": "Ethereum",
    "solana": "Solana",
    "ripple": "XRP",
    "binancecoin": "BNB",
    "chainlink": "Chainlink",
    "avalanche-2": "Avalanche",
    "sui": "Sui",
    "dogecoin": "Dogecoin",
    "cardano": "Cardano",
}

BINANCE_FUTURES_SYMBOLS = {
    "BTCUSDT": "BTC Perp",
    "ETHUSDT": "ETH Perp",
    "SOLUSDT": "SOL Perp",
}

GLASSNODE_METRICS = {
    "btc_mvrv": {
        "label": "BTC MVRV Ratio",
        "path": "/v1/metrics/market/mvrv",
        "asset": "BTC",
    },
    "btc_mvrv_z_score": {
        "label": "BTC MVRV Z-Score",
        "path": "/v1/metrics/market/mvrv_z_score",
        "asset": "BTC",
    },
    "eth_mvrv": {
        "label": "ETH MVRV Ratio",
        "path": "/v1/metrics/market/mvrv",
        "asset": "ETH",
    },
}

CRYPTO_NEWS_FEEDS = {
    "coindesk": {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    },
    "cointelegraph": {
        "name": "Cointelegraph",
        "url": "https://cointelegraph.com/rss",
    },
    "crypto_google_news": {
        "name": "Crypto Google News",
        "url": (
            "https://news.google.com/rss/search?"
            "q=(bitcoin%20OR%20ethereum%20OR%20crypto)%20market%20when%3A1d"
            "&hl=en-US&gl=US&ceid=US%3Aen"
        ),
    },
}

CRYPTO_NEWS_KEYWORDS = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency",
    "stablecoin", "etf", "spot etf", "sec", "fed", "rate", "inflation",
    "exchange", "binance", "coinbase", "solana", "xrp", "altcoin",
    "liquidation", "leverage", "defi", "token",
)

MARKET_CONTEXT_SYMBOLS = {
    "US": {
        "S&P 500": "^GSPC",
        "S&P 500 ETF": "SPY",
        "Equal Weight S&P 500": "RSP",
        "Nasdaq": "^IXIC",
        "Small Caps ETF": "IWM",
        "Russell 2000": "^RUT",
        "VIX": "^VIX",
        "US 10Y Yield": "^TNX",
        "Dollar Index": "DX-Y.NYB",
        "High Yield Bond ETF": "HYG",
        "Investment Grade Bond ETF": "LQD",
        "Regional Banks ETF": "KRE",
        "Long Treasury ETF": "TLT",
        "Semiconductors ETF": "SMH",
        "Gold ETF": "GLD",
        "Dollar Bullish ETF": "UUP",
    }
}

TREND_SYMBOLS = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
    "SPY": "S&P 500 ETF",
    "RSP": "Equal Weight S&P 500",
    "IWM": "Small Caps ETF",
    "HYG": "High Yield Bond ETF",
    "LQD": "Investment Grade Bond ETF",
    "^VIX": "VIX",
    "DX-Y.NYB": "Dollar Index",
    "^TNX": "US 10Y Yield",
}

CRYPTO_TECHNICAL_SYMBOLS = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
}

COINMETRICS_ONCHAIN_METRICS = {
    "btc": {
        "label": "Bitcoin",
        "metrics": ["AdrActCnt", "TxCnt", "FeeTotNtv", "HashRate", "SplyCur"],
    },
    "eth": {
        "label": "Ethereum",
        "metrics": ["AdrActCnt", "TxCnt", "FeeTotNtv"],
    },
}

MARKET_CONTEXT_FRED_SERIES = {
    "DGS10": "US 10Y Treasury",
    "DGS2": "US 2Y Treasury",
    "T10Y2Y": "US 10Y-2Y Spread",
    "BAMLH0A0HYM2": "US High Yield Spread",
}

MARKET_CONTEXT_FRED_FALLBACKS = {
    "DGS10": ("^TNX", "US 10Y Treasury"),
    "DGS2": ("2YY=F", "US 2Y Treasury proxy"),
}

MARKET_CONTEXT_NEWS_FEEDS = {
    "marketwatch": {
        "name": "MarketWatch Bulletins",
        "url": "https://www.marketwatch.com/rss/bulletins",
    },
    "reuters_markets": {
        "name": "Reuters Markets via Google News",
        "url": (
            "https://news.google.com/rss/search?"
            "q=site%3Areuters.com%2Fmarkets%20when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"
        ),
    },
}

MARKET_CONTEXT_KEYWORDS = (
    "stock", "stocks", "market", "markets", "vix", "yield", "treasury", "inflation",
    "fed", "rate", "dollar", "oil", "gold", "bond", "credit", "chip", "semiconductor",
)


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _compact_text(raw_html: str, max_chars: int = 1600) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous * 100


def fetch_yahoo_quote(symbol: str, range_: str = "5d", interval: str = "1d") -> Dict[str, Any]:
    query = urlencode({"range": range_, "interval": interval})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
    data = get_json(url, timeout=20)
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
    data = get_json(url, timeout=20)
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


def _sma(values: List[float], window: int) -> Optional[float]:
    if window <= 0 or len(values) < window:
        return None
    segment = values[-window:]
    return sum(segment) / window


def _rsi(values: List[float], window: int = 14) -> Optional[float]:
    if window <= 0 or len(values) <= window:
        return None
    deltas = [values[idx] - values[idx - 1] for idx in range(1, len(values))]
    gains = [max(delta, 0.0) for delta in deltas[:window]]
    losses = [abs(min(delta, 0.0)) for delta in deltas[:window]]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    for delta in deltas[window:]:
        gain = max(delta, 0.0)
        loss = abs(min(delta, 0.0))
        avg_gain = ((avg_gain * (window - 1)) + gain) / window
        avg_loss = ((avg_loss * (window - 1)) + loss) / window
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _volatility(values: List[float], window: int = 20) -> Optional[float]:
    if window <= 1 or len(values) <= window:
        return None
    returns = []
    for idx in range(-window + 1, 0):
        prev = values[idx - 1]
        current = values[idx]
        if prev:
            returns.append((current - prev) / prev)
    if len(returns) <= 1:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((ret - mean) ** 2 for ret in returns) / len(returns)
    return (variance ** 0.5) * (365 ** 0.5) * 100


def _fetch_yahoo_chart_series(symbol: str, label: str, range_: str = "1y", interval: str = "1d") -> Dict[str, Any]:
    query = urlencode({"range": range_, "interval": interval})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
    data = get_json(url, timeout=20)
    result = (data.get("chart", {}).get("result") or [{}])[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators", {}).get("quote") or [{}])[0])
    closes = [float(c) for c in quote.get("close", []) if c is not None]
    volumes = [float(v) for v in quote.get("volume", []) if v is not None]
    current = _safe_float(meta.get("regularMarketPrice"))
    if current is None and closes:
        current = closes[-1]
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    rsi14 = _rsi(closes, 14)
    vol20 = _volatility(closes, 20)
    high_52w = max(closes) if closes else None
    low_52w = min(closes) if closes else None

    def pct_distance(price: Optional[float], base: Optional[float]) -> Optional[float]:
        if price in (None, 0) or base in (None, 0):
            return None
        return (price - base) / base * 100

    def above(price: Optional[float], level: Optional[float]) -> Optional[bool]:
        if price is None or level is None:
            return None
        return price >= level

    stack_bullish = None
    if current is not None and sma20 is not None and sma50 is not None and sma200 is not None:
        stack_bullish = current >= sma20 >= sma50 >= sma200
    trend_state = "중립"
    if stack_bullish and isinstance(rsi14, (int, float)) and 45 <= rsi14 <= 70:
        trend_state = "상승 추세"
    elif current is not None and sma200 is not None and current < sma200 and isinstance(rsi14, (int, float)) and rsi14 < 40:
        trend_state = "약세 추세"
    elif current is not None and sma50 is not None and current >= sma50:
        trend_state = "회복 추세"

    return {
        "label": label,
        "symbol": symbol,
        "range": range_,
        "interval": interval,
        "price": current,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "dist_sma20_pct": pct_distance(current, sma20),
        "dist_sma50_pct": pct_distance(current, sma50),
        "dist_sma200_pct": pct_distance(current, sma200),
        "rsi14": rsi14,
        "volatility20_pct": vol20,
        "above_sma20": above(current, sma20),
        "above_sma50": above(current, sma50),
        "above_sma200": above(current, sma200),
        "stack_bullish": stack_bullish,
        "trend_state": trend_state,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "position_52w_pct": pct_distance(current, low_52w) if current is not None else None,
        "distance_from_high_pct": pct_distance(current, high_52w) if current is not None else None,
        "volume_latest": volumes[-1] if volumes else None,
        "sample_count": len(closes),
        "market_time": meta.get("regularMarketTime"),
        "source": "Yahoo Finance chart API",
    }


def collect_crypto_technical_analysis() -> Dict[str, Any]:
    output: List[Dict[str, Any]] = []

    def load(symbol: str, label: str) -> Dict[str, Any]:
        try:
            return _fetch_yahoo_chart_series(symbol, label)
        except Exception as exc:
            return {"label": label, "symbol": symbol, "error": str(exc), "source": "Yahoo Finance chart API"}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(load, symbol, label) for symbol, label in CRYPTO_TECHNICAL_SYMBOLS.items()]
        for future in as_completed(futures):
            output.append(future.result())
    order = {label: index for index, label in enumerate(CRYPTO_TECHNICAL_SYMBOLS.values())}
    output.sort(key=lambda row: order.get(row.get("label"), len(order)))
    return {"source": "Yahoo Finance chart API", "items": output}


def _fetch_market_context_quote(region: str, label: str, symbol: str) -> Tuple[str, Dict[str, Any]]:
    try:
        quote = fetch_yahoo_quote(symbol)
        quote["label"] = label
        return region, quote
    except Exception as exc:
        return region, {"label": label, "symbol": symbol, "error": str(exc)}


def fetch_market_context_quotes() -> Dict[str, Any]:
    output: Dict[str, List[Dict[str, Any]]] = {region: [] for region in MARKET_CONTEXT_SYMBOLS}
    jobs = [
        (region, label, symbol)
        for region, items in MARKET_CONTEXT_SYMBOLS.items()
        for label, symbol in items.items()
    ]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_fetch_market_context_quote, region, label, symbol) for region, label, symbol in jobs]
        for future in as_completed(futures):
            region, row = future.result()
            output[region].append(row)
    for region, items in MARKET_CONTEXT_SYMBOLS.items():
        order = {label: index for index, label in enumerate(items)}
        output[region].sort(key=lambda row: order.get(row.get("label"), len(order)))
    return {"source": "Yahoo Finance chart API", "items": output}


def _fetch_market_context_macro_series(series_id: str, label: str) -> Dict[str, Any]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        raw = get_text(url, timeout=7, retries=0)
        rows = list(csv.DictReader(io.StringIO(raw)))
        for row in reversed(rows):
            value = row.get(series_id)
            if value and value != ".":
                return {
                    "series_id": series_id,
                    "label": label,
                    "date": row.get("observation_date"),
                    "value": _safe_float(value),
                    "source": "FRED",
                }
        raise RuntimeError(f"No observation found for {series_id}")
    except Exception as fred_exc:
        fallback = MARKET_CONTEXT_FRED_FALLBACKS.get(series_id)
        if not fallback:
            return {"series_id": series_id, "label": label, "error": str(fred_exc)}
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
                "series_id": series_id,
                "label": label,
                "error": f"FRED failed: {fred_exc}; Yahoo fallback failed: {yahoo_exc}",
            }


def fetch_market_context_macro() -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_fetch_market_context_macro_series, series_id, label): series_id
            for series_id, label in MARKET_CONTEXT_FRED_SERIES.items()
        }
        for future in as_completed(futures):
            output[futures[future]] = future.result()
    dgs10 = output.get("DGS10", {}).get("value")
    dgs2 = output.get("DGS2", {}).get("value")
    if output.get("T10Y2Y", {}).get("error") and isinstance(dgs10, (int, float)) and isinstance(dgs2, (int, float)):
        output["T10Y2Y"] = {
            "series_id": "T10Y2Y",
            "label": MARKET_CONTEXT_FRED_SERIES["T10Y2Y"],
            "date": None,
            "value": dgs10 - dgs2,
            "source": "Computed from Yahoo Finance proxies",
        }
    return {"source": "FRED/Yahoo Finance", "items": output}


def _parse_market_context_rss_items(raw_xml: str, source_name: str, limit: int = 6) -> List[Dict[str, Any]]:
    root = ET.fromstring(raw_xml)
    items = []
    for node in root.findall(".//item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        if not any(keyword in title.lower() for keyword in MARKET_CONTEXT_KEYWORDS):
            continue
        pub_date = (node.findtext("pubDate") or "").strip()
        parsed_date = None
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


def fetch_market_context_news() -> Dict[str, Any]:
    feeds: Dict[str, Any] = {}
    combined: List[Dict[str, Any]] = []
    for key, feed in MARKET_CONTEXT_NEWS_FEEDS.items():
        try:
            raw = get_text(feed["url"], timeout=15)
            items = _parse_market_context_rss_items(raw, feed["name"])
            feeds[key] = {"ok": True, "name": feed["name"], "url": feed["url"], "items": items}
            combined.extend(items)
        except Exception as exc:
            feeds[key] = {"ok": False, "name": feed["name"], "url": feed["url"], "error": str(exc)}
    return {"feeds": feeds, "items": combined[:10]}


def fetch_market_context_sites() -> Dict[str, Any]:
    sites = {
        "feargreed_kr": "https://feargreed.co.kr/",
        "current_market_valuation": "https://www.currentmarketvaluation.com/",
    }
    snapshots: Dict[str, Any] = {}
    for key, url in sites.items():
        try:
            page = get_text(url, timeout=20)
            snapshots[key] = {"url": url, "ok": True, "excerpt": _compact_text(page)}
        except Exception as exc:
            snapshots[key] = {"url": url, "ok": False, "error": str(exc)}
    return snapshots


def collect_market_context() -> Dict[str, Any]:
    return {
        "quotes": fetch_market_context_quotes(),
        "macro": fetch_market_context_macro(),
        "news": fetch_market_context_news(),
        "context_sites": fetch_market_context_sites(),
    }


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


def fetch_crypto_markets() -> Dict[str, Any]:
    query = urlencode(
        {
            "vs_currency": "usd",
            "ids": ",".join(COINGECKO_IDS),
            "order": "market_cap_desc",
            "per_page": len(COINGECKO_IDS),
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "1h,24h,7d,30d",
            "locale": "en",
        }
    )
    url = f"https://api.coingecko.com/api/v3/coins/markets?{query}"
    try:
        rows = get_json(url, timeout=25)
        items = []
        for row in rows:
            items.append(
                {
                    "id": row.get("id"),
                    "symbol": str(row.get("symbol", "")).upper(),
                    "label": COINGECKO_IDS.get(row.get("id"), row.get("name")),
                    "price": row.get("current_price"),
                    "market_cap": row.get("market_cap"),
                    "market_cap_rank": row.get("market_cap_rank"),
                    "volume_24h": row.get("total_volume"),
                    "change_pct_1h": row.get("price_change_percentage_1h_in_currency"),
                    "change_pct_24h": row.get("price_change_percentage_24h_in_currency"),
                    "change_pct_7d": row.get("price_change_percentage_7d_in_currency"),
                    "change_pct_30d": row.get("price_change_percentage_30d_in_currency"),
                    "ath_change_pct": row.get("ath_change_percentage"),
                    "last_updated": row.get("last_updated"),
                }
            )
        order = {coin_id: index for index, coin_id in enumerate(COINGECKO_IDS)}
        items.sort(key=lambda item: order.get(item.get("id"), len(order)))
        return {"ok": True, "source": "CoinGecko coins/markets API", "url": url, "items": items}
    except Exception as exc:
        return {"ok": False, "source": "CoinGecko coins/markets API", "url": url, "error": str(exc), "items": []}


def fetch_crypto_global() -> Dict[str, Any]:
    url = "https://api.coingecko.com/api/v3/global"
    try:
        payload = get_json(url, timeout=20)
        data = payload.get("data", {})
        return {
            "ok": True,
            "source": "CoinGecko global API",
            "url": url,
            "total_market_cap_usd": (data.get("total_market_cap") or {}).get("usd"),
            "total_volume_usd": (data.get("total_volume") or {}).get("usd"),
            "market_cap_change_pct_24h_usd": data.get("market_cap_change_percentage_24h_usd"),
            "btc_dominance_pct": (data.get("market_cap_percentage") or {}).get("btc"),
            "eth_dominance_pct": (data.get("market_cap_percentage") or {}).get("eth"),
            "active_cryptocurrencies": data.get("active_cryptocurrencies"),
            "markets": data.get("markets"),
            "updated_at": data.get("updated_at"),
        }
    except Exception as exc:
        return {"ok": False, "source": "CoinGecko global API", "url": url, "error": str(exc)}


def fetch_stablecoin_liquidity() -> Dict[str, Any]:
    url = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
    try:
        payload = get_json(url, timeout=25)
        assets = payload.get("peggedAssets", [])
        total = 0.0
        total_prev_day = 0.0
        total_prev_week = 0.0
        total_prev_month = 0.0
        leaders = []
        for asset in assets:
            current = _safe_float((asset.get("circulating") or {}).get("peggedUSD")) or 0.0
            prev_day = _safe_float((asset.get("circulatingPrevDay") or {}).get("peggedUSD")) or 0.0
            prev_week = _safe_float((asset.get("circulatingPrevWeek") or {}).get("peggedUSD")) or 0.0
            prev_month = _safe_float((asset.get("circulatingPrevMonth") or {}).get("peggedUSD")) or 0.0
            total += current
            total_prev_day += prev_day
            total_prev_week += prev_week
            total_prev_month += prev_month
            if current:
                leaders.append(
                    {
                        "name": asset.get("name"),
                        "symbol": asset.get("symbol"),
                        "circulating_usd": current,
                        "change_pct_7d": _pct_change(current, prev_week),
                        "peg_type": asset.get("pegType"),
                        "peg_mechanism": asset.get("pegMechanism"),
                    }
                )
        leaders.sort(key=lambda item: item.get("circulating_usd") or 0, reverse=True)
        return {
            "ok": True,
            "source": "DefiLlama stablecoins API",
            "url": url,
            "total_circulating_usd": total,
            "change_pct_1d": _pct_change(total, total_prev_day),
            "change_pct_7d": _pct_change(total, total_prev_week),
            "change_pct_30d": _pct_change(total, total_prev_month),
            "leaders": leaders[:5],
        }
    except Exception as exc:
        return {"ok": False, "source": "DefiLlama stablecoins API", "url": url, "error": str(exc)}


def _fetch_binance_symbol(symbol: str, label: str) -> Dict[str, Any]:
    base = "https://fapi.binance.com"
    try:
        funding_rows = get_json(f"{base}/fapi/v1/fundingRate?{urlencode({'symbol': symbol, 'limit': 8})}", timeout=15)
        oi_rows = get_json(
            f"{base}/futures/data/openInterestHist?{urlencode({'symbol': symbol, 'period': '1d', 'limit': 4})}",
            timeout=15,
        )
        ls_rows = get_json(
            f"{base}/futures/data/globalLongShortAccountRatio?{urlencode({'symbol': symbol, 'period': '1d', 'limit': 4})}",
            timeout=15,
        )
        latest_funding = funding_rows[-1] if funding_rows else {}
        latest_oi = oi_rows[-1] if oi_rows else {}
        first_oi = oi_rows[0] if oi_rows else {}
        latest_ls = ls_rows[-1] if ls_rows else {}
        oi_value = _safe_float(latest_oi.get("sumOpenInterestValue"))
        first_oi_value = _safe_float(first_oi.get("sumOpenInterestValue"))
        funding_rate = _safe_float(latest_funding.get("fundingRate"))
        return {
            "ok": True,
            "symbol": symbol,
            "label": label,
            "source": "Binance USD-M Futures public API",
            "funding_rate": funding_rate,
            "funding_rate_pct_8h": funding_rate * 100 if funding_rate is not None else None,
            "funding_rate_annualized_pct": funding_rate * 3 * 365 * 100 if funding_rate is not None else None,
            "mark_price": _safe_float(latest_funding.get("markPrice")),
            "funding_time": latest_funding.get("fundingTime"),
            "open_interest_value_usd": oi_value,
            "open_interest_value_change_pct_3d": _pct_change(oi_value, first_oi_value),
            "long_short_ratio": _safe_float(latest_ls.get("longShortRatio")),
            "long_account_pct": (_safe_float(latest_ls.get("longAccount")) or 0) * 100 if latest_ls else None,
            "short_account_pct": (_safe_float(latest_ls.get("shortAccount")) or 0) * 100 if latest_ls else None,
            "timestamp": latest_oi.get("timestamp") or latest_ls.get("timestamp"),
        }
    except Exception as exc:
        return {"ok": False, "symbol": symbol, "label": label, "source": "Binance USD-M Futures public API", "error": str(exc)}


def fetch_derivatives_indicators() -> Dict[str, Any]:
    items: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_fetch_binance_symbol, symbol, label): symbol
            for symbol, label in BINANCE_FUTURES_SYMBOLS.items()
        }
        for future in as_completed(futures):
            items[futures[future]] = future.result()
    return {"ok": any(item.get("ok") for item in items.values()), "source": "Binance USD-M Futures public API", "items": items}


def fetch_fear_greed() -> Dict[str, Any]:
    url = "https://api.alternative.me/fng/?limit=7&format=json"
    try:
        payload = get_json(url, timeout=20)
        rows = payload.get("data", [])
        items = []
        for row in rows:
            timestamp = row.get("timestamp")
            date = None
            if timestamp:
                try:
                    date = dt.datetime.fromtimestamp(int(timestamp), dt.timezone.utc).date().isoformat()
                except (TypeError, ValueError, OSError):
                    date = None
            items.append(
                {
                    "value": _safe_float(row.get("value")),
                    "classification": row.get("value_classification"),
                    "timestamp": timestamp,
                    "date": date,
                    "time_until_update": row.get("time_until_update"),
                }
            )
        return {
            "ok": True,
            "source": "Alternative.me Crypto Fear & Greed Index API",
            "url": url,
            "items": items,
        }
    except Exception as exc:
        return {"ok": False, "source": "Alternative.me Crypto Fear & Greed Index API", "url": url, "error": str(exc), "items": []}


def _coinmetrics_metric_rows(asset: str, metrics: List[str], page_size: int = 10) -> List[Dict[str, Any]]:
    query = urlencode(
        {
            "assets": asset,
            "metrics": ",".join(metrics),
            "frequency": "1d",
            "page_size": str(page_size),
        }
    )
    url = f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?{query}"
    payload = get_json(url, timeout=25)
    rows = payload.get("data", [])
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Coin Metrics returned no rows")
    rows = [row for row in rows if row.get("asset") == asset]
    rows.sort(key=lambda row: row.get("time") or "")
    return rows


def _coinmetrics_metric_value(row: Dict[str, Any], metric: str) -> Optional[float]:
    return _safe_float(row.get(metric))


def _coinmetrics_change_pct(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous * 100


def fetch_coinmetrics_onchain() -> Dict[str, Any]:
    items: Dict[str, Any] = {}
    for asset, meta in COINMETRICS_ONCHAIN_METRICS.items():
        try:
            rows = _coinmetrics_metric_rows(asset, meta["metrics"], page_size=10)
            latest = rows[-1]
            prior = rows[-8] if len(rows) >= 8 else rows[0]
            metrics: Dict[str, Any] = {}
            for metric in meta["metrics"]:
                current = _coinmetrics_metric_value(latest, metric)
                previous = _coinmetrics_metric_value(prior, metric)
                metrics[metric] = {
                    "value": current,
                    "previous_value": previous,
                    "change_pct_7d": _coinmetrics_change_pct(current, previous),
                }
            items[asset] = {
                "ok": True,
                "source": "Coin Metrics Community API",
                "label": meta["label"],
                "asset": asset,
                "time": latest.get("time"),
                "metrics": metrics,
            }
        except Exception as exc:
            items[asset] = {
                "ok": False,
                "source": "Coin Metrics Community API",
                "label": meta["label"],
                "asset": asset,
                "error": str(exc),
            }
    return {"ok": any(item.get("ok") for item in items.values()), "source": "Coin Metrics Community API", "items": items}


def _parse_rss_items(raw_xml: str, source_name: str, limit: int = 8) -> List[Dict[str, Any]]:
    root = ET.fromstring(raw_xml)
    items = []
    for node in root.findall(".//item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        if not any(keyword in title.lower() for keyword in CRYPTO_NEWS_KEYWORDS):
            continue
        pub_date = (node.findtext("pubDate") or "").strip()
        parsed_date = None
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


def fetch_crypto_news() -> Dict[str, Any]:
    feeds: Dict[str, Any] = {}
    combined: List[Dict[str, Any]] = []
    for key, feed in CRYPTO_NEWS_FEEDS.items():
        try:
            raw = get_text(feed["url"], timeout=15)
            items = _parse_rss_items(raw, feed["name"])
            feeds[key] = {"ok": True, "name": feed["name"], "url": feed["url"], "items": items}
            combined.extend(items)
        except Exception as exc:
            feeds[key] = {"ok": False, "name": feed["name"], "url": feed["url"], "error": str(exc)}
    return {"feeds": feeds, "items": combined[:12]}


def fetch_crypto_context_sites() -> Dict[str, Any]:
    sites = {
        "alternative_fng": "https://alternative.me/crypto/fear-and-greed-index/",
    }
    snapshots: Dict[str, Any] = {}
    for key, url in sites.items():
        try:
            page = get_text(url, timeout=25)
            snapshots[key] = {"url": url, "ok": True, "excerpt": _compact_text(page)}
        except Exception as exc:
            snapshots[key] = {"url": url, "ok": False, "error": str(exc)}
    return snapshots


def collect_crypto_all() -> Dict[str, Any]:
    from .config import REPORT_TIMEZONE

    now = dt.datetime.now(REPORT_TIMEZONE)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "asset_class": "crypto",
        "markets": fetch_crypto_markets(),
        "global": fetch_crypto_global(),
        "market_context": collect_market_context(),
        "trend": collect_trend_indicators(),
        "technical": collect_crypto_technical_analysis(),
        "stablecoins": fetch_stablecoin_liquidity(),
        "derivatives": fetch_derivatives_indicators(),
        "fear_greed": fetch_fear_greed(),
        "onchain": fetch_coinmetrics_onchain(),
        "news": fetch_crypto_news(),
        "context_sites": fetch_crypto_context_sites(),
    }
