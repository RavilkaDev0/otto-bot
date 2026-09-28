import asyncio
import json
import logging
import os
import re
import sys
from collections import Counter

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings
from scripts.tr_rules import is_turkish_product

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("audit_timur")

TIMUR_FACTORIES = [
    ("Timur_for_Otto_Standard", 483356),
    ("Timur_for_Otto_Standard_II", 483367),
    ("Timur_for_Ottor_Standard_3", 483369),
    ("Timur_for_Ottor_Standard_4", 483378),
    ("Timur_for_Otto_Standard_5", 483381),
    ("Timur_for_Otto_Standard_6", 483391),
    ("Timur_for_Otto_Standard_7", 483396),
    ("Timur_for_Otto_Standard_8", 483403),
    ("Timur_for_Otto_Standard_9", 483406),
]

# Chinese brands / suppliers in Afterbuy image URLs or text
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
    log.info("Starting comprehensive audit of ALL 9 Timur factories directly from Afterbuy...")
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    all_factory_results = []
    total_ab_items = 0
    total_cn_detected = 0
    total_tr_detected = 0
    total_other_detected = 0

    all_cn_items = []

    for fname, fid in TIMUR_FACTORIES:
        log.info("Fetching %s (ID %d)...", fname, fid)
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        log.info("  -> Loaded %d items from Afterbuy", len(items))
        total_ab_items += len(items)

        fact_cn = []
        fact_tr = []
        fact_other = []
        supplier_counts = Counter()

        for it in items:
            row = it.get("row", {})
            sku = str(row.get("EAN") or row.get("sku") or "").strip()
            if not sku:
                continue

            title = str(row.get("Artikelbeschreibung") or "")
            desc = str(row.get("Description") or "") + " " + str(row.get("TranslatedDescription") or "")
            custom = str(row.get("CustomItemSpecifics") or "")
            gurl = str(row.get("GalleryURL") or row.get("PictureURL") or "")
            all_imgs = f"{gurl} {row.get('pictureurls', '')}"
            all_text = f"{title} {desc} {custom}"

            # Extract image supplier
            supplier = "UNKNOWN"
            m_supp = re.search(r"AfterbuyUploadService/([^/]+)/", all_imgs)
            if m_supp:
                supplier = m_supp.group(1).strip()
            else:
                m_url = re.search(r"https?://[^/]+/([^/]+)/", all_imgs)
                if m_url:
                    supplier = m_url.group(1).strip()
            supplier_counts[supplier] += 1

            # Check Chinese detection
            is_cn = False
            cn_reason = ""

            # Check 1: Image supplier / path
            supp_low = supplier.lower()
            for cn_s in CN_SUPPLIERS:
                if cn_s in supp_low or f"/{cn_s}/" in all_imgs.lower() or f"_{cn_s}_" in all_imgs.lower():
                    is_cn = True
                    cn_reason = f"Image path matches CN supplier '{cn_s}' ({supplier})"
                    break

            # Check 2: Text patterns
            if not is_cn:
                m_txt = cn_text_re.search(all_text)
                if m_txt:
                    is_cn = True
                    cn_reason = f"Text matched CN pattern '{m_txt.group(0)}'"

            # Check Turkish detection
            is_tr, tr_reason = is_turkish_product(row)

            item_info = {
                "sku": sku,
                "ean": str(row.get("EAN") or "").strip(),
                "title": title,
                "factory_name": fname,
                "factory_id": fid,
                "supplier": supplier,
                "gurl": gurl[:120],
                "is_stamm": row.get("I_stammartikel"),
            }

            if is_cn and not is_tr:
                item_info["reason"] = cn_reason
                fact_cn.append(item_info)
                all_cn_items.append(item_info)
            elif is_tr and not is_cn:
                item_info["reason"] = tr_reason
                fact_tr.append(item_info)
            elif is_cn and is_tr:
                # Conflict!
                item_info["reason"] = f"CONFLICT: CN ({cn_reason}) vs TR ({tr_reason})"
                log.warning("CONFLICT on SKU %s: %s vs %s", sku, cn_reason, tr_reason)
                # Image folder / URL takes precedence
                if "lenatees" in supp_low or "kuka" in supp_low or "ganasi" in supp_low:
                    fact_cn.append(item_info)
                    all_cn_items.append(item_info)
                else:
                    fact_tr.append(item_info)
            else:
                fact_other.append(item_info)

        log.info("  -> Breakdown for %s: CN=%d, TR=%d, Other/Uncertain=%d", fname, len(fact_cn), len(fact_tr), len(fact_other))
        log.info("     Top suppliers: %s", supplier_counts.most_common(5))
        total_cn_detected += len(fact_cn)
        total_tr_detected += len(fact_tr)
        total_other_detected += len(fact_other)

        all_factory_results.append({
            "factory_name": fname,
            "factory_id": fid,
            "total_items": len(items),
            "cn_count": len(fact_cn),
            "tr_count": len(fact_tr),
            "other_count": len(fact_other),
            "suppliers": dict(supplier_counts.most_common(10)),
            "other_samples": fact_other[:5]
        })

    print("\n=======================================================")
    print(f"AUDIT SUMMARY OF ALL 9 TIMUR FACTORIES:")
    print(f"  Total items across 9 Timur factories: {total_ab_items}")
    print(f"  Total CHINESE detected: {total_cn_detected}")
    print(f"  Total TURKISH detected: {total_tr_detected}")
    print(f"  Total OTHER / Uncertain: {total_other_detected}")
    print("=======================================================\n")

    # Save results to file
    with open("/app/timur_factories_audit.json", "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total_items": total_ab_items,
                "cn_detected": total_cn_detected,
                "tr_detected": total_tr_detected,
                "other_detected": total_other_detected
            },
            "factories": all_factory_results,
            "cn_items": all_cn_items
        }, f, indent=2, ensure_ascii=False)
    log.info("Saved report to /app/timur_factories_audit.json")

if __name__ == "__main__":
    asyncio.run(main())
