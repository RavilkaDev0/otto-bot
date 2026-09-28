import asyncio
import json
import logging
import re
import sys
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.clients.external_otto_client import ExternalOttoClient
from app.core.otto_auth import OttoAuth
from app.core.configs import settings
from scripts.tr_rules import is_turkish_product

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("audit_mixed_other")

EXTRA_FACTORIES = [
    ("Kuka_Home_(TR)", 481853),
    ("NEW_ROPEZ_SPECIAL", 483385),
    ("NEW_ROPEZ_SPECIAL_2", 483386),
    ("NEW_ROPEZ_SPECIAL_3", 483392),
    ("Others_MixFabriks(TR)", 482606),
]

CN_SUPPLIERS = [
    "lenatees", "leenatees", "jingyong", "dongguan_yafulai", "yafulai",
    "kuka", "mingcasa", "youme", "you-me", "alibaba", "ganasi", "pengpai",
    "youbond", "you-bond", "yqc", "evergo", "weitao", "bailong", "defaico",
    "biaodi", "kadi", "oubaolong", "haoyu", "hengxuan", "milliard", "fendi_casa",
    "foshan", "dongguan", "shenzhen", "guangzhou", "shunde"
]

CN_TEXT_PATTERNS = [
    r"\bcountry\s+CN\b",
    r"\bhergestellt\s+in\s+china\b",
    r"\bmade\s+in\s+china\b",
    r"\baus\s+china\b",
    r"\(cn\)",
    r"_cn\b",
]
cn_text_re = re.compile("|".join(CN_TEXT_PATTERNS), re.IGNORECASE)

async def main():
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    auth = OttoAuth(
        client_id=settings.otto_jv_client_id,
        client_secret=settings.otto_jv_client_secret,
        base_url=settings.otto_base_url,
        scope=settings.otto_scope,
        timeout=settings.otto_timeout_seconds,
        xl_client_id=settings.otto_xl_client_id,
        xl_client_secret=settings.otto_xl_client_secret,
    )
    otto = ExternalOttoClient(auth=auth, base_url=settings.otto_base_url)
    sem = asyncio.Semaphore(15)

    all_cn_items = []

    for fname, fid in EXTRA_FACTORIES:
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        log.info("Loaded %d items from %s (ID %d)", len(items), fname, fid)

        for it in items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku: continue

            title = str(row.get("Artikelbeschreibung") or "")
            desc = str(row.get("Description") or "") + " " + str(row.get("TranslatedDescription") or "")
            custom = str(row.get("CustomItemSpecifics") or "")
            gurl = str(row.get("GalleryURL") or row.get("PictureURL") or "")
            all_imgs = f"{gurl} {row.get('pictureurls', '')}"
            all_text = f"{title} {desc} {custom}"

            # Supplier
            m = re.search(r"AfterbuyUploadService/([^/]+)/", all_imgs)
            supp = m.group(1).strip() if m else "UNKNOWN"
            if supp == "UNKNOWN":
                m2 = re.search(r"https?://[^/]+/([^/]+)/", all_imgs)
                if m2: supp = m2.group(1).strip()

            is_cn = False
            cn_reason = ""
            supp_low = supp.lower()
            for cn_s in CN_SUPPLIERS:
                if cn_s in supp_low or f"/{cn_s}/" in all_imgs.lower() or f"_{cn_s}_" in all_imgs.lower():
                    is_cn = True
                    cn_reason = f"Supplier '{supp}' matches '{cn_s}'"
                    break
            if not is_cn:
                m_txt = cn_text_re.search(all_text)
                if m_txt:
                    is_cn = True
                    cn_reason = f"Text matches '{m_txt.group(0)}'"

            is_tr, tr_reason = is_turkish_product(row)

            if is_cn and not is_tr:
                all_cn_items.append({
                    "sku": sku,
                    "title": title,
                    "factory_name": fname,
                    "factory_id": fid,
                    "supplier": supp,
                    "gurl": gurl[:100],
                    "reason": cn_reason
                })
            elif is_cn and is_tr:
                # E.g. Kuka
                if "kuka" in supp_low or "kuka" in fname.lower():
                    all_cn_items.append({
                        "sku": sku,
                        "title": title,
                        "factory_name": fname,
                        "factory_id": fid,
                        "supplier": supp,
                        "gurl": gurl[:100],
                        "reason": f"Kuka CN brand ({cn_reason})"
                    })

    log.info("Total CN items detected in extra 5 folders: %d", len(all_cn_items))

    # Check live OTTO status for these extra items
    pt35_count = 0
    pt57_count = 0
    other_count = 0

    async def check_live(it):
        nonlocal pt35_count, pt57_count, other_count
        async with sem:
            try:
                res = await otto.get_shipping_profile_for_product(it["sku"], "xl")
                pt = str(res.get("processingTime") or "")
                it["live_pt"] = pt
                it["live_prof"] = str(res.get("shippingProfileId") or "")
                if pt == "35":
                    pt35_count += 1
                elif pt == "57":
                    pt57_count += 1
                else:
                    other_count += 1
            except Exception:
                pass

    await asyncio.gather(*[check_live(it) for it in all_cn_items])

    log.info("==================================================")
    log.info("EXTRA 5 FOLDERS CN STATUS:")
    log.info("  * Total CN: %d", len(all_cn_items))
    log.info("  * processingTime = 35 (NEED 12w): %d", pt35_count)
    log.info("  * processingTime = 57 (Already 12w): %d", pt57_count)
    log.info("  * other: %d", other_count)
    log.info("==================================================")

    with open("/app/extra_mixed_cn_status.json", "w", encoding="utf-8") as out:
        json.dump(all_cn_items, out, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    asyncio.run(main())
