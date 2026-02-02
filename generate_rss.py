import re
import sys
import html
import datetime as dt
from email.utils import format_datetime

import requests
from bs4 import BeautifulSoup


SITE = "https://ultimasgeek.com/"
OUTFILE = "rss.xml"
MAX_ITEMS = 25
TIMEOUT = 25

# Dónde se publica el feed (GitHub Pages)
FEED_URL = "https://franvillafanez.github.io/ultimasgeek-rss/rss.xml"

# Páginas que NO son artículos (ajustá si aparecen otras)
BLOCKED_SLUGS = {
    "lo-ultimo",
    "nota-de-voz",
    "contacto",
    "about",
}

# Prefijos típicos que no son posts
BLOCKED_PREFIXES = (
    "category/",
    "tag/",
    "page/",
    "author/",
    "wp-",
)

MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "ultimasgeek-rss-bot/1.0 (+https://github.com/franvillafanez/ultimasgeek-rss)"
    }
)


def fetch(url: str) -> str:
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def normalize_url(url: str) -> str:
    # saca query/hash para deduplicar bien
    if not url:
        return url
    url = url.split("#", 1)[0].split("?", 1)[0]
    return url


def is_post_url(url: str) -> bool:
    if not url or not url.startswith(SITE):
        return False

    clean = normalize_url(url)
    path = clean[len(SITE):].strip("/")  # slug o slug/subslug

    if not path:
        return False

    # bloqueos por prefijo (category/tag/etc.)
    if any(path.startswith(p) for p in BLOCKED_PREFIXES):
        return False

    # paginación
    if "/page/" in path:
        return False

    # bloquear slugs exactos de secciones
    if path in BLOCKED_SLUGS:
        return False

    # descartar assets obvios
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".pdf")):
        return False

    return True


def extract_post_urls_from_home(home_html: str) -> list[str]:
    soup = BeautifulSoup(home_html, "html.parser")
    urls = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if is_post_url(href):
            urls.append(normalize_url(href))

    # dedupe conservando orden
    seen = set()
    out = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def parse_date_es(text: str) -> dt.datetime | None:
    """
    Busca fecha tipo: "27 enero, 2026" dentro del texto.
    """
    m = re.search(r"(\d{1,2})\s+([a-záéíóúñ]+)\s*,\s*(\d{4})", text, re.IGNORECASE)
    if not m:
        return None

    day = int(m.group(1))
    mon_name = m.group(2).lower()
    year = int(m.group(3))

    mon = MONTHS_ES.get(mon_name)
    if not mon:
        return None

    # zona Argentina (-03) y lo pasamos a UTC para pubDate
    tz_ar = dt.timezone(dt.timedelta(hours=-3))
    return dt.datetime(year, mon, day, 12, 0, 0, tzinfo=tz_ar).astimezone(dt.timezone.utc)


def guess_image_mime(url: str) -> str:
    u = (url or "").lower().split("?", 1)[0].split("#", 1)[0]
    if u.endswith(".png"):
        return "image/png"
    if u.endswith(".webp"):
        return "image/webp"
    if u.endswith(".gif"):
        return "image/gif"
    if u.endswith(".svg"):
        return "image/svg+xml"
    return "image/jpeg"


def parse_post(post_url: str) -> dict | None:
    try:
        post_html = fetch(post_url)
    except Exception as e:
        print(f"[WARN] No pude bajar {post_url}: {e}", file=sys.stderr)
        return None

    soup = BeautifulSoup(post_html, "html.parser")

    def get_meta_property(prop: str) -> str:
        tag = soup.find("meta", attrs={"property": prop})
        return (tag.get("content") or "").strip() if tag else ""

    def get_meta_name(name: str) -> str:
        tag = soup.find("meta", attrs={"name": name})
        return (tag.get("content") or "").strip() if tag else ""

    # Título: OG -> h1 -> title
    title = get_meta_property("og:title")
    if not title:
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(" ", strip=True)
    if not title:
        t = soup.find("title")
        if t and t.get_text(strip=True):
            title = t.get_text(" ", strip=True)

    if not title:
        return None

    # Descripción: OG -> meta description -> primer párrafo
    desc = get_meta_property("og:description") or get_meta_name("description") or ""
    if not desc:
        p = soup.find("p")
        if p:
            desc = p.get_text(" ", strip=True)

    desc = (desc or "").strip()
    if len(desc) > 300:
        desc = desc[:300].rstrip() + "…"

    # Imagen: OG image
    image_url = (get_meta_property("og:image") or "").strip()

    # Fecha: buscar dentro del texto completo
    text_all = soup.get_text("\n", strip=True)
    published = parse_date_es(text_all) or dt.datetime.now(dt.timezone.utc)

    return {
        "title": title,
        "link": post_url,
        "guid": post_url,
        "pubDate": format_datetime(published),
        "description": desc,
        "image_url": image_url,
    }


def build_rss(items: list[dict]) -> str:
    now = format_datetime(dt.datetime.now(dt.timezone.utc))

    def esc_cdata(s: str) -> str:
        # CDATA no puede contener "]]>"
        return (s or "").replace("]]>", "]]]]><![CDATA[>")

    rss_items = []

    for it in items:
        link = html.escape(it["link"])
        guid = html.escape(it["guid"])
        title = esc_cdata(it["title"])
        desc_text = esc_cdata(it.get("description", ""))

        img_html = ""
        enclosure = ""

        img_url = (it.get("image_url") or "").strip()
        if img_url.startswith("http"):
            img_url_esc = html.escape(img_url)
            img_html = f'<p><img src="{img_url_esc}" alt="{html.escape(it["title"])}" /></p>'
            enclosure = f'\n      <enclosure url="{img_url_esc}" type="{guess_image_mime(img_url)}" />'

        # description en HTML (CDATA) para que muchos lectores muestren imagen + texto
        description_html = f"{img_html}<p>{desc_text}</p>" if desc_text else img_html

        rss_items.append(
            f"""
    <item>
      <title><![CDATA[{title}]]></title>
      <link>{link}</link>
      <guid isPermaLink="true">{guid}</guid>
      <pubDate>{it["pubDate"]}</pubDate>
      <description><![CDATA[{description_html}]]></description>{enclosure}
    </item>
""".rstrip()
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Últimas Geek</title>
    <link>{SITE}</link>
    <atom:link href="{FEED_URL}" rel="self" type="application/rss+xml" />
    <description>Feed generado automáticamente desde la home</description>
    <lastBuildDate>{now}</lastBuildDate>
{chr(10).join(rss_items)}
  </channel>
</rss>
"""


def main():
    home_html = fetch(SITE)
    urls = extract_post_urls_from_home(home_html)

    items = []
    seen = set()

    for u in urls:
        it = parse_post(u)
        if not it:
            continue

        # evita duplicados raros
        key = (it["link"], it["title"].strip().lower())
        if key in seen:
            continue
        seen.add(key)

        items.append(it)
        if len(items) >= MAX_ITEMS:
            break

    rss = build_rss(items)

    with open(OUTFILE, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"OK: generé {OUTFILE} con {len(items)} items")


if __name__ == "__main__":
    main()
