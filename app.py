import os, json, re, time, asyncio, logging, uuid
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.responses import StreamingResponse
import httpx
from bs4 import BeautifulSoup
import uvicorn

# override=True: .env is the source of truth for this personal local
# tool. Inherited shell env vars (e.g. an empty ANTHROPIC_API_KEY from
# a prior session) would otherwise silently shadow the real .env value.
load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Logging — file rotation under ./logs/ (gitignored). Used by the M1/M3
# security-hardened handlers to record full tracebacks keyed by request_id
# without leaking them to the client (BATCH_DESIGN.md §G.3).
# ---------------------------------------------------------------------------
_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_handler = TimedRotatingFileHandler(
    _LOG_DIR / "app.log", when="W0", backupCount=4, encoding="utf-8"
)
_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s [%(name)s] %(message)s"
))
logging.basicConfig(level=logging.INFO, handlers=[_log_handler, logging.StreamHandler()])
logger = logging.getLogger("analyzer")


def _error_envelope(code: str, message: str, request_id: str) -> dict:
    """Uniform error shape per BATCH_DESIGN §B.7. `message` is always generic
    (never str(exc)); the request_id correlates to the server-side traceback."""
    return {"error": {"code": code, "message": message, "request_id": request_id}}


app = FastAPI()

# ---------------------------------------------------------------------------
# Sprint 10A §10-3: security headers + CORS.
# The app runs local-only on 127.0.0.1, but defense-in-depth:
#   - CSP stops any injected script from phoning home (connect-src 'self').
#   - Nosniff + frame DENY + no-referrer close the usual trivial leaks.
#   - CORS whitelist defeats DNS-rebinding attacks: a malicious site can
#     still resolve a name to 127.0.0.1, but the browser will block the
#     fetch because the Origin header won't be in allow_origins.
# 'unsafe-inline' on script-src / style-src is REQUIRED — index.html ships
# all app logic inline (ADR-002 Phase B ESM import is the only external
# script, covered by 'self'). If we ever extract to a CDN or add a nonce
# pipeline, tighten the CSP in the same commit.
# ---------------------------------------------------------------------------
from starlette.middleware.cors import CORSMiddleware  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

_CSP = (
    "default-src 'self'; "
    "img-src 'self' https://*.rdcpix.com https://*.ssl.cdn-redfin.com "
    "https://*.zillowstatic.com data:; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'"
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", _CSP)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# Security audit M2: DNS-rebinding defense. Without this, evil.com that
# A-records to 127.0.0.1 can navigate Jose's browser to the app as a
# same-origin page. CORS won't help because the browser sees same origin.
# TrustedHost rejects any Host header not in the allowlist before routing.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1"],
)


# Ensure ./data/ directory exists and the schema is applied before any route
# can hit it. Idempotent — no-op on subsequent starts.
try:
    from scripts.init_db import init_db as _init_db
    _init_db()
except Exception as _db_exc:  # pragma: no cover - startup diagnostic
    logger.exception("DB init failed: %s", _db_exc)


# ---------------------------------------------------------------------------
# Playwright browser pool (Sprint 8-1)
#
# Each ``p.chromium.launch()`` spends 800ms-1.5s on cold start. On a 30-URL
# batch with 10 cache-miss rent-comp ZIPs, that's ~10-15s of pure launch
# overhead. We keep a single, process-lifetime browser around and mint a
# fresh ``new_context()`` (≈50ms) per call — contexts are isolated so the
# shared browser doesn't leak cookies or state across URLs. The existing
# ``_search_semaphore(3)`` still bounds concurrency; the pool only removes
# the launch cost, not the concurrency cap.
#
# Lazy init (NOT module-import) because cold-start matters: we only pay
# the first ~1.5s launch the first time a caller actually needs a browser.
# ---------------------------------------------------------------------------
_PLAYWRIGHT_BROWSER = None  # type: ignore[var-annotated]
_PLAYWRIGHT_HANDLE = None  # type: ignore[var-annotated]
_PLAYWRIGHT_BROWSER_LOCK = asyncio.Lock()


async def _get_browser():
    """Return a ready-to-use Playwright ``Browser``.

    Double-checked locking: the fast path is a plain global read, and the
    lock is only held across the launch itself. If the browser died
    (crash, OOM, external `pkill`) we detect via ``is_connected()`` and
    relaunch exactly once.
    """
    global _PLAYWRIGHT_BROWSER, _PLAYWRIGHT_HANDLE
    b = _PLAYWRIGHT_BROWSER
    if b is not None and b.is_connected():
        return b
    async with _PLAYWRIGHT_BROWSER_LOCK:
        b = _PLAYWRIGHT_BROWSER
        if b is not None and b.is_connected():
            return b
        # Either first-time init or the previous browser died. Clean up any
        # dead handle before relaunching so the playwright driver doesn't
        # hold a zombie process.
        if _PLAYWRIGHT_HANDLE is not None:
            try:
                await _PLAYWRIGHT_HANDLE.stop()
            except Exception:  # pragma: no cover - shutdown noise
                pass
            _PLAYWRIGHT_HANDLE = None
        from playwright.async_api import async_playwright
        _PLAYWRIGHT_HANDLE = await async_playwright().start()
        _PLAYWRIGHT_BROWSER = await _PLAYWRIGHT_HANDLE.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        return _PLAYWRIGHT_BROWSER


async def _get_browser_context(**context_kwargs):
    """Return ``(context, browser)`` with one retry if the shared browser
    died between the ``_get_browser`` readiness check and ``new_context``.

    This closes the tight race Perf Benchmarker flagged: ``is_connected()``
    returns True, then chromium OOMs, then ``new_context()`` raises. The
    caller gets a clean retry instead of a hard fail on a single URL.
    """
    for attempt in (0, 1):
        browser = await _get_browser()
        try:
            context = await browser.new_context(**context_kwargs)
            return context, browser
        except Exception:
            # Force a relaunch on next _get_browser() call by nulling the
            # handle. The double-checked lock inside _get_browser will
            # re-initialize. Re-raise on the second failure so the caller
            # sees a real error instead of spinning forever.
            global _PLAYWRIGHT_BROWSER
            _PLAYWRIGHT_BROWSER = None
            if attempt == 1:
                raise
    raise RuntimeError("unreachable")


async def _shutdown_browser_pool():
    """Close the shared browser + stop the driver. Idempotent."""
    global _PLAYWRIGHT_BROWSER, _PLAYWRIGHT_HANDLE
    try:
        if _PLAYWRIGHT_BROWSER is not None:
            try:
                await _PLAYWRIGHT_BROWSER.close()
            except Exception:  # pragma: no cover - shutdown noise
                pass
        if _PLAYWRIGHT_HANDLE is not None:
            try:
                await _PLAYWRIGHT_HANDLE.stop()
            except Exception:  # pragma: no cover - shutdown noise
                pass
    finally:
        _PLAYWRIGHT_BROWSER = None
        _PLAYWRIGHT_HANDLE = None


# ---------------------------------------------------------------------------
# Rate Limiter (in-memory, per-IP)
# ---------------------------------------------------------------------------
_rate_limits: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(ip: str, limit: int, window: int = 60) -> bool:
    """Return True if the request is within rate limits."""
    now = time.time()
    timestamps = _rate_limits[ip]
    # Prune old entries
    _rate_limits[ip] = [t for t in timestamps if now - t < window]
    if len(_rate_limits[ip]) >= limit:
        return False
    _rate_limits[ip].append(now)
    return True

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_get(obj, *keys, default=None):
    """Safely traverse nested dicts/lists."""
    current = obj
    for key in keys:
        try:
            if isinstance(current, dict):
                current = current[key]
            elif isinstance(current, (list, tuple)):
                current = current[int(key)]
            else:
                return default
        except (KeyError, IndexError, TypeError, ValueError):
            return default
    return current


def _format_address(addr_obj):
    """Build a single-line address from Zillow address dict."""
    if not addr_obj or not isinstance(addr_obj, dict):
        return None
    parts = [
        addr_obj.get("streetAddress", ""),
        addr_obj.get("city", ""),
    ]
    state = addr_obj.get("state", "")
    zipcode = addr_obj.get("zipcode", "")
    state_zip = f"{state} {zipcode}".strip()
    line = ", ".join(p for p in parts if p)
    if state_zip:
        line = f"{line}, {state_zip}" if line else state_zip
    return line or None


def _extract_tax_history(raw_history):
    """Normalise Zillow taxHistory array."""
    if not raw_history or not isinstance(raw_history, list):
        return []
    result = []
    for entry in raw_history:
        if not isinstance(entry, dict):
            continue
        year = entry.get("time") or entry.get("year")
        amount = entry.get("taxPaid") or entry.get("amount")
        # 'time' is sometimes an epoch-ms; convert to year
        if isinstance(year, (int, float)) and year > 3000:
            from datetime import datetime, timezone
            try:
                year = datetime.fromtimestamp(year / 1000, tz=timezone.utc).year
            except Exception:
                pass
        if year is not None:
            result.append({"year": int(year) if year else None, "amount": amount})
    return result


def _get_image_url(prop):
    """Extract a representative image URL."""
    url = prop.get("hiResImageLink")
    if url:
        return url
    photos = prop.get("responsivePhotos") or prop.get("photos") or []
    if photos and isinstance(photos, list):
        first = photos[0]
        if isinstance(first, dict):
            # Try multiple known sub-paths
            for subkey in ("mixedSources", "sources"):
                sources = first.get(subkey)
                if sources and isinstance(sources, dict):
                    for quality in ("jpeg", "webp", "png"):
                        imgs = sources.get(quality)
                        if imgs and isinstance(imgs, list):
                            # pick the largest
                            best = max(imgs, key=lambda x: x.get("width", 0) if isinstance(x, dict) else 0)
                            if isinstance(best, dict) and best.get("url"):
                                return best["url"]
            # Direct url on photo object
            if first.get("url"):
                return first["url"]
    return None


def _build_result(prop):
    """Build the flat result dict from a Zillow property dict."""
    tax_history = _extract_tax_history(prop.get("taxHistory"))
    annual_tax = None
    if tax_history:
        annual_tax = tax_history[0].get("amount")

    lot_size = prop.get("lotSize") or prop.get("lotAreaValue")
    # lotSize sometimes comes as a string like "6,000 sqft"
    if isinstance(lot_size, str):
        nums = re.findall(r"[\d,]+", lot_size)
        if nums:
            try:
                lot_size = int(nums[0].replace(",", ""))
            except ValueError:
                lot_size = None

    return {
        "address": _format_address(prop.get("address")),
        "price": prop.get("price") or prop.get("listPrice"),
        "beds": prop.get("bedrooms"),
        "baths": prop.get("bathrooms"),
        "sqft": prop.get("livingArea"),
        "lotSize": lot_size,
        "yearBuilt": prop.get("yearBuilt"),
        "propertyType": prop.get("homeType"),
        "zestimate": prop.get("zestimate"),
        "rentZestimate": prop.get("rentZestimate"),
        "taxHistory": tax_history,
        "annualTax": annual_tax,
        "hoaFee": prop.get("monthlyHoaFee") or 0,
        "description": prop.get("description"),
        "imageUrl": _get_image_url(prop),
    }


# ---------------------------------------------------------------------------
# Extraction strategies
# ---------------------------------------------------------------------------

def _extract_from_next_data(soup):
    """Primary: parse __NEXT_DATA__ -> gdpClientCache / apiCache."""
    script_tag = soup.find("script", id="__NEXT_DATA__")
    if not script_tag or not script_tag.string:
        return None

    try:
        next_data = json.loads(script_tag.string)
    except (json.JSONDecodeError, TypeError):
        return None

    # Strategy A: gdpClientCache (most common)
    gdp_cache = _safe_get(next_data, "props", "pageProps", "gdpClientCache")
    if gdp_cache and isinstance(gdp_cache, (dict, str)):
        # gdpClientCache may itself be a JSON string
        if isinstance(gdp_cache, str):
            try:
                gdp_cache = json.loads(gdp_cache)
            except json.JSONDecodeError:
                gdp_cache = {}

        if isinstance(gdp_cache, dict):
            for _key, value in gdp_cache.items():
                # Each value is often a stringified JSON blob
                parsed = value
                if isinstance(value, str):
                    try:
                        parsed = json.loads(value)
                    except json.JSONDecodeError:
                        continue

                # Look for property data
                prop = None
                if isinstance(parsed, dict):
                    prop = parsed.get("property")
                    if not prop:
                        # Sometimes nested under data -> property
                        prop = _safe_get(parsed, "data", "property")
                if prop and isinstance(prop, dict):
                    return _build_result(prop)

    # Strategy B: apiCache
    api_cache = _safe_get(next_data, "props", "pageProps", "apiCache")
    if api_cache and isinstance(api_cache, (dict, str)):
        if isinstance(api_cache, str):
            try:
                api_cache = json.loads(api_cache)
            except json.JSONDecodeError:
                api_cache = {}

        if isinstance(api_cache, dict):
            for _key, value in api_cache.items():
                parsed = value
                if isinstance(value, str):
                    try:
                        parsed = json.loads(value)
                    except json.JSONDecodeError:
                        continue
                if isinstance(parsed, dict):
                    prop = parsed.get("property")
                    if not prop:
                        prop = _safe_get(parsed, "data", "property")
                    if prop and isinstance(prop, dict):
                        return _build_result(prop)

    # Strategy C: direct pageProps.property (newer layouts)
    prop = _safe_get(next_data, "props", "pageProps", "property")
    if prop and isinstance(prop, dict) and (prop.get("address") or prop.get("price")):
        return _build_result(prop)

    # Strategy D: componentProps (may contain its own gdpClientCache)
    comp_props = _safe_get(next_data, "props", "pageProps", "componentProps")
    if comp_props and isinstance(comp_props, dict):
        # D1: direct property on componentProps values
        for _key, value in comp_props.items():
            if isinstance(value, dict):
                prop = value.get("property")
                if prop and isinstance(prop, dict):
                    return _build_result(prop)

        # D2: gdpClientCache nested inside componentProps
        gdp_nested = comp_props.get("gdpClientCache")
        if gdp_nested:
            if isinstance(gdp_nested, str):
                try:
                    gdp_nested = json.loads(gdp_nested)
                except json.JSONDecodeError:
                    gdp_nested = {}
            if isinstance(gdp_nested, dict):
                for _key, value in gdp_nested.items():
                    parsed = value
                    if isinstance(value, str):
                        try:
                            parsed = json.loads(value)
                        except json.JSONDecodeError:
                            continue
                    if isinstance(parsed, dict):
                        prop = parsed.get("property")
                        if not prop:
                            prop = _safe_get(parsed, "data", "property")
                        if prop and isinstance(prop, dict):
                            return _build_result(prop)

    return None


def _extract_from_ld_json(soup):
    """Fallback: parse application/ld+json structured data."""
    ld_scripts = soup.find_all("script", type="application/ld+json")
    for tag in ld_scripts:
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except (json.JSONDecodeError, TypeError):
            continue

        # Can be a list or single object
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type", "")
            if item_type in ("SingleFamilyResidence", "Residence", "Product", "House", "Apartment"):
                # ld+json has a different shape; map what we can
                address_obj = item.get("address", {})
                if isinstance(address_obj, dict):
                    addr = {
                        "streetAddress": address_obj.get("streetAddress", ""),
                        "city": address_obj.get("addressLocality", ""),
                        "state": address_obj.get("addressRegion", ""),
                        "zipcode": address_obj.get("postalCode", ""),
                    }
                else:
                    addr = None

                floor_size = item.get("floorSize", {})
                sqft = None
                if isinstance(floor_size, dict):
                    sqft = floor_size.get("value")
                elif isinstance(floor_size, (int, float)):
                    sqft = floor_size

                price = None
                offers = item.get("offers", {})
                if isinstance(offers, dict):
                    price = offers.get("price")
                if not price:
                    price = item.get("price")

                return {
                    "address": _format_address(addr) if addr else item.get("name"),
                    "price": price,
                    "beds": item.get("numberOfRooms") or item.get("bedrooms"),
                    "baths": item.get("bathrooms"),
                    "sqft": sqft,
                    "lotSize": None,
                    "yearBuilt": item.get("yearBuilt"),
                    "propertyType": item_type,
                    "zestimate": None,
                    "rentZestimate": None,
                    "taxHistory": [],
                    "annualTax": None,
                    "hoaFee": 0,
                    "description": item.get("description"),
                    "imageUrl": item.get("image"),
                }
    return None


