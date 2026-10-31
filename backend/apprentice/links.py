"""Read a store link (Amazon, IKEA, Walmart, Flipkart, Best Buy, any shop) into what ShowMe needs.

parse_link() works offline from the URL alone: the retailer, the product id (an Amazon ASIN, an IKEA
article number...) and the product name many stores put in the path. fetch_listing() then reads the
page itself: title, brand, model, feature bullets, JSON-LD product data, and links to manual PDFs.

Safety: only http(s) on ports 80/443, only public addresses (never this PC or the local network, also
after every redirect), and size limits on everything downloaded. Many stores block automated page
reads (Amazon shows a "Robot Check"); that is detected and reported, and the URL alone still gives
the product id and usually its name.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

import httpx

MAX_HTML = 4 * 1024 * 1024
MAX_PDF = 40 * 1024 * 1024
MAX_REDIRECTS = 6
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")

# Tests swap these for a fake network and a fake DNS.
TRANSPORT: Optional[httpx.AsyncBaseTransport] = None
RESOLVE: Optional[Callable[[str], list[str]]] = None

RETAILERS = [
    (r"(^|\.)amazon\.[a-z.]+$|(^|\.)amzn\.(to|eu|in|asia)$|^a\.co$", "Amazon"),
    (r"(^|\.)ikea\.[a-z.]+$", "IKEA"),
    (r"(^|\.)walmart\.[a-z.]+$", "Walmart"),
    (r"(^|\.)flipkart\.com$|^dl\.flipkart\.com$", "Flipkart"),
    (r"(^|\.)bestbuy\.[a-z.]+$", "Best Buy"),
    (r"(^|\.)target\.com$", "Target"),
    (r"(^|\.)wayfair\.[a-z.]+$", "Wayfair"),
    (r"(^|\.)homedepot\.[a-z.]+$", "The Home Depot"),
    (r"(^|\.)lowes\.com$", "Lowe's"),
    (r"(^|\.)costco\.[a-z.]+$", "Costco"),
    (r"(^|\.)ebay\.[a-z.]+$", "eBay"),
    (r"(^|\.)argos\.co\.uk$", "Argos"),
    (r"(^|\.)currys\.co\.uk$", "Currys"),
    (r"(^|\.)aliexpress\.[a-z.]+$", "AliExpress"),
]
SHORT_HOSTS = re.compile(r"^(amzn\.(to|eu|in|asia)|a\.co|dl\.flipkart\.com|bit\.ly|tinyurl\.com)$")


class LinkError(ValueError):
    """The link can't be used (not a web address, or points somewhere ShowMe won't fetch)."""


@dataclass
class Link:
    url: str
    host: str
    retailer: str
    product_id: str = ""
    name_hint: str = ""
    key: str = ""
    short: bool = False


@dataclass
class Listing:
    url: str
    final_url: str = ""
    retailer: str = ""
    product_id: str = ""
    title: str = ""
    brand: str = ""
    model: str = ""
    bullets: list[str] = field(default_factory=list)
    description: str = ""
    image: str = ""
    manual_links: list[dict] = field(default_factory=list)
    blocked: bool = False
    error: str = ""
    name_hint: str = ""  # the product name in the page's real address (short links redirect to it)

    def as_text(self, name_hint: str = "", typed_name: str = "") -> str:
        parts = [f"Store: {self.retailer or 'unknown'}", f"Link: {self.final_url or self.url}"]
        if self.product_id:
            parts.append(f"Product id: {self.product_id}")
        for label, value in (("Name typed by the person", typed_name), ("Title", self.title), ("Brand", self.brand),
                             ("Model", self.model), ("Name from the link", name_hint)):
            if value:
                parts.append(f"{label}: {value}")
        if self.bullets:
            parts.append("Feature bullets:\n" + "\n".join(f"- {b}" for b in self.bullets[:15]))
        if self.description:
            parts.append(f"Description: {self.description[:3000]}")
        if self.blocked:
            parts.append("Note: the store blocked reading the page, so only the link itself is known.")
        return "\n".join(parts)


# ---------------------------------------------------------------- the URL alone


def _slug_words(segment: str) -> str:
    words = re.sub(r"[-_+]+", " ", unquote(segment)).strip()
    words = re.sub(r"\s+", " ", words)
    # at least two words with real letters: "billy bookcase white", not an id like "B0CHX1W1XY"
    return words if " " in words and len(words) >= 6 and re.search(r"[A-Za-z]{3}", words) else ""


def parse_link(raw: str) -> Link:
    text = (raw or "").strip()
    found = re.search(r"https?://\S+", text)  # people paste "Check this out https://amzn.to/xyz"
    if found:
        text = found.group(0).rstrip(").,;'\"")
    elif re.match(r"^[\w.-]+\.[a-z]{2,}/", text, re.I):
        text = "https://" + text
    u = urlparse(text)
    if u.scheme not in {"http", "https"} or not u.hostname:
        raise LinkError("That isn't a web link. Paste the product page's address, starting with https://")
    host = u.hostname.lower().removeprefix("www.").removeprefix("m.")
    retailer = next((name for pat, name in RETAILERS if re.search(pat, host)), "")
    path = u.path or "/"
    query = parse_qs(u.query)
    pid, hint = "", ""
    if retailer == "Amazon":
        m = re.search(r"/(?:dp|gp/product|gp/aw/d|product|exec/obidos/ASIN)/([A-Z0-9]{10})(?:[/?]|$)", path + "/", re.I)
        pid = m.group(1).upper() if m else ""
        m = re.search(r"/([^/]+)/(?:dp|gp/product)/", path)
        hint = _slug_words(m.group(1)) if m else ""
    elif retailer == "IKEA":
        m = re.search(r"/p/([^/]+?)-(s?\d{8})/?$", path)
        if m:
            hint, pid = _slug_words(m.group(1)), m.group(2).upper()
    elif retailer == "Walmart":
        m = re.search(r"/ip/(?:([^/]+)/)?(\d{5,})", path)
        if m:
            hint, pid = _slug_words(m.group(1) or ""), m.group(2)
    elif retailer == "Flipkart":
        pid = (query.get("pid") or [""])[0]
        m = re.search(r"^/([^/]+)/p/", path)
        hint = _slug_words(m.group(1)) if m else ""
    elif retailer == "Best Buy":
        pid = (query.get("skuId") or [""])[0] or (re.search(r"/(\d{6,8})\.p", path) or [None, ""])[1]
        m = re.search(r"/site/([^/]+)/", path)
        hint = _slug_words(m.group(1)) if m else ""
    elif retailer == "Target":
        m = re.search(r"/p/([^/]+)/-/A-(\d+)", path)
        if m:
            hint, pid = _slug_words(m.group(1)), m.group(2)
    elif retailer == "Wayfair":
        m = re.search(r"/pdp/([^/]+?)-([a-z]{2,4}\d{3,})\.html", path, re.I)
        if m:
            hint, pid = _slug_words(m.group(1)), m.group(2).upper()
    elif retailer == "eBay":
        m = re.search(r"/itm/(?:([^/]+)/)?(\d{9,})", path)
        if m:
            hint, pid = _slug_words(m.group(1) or ""), m.group(2)
    if not hint:  # any shop: the longest wordy path segment is usually the product name
        segs = sorted((_slug_words(s) for s in path.split("/") if s and not s.endswith((".html", ".htm"))),
                      key=len, reverse=True)
        hint = segs[0] if segs else ""
    clean = urlunparse((u.scheme, u.netloc, path, "", u.query if retailer in {"Flipkart", "Best Buy"} else "", ""))
    key = f"{retailer.lower() or host}:{pid}" if pid else clean.lower()
    return Link(url=clean if not SHORT_HOSTS.match(host) else text, host=host, retailer=retailer, product_id=pid,
                name_hint=hint[:160], key=key, short=bool(SHORT_HOSTS.match(host)))


# ---------------------------------------------------------------- fetching, safely


def _resolve(host: str) -> list[str]:
    if RESOLVE:
        return RESOLVE(host)
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({i[4][0] for i in infos})


async def _check_public(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in {"http", "https"} or not u.hostname:
        raise LinkError("Only web links (http or https) can be read.")
    if u.port not in (None, 80, 443):
        raise LinkError("ShowMe only reads normal web pages (ports 80 and 443).")
    try:
        addrs = await asyncio.to_thread(_resolve, u.hostname)
    except OSError as exc:
        raise LinkError(f"Couldn't find {u.hostname}. Check the link.") from exc
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if not ip.is_global:
            raise LinkError("That link points at this computer or a private network, so ShowMe won't open it.")


async def _get(client: httpx.AsyncClient, url: str, limit: int) -> tuple[httpx.Response, bytes, str]:
    """GET with redirects followed by hand, checking every hop is a public address."""
    for _ in range(MAX_REDIRECTS + 1):
        await _check_public(url)
        async with client.stream("GET", url) as r:
            if r.is_redirect and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            body = bytearray()
            async for chunk in r.aiter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise LinkError("That file is too large to read.")
            return r, bytes(body), url
    raise LinkError("The link redirected too many times.")


# Stores serve product pages to browsers; these are the headers a normal visit sends. When a store still
# answers with a bot wall, the page is tried once more the way a phone browser asks for it.
DESKTOP = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
}
MOBILE = {
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _client(headers: Optional[dict] = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=TRANSPORT, timeout=20, follow_redirects=False, headers=headers or DESKTOP)


# ---------------------------------------------------------------- reading the page


class _PageParser(HTMLParser):
    """Collects meta tags, JSON-LD, links, and the text of a few well-known element ids."""

    IDS = {"productTitle", "title", "bylineInfo", "feature-bullets", "productDescription",
           "detailBullets_feature_div", "productDetails_techSpec_section_1", "prodDetails"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.ld: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.by_id: dict[str, list[str]] = {}
        self._stack: list[tuple[str, Optional[str]]] = []
        self._in_title = self._in_ld = False
        self._ld_buf: list[str] = []
        self._a: Optional[list] = None
        self._li: Optional[list[str]] = None
        self.bullets: list[str] = []

    def _capturing(self) -> Optional[str]:
        for _, ident in reversed(self._stack):
            if ident:
                return ident
        return None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            k = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if k and a.get("content") and k not in self.meta:
                self.meta[k] = a["content"].strip()
            return
        if tag == "title":
            self._in_title = True
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_ld, self._ld_buf = True, []
        if tag == "a" and a.get("href"):
            self._a = [a["href"], []]
        if tag in self.VOID:
            return
        ident = a.get("id") if a.get("id") in self.IDS else None
        self._stack.append((tag, ident))
        if tag == "li" and self._capturing() == "feature-bullets":
            self._li = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld.append("".join(self._ld_buf))
        if tag == "a" and self._a is not None:
            self.links.append((self._a[0], " ".join("".join(self._a[1]).split())))
            self._a = None
        if tag == "li" and self._li is not None:
            text = " ".join("".join(self._li).split())
            if text:
                self.bullets.append(text)
            self._li = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_ld:
            self._ld_buf.append(data)
        if self._a is not None:
            self._a[1].append(data)
        if self._li is not None:
            self._li.append(data)
        ident = self._capturing()
        if ident:
            self.by_id.setdefault(ident, []).append(data)


def _text(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def _ld_products(blocks: list[str]) -> list[dict]:
    out = []

    def walk(x):
        if isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            t = x.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(str(tt).lower() in {"product", "productgroup"} for tt in types):
                out.append(x)
            for k in ("@graph", "itemListElement", "mainEntity"):
                if k in x:
                    walk(x[k])

    for b in blocks:
        try:
            walk(json.loads(b.strip()))
        except ValueError:
            continue
    return out


MANUAL_WORDS = re.compile(r"manual|instruction|assembly|user.?guide|quick.?start|setup|owner|handbuch|notice|guide", re.I)
BOT_WALL = re.compile(r"robot check|captcha|api-services-support@amazon|are you a human|access denied|"
                      r"unusual traffic|verify you are human|pardon our interruption", re.I)


def read_page(html: str, url: str) -> dict:
    p = _PageParser()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - a broken page still gives whatever was parsed
        pass
    m = p.meta
    products = _ld_products(p.ld)
    prod = products[0] if products else {}
    brand = prod.get("brand")
    brand = brand.get("name", "") if isinstance(brand, dict) else (brand or "")
    image = prod.get("image")
    image = (image[0] if isinstance(image, list) and image else image) or m.get("og:image", "")
    image = image.get("url", "") if isinstance(image, dict) else image
    title = (_text(p.by_id.get("productTitle", [])) or str(prod.get("name") or "") or m.get("og:title", "")
             or m.get("twitter:title", "") or " ".join(p.title.split()))
    byline = _text(p.by_id.get("bylineInfo", []))
    if not brand and byline:
        brand = re.sub(r"^(visit the|brand:)\s*|\s*store$", "", byline, flags=re.I).strip()
    desc = (_text(p.by_id.get("productDescription", [])) or str(prod.get("description") or "")
            or m.get("og:description", "") or m.get("description", ""))
    bullets = [b for b in p.bullets if len(b) > 3][:20]
    details = _text(p.by_id.get("detailBullets_feature_div", []) + p.by_id.get("productDetails_techSpec_section_1", [])
                    + p.by_id.get("prodDetails", []))
    model = str(prod.get("model") or prod.get("mpn") or prod.get("sku") or "")
    if not model and details:
        mm = re.search(r"(?:item model number|model number|model name|model)\s*[:‎‏]*\s*([A-Za-z0-9][\w\-./ ]{1,40})", details, re.I)
        model = mm.group(1).strip() if mm else ""
    manuals = []
    seen = set()
    for href, label in p.links:
        full = urljoin(url, unescape(href))
        low = full.lower()
        is_pdf = re.search(r"\.pdf($|[?#])", low)
        if not (is_pdf or (MANUAL_WORDS.search(label) and "pdf" in low)):
            continue
        if full in seen:
            continue
        seen.add(full)
        score = (2 if MANUAL_WORDS.search(label) else 0) + (1 if MANUAL_WORDS.search(low) else 0)
        manuals.append({"url": full, "label": label[:120] or "PDF", "score": score})
    manuals.sort(key=lambda d: d["score"], reverse=True)
    blocked = bool(BOT_WALL.search(html[:20000])) and not p.by_id.get("productTitle") and not products
    return {"title": unescape(title)[:300], "brand": unescape(brand)[:80], "model": model[:60],
            "bullets": [unescape(b)[:400] for b in bullets], "description": unescape(desc)[:4000],
            "image": image if str(image).startswith("http") else "", "manual_links": manuals[:8], "blocked": blocked}


async def fetch_listing(link: Link) -> Listing:
    out = await _fetch_once(link, DESKTOP)
    if out.blocked and not out.manual_links:
        retry = await _fetch_once(link, MOBILE)
        if not retry.blocked or retry.title:
            return retry
        out.name_hint = out.name_hint or retry.name_hint
        out.product_id = out.product_id or retry.product_id
    return out


async def _fetch_once(link: Link, headers: dict) -> Listing:
    out = Listing(url=link.url, retailer=link.retailer, product_id=link.product_id, name_hint=link.name_hint)
    async with _client(headers) as client:
        try:
            r, body, final = await _get(client, link.url, MAX_HTML)
        except LinkError:
            raise
        except httpx.HTTPError as exc:
            out.error = f"Couldn't reach the store ({type(exc).__name__})."
            return out
    out.final_url = final
    if final != link.url:  # a short link: the real product page says which product it is
        real = parse_link(final)
        out.retailer = real.retailer or out.retailer
        out.product_id = real.product_id or out.product_id
        out.name_hint = out.name_hint or real.name_hint
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or body[:4] == b"%PDF":
        out.manual_links = [{"url": final, "label": "The link itself is a PDF", "score": 9}]
        return out
    if r.status_code >= 400 and r.status_code not in (403, 503):
        out.error = f"The store answered {r.status_code}."
        return out
    html = body.decode(r.encoding or "utf-8", errors="replace")
    data = read_page(html, final)
    if data["blocked"]:  # a "Robot Check" page: its title and text are about the wall, not the product
        data.update(title="", brand="", model="", bullets=[], description="", image="")
    for k, v in data.items():
        setattr(out, k, v)
    if r.status_code in (403, 503) and not out.title:
        out.blocked = True
    return out


async def download_pdf(url: str) -> bytes:
    async with _client() as client:
        r, body, _ = await _get(client, url, MAX_PDF)
    if r.status_code >= 400 or not body.startswith(b"%PDF"):
        raise LinkError("That manual link didn't give a PDF.")
    return body
