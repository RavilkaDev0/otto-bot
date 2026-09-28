"""
Быстрый параллельный аудит каталога XL OTTO.
Ищет товары с зеркальным логотипом JV Möbel (MD5-проверка).
Запускается detached, пишет в /app/logs/jv_audit.log
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
import time

import httpx

sys.path.insert(0, "/app")
from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.schemas.external_schemes.until_schemes import GetProductRequest

LOGO_MD5 = "0afd3bdf6e522b09848f786d16912a99"
CONTROLLER = "xl"
LIMIT = 100
CONCURRENCY = 30

os.makedirs("/app/logs", exist_ok=True)
os.makedirs("/app/reports", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/jv_audit.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("jv_audit")

_cache: dict[str, str | None] = {}
_sem: asyncio.Semaphore


async def get_md5(url: str, http: httpx.AsyncClient) -> str | None:
    if url in _cache:
        return _cache[url]
    async with _sem:
        if url in _cache:
            return _cache[url]
        try:
            r = await http.get(url, timeout=15.0, follow_redirects=True)
            if r.status_code == 200:
                d = hashlib.md5(r.content).hexdigest()
                _cache[url] = d
                return d
        except Exception:
            pass
        _cache[url] = None
        return None


async def check_variation(v: dict, http: httpx.AsyncClient) -> dict | None:
    sku = v.get("sku", "")
    assets = [a for a in v.get("mediaAssets", []) if a.get("type") == "IMAGE"]
    urls = [str(a.get("location") or a.get("url") or "") for a in assets]
    urls = [u for u in urls if u]
    if not urls:
        return None
    md5s = await asyncio.gather(*[get_md5(u, http) for u in urls])
    for url, md5 in zip(urls, md5s):
        if md5 == LOGO_MD5:
            remaining = len([u for u in urls if u != url])
            title = v.get("productDescription", {}).get("productLine", "")
            return {"sku": sku, "logo_url": url, "remaining": remaining, "title": title}
    return None


async def main():
    global _sem
    _sem = asyncio.Semaphore(CONCURRENCY)

    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)

    affected: list[dict] = []
    page = 0
    total_checked = 0
    t0 = time.time()

    log.info("=" * 60)
    log.info("СТАРТ: Поиск зеркального логотипа JV Möbel в XL OTTO")
    log.info("MD5 цели: %s | Параллельность: %d", LOGO_MD5, CONCURRENCY)
    log.info("=" * 60)

    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=60, max_keepalive_connections=40)
    ) as http:
        while True:
            # Загружаем страницу с повторами
            result = None
            for attempt in range(4):
                try:
                    result = await otto.get_products(
                        GetProductRequest(page=page, limit=LIMIT), CONTROLLER
                    )
                    break
                except Exception as e:
                    if attempt == 3:
                        log.error("Не удалось загрузить стр %d: %s", page, e)
                        break
                    await asyncio.sleep(5 * (attempt + 1))

            if result is None:
                break

            variations = result.get("productVariations", [])
            if not variations:
                log.info("[стр %d] пусто — конец каталога.", page)
                break

            log.info(
                "[стр %d] %d вариаций | всего проверено: %d | найдено: %d",
                page, len(variations), total_checked, len(affected),
            )

            # Параллельная проверка всех вариаций страницы
            hits = await asyncio.gather(*[check_variation(v, http) for v in variations])
            for hit in hits:
                if hit:
                    affected.append(hit)
                    log.info(
                        "  *** НАЙДЕН: SKU=%s | %s | останется фото: %d",
                        hit["sku"], hit["logo_url"][-50:], hit["remaining"],
                    )
            total_checked += len(variations)

            # Пагинация
            links = result.get("links", [])
            has_next = any(l.get("rel") == "next" for l in links)
            if not has_next:
                log.info("Нет следующей страницы — скан завершён.")
                break
            page += 1
            await asyncio.sleep(0.1)

    elapsed = time.time() - t0

    log.info("=" * 60)
    log.info("ИТОГ: проверено %d | найдено %d | время %.0f сек", total_checked, len(affected), elapsed)
    log.info("=" * 60)
    for h in affected:
        log.info("  SKU=%-20s | фото останется: %d | %s", h["sku"], h["remaining"], h["title"][:50])

    report = {
        "scan_timestamp": time.time(),
        "logo_md5": LOGO_MD5,
        "total_checked": total_checked,
        "total_affected": len(affected),
        "elapsed_seconds": round(elapsed, 1),
        "affected": affected,
    }
    with open("/app/reports/affected_xl_jv_logos.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    log.info("Отчёт: /app/reports/affected_xl_jv_logos.json")


if __name__ == "__main__":
    asyncio.run(main())