def _extract_from_dom(soup):
    """Fallback: extract property data from rendered DOM elements and meta tags."""
    result = {
        "address": None, "price": None, "beds": None, "baths": None,
        "sqft": None, "lotSize": None, "yearBuilt": None, "propertyType": None,
        "zestimate": None, "rentZestimate": None, "taxHistory": [],
        "annualTax": None, "hoaFee": 0, "description": None, "imageUrl": None,
    }

    # Try og:title for address
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        result["address"] = og_title["content"].split("|")[0].strip()

    # Try og:image
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        result["imageUrl"] = og_image["content"]

    # Try meta description for details
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        desc = meta_desc["content"]
        result["description"] = desc

        # Parse common patterns like "$350,000 - 3 bed, 2 bath, 1,500 sqft"
        price_m = re.search(r"\$[\d,]+", desc)
        if price_m:
            try:
                result["price"] = int(price_m.group().replace("$", "").replace(",", ""))
            except ValueError:
                pass

        beds_m = re.search(r"(\d+)\s*(?:bed|br)", desc, re.IGNORECASE)
        if beds_m:
            result["beds"] = int(beds_m.group(1))

        baths_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:bath|ba)", desc, re.IGNORECASE)
        if baths_m:
            result["baths"] = float(baths_m.group(1))

        sqft_m = re.search(r"([\d,]+)\s*(?:sq\s*ft|sqft)", desc, re.IGNORECASE)
        if sqft_m:
            try:
                result["sqft"] = int(sqft_m.group(1).replace(",", ""))
            except ValueError:
                pass

    # Search for JSON-like data blobs in script tags (Zillow often embeds property
    # data in various script tags beyond __NEXT_DATA__)
    for script in soup.find_all("script"):
        text = script.string or ""
        if not text or len(text) < 100:
            continue

        # Look for common Zillow data patterns
        for pattern in [r'"price"\s*:\s*(\d+)', r'"listPrice"\s*:\s*(\d+)']:
            m = re.search(pattern, text)
            if m and not result["price"]:
                try:
                    result["price"] = int(m.group(1))
                except ValueError:
                    pass

        if not result["beds"]:
            m = re.search(r'"bedrooms"\s*:\s*(\d+)', text)
            if m:
                result["beds"] = int(m.group(1))

        if not result["baths"]:
            m = re.search(r'"bathrooms"\s*:\s*([\d.]+)', text)
            if m:
                result["baths"] = float(m.group(1))

        if not result["sqft"]:
            m = re.search(r'"livingArea"\s*:\s*(\d+)', text)
            if m:
                result["sqft"] = int(m.group(1))

        if not result["yearBuilt"]:
            m = re.search(r'"yearBuilt"\s*:\s*(\d{4})', text)
            if m:
                result["yearBuilt"] = int(m.group(1))

        if not result["zestimate"]:
            m = re.search(r'"zestimate"\s*:\s*(\d+)', text)
            if m:
                result["zestimate"] = int(m.group(1))

        if not result["rentZestimate"]:
            m = re.search(r'"rentZestimate"\s*:\s*(\d+)', text)
            if m:
                result["rentZestimate"] = int(m.group(1))

    # Only return if we found at least an address or price
    if result["address"] or result["price"]:
        return result

    return None


# ---------------------------------------------------------------------------
# Playwright fallback fetcher
# ---------------------------------------------------------------------------

async def _fetch_with_playwright(url: str) -> str:
    """Use a headless browser to fetch the page (bypasses bot detection).

    Sprint 8-1: reuses the shared process-lifetime browser pool. Each call
    still mints a fresh ``new_context()`` so cookies / storage don't leak
    between URLs.
    """
    context, _ = await _get_browser_context(
        user_agent=HEADERS["User-Agent"],
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
    )
    try:
        page = await context.new_page()

        # Remove webdriver flag to avoid bot detection
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # Wait for JS to populate data (Zillow is heavily JS-rendered)
        await page.wait_for_timeout(3000)

        # Try scrolling to trigger lazy-loaded content
        await page.evaluate("window.scrollBy(0, 300)")
        await page.wait_for_timeout(1000)

        html = await page.content()
    finally:
        try:
            await context.close()
        except Exception:  # pragma: no cover - defensive
            pass
    return html


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

IS_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT"))


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html = Path("index.html").read_text(encoding="utf-8")
    if IS_CLOUD:
        # Inject flag so frontend can disable scraping-dependent features
        # Sprint 10-6: cloudDemo now lives on the RPDA namespace. Guard the
        # init so load order against the inline/module scripts doesn't matter.
        html = html.replace("</head>", '<script>window.RPDA=window.RPDA||{};window.RPDA.cloudDemo=true;</script></head>')
    return html


@app.get("/calc.js")
async def serve_calc_js():
    """ADR-002 Phase B: serve the pure-calc ESM for browser imports."""
    path = Path(__file__).parent / "calc.js"
    if not path.exists():
        return JSONResponse({"error": "calc.js not found"}, status_code=500)
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="application/javascript",
    )


@app.get("/spec/constants.json")
async def serve_spec_constants():
    """ADR-002: expose the shared PUBLIC constants file to the browser runtime.
    Hard-fail (500) on missing file — silent fallback is the drift mode
    this route was created to kill.

    Sprint 10A §10-2: this route ONLY serves public data (FHA rates,
    TOPSIS weights, ZIP tiers, etc.). Private fields (W-2 income, credit
    score, Jose thresholds) live in spec/profile.local.json and are served
    by /spec/profile.json with a 127.0.0.1-only gate.
    """
    # Anchor to this file's dir so a non-root cwd (systemd unit, container)
    # doesn't diverge from spec/__init__.py's Path(__file__).parent resolution.
    path = Path(__file__).parent / "spec" / "constants.json"
    if not path.exists():
        return JSONResponse({"error": "spec not found"}, status_code=500)
    try:
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        # Sprint 10A §10-5: don't leak parse-error detail to client.
        rid = uuid.uuid4().hex
        logger.exception("spec/constants.json malformed (request_id=%s)", rid)
        return JSONResponse(
            _error_envelope("SPEC_MALFORMED", "Spec malformed.", rid),
            status_code=500,
        )


@app.get("/spec/profile.json")
async def serve_spec_profile(request: Request):
    """Sprint 10A §10-2: serve PRIVATE profile data (W-2 income, credit
    score, Jose thresholds) ONLY to requests originating from 127.0.0.1.

    Tailscale IPs, LAN IPs, and anything else get a 403. Rationale: even if
    Jose later exposes the port through a reverse proxy or forwards it over
    a private mesh, this route must never leak financial profile data. The
    browser-side boot code tolerates a 403 here by showing a yellow banner
    and continuing with public-only defaults.
    """
    client_host = (request.client.host if request.client else "") or ""
    # Accept only the loopback literal. Reject ::1 out of an abundance of
    # caution — Jose's local dev uses 127.0.0.1:8000.
    if client_host != "127.0.0.1":
        rid = uuid.uuid4().hex
        logger.info(
            "blocked /spec/profile.json from non-loopback host=%s (request_id=%s)",
            client_host, rid,
        )
        return JSONResponse(
            _error_envelope(
                "PROFILE_FORBIDDEN",
                "Profile data is loopback-only.",
                rid,
            ),
            status_code=403,
        )

    path = Path(__file__).parent / "spec" / "profile.local.json"
    if not path.exists():
        # Missing local profile → return 204 so the browser banner can
        # distinguish "denied" from "unconfigured" cleanly.
        return Response(status_code=204)
    try:
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        rid = uuid.uuid4().hex
        logger.exception("spec/profile.local.json malformed (request_id=%s)", rid)
        return JSONResponse(
            _error_envelope("PROFILE_MALFORMED", "Profile malformed.", rid),
            status_code=500,
        )


def _detect_source(hostname: str) -> str:
    """Detect data source from URL hostname.

    Uses exact-match or dot-prefix match to prevent SSRF via lookalike
    hostnames like `evilredfin.com` or `redfin.com.attacker.tld`. The
    earlier `endswith(".com")` pattern matched those; this one does not.
    """
    if not hostname:
        return "unknown"
    h = hostname.lower()
    if h == "redfin.com" or h.endswith(".redfin.com"):
        return "redfin"
    if h == "zillow.com" or h.endswith(".zillow.com"):
        return "zillow"
    return "unknown"


