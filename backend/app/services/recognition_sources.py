"""Public reference retrieval; model-supplied identities never grant source authority."""

import base64
import io
import re
from urllib.parse import urldefrag, urljoin, urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup
from PIL import Image

from app.errors import DomainError
from app.identification import normalized
from app.security import normalize
from app.services.web_research import BRANDS, contains, official_url
from app.storage import sanitize_image

APPLE_ALIASES = {"apple", "苹果", "苹果公司"}


def safe_reference_url(url):
    try:
        p = urlsplit(url)
        return bool(
            p.scheme == "https"
            and p.hostname
            and not p.username
            and not p.password
            and p.port in (None, 443)
            and len(url) <= 2048
        )
    except ValueError:
        return False


def thumbnail(data, mime):
    if len(data) > 2 * 1024 * 1024:
        raise DomainError(413, "FILE_TOO_LARGE", "参考图过大")
    clean, _ = sanitize_image(data, mime)
    with Image.open(io.BytesIO(clean)) as image:
        image.thumbnail((384, 384))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=75)
    if len(out.getvalue()) > 80000:
        raise DomainError(413, "FILE_TOO_LARGE", "参考图过大")
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def supports_model(brand, model, text, *, trusted_brand=False):
    aliases = APPLE_ALIASES if normalize(brand) in APPLE_ALIASES else {normalize(brand)}
    if not trusted_brand and not any(contains(text, alias) for alias in aliases):
        return False
    match = contains(text, model)
    if not match:
        return False
    tail = normalize(text)[match.end() : match.end() + 30]
    # A family prefix cannot borrow a longer variant's evidence.
    return not re.match(r"^[\s_-]*(?:pro|max|plus|ultra|mini|lite|se)(?![a-z0-9])", tail)


def exact_image_title(title, brand, model):
    aliases = APPLE_ALIASES if normalize(brand) in APPLE_ALIASES else {brand}
    accepted = {normalized(model), *(normalized(alias + model) for alias in aliases)}
    return normalized(title) in accepted


def search_queries(observation, known_brands=()):

    public_brands = {alias: "Apple" for alias in APPLE_ALIASES}
    for aliases, _ in BRANDS:
        public_brands.update(dict.fromkeys(aliases, aliases[-1]))
    for name in (
        "Nikon",
        "Canon",
        "Samsung",
        "Microsoft",
        "Lenovo",
        "Dell",
        "HP",
        "Bose",
        "DJI",
        "Xiaomi",
        "Huawei",
        "Panasonic",
        "Philips",
        "LG",
        "Asus",
        "Acer",
    ):
        public_brands[name.casefold()] = name
    public_brands.update({normalize(name): name for name in known_brands})
    brand = public_brands.get(normalize(observation.brandHint or ""), "")
    # Model guesses may echo private text or serials. Only reviewed brand names
    # and a fixed public vocabulary may leave for the search service.
    categories = [
        (r"手机|smartphone|iphone", "手机"),
        (r"相机|camera", "相机"),
        (r"耳机|headphone|earbud", "耳机"),
        (r"音箱|speaker", "音箱"),
        (r"键盘|keyboard", "键盘"),
        (r"鼠标|mouse", "鼠标"),
        (r"投影|projector", "投影仪"),
        (r"电视|television", "电视"),
        (r"电脑|笔记本|computer|laptop", "电脑"),
        (r"平板|tablet", "平板电脑"),
        (r"路由|router", "路由器"),
        (r"开发板|电路板|单板|board", "开发板"),
        (r"打印|printer", "打印机"),
        (r"手表|watch", "手表"),
        (r"空调|冰箱|洗衣|appliance", "家用电器"),
    ]
    category = next(
        (term for pattern, term in categories if re.search(pattern, observation.category, re.I)),
        "产品",
    )
    terms = [brand, category]
    feature_text = " ".join(observation.features)
    features = [
        term
        for pattern, term in [
            (r"竖向|垂直", "竖向镜头"),
            (r"横向|水平", "横向布局"),
            (r"摄像头|镜头", "摄像头"),
            (r"圆形", "圆形"),
            (r"usb.?c|type.?c", "USB-C接口"),
            (r"按键|按钮", "按键布局"),
            (r"旋钮", "旋钮"),
            (r"翻盖|折叠", "折叠外形"),
        ]
        if re.search(pattern, feature_text, re.I)
    ][:3]
    first = " ".join(filter(None, terms))
    second = " ".join(filter(None, terms + features))
    return [first + " 外观 技术规格", second + " 产品 型号 对比"]


