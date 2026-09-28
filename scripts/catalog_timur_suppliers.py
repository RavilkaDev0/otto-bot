import asyncio
import json
import logging
import re
import sys
from collections import Counter, defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

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

async def main():
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)

    suppliers = defaultdict(lambda: {"count": 0, "urls": set(), "titles": [], "factories": set()})

    for fname, fid in TIMUR_FACTORIES:
        resp = await ab.send_request(
            "GET",
            "/api/products",
            params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
            cookies={"session": session},
        )
        items = resp.json().get("items", [])
        for it in items:
            row = it.get("row", {})
            gurl = str(row.get("GalleryURL") or row.get("PictureURL") or "")
            title = str(row.get("Artikelbeschreibung") or "")
            m = re.search(r"AfterbuyUploadService/([^/]+)/", gurl)
            supp = m.group(1).strip() if m else "UNKNOWN"
            if supp == "UNKNOWN":
                m2 = re.search(r"https?://[^/]+/([^/]+)/", gurl)
                if m2:
                    supp = m2.group(1).strip()
            suppliers[supp]["count"] += 1
            suppliers[supp]["factories"].add(fname)
            if len(suppliers[supp]["urls"]) < 3 and gurl:
                suppliers[supp]["urls"].add(gurl)
            if len(suppliers[supp]["titles"]) < 3 and title:
                suppliers[supp]["titles"].append(title)

    print(f"Total distinct suppliers found: {len(suppliers)}")
    # Sort by count
    sorted_supp = sorted(suppliers.items(), key=lambda x: x[1]["count"], reverse=True)
    out_list = []
    for supp, d in sorted_supp:
        out_list.append({
            "supplier": supp,
            "count": d["count"],
            "factories": list(d["factories"]),
            "sample_urls": list(d["urls"])[:2],
            "sample_titles": d["titles"][:2],
        })

    with open("/app/all_timur_suppliers_catalog.json", "w", encoding="utf-8") as f:
        json.dump(out_list, f, indent=2, ensure_ascii=False)
    print("Saved to /app/all_timur_suppliers_catalog.json")

if __name__ == "__main__":
    asyncio.run(main())