def _extract_redfin(soup) -> dict | None:
    """Extract property data from a Redfin listing page."""
    result = {
        "address": None, "price": None, "beds": None, "baths": None,
        "sqft": None, "lotSize": None, "yearBuilt": None, "propertyType": None,
        "zestimate": None, "rentZestimate": None, "taxHistory": [],
        "annualTax": None, "hoaFee": 0, "description": None, "imageUrl": None,
        # Sprint 16.6 Bundle 1A: capture numberOfUnits from ld+json so the
        # batch pipeline stops defaulting to duplex math for listings that
        # explicitly tag their unit count. Populated below from top-level +
        # mainEntity ld+json blocks and a regex fallback over the JS blob.
        "numberOfUnits": None,
    }

    # Helper to extract address from a schema.org object
    def _extract_address(obj: dict) -> str | None:
        addr_obj = obj.get("address", {})
        if isinstance(addr_obj, dict):
            parts = [addr_obj.get("streetAddress", ""),
                     addr_obj.get("addressLocality", "")]
            state = addr_obj.get("addressRegion", "")
            zipcode = addr_obj.get("postalCode", "")
            addr = ", ".join(p for p in parts if p)
            if state:
                addr += f", {state} {zipcode}".rstrip()
            return addr if addr else None
        elif isinstance(addr_obj, str):
            return addr_obj
        return None

    # Helper to check if @type matches any known residential/listing type
    def _type_matches(item_type, targets) -> bool:
        if isinstance(item_type, list):
            return any(t in targets for t in item_type)
        return item_type in targets

    LISTING_TYPES = {"SingleFamilyResidence", "Residence", "Product",
                     "House", "Apartment", "RealEstateListing"}
    RESIDENTIAL_TYPES = {"SingleFamilyResidence", "Residence", "House",
                         "Apartment", "Condominium", "TownHouse"}

    # 1) ld+json (Redfin usually has good structured data)
    ld_scripts = soup.find_all("script", type="application/ld+json")
    for tag in ld_scripts:
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type", "")
            if not _type_matches(item_type, LISTING_TYPES):
                continue

            # Sprint 15.5 fix #1: capture ld+json @type as propertyType.
            # Redfin's top-level ld+json often says "Apartment",
            # "Condominium", "SingleFamilyResidence", etc. — the old
            # code read this but never stored it, leaving propertyType
            # null and breaking the PR #29 multi-family filter.
            if not result["propertyType"] and item_type:
                result["propertyType"] = item_type if isinstance(item_type, str) else (item_type[0] if item_type else None)

            # Extract top-level data (address, image, description, price)
            if not result["address"]:
                result["address"] = _extract_address(item)
            result["description"] = result["description"] or item.get("description")
            img = item.get("image") or item.get("photo")
            if isinstance(img, list) and img:
                img = img[0]
            if isinstance(img, dict):
                img = img.get("contentUrl") or img.get("url")
            if not result["imageUrl"] and isinstance(img, str):
                result["imageUrl"] = img

            # Price from offers or top-level
            offers = item.get("offers", {})
            if isinstance(offers, dict) and not result["price"]:
                result["price"] = offers.get("price")
            if not result["price"]:
                result["price"] = item.get("price")

            # Direct property fields (if at top level)
            result["beds"] = result["beds"] or item.get("numberOfRooms") or item.get("numberOfBedrooms")
            result["baths"] = result["baths"] or item.get("numberOfBathroomsTotal") or item.get("numberOfFullBathrooms")
            result["yearBuilt"] = result["yearBuilt"] or item.get("yearBuilt")
            # Sprint 16.6 Bundle 1A: numberOfUnits at listing top level.
            if not result["numberOfUnits"]:
                _nu = item.get("numberOfUnits")
                if isinstance(_nu, (int, float)):
                    result["numberOfUnits"] = int(_nu)
                elif isinstance(_nu, str) and _nu.isdigit():
                    result["numberOfUnits"] = int(_nu)
            floor_size = item.get("floorSize", {})
            if not result["sqft"]:
                if isinstance(floor_size, dict):
                    result["sqft"] = floor_size.get("value")
                elif isinstance(floor_size, (int, float)):
                    result["sqft"] = int(floor_size)

            # Traverse mainEntity for nested residential data (Redfin pattern)
            main_entity = item.get("mainEntity", {})
            if isinstance(main_entity, dict):
                me_type = main_entity.get("@type", "")
                if _type_matches(me_type, RESIDENTIAL_TYPES) or main_entity.get("numberOfBedrooms"):
                    # Sprint 15.5 fix #1: mainEntity @type is often more
                    # specific than the top-level listing type — prefer it
                    # when available.
                    if me_type and isinstance(me_type, str):
                        result["propertyType"] = me_type
                    elif me_type and isinstance(me_type, list) and me_type:
                        result["propertyType"] = me_type[0]
                    if not result["address"]:
                        result["address"] = _extract_address(main_entity)
                    result["beds"] = result["beds"] or main_entity.get("numberOfBedrooms") or main_entity.get("numberOfRooms")
                    result["baths"] = result["baths"] or main_entity.get("numberOfBathroomsTotal") or main_entity.get("numberOfFullBathrooms")
                    result["yearBuilt"] = result["yearBuilt"] or main_entity.get("yearBuilt")
                    # Sprint 16.6 Bundle 1A: numberOfUnits at mainEntity level.
                    if not result["numberOfUnits"]:
                        _nu = main_entity.get("numberOfUnits")
                        if isinstance(_nu, (int, float)):
                            result["numberOfUnits"] = int(_nu)
                        elif isinstance(_nu, str) and _nu.isdigit():
                            result["numberOfUnits"] = int(_nu)
                    me_floor = main_entity.get("floorSize", {})
                    if not result["sqft"]:
                        if isinstance(me_floor, dict):
                            result["sqft"] = me_floor.get("value")
                        elif isinstance(me_floor, (int, float)):
                            result["sqft"] = int(me_floor)

    # 2) Fallback: parse from meta tags
    if not result["address"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            result["address"] = og_title["content"].split("|")[0].strip()

    if not result["imageUrl"]:
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            result["imageUrl"] = og_img["content"]

    # 3) Fallback: regex scan for Redfin's JS data
    for script in soup.find_all("script"):
        text = script.string or ""
        if len(text) < 50:
            continue

        if not result["price"]:
            m = re.search(r'"price(?:Info)?"\s*:\s*\{[^}]*"amount"\s*:\s*(\d+)', text)
            if not m:
                m = re.search(r'"listingPrice"\s*:\s*(\d+)', text)
            if m:
                try:
                    result["price"] = int(m.group(1))
                except ValueError:
                    pass

        # Sprint 15.5 fix #2: match both plain integers (`"beds": 3`) AND
        # object-wrapped values (`"beds": {"value": 3}` / `{"amount": 3}`).
        # Redfin uses both forms depending on the listing variant; the
        # old plain-integer regex silently missed the wrapped form.
        if not result["beds"]:
            m = re.search(r'"beds"\s*:\s*(?:\{[^}]*"(?:value|amount)"\s*:\s*)?(\d+)', text)
            if m:
                result["beds"] = int(m.group(1))

        if not result["baths"]:
            m = re.search(r'"baths"\s*:\s*(?:\{[^}]*"(?:value|amount)"\s*:\s*)?(\d+(?:\.\d+)?)', text)
            if m:
                result["baths"] = float(m.group(1))

        # Sprint 15.5 fix #3: consolidate sqft patterns — try wrapped
        # first (most common), then plain-integer fallback. Case-insensitive
        # because Redfin uses both "sqFt" (camelCase) and "sqftInfo"
        # (lowercase) depending on the listing variant.
        if not result["sqft"]:
            m = re.search(
                r'"sqft(?:info)?"\s*:\s*\{[^}]*"(?:value|amount)"\s*:\s*(\d+)',
                text, re.IGNORECASE,
            )
            if not m:
                m = re.search(
                    r'"sqft(?:info)?"\s*:\s*(\d+)', text, re.IGNORECASE,
                )
            if m:
                result["sqft"] = int(m.group(1))

        # Sprint 15.5 fix #4: regex fallback for propertyType when
        # ld+json was missing/malformed. Redfin's JS blob often carries
        # "propertyType" or "homeType" as a plain string. Multi keyword
        # variants we've seen: "Duplex", "Triplex", "Multi-Family",
        # "2-Unit", "3-Unit", "4-Unit".
        if not result["propertyType"]:
            m = re.search(r'"propertyType"\s*:\s*"([^"]+)"', text)
            if not m:
                m = re.search(r'"homeType"\s*:\s*"([^"]+)"', text)
            if m:
                result["propertyType"] = m.group(1)

        if not result["yearBuilt"]:
            m = re.search(r'"yearBuilt"\s*:\s*\{[^}]*"value"\s*:\s*(\d{4})', text)
            if m:
                result["yearBuilt"] = int(m.group(1))

        # Sprint 16.6 Bundle 1A: numberOfUnits regex fallback on the JS blob.
        # Accepts both plain int (`"numberOfUnits": 3`) and object-wrapped
        # (`"numberOfUnits": {"value": 3}` / `{"amount": 3}`) shapes — mirrors
        # the pattern used for beds/baths in PR #30. Cap at 20 units to avoid
        # spurious matches on unrelated counters.
        if not result["numberOfUnits"]:
            m = re.search(
                r'"numberOfUnits"\s*:\s*(?:\{[^}]*"(?:value|amount)"\s*:\s*)?(\d+)',
                text,
            )
            if m:
                try:
                    _nu = int(m.group(1))
                    if 1 <= _nu <= 20:
                        result["numberOfUnits"] = _nu
                except ValueError:
                    pass

        if not result["annualTax"]:
            m = re.search(r'"taxInfo"\s*:\s*\{[^}]*"amount"\s*:\s*(\d+)', text)
            if m:
                result["annualTax"] = int(m.group(1))

        if result["hoaFee"] == 0:
            m = re.search(r'"hoaDues"\s*:\s*\{[^}]*"amount"\s*:\s*(\d+)', text)
            if m:
                result["hoaFee"] = int(m.group(1))

    # Price might come as string "$350,000" — normalize
    if isinstance(result["price"], str):
        try:
            result["price"] = int(re.sub(r"[^\d]", "", result["price"]))
        except ValueError:
            result["price"] = None

    if result["address"] or result["price"]:
        return result
    return None


# ---------------------------------------------------------------------------
# Neighborhood Search — Redfin search page scraping
# ---------------------------------------------------------------------------

# Global semaphore: max 3 concurrent Playwright browsers for search
_search_semaphore = asyncio.Semaphore(3)

_REDFIN_SEARCH_JS = """
() => {
    const cards = document.querySelectorAll('.MapHomeCardReact, [class*="HomeCard"]');
    const results = [];
    const seen = new Set();

    // Helper: extract beds/baths/sqft from a text string
    function parseStats(t) {
        const b = t.match(/(\\d+)\\s*(?:beds?|bd|BR)\\b/i);
        const bt = t.match(/(\\d+\\.?\\d*)\\s*(?:baths?|ba)\\b/i);
        const s = t.match(/(\\d[\\d,]*)\\s*(?:sq|SF)\\b/i);
        return {
            beds: b ? parseInt(b[1]) : null,
            baths: bt ? parseFloat(bt[1]) : null,
            sqft: s ? parseInt(s[1].replace(/,/g, '')) : null
        };
    }

    cards.forEach(card => {
        const linkEl = card.querySelector('a[href*="/home/"]');
        const url = linkEl ? linkEl.href : null;
        if (!url || seen.has(url)) return;
        seen.add(url);

        // --- Price ---
        const priceDiv = card.querySelector('.bp-Homecard__Price, [class*="Price"]');
        let price = null;
        if (priceDiv) {
            const m = priceDiv.textContent.match(/\\$(\\d[\\d,]*)/);
            if (m) price = parseInt(m[1].replace(/,/g, ''));
        }

        // --- Address ---
        const addrEl = card.querySelector('.bp-Homecard__Address, [class*="homeAddressV2"], [class*="address"]');

        // --- Beds / Baths / Sqft ---
        let beds = null, baths = null, sqft = null;

        // Method 1: Dedicated stats element
        const statsEls = card.querySelectorAll('.bp-Homecard__Stats, [class*="HomeStats"], [class*="homeStat"], [class*="home-stat"], [class*="KeyStats"], [class*="keyStats"]');
        for (const el of statsEls) {
            const p = parseStats(el.textContent);
            if (p.beds !== null) beds = p.beds;
            if (p.baths !== null) baths = p.baths;
            if (p.sqft !== null) sqft = p.sqft;
            if (beds !== null) break;
        }

        // Method 2: Look for individual stat spans/divs inside the card
        if (beds === null) {
            const spans = card.querySelectorAll('span, div');
            for (const sp of spans) {
                const txt = sp.textContent.trim();
                // Match standalone "3 Beds" or "2 Baths" text nodes (short, focused)
                if (txt.length < 15) {
                    if (beds === null) {
                        const bm = txt.match(/^(\\d+)\\s*(?:beds?|bd|BR)$/i);
                        if (bm) beds = parseInt(bm[1]);
                    }
                    if (baths === null) {
                        const btm = txt.match(/^(\\d+\\.?\\d*)\\s*(?:baths?|ba)$/i);
                        if (btm) baths = parseFloat(btm[1]);
                    }
                    if (sqft === null) {
                        const sm = txt.match(/^(\\d[\\d,]*)\\s*(?:sq|SF)/i);
                        if (sm) sqft = parseInt(sm[1].replace(/,/g, ''));
                    }
                }
            }
        }

        // Method 3: Card aria-label or title attribute (Redfin sometimes puts stats here)
        if (beds === null) {
            const ariaEl = card.querySelector('[aria-label]');
            if (ariaEl) {
                const p = parseStats(ariaEl.getAttribute('aria-label'));
                if (p.beds !== null && p.beds <= 20) beds = p.beds;
                if (p.baths !== null && baths === null) baths = p.baths;
                if (p.sqft !== null && sqft === null) sqft = p.sqft;
            }
        }

        // Method 4: Full card text fallback (with sanity checks)
        if (beds === null) {
            const fullText = card.textContent;
            const p = parseStats(fullText);
            if (p.beds !== null && p.beds <= 20) beds = p.beds;
            if (p.baths !== null && p.baths <= 20 && baths === null) baths = p.baths;
            if (p.sqft !== null && sqft === null) sqft = p.sqft;
        }

        // --- Image ---
        const imgEl = card.querySelector('img[src*="cdn-redfin"], img[src*="photos"], img[src*="ssl.cdn"], img[src*="rdcpix"]');

        results.push({
            address: addrEl ? addrEl.textContent.trim() : null,
            price: price,
            beds: beds,
            baths: baths,
            sqft: sqft,
            listingUrl: url,
            imageUrl: imgEl ? imgEl.src : null
        });
    });
    return results;
}
"""


def _build_redfin_filter_path(filters: dict) -> str:
    """Build Redfin filter path segments from filters dict."""
    filter_parts = []
    if filters.get("min_price"):
        filter_parts.append(f"min-price={int(filters['min_price'])}")
    if filters.get("max_price"):
        filter_parts.append(f"max-price={int(filters['max_price'])}")
    if filters.get("min_beds") and filters["min_beds"] > 0:
        filter_parts.append(f"min-beds={int(filters['min_beds'])}")
    ptype_map = {"house": "house", "condo": "condo,townhouse", "multi-family": "multifamily"}
    if filters.get("property_type") and filters["property_type"] in ptype_map:
        filter_parts.append(f"property-type={ptype_map[filters['property_type']]}")
    if filters.get("sort") == "price-asc":
        filter_parts.append("sort=lo-price")
    if filter_parts:
        return "/filter/" + ",".join(filter_parts)
    return ""


def _build_redfin_search_url(location: str, filters: dict) -> str:
    """Build a Redfin search URL from location and filters.

    For zip codes, we can construct the URL directly.
    For city names, returns None — caller must use Playwright search bar.
    """
    query = location.strip()

    # Detect zip code (direct URL) vs city name (needs search)
    if re.match(r"^\d{5}$", query):
        base = f"https://www.redfin.com/zipcode/{query}"
        return base + _build_redfin_filter_path(filters)

    # City names can't be constructed as URLs (Redfin uses numeric city IDs)
    return None


async def _search_redfin_page(location: str, filters: dict) -> dict:
    """Search Redfin by loading the search results page with Playwright.

    Scrolls down multiple times to load more listings via lazy-loading.
    """
    direct_url = _build_redfin_search_url(location, filters)
    max_results = filters.get("max_results", 40)

    # Sprint 8-1: shared browser pool. Context is per-call (isolates cookies);
    # the semaphore still caps concurrent page sessions.
    async with _search_semaphore:
        context, _ = await _get_browser_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        try:
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )

            if direct_url:
                # Zip code — navigate directly
                try:
                    await page.goto(direct_url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    return {"error": "Could not connect to Redfin. Please try again later."}
            else:
                # City name — use Redfin search bar to resolve
                try:
                    await page.goto("https://www.redfin.com", wait_until="domcontentloaded", timeout=20000)
                    # Type in search box and pick first suggestion
                    search_input = page.locator("input[type='text'][placeholder*='Search'], input[type='search'], #search-box-input, [data-testid='search-box-input']").first
                    await search_input.fill(location.strip())
                    await page.wait_for_timeout(1500)
                    # Press Enter to search (autocomplete should resolve)
                    await search_input.press("Enter")
                    await page.wait_for_timeout(3000)
                    # Now append filters to the URL
                    current_url = page.url
                    filter_path = _build_redfin_filter_path(filters)
                    if filter_path and "/filter/" not in current_url:
                        await page.goto(current_url.rstrip("/") + filter_path, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    return {"error": "Could not connect to Redfin. Please try again later."}

            # Check for redirect to main page (bad location)
            final_url = page.url
            if "/zipcode/" not in final_url and "/city/" not in final_url and "/neighborhood/" not in final_url and "/filter/" not in final_url and "/county/" not in final_url and "/state/" not in final_url:
                return {"error": f'Could not find location "{location}". Try a zip code (e.g. "78701") or city + state (e.g. "Austin, TX").'}

            # Wait for listing cards to render
            try:
                await page.wait_for_selector(".MapHomeCardReact, [class*='HomeCard']", timeout=8000)
            except Exception:
                # No listings found or page didn't load cards
                html_text = await page.content()
                if "No results found" in html_text or "0 homes" in html_text:
                    return {"listings": [], "total": 0}
                return {"error": "No listings found. Try adjusting your filters or searching a different area."}

            await page.wait_for_timeout(2000)

            # Scroll down to load more lazy-loaded listings
            # Sprint 16.9: scroll until Redfin stops loading new cards, with
            # safety cap + stability gate + overall timeout.
            #
            # Previous logic: hard range(8) ceiling stopped prematurely in
            # large markets — a ZIP with 200 listings would silently truncate
            # at ~80-120. The single-observation break (`cur_count == prev_count`)
            # was also fragile: Redfin occasionally has a render cycle that
            # reports no new cards but loads more on the next scroll.
            #
            # New behavior:
            #   - MAX_SCROLLS=50 safety (~50s worst case per ZIP) — prevents
            #     runaway if Redfin ever adopts an infinite pagination model
            #   - Require STABLE_ROUNDS=2 consecutive no-new-card observations
            #     before breaking — kills the false-done case
            #   - Keep the cur_count >= 10 guard so we don't stop during
            #     initial slow renders
            #   - asyncio.wait_for 90s hard timeout wraps the whole loop
            #     (review P1 on PR #44: without this, a stuck page.evaluate
            #     could block the Semaphore(3) slot for minutes)
            #   - Every 10 scrolls, sniff for a mid-scroll bot-wall (review
            #     P1 on PR #44: goto's initial bot-wall check doesn't
            #     protect against interstitials injected later)
            MAX_SCROLLS = 50
            STABLE_ROUNDS_NEEDED = 2
            BOT_WALL_CHECK_EVERY = 10
            SCROLL_PHASE_TIMEOUT_SEC = 90

            async def _scroll_phase():
                prev_count = 0
                stable_rounds = 0
                scrolls_done = 0
                for i in range(MAX_SCROLLS):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1000)
                    cur_count = await page.evaluate(
                        "document.querySelectorAll('.MapHomeCardReact, [class*=\"HomeCard\"]').length"
                    )
                    scrolls_done = i + 1
                    if cur_count == prev_count:
                        stable_rounds += 1
                        if stable_rounds >= STABLE_ROUNDS_NEEDED and cur_count >= 10:
                            break
                    else:
                        stable_rounds = 0
                    prev_count = cur_count
                    # Mid-scroll bot-wall check: Redfin can inject a captcha
                    # or "Attention Required" interstitial after aggressive
                    # scrolls. Every 10 rounds, probe for it and bail early
                    # with whatever we have.
                    if (i + 1) % BOT_WALL_CHECK_EVERY == 0:
                        try:
                            wall = await page.locator(
                                '[class*="captcha"], text=/Attention Required/i, [class*="AccessDenied"]'
                            ).count()
                            if wall > 0:
                                logger.warning(
                                    "scan: bot-wall detected mid-scroll for %s "
                                    "after %d scrolls — stopping with %d cards",
                                    location, scrolls_done, cur_count,
                                )
                                break
                        except Exception:
                            # Best-effort probe — never block the scroll loop
                            # on the locator call itself.
                            pass
                return scrolls_done, prev_count

            try:
                scrolls_used, final_card_count = await asyncio.wait_for(
                    _scroll_phase(), timeout=SCROLL_PHASE_TIMEOUT_SEC,
                )
                logger.info(
                    "scan: %s scroll phase used %d/%d rounds, %d cards",
                    location, scrolls_used, MAX_SCROLLS, final_card_count,
                )
            except asyncio.TimeoutError:
                # Timed out — whatever's already rendered in the DOM is
                # what we work with. Log so we can tune later.
                logger.warning(
                    "scan: %s scroll phase hit %ds timeout — returning "
                    "partial results", location, SCROLL_PHASE_TIMEOUT_SEC,
                )

            # Extract total count from page (e.g., "47 homes" in the results header)
            page_total = await page.evaluate("""
                () => {
                    const el = document.querySelector('[class*="homes"], [class*="result"]');
                    if (el) {
                        const m = el.textContent.match(/(\\d+)\\s*home/i);
                        if (m) return parseInt(m[1]);
                    }
                    return null;
                }
            """)

            # Extract location label from page title
            title = await page.title()
            label = location
            if title:
                # "78701, TX Real Estate & Homes for Sale | Redfin"
                # "Memphis, TN Homes for Sale & Real Estate | Redfin"
                label = re.sub(r"\s*\|.*$", "", title)
                label = re.sub(r"\s*(Real Estate|Homes for Sale|Houses for Sale|&).*$", "", label).strip()
                if not label:
                    label = location

            listings = await page.evaluate(_REDFIN_SEARCH_JS)
        finally:
            try:
                await context.close()
            except Exception:  # pragma: no cover - defensive
                pass

    # Sprint 11.5: Python-side post-filter. Redfin's URL filters sometimes
    # no-op (multi-ZIP input, autocomplete fallback, layout changes) and
    # Redfin returns vacant lots even when property-type=multifamily. Defense
    # in depth — re-enforce min/max/beds/property_type on what actually came
    # back, and drop "likely lot" entries (no beds AND no sqft, low price).
    filter_min_price = filters.get("min_price") or 0
    filter_max_price = filters.get("max_price") or 0
    filter_min_beds = filters.get("min_beds") or 0
    filter_ptype = filters.get("property_type") or None

    def _passes_filters(l: dict) -> bool:
        price = l.get("price") or 0
        if price <= 0:
            return False
        if filter_min_price and price < filter_min_price:
            return False
        if filter_max_price and price > filter_max_price:
            return False
        beds = l.get("beds") or 0
        sqft = l.get("sqft") or 0
        if filter_min_beds and beds < filter_min_beds:
            return False
        # "Likely lot" heuristic: no beds, no sqft, suspiciously cheap. Triggers
        # when beds=0 AND sqft=0 AND (price < 200_000 OR no address). Addresses
        # the Vallejo 94591 $95K / $64.9K cases where Redfin returned vacant
        # lots under a multi-family filter.
        if (not beds) and (not sqft) and (price < 200_000 or not l.get("address")):
            return False
        # Property-type enforcement — Sprint 16.2: relaxed post Sprint 15.5.
        #
        # History:
        #   - Sprint 15.5 (PR #29) made this strict: allowlist-only for
        #     multi-family, reject null.
        #   - That broke Scan ZIPs for any ZIP Redfin returned >0 listings
        #     for: the LIST-page scraper (_REDFIN_SEARCH_JS) never populates
        #     propertyType — only the individual listing page does. So all
        #     rows came through with propertyType=null and got rejected
        #     wholesale. User reported 0 survivors on 95815 + 95205 even
        #     though Redfin returned 38 multi-family listings.
        #
        # Current policy for `multi-family`:
        #   1. REJECT when propertyType contains a known non-multi keyword
        #      (SFR / condo / land / etc.) — defense against Redfin's URL
        #      filter leaking. These rows only reach us if propertyType
        #      was populated (single-URL analysis path, or future scraper
        #      improvements); when set, trust the label.
        #   2. ACCEPT when propertyType is null/empty/unknown-keyword AND
        #      Redfin's URL filter was applied (we navigated to
        #      /filter/property-type=multifamily). The individual listing
        #      page scraper (batch/pipeline.py) re-populates propertyType
        #      per URL and the verdict's SFR hard-fail will catch any real
        #      SFRs that leaked through. The "likely lot" heuristic above
        #      (beds=0 + sqft=0 + price<200k) already catches vacant lots.
        if filter_ptype:
            ptype = (l.get("propertyType") or "").lower()
            if filter_ptype == "multi-family":
                SFR_LAND_KEYWORDS = ("single family", "single-family", "single_family",
                                     "sfr", "sfh", "land", "lot", "vacant",
                                     "condo", "townhouse", "townhome",
                                     "manufactured", "mobile", "mfh")
                is_reject = ptype and any(k in ptype for k in SFR_LAND_KEYWORDS)
                if is_reject:
                    return False
                # Null / unknown-keyword propertyType: accept. Downstream
                # per-URL scrape will classify and the verdict will flag.
            elif filter_ptype == "house":
                # Reject obvious non-houses (condos, townhomes, land).
                if "land" in ptype or "lot" in ptype or "manufactured" in ptype:
                    return False
                # Also reject explicit multi/condo/townhouse when house
                # was requested — same consistency fix as multi-family.
                if any(k in ptype for k in ("multi", "duplex", "triplex", "fourplex",
                                            "condo", "townhouse", "townhome")):
                    return False
            elif filter_ptype == "condo":
                if ptype and "condo" not in ptype and "town" not in ptype:
                    return False
        return True

    listings = [l for l in listings if _passes_filters(l)][:max_results]

    return {
        "listings": listings,
        "total": page_total or len(listings),
        "location_label": label,
    }


@app.post("/api/search")
async def search_neighborhood(request: Request):
    """Search for listings in a neighborhood/zip/city via Redfin."""
    client_ip = request.client.host if request.client else "unknown"
    # Sprint 14.5 rate-limit refresh: the original 3/min cap here was sized
    # for a hypothetical multi-user SaaS; this tool is local-only single-user
    # per Sprint 10A's loopback-only posture. 30/min matches a human clicking
    # Search Listings at a realistic pace (one every 2s max) while still
    # catching runaway loops in dev. Mirrors the same reasoning behind the
    # `batch_scrape` bucket bump in PR #11.
    if not _check_rate_limit(f"search:{client_ip}", 30):
        return JSONResponse(
            {"error": "Too many searches. Please wait a minute before trying again."},
            status_code=429,
        )

    body = await request.json()
    location = (body.get("location") or "").strip()
    if not location:
        return JSONResponse({"error": "Location is required."}, status_code=400)
    if len(location) > 200:
        return JSONResponse({"error": "Location query is too long."}, status_code=400)

    # Sprint 11.5: reject comma-separated multi-ZIP in the Location field.
    # Before this guard, Redfin fell through to the search-bar fallback and
    # silently returned unfiltered results (min/max/property-type ignored).
    # Multi-ZIP now belongs in the Scan ZIPs panel, which fans out per ZIP.
    if "," in location:
        parts = [p.strip() for p in location.split(",") if p.strip()]
        if len(parts) > 1 and all(re.fullmatch(r"\d{5}", p) for p in parts):
            return JSONResponse(
                {"error": "Multi-ZIP searches belong in the Scan ZIPs panel — the Location field accepts a single ZIP or city."},
                status_code=400,
            )

    filters = {
        "min_price": body.get("min_price"),
        "max_price": body.get("max_price"),
        "min_beds": body.get("min_beds"),
        "property_type": body.get("property_type"),
        # Sprint 14-3: raise ceiling 75 → 500. Redfin's own paginated result
        # page rarely exceeds ~500 listings per ZIP anyway; the scroll-loop in
        # _search_redfin_page is already bounded to 8 scroll-down cycles + an
        # early-out when no new cards load, so the upper bound is natural.
        "max_results": min(int(body.get("max_results") or 25), 500),
    }

    result = await _search_redfin_page(location, filters)

    if "error" in result and "listings" not in result:
        return JSONResponse({"error": result["error"]}, status_code=404)

    return JSONResponse(result)


@app.post("/api/smart-search")
async def smart_search(request: Request):
    """Smart Deal Finder: search listings + auto-estimate rent from market data.

    Strategy: fetch rentals first, compute a smart max price from rent data,
    then search for-sale listings within that price range so results are
    more likely to be viable investment deals.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"smart:{client_ip}", 30):  # Sprint 14.5: 3 → 30.
        return JSONResponse(
            {"error": "Too many searches. Please wait a minute before trying again."},
            status_code=429,
        )

    body = await request.json()
    location = (body.get("location") or "").strip()
    if not location:
        return JSONResponse({"error": "Location is required."}, status_code=400)
    if len(location) > 200:
        return JSONResponse({"error": "Location query is too long."}, status_code=400)

    user_min_beds = body.get("min_beds")
    user_property_type = body.get("property_type")
    min_price = body.get("min_price") or 25000
    user_max_results = body.get("max_results") or 50

    # Step 1+2: Fetch rental data AND for-sale listings IN PARALLEL
    # Use a generous max price for the initial search; we'll filter down
    # once we know the smart price cap from rental data.
    initial_filters = {
        "min_price": min_price,
        "max_price": 750000,  # generous cap; will narrow after rent data
        "min_beds": user_min_beds,
        "property_type": user_property_type or "house",
        "max_results": min(user_max_results + 20, 80),
        "sort": "price-asc",
    }

    # Run rentals, for-sale listings, AND mortgage rate fetch in parallel
    rental_beds = user_min_beds if user_min_beds and user_min_beds >= 2 else None
    rentals_task = asyncio.create_task(_search_redfin_rentals(location, rental_beds))
    listings_task = asyncio.create_task(_search_redfin_page(location, initial_filters))
    rate_task = asyncio.create_task(_ensure_mortgage_rate())
    rentals_result, listings_result, _ = await asyncio.gather(
        rentals_task, listings_task, rate_task
    )

    # Build rent lookup by bedroom count
    rent_by_beds: dict[int, list[int]] = {}
    all_rents: list[int] = []
    for r in rentals_result.get("rentals", []):
        rent_val = r.get("rent", 0)
        if rent_val <= 0:
            continue
        all_rents.append(rent_val)
        b = r.get("beds")
        if b is not None and b > 0:
            rent_by_beds.setdefault(b, []).append(rent_val)

    # Compute median rent per bedroom count, and also the 75th percentile
    rent_median_by_beds: dict[int, int] = {}
    rent_p75_by_beds: dict[int, int] = {}
    for beds, rents in rent_by_beds.items():
        rents.sort()
        rent_median_by_beds[beds] = rents[len(rents) // 2]
        rent_p75_by_beds[beds] = rents[min(int(len(rents) * 0.75), len(rents) - 1)]

    overall_median = 0
    overall_p75 = 0
    if all_rents:
        all_rents.sort()
        overall_median = all_rents[len(all_rents) // 2]
        overall_p75 = all_rents[min(int(len(all_rents) * 0.75), len(all_rents) - 1)]

    # Compute smart max price from rent data
    # Use median (not P75) to avoid luxury apartment skew.
    # Multiplier of 200 (~0.5% rent/price) is conservative for 7% rate
    # environment — deals above this ratio rarely cash-flow positive.
    smart_max_price = None
    if overall_median > 0:
        # Use overall median (not max across bedrooms) to avoid
        # inflated caps from high-bedroom luxury rentals.
        # Multiplier of 250 ≈ GRM 20.8, upper bound for viable investment deals.
        # See README "Smart Price Cap" section for the multiplier table.
        best_rent = overall_median
        smart_max_price = int(best_rent * 250)
        smart_max_price = ((smart_max_price + 24999) // 25000) * 25000
        smart_max_price = max(smart_max_price, 75000)

    if "error" in listings_result and "listings" not in listings_result:
        return JSONResponse({"error": listings_result["error"]}, status_code=404)

    # If no rental data at all, we can't score deals meaningfully
    if not all_rents:
        return JSONResponse(
            {"error": "No rental data found for this area. Try a nearby zip code — rent comps are needed to estimate deals."},
            status_code=404,
        )

    listings = listings_result.get("listings", [])

    # Filter by smart max price (initial search used generous $500K cap)
    if smart_max_price and listings:
        listings = [l for l in listings if l.get("price", 0) <= smart_max_price]

    if not listings:
        return JSONResponse(
            {"error": "No for-sale listings found. Try a different location."},
            status_code=404,
        )

    # Step 4: Filter out likely vacant parcels
    # Addresses starting with "0 " are empty land listings on Redfin
    listings = [
        l for l in listings
        if not (l.get("address") or "").strip().startswith("0 ")
    ]

    # Step 5: Attach estimated rent to each listing
    # Use bedroom-specific rent when available, otherwise find closest match.
    # Prefer a blend over bedroom-specific rent when it's >30% above the
    # overall median — likely skewed by luxury apartments.
    for listing in listings:
        beds = listing.get("beds")
        bed_rent = None
        if beds and beds in rent_median_by_beds:
            bed_rent = rent_median_by_beds[beds]
        elif beds and rent_median_by_beds:
            closest = min(rent_median_by_beds.keys(), key=lambda b: abs(b - beds))
            bed_rent = rent_median_by_beds[closest]

        if bed_rent and overall_median > 0:
            # If bedroom-specific rent is >30% above overall median, it may be
            # skewed by luxury apartments. Use a blend to moderate the estimate.
            if bed_rent > overall_median * 1.3:
                listing["estRent"] = int((bed_rent + overall_median) / 2)
            else:
                listing["estRent"] = bed_rent
        elif bed_rent:
            listing["estRent"] = bed_rent
        elif overall_median > 0:
            listing["estRent"] = overall_median
        else:
            listing["estRent"] = None

        # Sanity cap: rent shouldn't exceed 2% of price monthly (24% annual).
        # Even aggressive cash-flow markets rarely exceed 1.5%.
        # Floor of $500 ensures very cheap properties get usable estimates.
        price = listing.get("price") or 0
        if listing["estRent"] and price > 0:
            max_plausible_rent = max(int(price * 0.02), 500)
            listing["estRent"] = min(listing["estRent"], max_plausible_rent)

    # Cap to user's requested max
    listings = listings[:user_max_results]

    # Rent confidence: how reliable is the estimate?
    rent_count = len(all_rents)
    rent_confidence = "high" if rent_count >= 15 else "medium" if rent_count >= 5 else "low"

    # Include current mortgage rate for scoring calibration
    current_rate = _mortgage_rate_cache.get("rate")

    return JSONResponse({
        "listings": listings,
        "total": listings_result.get("total", len(listings)),
        "location_label": listings_result.get("location_label", location),
        "rent_stats": rentals_result.get("stats"),
        "rent_by_beds": {str(k): v for k, v in rent_median_by_beds.items()},
        "smart_max_price": smart_max_price,
        "rent_confidence": rent_confidence,
        "mortgage_rate": current_rate,
    })


# ---------------------------------------------------------------------------
# Mortgage Rate — FRED API (free, no key required for this endpoint)
# ---------------------------------------------------------------------------
_mortgage_rate_cache: dict = {"rate": None, "fetched_at": 0}


async def _ensure_mortgage_rate() -> float | None:
    """Fetch and cache mortgage rate if not already cached. Returns the rate."""
    now = time.time()
    if _mortgage_rate_cache["rate"] is not None and now - _mortgage_rate_cache["fetched_at"] < 21600:
        return _mortgage_rate_cache["rate"]
    # Sprint 9-3: breaker short-circuits Freddie Mac when PMMS is flaking.
    from batch.circuit_breaker import get_breaker as _get_breaker
    breaker = _get_breaker("freddie_mac")
    if not breaker.before_call():
        return _mortgage_rate_cache.get("rate")
    try:
        hdrs = {k: v for k, v in HEADERS.items() if k != "Accept-Encoding"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://www.freddiemac.com/pmms", headers=hdrs)
            if resp.status_code == 200:
                match = re.search(r"(\d+\.\d+)%", resp.text)
                if match:
                    rate = float(match.group(1))
                    if 2.0 <= rate <= 15.0:
                        _mortgage_rate_cache["rate"] = rate
                        _mortgage_rate_cache["fetched_at"] = now
                        breaker.record_success()
                        return rate
            # 200 without a parseable rate, OR non-200 status — treat as failure.
            breaker.record_failure()
    except Exception:
        breaker.record_failure()
    return _mortgage_rate_cache.get("rate")


@app.get("/api/mortgage-rate")
async def get_mortgage_rate():
    """Fetch current average 30-year fixed mortgage rate from FRED."""
    rate = await _ensure_mortgage_rate()
    if rate is not None:
        return JSONResponse({"rate": rate})
    return JSONResponse({"rate": None, "error": "Could not fetch current rate."})


@app.get("/api/source-health")
async def get_source_health():
    """Sprint 9-3 — return the current state of every circuit breaker.

    Not wired into any UI yet; Jose can `curl` this when a batch looks
    degraded to see which upstream is tripped (FEMA, Cal Fire, Overpass,
    Census, Freddie Mac). The breakers are in-memory so each process
    restart resets them.
    """
    import time as _time
    from datetime import datetime, timedelta, timezone
    from batch.circuit_breaker import all_breakers as _all_breakers
    sources: dict[str, dict] = {}
    now_mono = _time.monotonic()
    now_wall = datetime.now(timezone.utc)
    for br in _all_breakers():
        snap = br.snapshot()
        cooldown_iso: str | None = None
        if snap.cooldown_until is not None:
            # Convert monotonic cooldown-until to wall-clock ISO for readability.
            delta = snap.cooldown_until - now_mono
            cooldown_iso = (now_wall + timedelta(seconds=delta)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        sources[snap.name] = {
            "state": snap.state,
            "failures": snap.failures,
            "cooldown_until": cooldown_iso,
        }
    return JSONResponse({"sources": sources})


# ---------------------------------------------------------------------------
# Rent Estimation — Redfin rental listings search
# ---------------------------------------------------------------------------
_REDFIN_RENT_JS = """
() => {
    const cards = document.querySelectorAll('.MapHomeCardReact, [class*="HomeCard"]');
    const results = [];
    const seen = new Set();
    cards.forEach(card => {
        const priceDiv = card.querySelector('.bp-Homecard__Price, [class*="Price"]');
        if (!priceDiv) return;
        const priceText = priceDiv.textContent;
        // Only include rental listings (contain /mo or /month)
        if (!/\\/mo/i.test(priceText) && !/rent/i.test(priceText)) {
            // Also check if it looks like a rent price (< $10k/mo typically)
            const m = priceText.match(/\\$(\\d[\\d,]*)/);
            if (m) {
                const p = parseInt(m[1].replace(/,/g, ''));
                if (p > 15000) return; // likely a sale price, skip
            }
        }
        const m = priceText.match(/\\$(\\d[\\d,]*)/);
        if (!m) return;
        const rent = parseInt(m[1].replace(/,/g, ''));
        if (rent <= 0 || rent > 50000) return;

        let beds = null, baths = null, sqft = null;
        // Try multiple stat selectors
        const statsEls = card.querySelectorAll('.bp-Homecard__Stats, [class*="HomeStats"], [class*="homeStat"], [class*="KeyStats"]');
        for (const el of statsEls) {
            const t = el.textContent;
            const bM = t.match(/(\\d+)\\s*(?:beds?|bd|BR)\\b/i);
            const btM = t.match(/(\\d+\\.?\\d*)\\s*(?:baths?|ba)\\b/i);
            const sM = t.match(/(\\d[\\d,]*)\\s*(?:sq|SF)\\b/i);
            if (bM) beds = parseInt(bM[1]);
            if (btM) baths = parseFloat(btM[1]);
            if (sM) sqft = parseInt(sM[1].replace(/,/g, ''));
            if (beds !== null) break;
        }
        // Fallback: individual short spans
        if (beds === null) {
            const spans = card.querySelectorAll('span, div');
            for (const sp of spans) {
                const txt = sp.textContent.trim();
                if (txt.length < 15) {
                    if (beds === null) { const bm = txt.match(/^(\\d+)\\s*(?:beds?|bd|BR)$/i); if (bm) beds = parseInt(bm[1]); }
                    if (baths === null) { const btm = txt.match(/^(\\d+\\.?\\d*)\\s*(?:baths?|ba)$/i); if (btm) baths = parseFloat(btm[1]); }
                }
            }
        }
        const addrEl = card.querySelector('.bp-Homecard__Address, [class*="homeAddressV2"]');
        const addr = addrEl ? addrEl.textContent.trim() : null;
        const key = addr || rent.toString();
        if (seen.has(key)) return;
        seen.add(key);
        results.push({ rent: rent, beds: beds, baths: baths, sqft: sqft, address: addr });
    });
    return results;
}
"""


async def _search_redfin_rentals(location: str, beds: int | None = None) -> dict:
    """Search Redfin for rental listings to estimate market rent.

    For zip codes, navigates directly. For city names, uses Playwright
    search bar (Redfin uses numeric city IDs that can't be URL-constructed).

    Sprint 8-1: reuses the shared browser pool so cold-cache rent-comp
    batches don't pay a ~1s launch per ZIP.
    """
    query = location.strip()
    is_zip = bool(re.match(r"^\d{5}$", query))

    # Build bed filter suffix
    bed_filter = ""
    if beds and beds > 0:
        bed_filter = f"/filter/min-beds={int(beds)},max-beds={int(beds)}"

    async with _search_semaphore:
        context, _ = await _get_browser_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        try:
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )

            if is_zip:
                # Zip code — navigate directly
                base = f"https://www.redfin.com/zipcode/{query}/apartments-for-rent{bed_filter}"
                try:
                    await page.goto(base, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    return {"error": "Could not connect to Redfin."}
            else:
                # City name — use Redfin search bar to resolve, then switch to rentals
                try:
                    await page.goto("https://www.redfin.com", wait_until="domcontentloaded", timeout=20000)
                    search_input = page.locator(
                        "input[type='text'][placeholder*='Search'], input[type='search'], "
                        "#search-box-input, [data-testid='search-box-input']"
                    ).first
                    await search_input.fill(query)
                    await page.wait_for_timeout(1500)
                    await search_input.press("Enter")
                    await page.wait_for_timeout(3000)
                    # Now on the for-sale page; switch to rentals
                    current_url = page.url
                    # Replace for-sale path with rental path
                    rental_url = re.sub(
                        r"(/filter/.*)?$", "/apartments-for-rent" + bed_filter, current_url.rstrip("/")
                    )
                    if "/apartments-for-rent" not in rental_url:
                        rental_url = current_url.rstrip("/") + "/apartments-for-rent" + bed_filter
                    await page.goto(rental_url, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    return {"error": "Could not connect to Redfin."}

            try:
                await page.wait_for_selector(
                    ".MapHomeCardReact, [class*='HomeCard']", timeout=8000
                )
            except Exception:
                return {"rentals": [], "total": 0}

            await page.wait_for_timeout(1500)

            # Scroll to load more rental listings
            for _ in range(4):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(800)

            rentals = await page.evaluate(_REDFIN_RENT_JS)
        finally:
            try:
                await context.close()
            except Exception:  # pragma: no cover - defensive
                pass

    rentals = [r for r in rentals if r.get("rent") and r["rent"] > 0][:40]
    if not rentals:
        return {"rentals": [], "total": 0}

    rents = [r["rent"] for r in rentals]
    rents.sort()
    avg_rent = sum(rents) / len(rents)
    median_rent = rents[len(rents) // 2]
    low_rent = rents[int(len(rents) * 0.25)] if len(rents) >= 4 else rents[0]
    high_rent = rents[int(len(rents) * 0.75)] if len(rents) >= 4 else rents[-1]

    return {
        "rentals": rentals[:15],
        "total": len(rentals),
        "stats": {
            "avg": round(avg_rent),
            "median": round(median_rent),
            "low": round(low_rent),
            "high": round(high_rent),
            "count": len(rents),
        },
    }


@app.post("/api/rent-estimate")
async def estimate_rent(request: Request):
    """Estimate market rent for a location using Redfin rental listings."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"rent:{client_ip}", 30):  # Sprint 14.5: 3 → 30.
        return JSONResponse(
            {"error": "Too many requests. Please wait a minute."},
            status_code=429,
        )

    body = await request.json()
    location = (body.get("location") or "").strip()
    if not location:
        return JSONResponse({"error": "Location is required."}, status_code=400)
    if len(location) > 200:
        return JSONResponse({"error": "Location too long."}, status_code=400)

    beds = body.get("beds")
    result = await _search_redfin_rentals(location, beds)

    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=404)

    return JSONResponse(result)


@app.post("/api/scrape")
async def scrape_property(request: Request):
    # Rate limit: 5 requests per minute per IP
    client_ip = request.client.host if request.client else "unknown"
    # Sprint 14.5: 5 → 30. Human pastes a URL at most a few times per minute;
    # batch flows use the separate `batch_scrape:{ip}` bucket (180/min per PR #11).
    if not _check_rate_limit(f"scrape:{client_ip}", 30):
        return JSONResponse({"error": "Too many requests. Please wait a minute before trying again."}, status_code=429)

    body = await request.json()
    url = body.get("url", "").strip()

    # --- Validate URL ---
    if not url:
        return JSONResponse({"error": "URL is required."}, status_code=400)

    if len(url) > 2000:
        return JSONResponse({"error": "URL is too long."}, status_code=400)

    parsed = urlparse(url)
    source = _detect_source(parsed.hostname)

    if source == "unknown":
        return JSONResponse(
            {"error": "Unsupported URL. Paste a Zillow or Redfin listing URL."},
            status_code=400,
        )

    # Source-specific path validation
    if source == "zillow" and not re.search(r"/homedetails/|/zpid_|/homes/", parsed.path or ""):
        return JSONResponse(
            {"error": "Please provide a direct Zillow property listing URL (e.g. zillow.com/homedetails/...)."},
            status_code=400,
        )
    if source == "redfin" and not re.search(r"/home/\d+", parsed.path or ""):
        return JSONResponse(
            {"error": "Please provide a direct Redfin property listing URL (e.g. redfin.com/.../home/12345)."},
            status_code=400,
        )

    # --- Fetch page (try httpx first, fallback to Playwright) ---
    html_text = None
    site_label = "Redfin" if source == "redfin" else "Zillow"

    # Sprint 10A §10-8: share the bot-wall sentinel table with the batch path.
    # Lazy import because batch.pipeline imports app.py at module level —
    # top-level import would circular.
    from batch.pipeline import _looks_like_bot_wall as _scrape_is_bot_wall

    # Attempt 1: httpx (fast, but Zillow often blocks this)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            resp = await client.get(url, headers=HEADERS)
        if resp.status_code < 400 and not _scrape_is_bot_wall(resp.text):
            html_text = resp.text
    except httpx.RequestError:
        pass

    # Attempt 2: Playwright headless browser
    if html_text is None:
        try:
            html_text = await _fetch_with_playwright(url)
            # Check for bot block even in Playwright response
            if html_text and _scrape_is_bot_wall(html_text):
                html_text = None
        except Exception:
            pass

    if not html_text:
        return JSONResponse(
            {"error": f"Could not fetch the {site_label} page. The site may be blocking automated requests. Try again later or enter data manually."},
            status_code=503,
        )

    # --- Parse HTML ---
    try:
        soup = BeautifulSoup(html_text, "lxml")
    except Exception:
        return JSONResponse(
            {"error": "Failed to parse the page HTML. The page may be malformed."},
            status_code=422,
        )

    # Check for CAPTCHA / bot block pages (Sprint 10A §10-8: broader sentinels).
    if soup.find("div", class_="captcha-container") or _scrape_is_bot_wall(html_text):
        return JSONResponse(
            {"error": f"{site_label} blocked the request. Please try again later or enter data manually."},
            status_code=503,
        )

    # --- Extract property data ---
    if source == "redfin":
        result = _extract_redfin(soup)
        if result:
            return JSONResponse(result)
        return JSONResponse(
            {"error": "Could not extract property data from this Redfin listing."},
            status_code=422,
        )

    # Zillow extraction strategies
    result = _extract_from_next_data(soup)
    if result:
        return JSONResponse(result)

    result = _extract_from_ld_json(soup)
    if result:
        return JSONResponse(result)

    result = _extract_from_dom(soup)
    if result:
        return JSONResponse(result)

    return JSONResponse(
        {"error": "Could not extract property data. Zillow may have changed their page structure. Try using a Redfin URL instead, or enter data manually."},
        status_code=422,
    )


AI_SYSTEM_PROMPT = (
    "You are a real estate investment analyst. Analyze this rental "
    "property deal and provide a plain-English investment summary "
    "with: 1) Overall Assessment, 2) Key Strengths, 3) Key Risks, "
    "4) Recommendation. Be concise but thorough. "
    "Jump straight to the analysis."
)


def _strip_thinking(text: str) -> str:
    """Remove thinking/reasoning blocks from LLM output."""
    # Strip <think>...</think> blocks (qwen3, deepseek-r1)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip plain-text thinking blocks that appear before the actual analysis.
    # Look for the first analysis header pattern and discard everything before it.
    header = re.search(
        r"^(#{1,3}\s+|\*\*\s*|\d+[\.\)]\s*\*\*\s*)"
        r"(Overall|Investment|Key Strength|Key Risk|Recommendation|Summary|Assessment|Analysis)",
        text, re.MULTILINE | re.IGNORECASE,
    )
    if header and header.start() > 100:
        text = text[header.start():].strip()
    return text


async def _analyze_with_ollama(metrics: str, model_override: str | None = None) -> str:
    """Call local Ollama API."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = model_override or os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{ollama_url}/api/chat",
            json={
                "model": ollama_model,
                "messages": [
                    {"role": "system", "content": AI_SYSTEM_PROMPT},
                    {"role": "user", "content": metrics},
                ],
                "stream": False,
            },
        )
    if resp.status_code != 200:
        # Sprint 10A §10-5: don't embed resp.text in the exception message —
        # it bubbles through logger.exception server-side and we want the
        # upstream body gated to .debug, not .error logs.
        logger.debug("Ollama non-200 body: %s", resp.text[:200])
        raise Exception(f"Ollama error: HTTP {resp.status_code}")
    data = resp.json()
    return _strip_thinking(data["message"]["content"])


async def _analyze_with_lmstudio(metrics: str, model_override: str | None = None) -> str:
    """Call LM Studio's OpenAI-compatible API."""
    lmstudio_url = os.getenv("LMSTUDIO_URL", "http://localhost:1234")
    lmstudio_model = model_override or os.getenv("LMSTUDIO_MODEL", "")  # empty = use whatever is loaded
    async with httpx.AsyncClient(timeout=300) as client:
        payload = {
            "messages": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": metrics},
            ],
            "temperature": 0.7,
            "max_tokens": 8192,
            "stream": False,
        }
        if lmstudio_model:
            payload["model"] = lmstudio_model
        resp = await client.post(
            f"{lmstudio_url}/v1/chat/completions",
            json=payload,
        )
    if resp.status_code != 200:
        # Sprint 10A §10-5: scrub upstream body from the exception message.
        logger.debug("LM Studio non-200 body: %s", resp.text[:200])
        raise Exception(f"LM Studio error: HTTP {resp.status_code}")
    data = resp.json()
    return _strip_thinking(data["choices"][0]["message"]["content"])


