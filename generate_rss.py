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

def is_post_url(url: str) -> bool:
    # Posts suelen ser: https://ultimasgeek.com/<slug>/
    if not url.startswith(SITE):
        return False
    path = url[len(SITE):].strip("/")
    if not path:
        return False
    # excluir secciones típicas
    blocked_prefixes = ("category/", "tag/", "page/", "wp-", "author/")
    if any(path.startswith(p) for p in blocked_prefixes):
        return False
    # excluir paginación y cosas raras
    if "/page/" in path:
        return False
    # bastante “permisivo” a propósito
    return True

def extract_post_urls_from_home(home_html: str) -> list[str]:
    soup = BeautifulSoup(home_html, "html.parser")
    urls = []
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if is_post_url(href):
            urls.append(href)

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
    Busca fecha tipo: "27 enero, 2026"
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
    # Asumimos zona horaria Argentina para la medianoche y luego pasamos a UTC
    # (si preferís, lo dejamos como "naive" o lo ponemos en UTC directo)
    tz_ar = dt.timezone(dt.timedelta(hours=-3))
    return dt.datetime(year, mon, day, 12, 0, 0, tzinfo=tz_ar).astimezone(dt.timezone.utc)

def parse_post(post_url: str) -> dict | None:
    try:
        post_html = fetch(post_url)
    except Exception as e:
        print(f"[WARN] No pude bajar {post_url}: {e}", file=sys.stderr)
        return None

    soup = BeautifulSoup(post_html, "html.parser")

    # título: probar h1/h2 fuerte
    title = None
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(" ", strip=True)
    if not title:
        # fallback: primer encabezado grande
        for tag in ["h2", "h3"]:
            h = soup.find(tag)
            if h and h.get_text(strip=True):
                title = h.get_text(" ", strip=True)
                break

    if not title:
        return None

    # descripción: primer párrafo “real”
    desc = ""
    p = soup.find("p")
    if p:
        desc = p.get_text(" ", strip=True)
        desc = desc[:280]

    # fecha: buscar bloque "Publicado ... el 27 enero, 2026"
    text_all = soup.get_text("\n", strip=True)
    published = parse_date_es(text_all)

    if not published:
        published = dt.datetime.now(dt.timezone.utc)

    return {
        "title": title,
        "link": post_url,
        "guid": post_url,
        "pubDate": format_datetime(published),
        "description": desc,
    }

def build_rss(items: list[dict]) -> str:
    now = format_datetime(dt.datetime.now(dt.timezone.utc))

    def esc_cdata(s: str) -> str:
        # CDATA no puede contener "]]>"
        return s.replace("]]>", "]]]]><![CDATA[>")

    rss_items = []
    for it in items:
        rss_items.append(
            f"""
    <item>
      <title><![CDATA[{esc_cdata(it["title"])}]]></title>
      <link>{html.escape(it["link"])}</link>
      <guid isPermaLink="true">{html.escape(it["guid"])}</guid>
      <pubDate>{it["pubDate"]}</pubDate>
      <description><![CDATA[{esc_cdata(it.get("description",""))}]]></description>
    </item>
""".rstrip()
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Últimas Geek</title>
    <link>{SITE}</link>
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
    for u in urls:
        it = parse_post(u)
        if it:
            items.append(it)
        if len(items) >= MAX_ITEMS:
            break

    # ordenar por pubDate (string RFC822) no es ideal; lo dejamos como viene (ya suele ser “de hoy hacia atrás”)
    rss = build_rss(items)

    with open(OUTFILE, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"OK: generé {OUTFILE} con {len(items)} items")

if __name__ == "__main__":
    main()
