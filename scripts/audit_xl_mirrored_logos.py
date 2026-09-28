"""
Этап 1: Полный скан каталога XL OTTO.
Ищем товары, у которых в mediaAssets есть изображение с зеркально отражённым
логотипом JV Möbel. Опознаём по MD5-хэшу эталонного изображения.

Результат: /app/reports/affected_xl_jv_logos.json
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from typing import Any

import httpx

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.external_otto_client import ExternalOttoClient
from app.core.configs import settings
from app.core.otto_auth import OttoAuth
from app.schemas.external_schemes.until_schemes import GetProductRequest

# ── Эталон зеркального логотипа JV ──────────────────────────────────────────
LOGO_MD5 = "0afd3bdf6e522b09848f786d16912a99"
LOGO_URL_HINT = "4071489324143_4.jpg"  # для быстрой предварительной проверки

# ── Настройки сканирования ────────────────────────────────────────────────────
CONTROLLER = "xl"
PAGE_LIMIT = 30
HTTP_TIMEOUT = 20.0
DELAY_BETWEEN_PAGES = 0.2

# ── Логирование ───────────────────────────────────────────────────────────────
os.makedirs("/app/logs", exist_ok=True)
os.makedirs("/app/reports", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/audit_xl_mirrored_logos.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("audit_xl_mirrored_logos")

# Глобальный кэш URL → MD5
_image_md5_cache: dict[str, str] = {}


def _build_otto_client() -> ExternalOttoClient:
    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
    )
    return ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)


async def _md5_of_url(url: str, client: httpx.AsyncClient) -> str | None:
    """Скачивает изображение по URL и возвращает его MD5. Использует кэш."""
    if url in _image_md5_cache:
        return _image_md5_cache[url]
    try:
        resp = await client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        if resp.status_code == 200:
            digest = hashlib.md5(resp.content).hexdigest()
            _image_md5_cache[url] = digest
            return digest
        log.warning("HTTP %s при скачивании %s", resp.status_code, url)
    except Exception as exc:
        log.warning("Ошибка скачивания %s: %s", url, exc)
    return None


def _extract_image_urls(variation: dict[str, Any]) -> list[str]:
    """Извлекает все URL изображений из вариации товара."""
    urls = []
    for asset in variation.get("mediaAssets", []):
        if asset.get("type") == "IMAGE":
            loc = asset.get("location") or asset.get("url") or ""
            if loc:
                urls.append(str(loc))
    return urls


async def _check_variation_for_logo(
    variation: dict[str, Any],
    http: httpx.AsyncClient,
) -> dict[str, Any] | None:
    """
    Проверяет одну вариацию товара.
    Возвращает dict с описанием проблемы или None если логотипа нет.
    """
    image_urls = _extract_image_urls(variation)
    if not image_urls:
        return None

    for url in image_urls:
        url_hint_match = LOGO_URL_HINT in url
        md5 = await _md5_of_url(url, http)
        if md5 is None:
            continue
        if md5 == LOGO_MD5:
            remaining_urls = [u for u in image_urls if u != url]
            return {
                "sku": variation.get("sku"),
                "ean": variation.get("ean"),
                "productReference": variation.get("productReference"),
                "title": variation.get("productDescription", {}).get("productLine", ""),
                "logo_url": url,
                "logo_md5": md5,
                "url_hint_matched": url_hint_match,
                "remaining_images": remaining_urls,
                "remaining_count": len(remaining_urls),
            }

    return None


async def main():
    log.info("=" * 70)
    log.info("  АУДИТ: Поиск зеркального логотипа JV Möbel в каталоге XL OTTO")
    log.info("  Эталон MD5: %s", LOGO_MD5)
    log.info("=" * 70)

    otto = _build_otto_client()
    start_time = time.time()

    affected: list[dict[str, Any]] = []
    total_products_checked = 0
    total_variations_checked = 0
    page = 0

    async with httpx.AsyncClient() as http:
        while True:
            log.info("Загружаем страницу %d (limit=%d)...", page, PAGE_LIMIT)

            result = None
            for attempt in range(4):
                try:
                    result = await otto.get_products(
                        GetProductRequest(page=page, limit=PAGE_LIMIT),
                        CONTROLLER,
                    )
                    break
                except httpx.ReadTimeout:
                    wait = [3, 8, 20][min(attempt, 2)]
                    if attempt == 3:
                        log.error("Таймаут на странице %d — прерываем", page)
                        break
                    log.warning("Таймаут. Повтор через %ds (попытка %d/4)...", wait, attempt + 1)
                    await asyncio.sleep(wait)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429:
                        log.warning("Rate limit 429. Ждём 30с...")
                        await asyncio.sleep(30)
                        continue
                    log.error("HTTP ошибка на странице %d: %s", page, exc)
                    break
                except Exception as exc:
                    if attempt == 3:
                        log.error("Ошибка на странице %d: %s", page, exc)
                        break
                    await asyncio.sleep(5)

            if result is None:
                break

            # API может вернуть resources (nested) или productVariations (flat)
            resources = result.get("resources", [])
            product_variations_flat = result.get("productVariations", [])

            # Собираем все вариации в единый список
            all_variations: list[dict] = []
            if resources:
                for product in resources:
                    if "variations" in product:
                        all_variations.extend(product["variations"])
                    else:
                        all_variations.append(product)
            elif product_variations_flat:
                all_variations = product_variations_flat

            if not all_variations:
                log.info("Страница %d пуста — скан завершён.", page)
                break

            log.info(
                "Страница %d: %d вариаций | Всего проверено: %d",
                page, len(all_variations), total_variations_checked,
            )

            for variation in all_variations:
                total_products_checked += 1
                total_variations_checked += 1
                hit = await _check_variation_for_logo(variation, http)
                if hit:
                    affected.append(hit)
                    log.info(
                        "  ✅ НАЙДЕН логотип: SKU=%s | %s",
                        hit["sku"], hit["logo_url"],
                    )

            # Пагинация
            total_pages = result.get("totalPages")
            links = result.get("links", [])
            has_next = any(l.get("rel") == "next" for l in links)
            if total_pages is not None and page >= total_pages - 1:
                log.info("Достигнута последняя страница (%d/%d).", page, total_pages - 1)
                break
            if not has_next and total_pages is None:
                log.info("Нет ссылки 'next' — скан завершён.")
                break

            page += 1
            if DELAY_BETWEEN_PAGES > 0:
                await asyncio.sleep(DELAY_BETWEEN_PAGES)

    elapsed = time.time() - start_time

    log.info("=" * 70)
    log.info("  СКАН ЗАВЕРШЁН за %.1f сек", elapsed)
    log.info("  Проверено продуктов:  %d", total_products_checked)
    log.info("  Проверено вариаций:   %d", total_variations_checked)
    log.info("  Найдено с логотипом:  %d", len(affected))
    log.info("=" * 70)

    safe_to_clean = [h for h in affected if h["remaining_count"] >= 1]
    unsafe = [h for h in affected if h["remaining_count"] == 0]

    if unsafe:
        log.warning(
            "ВНИМАНИЕ: %d вариаций, у которых логотип — единственное изображение! "
            "Они будут ПРОПУЩЕНЫ при удалении (Safety Guard).",
            len(unsafe),
        )
        for u in unsafe:
            log.warning("  Пропуск (нет других фото): SKU=%s %s", u["sku"], u.get("title", ""))

    report = {
        "scan_timestamp": time.time(),
        "controller": CONTROLLER,
        "logo_md5": LOGO_MD5,
        "elapsed_seconds": round(elapsed, 1),
        "total_products_checked": total_products_checked,
        "total_variations_checked": total_variations_checked,
        "total_affected": len(affected),
        "safe_to_clean": len(safe_to_clean),
        "unsafe_skipped": len(unsafe),
        "affected_variations": affected,
    }

    report_path = "/app/reports/affected_xl_jv_logos.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    log.info("Отчёт сохранён: %s", report_path)

    if safe_to_clean:
        log.info("\nТовары для очистки:")
        for h in safe_to_clean:
            log.info(
                "  SKU=%-15s | %d фото останется | %s",
                h["sku"], h["remaining_count"], h.get("title", ""),
            )
    else:
        log.info("Зеркальных логотипов JV Möbel на XL не найдено — всё чисто!")


if __name__ == "__main__":
    asyncio.run(main())