async def _analyze_with_anthropic(metrics: str, api_key: str, model_override: str | None = None) -> str:
    """Call Anthropic Claude API."""
    anthropic_model = model_override or "claude-sonnet-4-6"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": anthropic_model,
                "max_tokens": 1024,
                "system": AI_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": metrics}],
            },
        )
    if resp.status_code != 200:
        # Sprint 10A §10-5: scrub upstream body from the exception message.
        logger.debug("Anthropic non-200 body: %s", resp.text[:200])
        raise Exception(f"Anthropic API error: HTTP {resp.status_code}")
    data = resp.json()
    return data["content"][0]["text"]


def _resolve_provider():
    """Return (provider, api_key) based on env configuration."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    provider = os.getenv("AI_PROVIDER", "auto").lower()
    return provider, api_key


@app.post("/api/analyze-ai")
async def analyze_ai(request: Request):
    # Rate limit: 10 requests per minute per IP
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"ai:{client_ip}", 30):  # Sprint 14.5: 10 → 30.
        return JSONResponse({"error": "Too many requests. Please wait before trying again."}, status_code=429)

    body = await request.json()
    metrics = body.get("metrics", "")
    model = body.get("model")  # optional model override
    if not metrics:
        return JSONResponse(
            {"error": "Missing 'metrics' in request body."},
            status_code=400,
        )
    if len(metrics) > 50_000:
        return JSONResponse(
            {"error": "Input too large."},
            status_code=400,
        )

    # Determine AI provider
    provider, api_key = _resolve_provider()

    # LM Studio provider
    if provider == "lmstudio":
        try:
            text = await _analyze_with_lmstudio(metrics, model_override=model)
            return JSONResponse({"analysis": text, "provider": "lmstudio"})
        except Exception:
            # Security: never return str(exc) to the client — upstream error
            # bodies can include request headers. Log traceback server-side
            # with a request_id for correlation (same pattern as M1/M3).
            rid = uuid.uuid4().hex
            logger.exception("analyze-ai lmstudio call failed (request_id=%s)", rid)
            return JSONResponse(
                _error_envelope(
                    "AI_SERVICE_ERROR",
                    "LM Studio is not running or not reachable. Start LM Studio, load a model, and enable the local server.",
                    rid,
                ),
                status_code=502,
            )

    # Auto mode: try lmstudio first, then ollama, then anthropic
    if provider == "auto":
        # Try LM Studio
        lmstudio_url = os.getenv("LMSTUDIO_URL", "http://localhost:1234")
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                probe = await client.get(f"{lmstudio_url}/v1/models")
            if probe.status_code == 200:
                try:
                    text = await _analyze_with_lmstudio(metrics, model_override=model)
                    return JSONResponse({"analysis": text, "provider": "lmstudio"})
                except Exception:
                    pass
        except Exception:
            pass

    # Ollama provider (explicit or auto-detected)
    if provider == "ollama" or (provider == "auto" and not api_key):
        try:
            text = await _analyze_with_ollama(metrics, model_override=model)
            return JSONResponse({"analysis": text, "provider": "ollama"})
        except Exception:
            if api_key:
                pass  # fall through to Anthropic
            else:
                # Security: no str(exc) to client. Log with request_id.
                rid = uuid.uuid4().hex
                logger.exception("analyze-ai ollama call failed (request_id=%s)", rid)
                ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
                return JSONResponse(
                    _error_envelope(
                        "AI_SERVICE_ERROR",
                        f"Ollama is not running or model not available. Start Ollama: `ollama serve` then `ollama pull {ollama_model}`.",
                        rid,
                    ),
                    status_code=502,
                )

    if not api_key:
        return JSONResponse(
            {"error": f"No AI provider configured. Either:\n1) Set ANTHROPIC_API_KEY in .env (paid)\n2) Run LM Studio locally (free): set AI_PROVIDER=lmstudio\n3) Run Ollama locally (free): ollama serve && ollama pull {os.getenv('OLLAMA_MODEL', 'llama3.2:3b')}"},
            status_code=400,
        )

    try:
        text = await _analyze_with_anthropic(metrics, api_key, model_override=model)
        return JSONResponse({"analysis": text, "provider": "anthropic"})
    except (httpx.RequestError, httpx.TimeoutException):
        return JSONResponse(
            {"error": "Could not reach AI service. Check your connection and try again."},
            status_code=502,
        )
    except Exception:
        # M1 fix (BATCH_DESIGN §G.1): never return str(exc) to clients. The
        # upstream exception may contain API response bodies, stack frames, or
        # file paths. Log the traceback server-side keyed by request_id.
        request_id = uuid.uuid4().hex
        logger.exception("analyze-ai Anthropic call failed (request_id=%s)", request_id)
        return JSONResponse(
            _error_envelope("AI_SERVICE_ERROR", "AI service error", request_id),
            status_code=502,
        )


# ---------------------------------------------------------------------------
# GET /api/models — list available models from the configured AI provider
# ---------------------------------------------------------------------------

ANTHROPIC_MODELS = [
    {"id": "claude-opus-4-7", "name": "Claude Opus 4.7"},
    {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
    {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
]


async def _get_lmstudio_models() -> dict | None:
    """Fetch models from LM Studio. Returns dict or None on failure."""
    lmstudio_url = os.getenv("LMSTUDIO_URL", "http://localhost:1234")
    current = os.getenv("LMSTUDIO_MODEL", "")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{lmstudio_url}/v1/models")
        if resp.status_code != 200:
            return None
        data = resp.json()
        models = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            # Filter out embedding models
            if "embed" in mid.lower():
                continue
            models.append({"id": mid, "name": mid})
        if not current and models:
            current = models[0]["id"]
        return {"provider": "lmstudio", "models": models, "current": current}
    except Exception:
        return None


async def _get_ollama_models() -> dict | None:
    """Fetch models from Ollama. Returns dict or None on failure."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    current = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
        if resp.status_code != 200:
            return None
        data = resp.json()
        models = []
        for m in data.get("models", []):
            mid = m.get("name", "") or m.get("model", "")
            models.append({"id": mid, "name": mid})
        return {"provider": "ollama", "models": models, "current": current}
    except Exception:
        return None


