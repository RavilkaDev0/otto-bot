"""
Этап 4: Безопасное удаление зеркального логотипа JV Möbel из товаров XL OTTO.

Читает отчёт из /app/reports/affected_xl_jv_logos.json,
созданный скриптом audit_xl_mirrored_logos.py.

Для каждого затронутого товара:
 1. GET /v5/products?sku=... — получить актуальные данные вариации
 2. Убрать из mediaAssets только элемент с совпадающим MD5
 3. Safety Guard: если не остаётся ни одного IMAGE — пропустить вариацию
 4. POST /v5/products — обновить mediaAssets (только это поле меняется)

Запуск:
  python scripts/remove_mirrored_logos_xl.py           # реальное выполнение
  python scripts/remove_mirrored_logos_xl.py --dry-run  # симуляция без изменений
"""
import argparse
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

# ── Константы ─────────────────────────────────────────────────────────────────
LOGO_MD5 = "0afd3bdf6e522b09848f786d16912a99"
CONTROLLER = "xl"
REPORT_PATH = "/app/reports/affected_xl_jv_logos.json"
HTTP_TIMEOUT = 20.0
DELAY_BETWEEN_ITEMS = 0.3     # задержка между обновлениями (rate-limit)

# ── Логирование ───────────────────────────────────────────────────────────────
os.makedirs("/app/logs", exist_ok=True)
os.makedirs("/app/backups", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/logs/remove_mirrored_logos_xl.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("remove_mirrored_logos_xl")

# Глобальный кэш MD5
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


async def _retry(coro_func, *args, max_retries=4):
    """Вызывает корутину с повторами при временных ошибках."""
    delays = [2, 5, 15, 30]
    for attempt in range(max_retries):
        try:
            return await coro_func(*args)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (400, 401, 403, 404, 422):
                raise  # не повторяем при клиентских ошибках
            if status == 429:
                wait = 30
            else:
                wait = delays[min(attempt, len(delays) - 1)]
            if attempt == max_retries - 1:
                raise
            log.warning("HTTP %s. Повтор через %ds...", status, wait)
            await asyncio.sleep(wait)
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            wait = delays[min(attempt, len(delays) - 1)]
            log.warning("Ошибка: %s. Повтор через %ds...", exc, wait)
            await asyncio.sleep(wait)


async def _get_live_variation(otto: ExternalOttoClient, sku: str) -> dict[str, Any] | None:
    """
    Получает актуальную вариацию товара из OTTO API по SKU.
    Возвращает dict вариации или None если не найдено.
    """
    try:
        result = await _retry(otto.get_products, GetProductRequest(sku=sku), CONTROLLER)
    except Exception as exc:
        log.error("Не удалось получить товар SKU=%s: %s", sku, exc)
        return None

    resources = result.get("resources", [])
    for product in resources:
        for variation in product.get("variations", []):
            if str(variation.get("sku")) == str(sku):
                return variation
    return None


async def _remove_logo_from_variation(
    variation: dict[str, Any],
    http: httpx.AsyncClient,
) -> tuple[list[dict], list[dict]] | None:
    """
    Проверяет mediaAssets вариации и удаляет элемент с зеркальным логотипом.

    Возвращает: (cleaned_assets, removed_assets) или None если логотип не найден.
    """
    media_assets = variation.get("mediaAssets", [])
    cleaned = []
    removed = []

    for asset in media_assets:
        if asset.get("type") != "IMAGE":
            cleaned.append(asset)
            continue

        loc = asset.get("location") or asset.get("url") or ""
        url = str(loc)
        if not url:
            cleaned.append(asset)
            continue

        md5 = await _md5_of_url(url, http)
        if md5 == LOGO_MD5:
            removed.append(asset)
            log.info("    Удаляем: %s (MD5=%s)", url, md5)
        else:
            cleaned.append(asset)

    if not removed:
        return None

    return cleaned, removed


async def _build_update_payload(variation: dict[str, Any], new_media: list[dict]) -> dict[str, Any]:
    """
    Строит payload для POST /v5/products из оригинальной вариации,
    заменяя только mediaAssets. Все остальные поля берутся как есть.
    """
    updated = dict(variation)
    updated["mediaAssets"] = new_media
    return updated


async def main():
    parser = argparse.ArgumentParser(
        description="Удаление зеркального логотипа JV Möbel из товаров XL OTTO"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Симуляция без реальных изменений в OTTO API",
    )
    args = parser.parse_args()

    log.info("=" * 70)
    log.info(
        "  УДАЛЕНИЕ зеркального логотипа JV Möbel из XL OTTO%s",
        " [DRY-RUN]" if args.dry_run else "",
    )
    log.info("  MD5 цели: %s", LOGO_MD5)
    log.info("=" * 70)

    # ── 1. Загрузка отчёта аудита ─────────────────────────────────────────────
    if not os.path.exists(REPORT_PATH):
        log.error("Отчёт аудита не найден: %s", REPORT_PATH)
        log.error("Сначала запустите: python scripts/audit_xl_mirrored_logos.py")
        return

    with open(REPORT_PATH, encoding="utf-8") as f:
        report = json.load(f)

    affected = report.get("affected_variations", [])
    total_affected = len(affected)

    if total_affected == 0:
        log.info("Отчёт аудита: затронутых товаров нет. Удалять нечего!")
        return

    # Только те у кого после удаления останется >= 1 IMAGE (Safety Guard)
    safe = [h for h in affected if h.get("remaining_count", 0) >= 1]
    skipped_unsafe = [h for h in affected if h.get("remaining_count", 0) == 0]

    log.info(
        "Загружен отчёт: %d затронутых | %d безопасно удалять | %d пропуск (нет других фото)",
        total_affected, len(safe), len(skipped_unsafe),
    )

    if skipped_unsafe:
        log.warning("Пропускаем %d вариаций (единственное фото — логотип):", len(skipped_unsafe))
        for u in skipped_unsafe:
            log.warning("  SKU=%s %s", u["sku"], u.get("title", ""))

    if not safe:
        log.info("Нет безопасных вариаций для обновления.")
        return

    # ── 2. Бэкап ─────────────────────────────────────────────────────────────
    backup_ts = int(time.time())
    backup_path = f"/app/backups/xl_media_assets_backup_{backup_ts}.json"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump({"report": report, "timestamp": backup_ts}, f, indent=2, ensure_ascii=False)
    log.info("Бэкап сохранён: %s", backup_path)

    # ── 3. Обновление ─────────────────────────────────────────────────────────
    otto = _build_otto_client()
    start_time = time.time()
    success_count = 0
    error_count = 0
    skip_count = 0

    async with httpx.AsyncClient() as http:
        for idx, hit in enumerate(safe, 1):
            sku = hit["sku"]
            log.info("[%d/%d] Обрабатываем SKU=%s ...", idx, len(safe), sku)

            # Получаем актуальные данные из OTTO (чтобы не перезаписать изменения)
            variation = await _get_live_variation(otto, sku)
            if variation is None:
                log.error("  SKU=%s: не удалось получить актуальные данные — пропуск", sku)
                skip_count += 1
                continue

            # Удаляем логотип
            result = await _remove_logo_from_variation(variation, http)
            if result is None:
                log.info("  SKU=%s: логотип уже отсутствует — пропуск", sku)
                skip_count += 1
                continue

            cleaned_assets, removed_assets = result

            # Safety Guard: не обновляем если не останется IMAGE
            remaining_images = [a for a in cleaned_assets if a.get("type") == "IMAGE"]
            if not remaining_images:
                log.warning(
                    "  SKU=%s: Safety Guard — после удаления не остаётся ни одного IMAGE! Пропуск.",
                    sku,
                )
                skip_count += 1
                continue

            log.info(
                "  SKU=%s: удаляем %d лого-фото, остаётся %d изображений",
                sku, len(removed_assets), len(remaining_images),
            )

            if args.dry_run:
                log.info("  [DRY-RUN] POST /v5/products был бы отправлен для SKU=%s", sku)
                success_count += 1
                continue

            # Строим payload: оригинальная вариация + обновлённый mediaAssets
            payload = await _build_update_payload(variation, cleaned_assets)

            try:
                await _retry(otto.create_or_update_product, [payload], CONTROLLER)
                success_count += 1
                log.info("  SKU=%s: ✅ Логотип удалён успешно", sku)
            except Exception as exc:
                log.error("  SKU=%s: ❌ Ошибка обновления: %s", sku, exc)
                error_count += 1

            if DELAY_BETWEEN_ITEMS > 0:
                await asyncio.sleep(DELAY_BETWEEN_ITEMS)

    elapsed = time.time() - start_time

    log.info("=" * 70)
    log.info("  УДАЛЕНИЕ ЗАВЕРШЕНО за %.1f сек%s", elapsed, " [DRY-RUN]" if args.dry_run else "")
    log.info("  Всего затронутых:  %d", total_affected)
    log.info("  ✅ Успешно:         %d", success_count)
    log.info("  ⚠️  Пропущено:      %d", skip_count + len(skipped_unsafe))
    log.info("  ❌ Ошибок:          %d", error_count)
    log.info("=" * 70)

    # Итоговый лог
    summary = {
        "timestamp": time.time(),
        "dry_run": args.dry_run,
        "controller": CONTROLLER,
        "logo_md5": LOGO_MD5,
        "total_affected": total_affected,
        "success_count": success_count,
        "skip_count": skip_count + len(skipped_unsafe),
        "error_count": error_count,
        "elapsed_seconds": round(elapsed, 1),
    }
    summary_path = "/app/reports/remove_jv_logos_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    log.info("Итоговый лог: %s", summary_path)


if __name__ == "__main__":
    asyncio.run(main())
