import asyncio
import json
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.clients.afterbuy_client import AfterbuyClient
from app.core.configs import settings

async def main():
    ab = AfterbuyClient(base_url=settings.afterbuy_base_url, timeout=60.0)
    session = await ab.login(username=settings.afterbuy_username, password=settings.afterbuy_password)
    
    # Factory 8
    fid = 483403
    print(f"Fetching products for factory {fid} (Timur_for_Otto_Standard_8)...")
    resp = await ab.send_request(
        "GET",
        "/api/products",
        params={"account": "xl", "dataset": "lister", "factory_id": fid, "limit": 0, "include_row": 1},
        cookies={"session": session},
    )
    items = resp.json().get("items", [])
    print(f"Total items in Afterbuy for Timur 8: {len(items)}")
    
    # Check fields in first item
    if items:
        row = items[0].get("row", {})
        print("Keys in row:", sorted(list(row.keys())))
        
    # Search for 'country' or 'CN' or 'China' in all items
    cn_matches = []
    country_vals = set()
    for it in items:
        row = it.get("row", {})
        sku = str(row.get("EAN") or row.get("sku") or "").strip()
        title = str(row.get("Artikelbeschreibung") or "")
        short_desc = str(row.get("Kurzbeschreibung") or row.get("ShortDescription") or "")
        desc = str(row.get("Description") or "")
        custom = str(row.get("CustomItemSpecifics") or "")
        gurl = str(row.get("GalleryURL") or row.get("PictureURL") or "")
        
        all_text = f"{title} {short_desc} {desc} {custom}"
        
        # Check for country markers
        import re
        m_country = re.findall(r"country\s+([A-Za-z0-9_-]+)", all_text, re.I)
        for c in m_country:
            country_vals.add(c.upper())
            
        m_cn = re.search(r"\bcountry\s+CN\b", all_text, re.I) or "country cn" in all_text.lower()
        if m_cn:
            cn_matches.append({
                "sku": sku,
                "title": title,
                "short_desc": short_desc,
                "gurl": gurl[:80]
            })
            
    print(f"\nDiscovered country markers in Timur 8: {country_vals}")
    print(f"Matches for 'country CN': {len(cn_matches)}")
    for m in cn_matches[:10]:
        print(f"--- SKU: {m['sku']} ---")
        print(f"Title: {m['title']}")
        print(f"Kurzbeschreibung: {m['short_desc']}")
        print(f"GURL: {m['gurl']}")

if __name__ == "__main__":
    asyncio.run(main())