def _get_anthropic_models() -> dict | None:
    """Return hardcoded Anthropic models if API key is set."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return {
        "provider": "anthropic",
        "models": ANTHROPIC_MODELS,
        "current": "claude-sonnet-4-6",
    }


@app.get("/api/models")
async def list_models():
    provider, api_key = _resolve_provider()

    if provider == "lmstudio":
        result = await _get_lmstudio_models()
        if result:
            return JSONResponse(result)
        return JSONResponse({"error": "LM Studio is not reachable."}, status_code=502)

    if provider == "ollama":
        result = await _get_ollama_models()
        if result:
            return JSONResponse(result)
        return JSONResponse({"error": "Ollama is not reachable."}, status_code=502)

    if provider == "anthropic":
        result = _get_anthropic_models()
        if result:
            return JSONResponse(result)
        return JSONResponse({"error": "ANTHROPIC_API_KEY is not set."}, status_code=400)

    # auto: try lmstudio -> ollama -> anthropic
    result = await _get_lmstudio_models()
    if result:
        return JSONResponse(result)

    result = await _get_ollama_models()
    if result:
        return JSONResponse(result)

    result = _get_anthropic_models()
    if result:
        return JSONResponse(result)

    return JSONResponse(
        {"error": "No AI provider available."},
        status_code=502,
    )


# ---------------------------------------------------------------------------
# POST /api/analyze-ai-stream — SSE streaming version of analyze-ai
# ---------------------------------------------------------------------------

async def _stream_lmstudio(metrics: str, model_override: str | None = None):
    """Stream from LM Studio's OpenAI-compatible API."""
    lmstudio_url = os.getenv("LMSTUDIO_URL", "http://localhost:1234")
    lmstudio_model = model_override or os.getenv("LMSTUDIO_MODEL", "")

    payload = {
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": metrics},
        ],
        "temperature": 0.7,
        "max_tokens": 8192,
        "stream": True,
    }
    if lmstudio_model:
        payload["model"] = lmstudio_model

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream(
            "POST", f"{lmstudio_url}/v1/chat/completions", json=payload
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                yield f"data: {json.dumps({'error': f'LM Studio error: {resp.status_code} - {body[:200].decode()}'})}\n\n"
                return
            buffer = ""
            in_think = False
            found_header = False
            pending = ""
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content", "")
                if not token:
                    continue

                # Strip thinking: handle <think> tags
                processed = _process_stream_token(token, buffer, in_think, found_header, pending)
                buffer = processed["buffer"]
                in_think = processed["in_think"]
                found_header = processed["found_header"]
                pending = processed["pending"]
                if processed["output"]:
                    yield f"data: {json.dumps({'token': processed['output'], 'done': False})}\n\n"
    yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"


async def _stream_ollama(metrics: str, model_override: str | None = None):
    """Stream from Ollama API."""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = model_override or os.getenv("OLLAMA_MODEL", "llama3.2:3b")

    payload = {
        "model": ollama_model,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": metrics},
        ],
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream(
            "POST", f"{ollama_url}/api/chat", json=payload
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                yield f"data: {json.dumps({'error': f'Ollama error: {resp.status_code} - {body[:200].decode()}'})}\n\n"
                return
            buffer = ""
            in_think = False
            found_header = False
            pending = ""
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = chunk.get("message", {}).get("content", "")
                if not token:
                    if chunk.get("done"):
                        break
                    continue

                processed = _process_stream_token(token, buffer, in_think, found_header, pending)
                buffer = processed["buffer"]
                in_think = processed["in_think"]
                found_header = processed["found_header"]
                pending = processed["pending"]
                if processed["output"]:
                    yield f"data: {json.dumps({'token': processed['output'], 'done': False})}\n\n"
    yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"


async def _stream_anthropic(metrics: str, api_key: str, model_override: str | None = None):
    """Stream from Anthropic API."""
    anthropic_model = model_override or "claude-sonnet-4-6"
    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": anthropic_model,
                "max_tokens": 1024,
                "system": AI_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": metrics}],
                "stream": True,
            },
        ) as resp:
            if resp.status_code != 200:
                # M3 fix (BATCH_DESIGN §G.1): do not forward the upstream
                # response body to the client — it can contain API keys in
                # error reflections, upstream request IDs, or provider PII.
                # Log full body server-side, emit a generic SSE error with a
                # correlation request_id.
                body = await resp.aread()
                request_id = uuid.uuid4().hex
                logger.error(
                    "Anthropic stream HTTP %s (request_id=%s): %s",
                    resp.status_code, request_id, body[:500].decode(errors="replace"),
                )
                yield f"data: {json.dumps({'error': 'AI service error', 'request_id': request_id})}\n\n"
                return
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str:
                    continue
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                event_type = chunk.get("type", "")
                if event_type == "content_block_delta":
                    delta = chunk.get("delta", {})
                    token = delta.get("text", "")
                    if token:
                        yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"
                elif event_type == "message_stop":
                    break
    yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"