def page_references(url, title, raw, domains=()):
    soup = BeautifulSoup(raw[:2_000_000], "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "iframe", "form"]):
        tag.decompose()
    kind = "official" if official_url(url, domains) else "third_party"
    refs = []
    headings = soup.find_all(["h1", "h2", "h3"], limit=100)
    for heading in headings:
        text = [heading.get_text(" ", strip=True)]
        image_url = None
        for item in heading.find_all_next(limit=400):
            # A parent heading must not borrow the first child product's image.
            if item.name in {"h1", "h2", "h3"}:
                break
            if item.name in {"p", "li", "dt", "dd"}:
                text.append(item.get_text(" ", strip=True))
            if item.name == "img" and not image_url:
                src = item.get("src") or item.get("data-src")
                if src:
                    target = urljoin(url, src)
                    if safe_reference_url(target):
                        image_url = target
        body = "\n".join(text)[:8000]
        if len(body) > len(text[0]) + 5:
            refs.append(
                {
                    "id": uuid4().hex,
                    "url": url,
                    "title": text[0][:200],
                    "text": body,
                    "sourceKind": kind,
                    "imageUrl": image_url,
                }
            )
    # Keep a page-level fallback, but do not assign a site-wide image to a multi-product page.
    image = soup.find("meta", attrs={"property": "og:image"}) if len(headings) <= 1 else None
    target = urljoin(url, image.get("content", "")) if image else None
    refs.append(
        {
            "id": uuid4().hex,
            "url": url,
            "title": title[:200],
            "text": soup.get_text("\n", strip=True)[:12000],
            "sourceKind": kind,
            "imageUrl": target if target and safe_reference_url(target) else None,
        }
    )
    return refs


def fetch_references(rows, observation, domains=(), *, fetcher=None):
    from app.fetch import fetch_public

    fetcher = fetcher or fetch_public
    refs, visited = [], set()
    for row in rows[:5]:
        url = row.get("url", "")
        if not safe_reference_url(url) or urldefrag(url)[0] in visited:
            continue
        visited.add(urldefrag(url)[0])
        try:
            final, mime, data = fetcher(url, maximum=2_000_000)
            if mime != "text/html":
                continue
            refs.extend(page_references(final, row.get("title", "产品参考"), data, domains))
        except DomainError as error:
            if error.status in {429, 504}:
                raise

    def priority(row):
        wanted = any(
            supports_model(h.brandName, h.modelCode, row["title"], trusted_brand=True)
            for h in observation.hypotheses
        )
        return (not wanted, row["sourceKind"] != "official")

    refs.sort(key=priority)
    selected = refs[:8]
    for row in selected[:3]:
        if not row.get("imageUrl") or not any(
            exact_image_title(row["title"], h.brandName, h.modelCode)
            for h in observation.hypotheses
        ):
            continue
        try:
            _, mime, data = fetcher(
                row["imageUrl"],
                maximum=2 * 1024 * 1024,
                accepted_mimes={"image/jpeg", "image/png", "image/webp"},
            )
            row["imageData"] = thumbnail(data, mime)
        except DomainError as error:
            if error.status in {429, 504}:
                raise
    return [{k: v for k, v in row.items() if k != "imageUrl"} for row in selected]