def _process_stream_token(
    token: str, buffer: str, in_think: bool, found_header: bool, pending: str
) -> dict:
    """Process a streaming token, stripping thinking blocks.

    Returns dict with keys: output, buffer, in_think, found_header, pending.
    """
    output = ""
    full = buffer + token

    # Handle <think> tags
    while True:
        if in_think:
            end_idx = full.find("</think>")
            if end_idx == -1:
                # Still inside think block, consume everything
                return {"output": output, "buffer": "", "in_think": True, "found_header": found_header, "pending": pending}
            else:
                full = full[end_idx + 8:]
                in_think = False
        else:
            start_idx = full.find("<think>")
            if start_idx != -1:
                # Text before <think> is real content
                before = full[:start_idx]
                if before:
                    pending += before
                full = full[start_idx + 7:]
                in_think = True
            else:
                break

    pending += full

    # If we haven't found the analysis header yet, check if the pending text
    # has enough content to determine it starts with "Thinking Process" or similar.
    if not found_header:
        # Check if the pending text contains an analysis header
        header = re.search(
            r"^(#{1,3}\s+|\*\*\s*|\d+[\.\)]\s*\*\*\s*)"
            r"(Overall|Investment|Key Strength|Key Risk|Recommendation|Summary|Assessment|Analysis)",
            pending, re.MULTILINE | re.IGNORECASE,
        )
        if header and header.start() > 100:
            # There's a thinking preamble — skip it
            pending = pending[header.start():]
            found_header = True
            output += pending
            pending = ""
        elif header:
            # Header found near the start — this is real content
            found_header = True
            output += pending
            pending = ""
        elif len(pending) > 300:
            # We've buffered enough without finding a thinking preamble, just emit
            found_header = True
            output += pending
            pending = ""
        # else: keep buffering
    else:
        output += pending
        pending = ""

    return {"output": output, "buffer": "", "in_think": in_think, "found_header": found_header, "pending": pending}


@app.post("/api/analyze-ai-stream")
async def analyze_ai_stream(request: Request):
    # Rate limit: 10 requests per minute per IP
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"ai-stream:{client_ip}", 30):  # Sprint 14.5: 10 → 30.
        return JSONResponse({"error": "Too many requests. Please wait before trying again."}, status_code=429)

    body = await request.json()
    metrics = body.get("metrics", "")
    model = body.get("model")  # optional model override
    if not metrics:
        return JSONResponse(
            {"error": "Missing 'metrics' in request body."},
            status_code=400,
        )
    if len(metrics) > 50_000:
        return JSONResponse(
            {"error": "Input too large."},
            status_code=400,
        )

    provider, api_key = _resolve_provider()

    async def _pick_generator():
        # LM Studio explicit
        if provider == "lmstudio":
            return _stream_lmstudio(metrics, model_override=model)

        # Auto: try lmstudio first
        if provider == "auto":
            lmstudio_url = os.getenv("LMSTUDIO_URL", "http://localhost:1234")
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    probe = await client.get(f"{lmstudio_url}/v1/models")
                if probe.status_code == 200:
                    return _stream_lmstudio(metrics, model_override=model)
            except Exception:
                pass

        # Ollama explicit or auto fallback
        if provider == "ollama" or (provider == "auto" and not api_key):
            return _stream_ollama(metrics, model_override=model)

        # Anthropic
        if api_key:
            return _stream_anthropic(metrics, api_key, model_override=model)

        return None

    gen = await _pick_generator()
    if gen is None:
        return JSONResponse(
            {"error": f"No AI provider available. Configure one in .env."},
            status_code=400,
        )

    async def _with_timeout(generator, timeout_seconds=300):
        """Wrap a streaming generator with a timeout."""
        try:
            async for chunk in generator:
                yield chunk
        except asyncio.CancelledError:
            yield f"data: {json.dumps({'error': 'Stream timed out.'})}\n\n"

    return StreamingResponse(
        _with_timeout(gen),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Batch analyze + supporting endpoints (BATCH_DESIGN.md §B).
# Imports are module-local so a missing dependency (e.g. nothing in `batch/`)
# can't crash app boot; the handlers error out gracefully instead.
# ---------------------------------------------------------------------------

from batch.db import get_connection as _batch_conn  # noqa: E402
from batch.db import url_hash as _batch_url_hash  # noqa: E402
from batch.pipeline import run_sync_batch as _run_sync_batch  # noqa: E402
from batch.pipeline import process_url as _batch_process_url  # noqa: E402
from batch.llm import extract_property as _llm_extract_property  # noqa: E402
from batch.llm import is_cache_stale as _llm_is_cache_stale  # noqa: E402
from batch.async_pipeline import (  # noqa: E402
    submit_async_batch as _submit_async_batch,
    poll_async_batch as _poll_async_batch,
    reconcile_pending_batches_on_startup as _reconcile_async_batches,
)
from scripts.init_db import DEFAULT_DB_PATH as _BATCH_DB_PATH  # noqa: E402
from spec import constants as _spec_constants  # noqa: E402 — Sprint 11-4


_BATCH_MAX_URLS_SYNC = 1000
_BATCH_HARD_CAP = 1500
# Anthropic Message Batches hard limit is 10_000 requests per submission.
_BATCH_ASYNC_MAX_URLS = 10_000
_BATCH_ASYNC_WARN_URLS = 1_000

_URL_HASH_RE = re.compile(r"[a-f0-9]{64}")


def _validate_url_hash(url_hash: str):
    """Return a 400 JSONResponse if url_hash is malformed, else None."""
    if not _URL_HASH_RE.fullmatch(url_hash or ""):
        return JSONResponse(
            _error_envelope(
                "VALIDATION_ERROR",
                "Invalid url_hash format",
                uuid.uuid4().hex,
            ),
            status_code=400,
        )
    return None


@app.post("/api/batch-analyze")
async def batch_analyze(request: Request):
    """Sync batch endpoint — BATCH_DESIGN §B.1."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"batch:{client_ip}", 10):  # Sprint 14.5: 3 → 10 submissions/min.
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("RATE_LIMIT_EXCEEDED", "Too many batch requests.", request_id),
            status_code=429,
        )

    try:
        body = await request.json()
    except Exception:
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", "Invalid JSON body.", request_id),
            status_code=400,
        )

    urls = body.get("urls") or []
    preset_name = body.get("preset_name")
    include_narrative = bool(body.get("include_narrative", True))

    # Defense-in-depth: apply the same per-URL scheme/hostname/length checks
    # as the async endpoint. Previously only _scrape_url enforced them, which
    # left this endpoint relying on downstream validation — a trust-boundary
    # asymmetry flagged in the post-V1 security audit (M-4).
    clean_urls, err = _validate_batch_urls(urls)
    if err is not None:
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", err[0], request_id),
            status_code=err[1],
        )
    urls = clean_urls
    if len(urls) > _BATCH_MAX_URLS_SYNC:
        if len(urls) > _BATCH_HARD_CAP:
            request_id = uuid.uuid4().hex
            return JSONResponse(
                _error_envelope(
                    "VALIDATION_ERROR",
                    f"Too many URLs. Sync cap is {_BATCH_MAX_URLS_SYNC}; hard cap {_BATCH_HARD_CAP}.",
                    request_id,
                ),
                status_code=400,
            )
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope(
                "VALIDATION_ERROR",
                f"Sync batch supports up to {_BATCH_MAX_URLS_SYNC} URLs — use async mode (Commit 2).",
                request_id,
            ),
            status_code=400,
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")

    # Sprint 15.5: convert batch-analyze to submit-and-poll. Previously
    # this call blocked until `_run_sync_batch` finished, which worked fine
    # at the 30-100 URL cap but times out everything above 150. Now we:
    #   1. pre-write a `batches` row with status='pending' so status polls
    #      can find it immediately,
    #   2. fire the actual work via asyncio.create_task so it continues
    #      after we return,
    #   3. return 202 + {batch_id, status: 'pending'} right away so the
    #      client can start its existing auto-poll machinery.
    # The background task's final INSERT is now an UPSERT that flips the
    # same row to status='complete' when it finishes.
    from batch.db import get_connection as _get_conn, new_uuid_hex as _new_uuid, utc_now_iso as _now_iso  # noqa: PLC0415
    batch_id = _new_uuid()
    created_at = _now_iso()
    try:
        conn = _get_conn(str(_BATCH_DB_PATH))
        try:
            conn.execute(
                """INSERT INTO batches
                   (batch_id, created_at, mode, input_count, status, preset_name)
                   VALUES (?, ?, 'sync', ?, 'pending', ?)""",
                (batch_id, created_at, len(urls), preset_name),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        request_id = uuid.uuid4().hex
        logger.exception("batch-analyze pending-row insert failed (request_id=%s)", request_id)
        return JSONResponse(
            _error_envelope("INTERNAL_ERROR", "Batch submit failed.", request_id),
            status_code=500,
        )

    async def _bg_run() -> None:
        try:
            await _run_sync_batch(
                urls,
                db_path=str(_BATCH_DB_PATH),
                api_key=api_key,
                preset_name=preset_name,
                include_narrative=include_narrative,
                client_ip=client_ip,
                batch_id=batch_id,
                created_at=created_at,
            )
        except Exception:
            logger.exception("batch-analyze background task failed for %s", batch_id)
            # Best-effort flip the pending row to failed so the poll
            # response doesn't leave the client spinning forever.
            try:
                conn2 = _get_conn(str(_BATCH_DB_PATH))
                try:
                    conn2.execute(
                        "UPDATE batches SET status='failed', completed_at=?, error_reason=? WHERE batch_id=?",
                        (_now_iso(), "worker_exception", batch_id),
                    )
                    conn2.commit()
                finally:
                    conn2.close()
            except Exception:
                logger.exception("failed to mark batch_id=%s as failed", batch_id)

    asyncio.create_task(_bg_run())

    return JSONResponse(
        {
            "batch_id": batch_id,
            "mode": "sync",
            "status": "pending",
            "created_at": created_at,
            "input_count": len(urls),
        },
        status_code=202,
    )


_BATCH_ASYNC_MAX_URL_LEN = 2000                  # matches /api/scrape guard
_BATCH_ASYNC_MAX_BODY_BYTES = 8 * 1024 * 1024    # 8 MB total request body


def _validate_batch_urls(urls) -> tuple[list[str] | None, tuple[str, int] | None]:
    """Shared validation for /api/batch-analyze and /api/batch-submit-async.

    Returns (clean_urls, None) on success or (None, (error_message, status_code))
    on failure. Used by both endpoints so sync and async enforce identical
    scheme + hostname + length checks — defense-in-depth.
    """
    if not isinstance(urls, list) or not urls:
        return None, ("`urls` must be a non-empty array.", 400)
    clean: list[str] = []
    for raw_u in urls:
        if not isinstance(raw_u, str):
            return None, ("Each URL must be a string.", 400)
        u = raw_u.strip()
        if not u:
            continue
        if len(u) > _BATCH_ASYNC_MAX_URL_LEN:
            return None, ("URL too long.", 400)
        try:
            parsed_u = urlparse(u)
        except Exception:
            parsed_u = None
        if (
            parsed_u is None
            or parsed_u.scheme not in ("http", "https")
            or _detect_source(parsed_u.hostname) == "unknown"
        ):
            return None, ("Unsupported URL. Paste Zillow or Redfin listing URLs.", 400)
        clean.append(u)
    if not clean:
        return None, ("`urls` must contain at least one non-empty URL.", 400)
    return clean, None


@app.post("/api/batch-submit-async")
async def batch_submit_async(request: Request):
    """Async batch submit — BATCH_DESIGN §B.2. Delegates the LLM extraction
    step to Anthropic Message Batches so we can ship arbitrarily large batches
    at 50% of the standard per-token rate with a 24h SLA."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"batch:{client_ip}", 10):  # Sprint 14.5: 3 → 10 submissions/min.
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("RATE_LIMIT_EXCEEDED", "Too many batch requests.", request_id),
            status_code=429,
        )

    # Early Content-Length check — reject obvious oversize uploads before
    # reading the body into memory. We still verify the post-read length
    # below for chunked encodings that don't advertise Content-Length.
    content_length_hdr = request.headers.get("content-length")
    if content_length_hdr:
        try:
            declared = int(content_length_hdr)
        except ValueError:
            declared = 0
        if declared > _BATCH_ASYNC_MAX_BODY_BYTES:
            request_id = uuid.uuid4().hex
            return JSONResponse(
                _error_envelope("VALIDATION_ERROR", "Payload too large.", request_id),
                status_code=413,
            )

    raw_body = await request.body()
    if len(raw_body) > _BATCH_ASYNC_MAX_BODY_BYTES:
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", "Payload too large.", request_id),
            status_code=413,
        )
    try:
        body = json.loads(raw_body) if raw_body else {}
    except Exception:
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", "Invalid JSON body.", request_id),
            status_code=400,
        )

    urls = body.get("urls") or []
    preset_name = body.get("preset_name")
    # Shared validator — identical checks as sync endpoint.
    clean_urls, err = _validate_batch_urls(urls)
    if err is not None:
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", err[0], request_id),
            status_code=err[1],
        )
    urls = clean_urls
    if len(urls) > _BATCH_ASYNC_MAX_URLS:
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope(
                "VALIDATION_ERROR",
                f"Too many URLs. Async cap is {_BATCH_ASYNC_MAX_URLS} (Anthropic batch hard limit).",
                request_id,
            ),
            status_code=400,
        )
    if len(urls) > _BATCH_ASYNC_WARN_URLS:
        logger.warning(
            "Large async batch submitted (%d URLs, client=%s) — provider cap is %d",
            len(urls), client_ip, _BATCH_ASYNC_MAX_URLS,
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    try:
        result = await _submit_async_batch(
            urls,
            db_path=str(_BATCH_DB_PATH),
            client_ip=client_ip,
            api_key=api_key,
            preset_name=preset_name,
        )
    except Exception:
        request_id = uuid.uuid4().hex
        logger.exception("batch-submit-async failed (request_id=%s)", request_id)
        return JSONResponse(
            _error_envelope("INTERNAL_ERROR", "Async batch submission failed.", request_id),
            status_code=500,
        )

    status = result.get("status")
    if status == "complete":
        return JSONResponse(result)
    if status == "failed":
        return JSONResponse(result, status_code=502)
    # pending — provider accepted the batch.
    return JSONResponse(result, status_code=202)


# =====================================================================
# Sprint 11-4: /api/scan-zips — paste a list of ZIPs + optional preset,
# fan out Redfin searches, take top N per ZIP, reject excluded markets,
# and submit survivors to the existing batch pipeline. Inherits every
# Sprint 10A invariant: _error_envelope paths, rate limiting, shared
# URL validator, loopback-only profile. Must NOT echo profile PII
# (income, cash, credit) in any response.
# =====================================================================
# Sprint 16.11: cap raised 20 → 100. Bundle 2's per-ZIP streaming
# architecture means each ZIP is an independent background sub-batch, so
# the old 20-cap's premise (one combined sync envelope) no longer
# applies. At 100 ZIPs: Redfin-phase ~25 min under Semaphore(3) + 100
# concurrent _run_sync_batch background tasks — still within typical
# Anthropic per-minute rate limits. Above ~100 the concurrent LLM fan-
# out risks tripping rate limits and needs server-side throttling.
_SCAN_ZIPS_MAX_ZIPS = 100
_SCAN_ZIPS_DEFAULT_TOP_N = 10
# Sprint 16.9: cap raised 50 → 1000. Redfin's own lazy-load still caps
# rendered cards per ZIP around 150-250, and `top_n` is applied AFTER
# the multi-family post-filter, so this bump is aspirational — it stops
# the silent truncation when Redfin actually has more, without changing
# the practical maximum for most scans.
_SCAN_ZIPS_MAX_TOP_N = 1000
_SCAN_ZIPS_MAX_STRING_LEN = 64
_ZIP_RE = re.compile(r"^\d{5}$")
# Process-lifetime semaphore: Redfin searches are Playwright-heavy and a
# second parallel scan would starve the browser pool. Sprint 11-5 ask.
_scan_zips_sem = asyncio.Semaphore(1)


def _scan_excluded_city_match(address: str, excluded_cities: list) -> str | None:
    """Case-insensitive substring match; returns the matched city name."""
    if not address:
        return None
    lower = address.lower()
    for city in excluded_cities or []:
        if not isinstance(city, str) or not city:
            continue
        if city.lower() in lower:
            return city
    return None


@app.post("/api/scan-zips")
async def scan_zips(request: Request):
    """Orchestrator: N ZIPs → N Redfin searches → top K per ZIP → batch.
    Returns a single envelope with rankings (sync) or batchId (async), plus
    per-zip + rejection summaries so Jose can see why anything was dropped.
    """
    request_id = uuid.uuid4().hex
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"scan:{client_ip}", 10):  # Sprint 14.5: 2 → 10 submissions/min.
        return JSONResponse(
            _error_envelope("RATE_LIMIT_EXCEEDED", "Too many scan requests.", request_id),
            status_code=429,
        )
    if _scan_zips_sem.locked():
        return JSONResponse(
            _error_envelope(
                "SCAN_BUSY",
                "Another ZIP scan is already running. Wait for it to finish.",
                request_id,
            ),
            status_code=429,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", "Invalid JSON body.", request_id),
            status_code=400,
        )

    # --- Validate zips ---
    raw_zips = body.get("zips") or []
    if not isinstance(raw_zips, list) or not raw_zips:
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", "`zips` must be a non-empty array.", request_id),
            status_code=400,
        )
    if len(raw_zips) > _SCAN_ZIPS_MAX_ZIPS:
        return JSONResponse(
            _error_envelope(
                "VALIDATION_ERROR",
                f"Too many ZIPs. Cap is {_SCAN_ZIPS_MAX_ZIPS} per scan.",
                request_id,
            ),
            status_code=400,
        )
    zips: list[str] = []
    for raw_z in raw_zips:
        if not isinstance(raw_z, str):
            return JSONResponse(
                _error_envelope(
                    "VALIDATION_ERROR",
                    "Each ZIP must be a 5-digit string.",
                    request_id,
                ),
                status_code=400,
            )
        z = raw_z.strip()
        if not _ZIP_RE.fullmatch(z):
            return JSONResponse(
                _error_envelope(
                    "VALIDATION_ERROR",
                    "Invalid ZIP — expected 5-digit US ZIP.",
                    request_id,
                ),
                status_code=400,
            )
        zips.append(z)
    seen_z: set[str] = set()
    zips = [z for z in zips if not (z in seen_z or seen_z.add(z))]

    # --- Validate top_n_per_zip ---
    try:
        top_n = int(body.get("top_n_per_zip") or _SCAN_ZIPS_DEFAULT_TOP_N)
    except (TypeError, ValueError):
        top_n = _SCAN_ZIPS_DEFAULT_TOP_N
    top_n = max(1, min(top_n, _SCAN_ZIPS_MAX_TOP_N))

    # --- Resolve preset (optional) ---
    preset_name = body.get("preset_name") or None
    if preset_name is not None:
        if (
            not isinstance(preset_name, str)
            or len(preset_name) > _SCAN_ZIPS_MAX_STRING_LEN
            or preset_name not in _spec_constants.presets
        ):
            return JSONResponse(
                _error_envelope("VALIDATION_ERROR", "Invalid preset_name.", request_id),
                status_code=400,
            )
    preset_search = (
        (_spec_constants.presets.get(preset_name) or {}).get("search", {})
        if preset_name
        else {}
    )

    # --- Per-request filter overrides (caller beats preset) ---
    def _opt_int(field, default=None):
        v = body.get(field)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _opt_str(field):
        v = body.get(field)
        if v is None:
            return None
        if not isinstance(v, str) or len(v) > _SCAN_ZIPS_MAX_STRING_LEN:
            return None
        return v.strip() or None

    min_price = _opt_int("min_price", preset_search.get("minPrice"))
    max_price = _opt_int("max_price", preset_search.get("maxPrice"))
    min_beds = _opt_int("min_beds", preset_search.get("minBeds"))
    property_type = _opt_str("property_type") or preset_search.get("propertyType")
    max_results_per_search = max(top_n, 20)

    mode = (_opt_str("mode") or "").lower()
    if mode not in ("sync", "async", ""):
        mode = ""

    # --- Apply ZIP-level exclusion ---
    excluded_zips_set = set(_spec_constants.zip_tiers.get("excludedZips") or [])
    excluded_cities = _spec_constants.zip_tiers.get("excludedCities") or []
    rejected_zip_excluded: list = []
    scan_zips_in: list[str] = []
    for z in zips:
        if z in excluded_zips_set:
            rejected_zip_excluded.append({"zip": z, "reason": f"ZIP {z} is in excludedZips"})
        else:
            scan_zips_in.append(z)

    if not scan_zips_in:
        return JSONResponse(
            _error_envelope(
                "VALIDATION_ERROR",
                "All requested ZIPs are in the excluded list — nothing to scan.",
                request_id,
            ),
            status_code=400,
        )

    t0 = time.monotonic()
    search_sem = asyncio.Semaphore(3)

    async def _one_search(zip_code: str):
        filters = {
            "min_price": min_price,
            "max_price": max_price,
            "min_beds": min_beds,
            "property_type": property_type,
            "max_results": max_results_per_search,
        }
        async with search_sem:
            try:
                res = await _search_redfin_page(zip_code, filters)
                return zip_code, res
            except Exception:
                logger.exception(
                    "scan-zips search failed for zip %s (request_id=%s)",
                    zip_code, request_id,
                )
                return zip_code, {"error": "search_failed", "listings": []}

    async with _scan_zips_sem:
        results = await asyncio.gather(*[_one_search(z) for z in scan_zips_in])

        # Sprint 16.10: track survivors per-ZIP (not just a flat combined
        # list) so we can submit each ZIP as its own async sub-batch
        # downstream. Enables per-ZIP streaming — user sees each ZIP's
        # results as soon as that ZIP's analysis finishes, without waiting
        # for the slowest ZIP.
        survivors: list[str] = []
        survivors_by_zip: dict[str, list[str]] = {}
        seen_urls: set[str] = set()
        per_zip_summary: list = []
        rejected_city: list = []
        search_errors: list = []
        for zip_code, res in results:
            survivors_by_zip.setdefault(zip_code, [])
            if res.get("error") and not res.get("listings"):
                search_errors.append({"zip": zip_code, "reason": "search_failed"})
                per_zip_summary.append({"zip": zip_code, "found": 0, "kept": 0})
                continue
            listings = res.get("listings") or []
            usable = [l for l in listings if l.get("price") and l.get("listingUrl")]
            usable.sort(key=lambda l: (l.get("price") or 0))
            kept_for_zip = 0
            for l in usable:
                if kept_for_zip >= top_n:
                    break
                url = l.get("listingUrl") or ""
                if not url or url in seen_urls:
                    continue
                addr = l.get("address") or ""
                hit = _scan_excluded_city_match(addr, excluded_cities)
                if hit:
                    rejected_city.append({
                        "zip": zip_code,
                        "url": url,
                        "address": addr,
                        "reason": f"excluded city: {hit}",
                    })
                    continue
                seen_urls.add(url)
                survivors.append(url)
                survivors_by_zip[zip_code].append(url)
                kept_for_zip += 1
            per_zip_summary.append({
                "zip": zip_code,
                "found": len(listings),
                "kept": kept_for_zip,
            })

        rejected_block = {
            "zip_excluded": rejected_zip_excluded,
            "city_excluded": rejected_city,
            "search_errors": search_errors,
        }

        if not survivors:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "scan_zips zip_count=%d survivors=0 elapsed=%dms",
                len(scan_zips_in), elapsed_ms,
            )
            return JSONResponse({
                "status": "empty",
                "mode": "none",
                "request_id": request_id,
                "summary": {
                    "zip_count": len(zips),
                    "zips_scanned": len(scan_zips_in),
                    "survivors": 0,
                    "top_n_per_zip": top_n,
                    "elapsed_ms": elapsed_ms,
                },
                "per_zip": per_zip_summary,
                "rejected": rejected_block,
            })

        # Defense-in-depth: revalidate URLs with the shared batch validator.
        clean_urls, err = _validate_batch_urls(survivors)
        if err is not None:
            logger.warning(
                "scan-zips produced invalid URLs (request_id=%s): %s",
                request_id, err[0],
            )
            return JSONResponse(
                _error_envelope("VALIDATION_ERROR", err[0], request_id),
                status_code=err[1],
            )
        survivors = clean_urls

        # Sprint 16.10: scan mode semantics
        #   mode="sync"          → one combined sync batch, inline envelope
        #                          (old behavior — escape hatch for tiny scans)
        #   mode="async" | ""    → NEW per-ZIP streaming: each ZIP's URLs
        #                          become their own background batch, all
        #                          polled in parallel by the client. User
        #                          sees results surface per-ZIP as each
        #                          finishes (fastest-to-slowest order).
        #
        # Force-sync guardrail preserved from Sprint 12: reject above the
        # hard cap since a single sync envelope of that size would timeout
        # HTTP.
        if mode == "sync":
            if len(survivors) > _BATCH_HARD_CAP:
                return JSONResponse(
                    _error_envelope(
                        "VALIDATION_ERROR",
                        f"Force sync rejected: {len(survivors)} URLs exceeds hard cap {_BATCH_HARD_CAP}. Reduce ZIPs/top-N or choose Async.",
                        request_id,
                    ),
                    status_code=400,
                )
            want_stream = False
        else:
            want_stream = True

        api_key = os.getenv("ANTHROPIC_API_KEY")

        if want_stream:
            # Sprint 16.10: per-ZIP sub-batch fan-out. For each ZIP that
            # produced >0 survivors, insert a pending `batches` row, spawn
            # a background task that runs _run_sync_batch against just
            # that ZIP's URLs, and collect the batch_id. Return all
            # batch_ids in a single response — client polls each via the
            # existing /api/batch-status endpoint and appends results to
            # the unified store as each finishes.
            #
            # Uses the same create_task pattern as /api/batch-analyze's
            # Sprint 15.5 submit-and-poll refactor — local execution, not
            # the hours-long Anthropic Batches queue, so per-ZIP results
            # surface in 30-90s each.
            from batch.db import (  # noqa: PLC0415
                get_connection as _get_conn,
                new_uuid_hex as _new_uuid,
                utc_now_iso as _now_iso,
            )

            per_zip_batches: list[dict[str, Any]] = []
            # Iterate in the order the user submitted the ZIPs so the
            # streaming UI's "first ZIP, then next" mental model matches.
            for zip_code in scan_zips_in:
                zip_survivors = survivors_by_zip.get(zip_code) or []
                if not zip_survivors:
                    # No URLs to analyze → immediate null-batch entry so
                    # the UI can still render a "95205: 0 survivors" card.
                    per_zip_batches.append({
                        "zip": zip_code,
                        "batch_id": None,
                        "survivors": 0,
                        "status": "empty",
                    })
                    continue

                batch_id = _new_uuid()
                created_at = _now_iso()
                try:
                    conn = _get_conn(str(_BATCH_DB_PATH))
                    try:
                        conn.execute(
                            """INSERT INTO batches
                               (batch_id, created_at, mode, input_count, status, preset_name)
                               VALUES (?, ?, 'sync', ?, 'pending', ?)""",
                            (batch_id, created_at, len(zip_survivors), preset_name),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                except Exception:
                    logger.exception(
                        "scan-zips per-zip pending-row insert failed "
                        "(request_id=%s zip=%s)", request_id, zip_code,
                    )
                    per_zip_batches.append({
                        "zip": zip_code,
                        "batch_id": None,
                        "survivors": len(zip_survivors),
                        "status": "failed",
                        "error_reason": "pending_insert_failed",
                    })
                    continue

                # Spawn background task. Closure captures zip_survivors
                # and batch_id for THIS iteration (Python's default late-
                # binding would be wrong in a loop — use default-arg
                # trick to freeze both).
                async def _bg_run_zip(
                    _urls: list[str] = zip_survivors,
                    _batch_id: str = batch_id,
                    _created_at: str = created_at,
                    _zip: str = zip_code,
                ) -> None:
                    try:
                        await _run_sync_batch(
                            _urls,
                            db_path=str(_BATCH_DB_PATH),
                            api_key=api_key,
                            preset_name=preset_name,
                            include_narrative=True,
                            client_ip=client_ip,
                            batch_id=_batch_id,
                            created_at=_created_at,
                        )
                    except Exception:
                        logger.exception(
                            "scan-zips per-zip background task failed "
                            "(batch_id=%s zip=%s)", _batch_id, _zip,
                        )
                        try:
                            conn2 = _get_conn(str(_BATCH_DB_PATH))
                            try:
                                conn2.execute(
                                    "UPDATE batches SET status='failed', completed_at=?, error_reason=? WHERE batch_id=?",
                                    (_now_iso(), "worker_exception", _batch_id),
                                )
                                conn2.commit()
                            finally:
                                conn2.close()
                        except Exception:
                            logger.exception(
                                "failed to mark per-zip batch_id=%s as failed",
                                _batch_id,
                            )

                asyncio.create_task(_bg_run_zip())

                per_zip_batches.append({
                    "zip": zip_code,
                    "batch_id": batch_id,
                    "survivors": len(zip_survivors),
                    "status": "pending",
                    "created_at": created_at,
                })

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "scan_zips zip_count=%d survivors=%d mode=stream batches=%d elapsed=%dms",
                len(scan_zips_in), len(survivors),
                sum(1 for b in per_zip_batches if b.get("batch_id")),
                elapsed_ms,
            )
            return JSONResponse({
                "status": "streaming",
                "mode": "async-per-zip",
                "request_id": request_id,
                "batches": per_zip_batches,
                "summary": {
                    "zip_count": len(zips),
                    "zips_scanned": len(scan_zips_in),
                    "survivors": len(survivors),
                    "top_n_per_zip": top_n,
                    "elapsed_ms": elapsed_ms,
                },
                "per_zip": per_zip_summary,
                "rejected": rejected_block,
            }, status_code=202)

        try:
            envelope = await _run_sync_batch(
                survivors,
                db_path=str(_BATCH_DB_PATH),
                api_key=api_key,
                preset_name=preset_name,
                include_narrative=True,
                client_ip=client_ip,
            )
        except Exception:
            logger.exception(
                "scan-zips sync batch failed (request_id=%s)", request_id
            )
            return JSONResponse(
                _error_envelope(
                    "INTERNAL_ERROR", "Batch processing failed.", request_id,
                ),
                status_code=500,
            )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "scan_zips zip_count=%d survivors=%d mode=sync elapsed=%dms",
            len(scan_zips_in), len(survivors), elapsed_ms,
        )
        return JSONResponse({
            "status": "complete",
            "mode": "sync",
            "request_id": request_id,
            "envelope": envelope,
            "summary": {
                "zip_count": len(zips),
                "zips_scanned": len(scan_zips_in),
                "survivors": len(survivors),
                "top_n_per_zip": top_n,
                "elapsed_ms": elapsed_ms,
            },
            "per_zip": per_zip_summary,
            "rejected": rejected_block,
        })


_BATCH_ID_RE = re.compile(r"[a-f0-9]{32}")


@app.get("/api/batch-status/{batch_id}")
async def batch_status(batch_id: str):
    """Poll the status of a batch — BATCH_DESIGN §B.3.

    Sprint 15.5: mode='sync' batches (now async-local, polling-backed)
    short-circuit Anthropic entirely — their status lives wholly in our
    SQLite, so we just load the batch row and call the existing
    `_build_poll_response` helper.
    """
    if not _BATCH_ID_RE.fullmatch(batch_id or ""):
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", "Invalid batch_id format.", request_id),
            status_code=400,
        )
    # Sync-local fast path — check the row mode up front before burning a
    # provider call on something we own locally.
    try:
        from batch.async_pipeline import _load_batch_row as _load_row, _build_poll_response as _build_resp  # noqa: PLC0415
        batch_row = _load_row(str(_BATCH_DB_PATH), batch_id)
        if batch_row and batch_row.get("mode") == "sync":
            return JSONResponse(_build_resp(str(_BATCH_DB_PATH), batch_row))
    except Exception:
        logger.exception("batch-status sync-local fast-path failed for %s", batch_id)
        # Fall through to the async path — safe default, just slower.

    api_key = os.getenv("ANTHROPIC_API_KEY")
    try:
        result = await _poll_async_batch(
            batch_id, db_path=str(_BATCH_DB_PATH), api_key=api_key,
        )
    except Exception:
        request_id = uuid.uuid4().hex
        logger.exception("batch-status failed (request_id=%s)", request_id)
        return JSONResponse(
            _error_envelope("INTERNAL_ERROR", "Status poll failed.", request_id),
            status_code=500,
        )

    if result.get("status") == "unknown":
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("BATCH_NOT_FOUND", "Unknown batch_id.", request_id),
            status_code=404,
        )
    return JSONResponse(result)


@app.on_event("startup")
async def _on_startup_reconcile_async_batches():
    """Fire-and-forget: poll any async batches left pending on last boot."""
    try:
        asyncio.create_task(_reconcile_async_batches(str(_BATCH_DB_PATH)))
    except Exception:  # pragma: no cover - startup diagnostic
        logger.exception("Failed to schedule async batch reconcile")


@app.on_event("shutdown")
async def _on_shutdown_close_browser_pool():
    """Sprint 8-1: tear down the shared Playwright browser cleanly so we
    don't leak chromium processes between reloads."""
    try:
        await _shutdown_browser_pool()
    except Exception:  # pragma: no cover - shutdown diagnostic
        logger.exception("Browser pool shutdown failed")


@app.post("/api/property/extract")
async def property_extract(request: Request):
    """Single-property structured extraction (BATCH_DESIGN §B.5a).

    Scrapes (if needed) then runs the cached LLM extraction call. Honors
    `force: true` to bypass the cache.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"prop-extract:{client_ip}", 10):
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("RATE_LIMIT_EXCEEDED", "Too many requests.", request_id),
            status_code=429,
        )

    try:
        body = await request.json()
    except Exception:
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", "Invalid JSON body.", request_id),
            status_code=400,
        )

    url = (body.get("url") or "").strip()
    provided_hash = (body.get("url_hash") or "").strip() or None
    force = bool(body.get("force"))

    if not url and not provided_hash:
        request_id = uuid.uuid4().hex
        return JSONResponse(
            _error_envelope("VALIDATION_ERROR", "`url` or `url_hash` required.", request_id),
            status_code=400,
        )

    # If only url_hash is provided, look up the canonical_url from the DB.
    if not url and provided_hash:
        conn = _batch_conn(_BATCH_DB_PATH)
        try:
            row = conn.execute(
                "SELECT canonical_url FROM properties WHERE url_hash = ?",
                (provided_hash,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            request_id = uuid.uuid4().hex
            return JSONResponse(
                _error_envelope("PROPERTY_NOT_FOUND", "Unknown url_hash.", request_id),
                status_code=404,
            )
        url = row["canonical_url"]

    api_key = os.getenv("ANTHROPIC_API_KEY")

    # Run the single-URL pipeline via the batch machinery (guarantees parity).
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            # Soft-force: if force=True, delete cached llm_analysis first so the
            # pipeline re-runs extraction.
            if force and provided_hash is None:
                provided_hash = _batch_url_hash(url)
            if force:
                conn = _batch_conn(_BATCH_DB_PATH)
                try:
                    conn.execute(
                        "UPDATE properties SET llm_analysis = NULL, llm_analyzed_at = NULL WHERE url_hash = ?",
                        (provided_hash,),
                    )
                finally:
                    conn.close()

            # Run through the full per-URL pipeline.
            processed = await _batch_process_url(
                url=url,
                http_client=client,
                api_key=api_key,
                db_path=str(_BATCH_DB_PATH),
            )
        cached = processed.get("cache_stale_reason") is None and not force
        return JSONResponse({
            "url_hash": processed["url_hash"],
            "canonical_url": processed["canonical_url"],
            "cached": cached,
            "cache_stale_reason": "forced" if force else processed.get("cache_stale_reason"),
            "llm_analysis": processed.get("llm_analysis"),
            "tokens_used": {
                "input": processed.get("llm_tokens", {}).get("input"),
                "cached_input_read": processed.get("llm_tokens", {}).get("cached_input_read"),
                "output": processed.get("llm_tokens", {}).get("output"),
            },
            "insurance_breakdown": processed.get("insurance_breakdown"),
        })
    except Exception:
        request_id = uuid.uuid4().hex
        logger.exception("property-extract failed (request_id=%s)", request_id)
        return JSONResponse(
            _error_envelope("LLM_EXTRACTION_FAILED", "Extraction failed.", request_id),
            status_code=502,
        )


@app.get("/api/property/{url_hash}")
async def property_cached(url_hash: str):
    """Read-only projection of everything we know about a url_hash (§B.5b)."""
    bad = _validate_url_hash(url_hash)
    if bad is not None:
        return bad
    conn = _batch_conn(_BATCH_DB_PATH)
    try:
        prop = conn.execute(
            "SELECT * FROM properties WHERE url_hash = ?", (url_hash,)
        ).fetchone()
        if not prop:
            request_id = uuid.uuid4().hex
            return JSONResponse(
                _error_envelope("PROPERTY_NOT_FOUND", "Unknown url_hash.", request_id),
                status_code=404,
            )
        snap = conn.execute(
            "SELECT * FROM scrape_snapshots WHERE url_hash = ? ORDER BY scraped_at DESC LIMIT 1",
            (url_hash,),
        ).fetchone()
        enrich = conn.execute(
            "SELECT * FROM property_enrichment WHERE url_hash = ?", (url_hash,)
        ).fetchone()
    finally:
        conn.close()

    def _maybe_json(s):
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    return JSONResponse({
        "url_hash": url_hash,
        "canonical_url": prop["canonical_url"],
        "address": prop["address"],
        "zip_code": prop["zip_code"],
        "latest_snapshot": {
            "scraped_at": snap["scraped_at"] if snap else None,
            "price": snap["price"] if snap else None,
            "beds": snap["beds"] if snap else None,
            "baths": snap["baths"] if snap else None,
            "sqft": snap["sqft"] if snap else None,
            "year_built": snap["year_built"] if snap else None,
            "units": snap["units"] if snap else None,
            "dom": snap["dom"] if snap else None,
            "description": snap["description"] if snap else None,
            "image_url": snap["image_url"] if snap else None,
        } if snap else None,
        "enrichment": {
            "lat": enrich["lat"],
            "lng": enrich["lng"],
            "flood_zone": enrich["flood_zone"],
            "flood_zone_risk": enrich["flood_zone_risk"],
            "fire_zone": enrich["fire_zone"],
            "fire_zone_risk": enrich["fire_zone_risk"],
            "amenity_counts": _maybe_json(enrich["amenity_counts"]),
            "walkability_index": enrich["walkability_index"],
            "enriched_at": enrich["enriched_at"],
        } if enrich else None,
        "llm_analysis": _maybe_json(prop["llm_analysis"]),
        "llm_analyzed_at": prop["llm_analyzed_at"],
        "insurance": {
            "annual_usd": prop["cached_insurance"],
            "breakdown": _maybe_json(prop["cached_insurance_breakdown"]),
        },
        "cache_stale_reason": prop["cache_stale_reason"],
    })


@app.get("/api/batches")
async def list_batches(limit: int = 20, offset: int = 0):
    """Historical batches for the My Batches view (§B.4)."""
    limit = max(1, min(100, int(limit or 20)))
    offset = max(0, int(offset or 0))
    conn = _batch_conn(_BATCH_DB_PATH)
    try:
        total_row = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()
        total = int(total_row["n"])
        rows = conn.execute(
            """SELECT b.batch_id, b.created_at, b.completed_at, b.mode,
                      b.input_count, b.status, b.preset_name,
                      b.external_batch_id,
                      (SELECT p.address FROM rankings r
                         JOIN properties p ON p.url_hash = r.url_hash
                        WHERE r.batch_id = b.batch_id
                        ORDER BY r.rank ASC LIMIT 1) AS top_rank_address
                 FROM batches b
                 ORDER BY b.created_at DESC
                 LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return JSONResponse({
        "total": total,
        "batches": [dict(r) for r in rows],
    })


@app.get("/api/properties/{url_hash}/history")
async def property_history(url_hash: str):
    """Scrape history + every rank this property received (§B.5)."""
    bad = _validate_url_hash(url_hash)
    if bad is not None:
        return bad
    conn = _batch_conn(_BATCH_DB_PATH)
    try:
        prop = conn.execute(
            "SELECT canonical_url, address, scrape_count FROM properties WHERE url_hash = ?",
            (url_hash,),
        ).fetchone()
        if not prop:
            request_id = uuid.uuid4().hex
            return JSONResponse(
                _error_envelope("PROPERTY_NOT_FOUND", "Unknown url_hash.", request_id),
                status_code=404,
            )
        snaps = conn.execute(
            """SELECT scraped_at, price, dom, scrape_ok
                 FROM scrape_snapshots
                WHERE url_hash = ?
                ORDER BY scraped_at DESC""",
            (url_hash,),
        ).fetchall()
        ranks = conn.execute(
            """SELECT r.batch_id, r.rank, r.topsis_score, r.verdict, b.created_at
                 FROM rankings r JOIN batches b ON b.batch_id = r.batch_id
                WHERE r.url_hash = ?
                ORDER BY b.created_at DESC""",
            (url_hash,),
        ).fetchall()
    finally:
        conn.close()
    return JSONResponse({
        "url_hash": url_hash,
        "canonical_url": prop["canonical_url"],
        "address": prop["address"],
        "scrape_count": prop["scrape_count"],
        "snapshots": [dict(s) for s in snaps],
        "rankings": [dict(r) for r in ranks],
    })


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
