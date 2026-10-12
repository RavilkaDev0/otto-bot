import os
import sys
import json
import time
import subprocess

FACTORIES = [
    {"id": "409886", "name": "SKORZANYSWIAT_(PL)"},
    {"id": "499428", "name": "Chesterfield_2021_(PL)"},
    {"id": "499258", "name": "EMKA_CHESTERFIELD_(PL)"},
]

COOKIES_FILE = "/tmp/aftercool_cookies.txt"
OUTPUT_FILE = "/home/Altera/workdir/otto-bot/logs/aftercool_factory_prices.json"

def login():
    print("Logging into Aftercool...")
    cmd = [
        "curl", "-s", "-c", COOKIES_FILE,
        "-X", "POST", "https://aftercool.de/auth/login",
        "-H", "Content-Type: application/json",
        "-d", '{"username": "Miras", "password": "Miras112112"}'
    ]
    out = subprocess.check_output(cmd)
    res = json.loads(out)
    if not res.get("ok"):
        raise RuntimeError(f"Login failed: {res}")
    print("Login successful.")

def parse_price(val):
    if val is None:
        return None
    s = str(val).strip().replace(" ", "").replace("€", "").replace(",", ".")
    try:
        p = float(s)
        return round(p, 2) if p > 0 else None
    except (ValueError, TypeError):
        return None

def process_uvp(price: float) -> float:
    if price > 5000:
        value = price * 1.10
    elif 2500 <= price <= 4999:
        value = price * 1.18
    elif 1000 <= price <= 2499:
        value = price * 1.25
    else:
        value = price * 1.35
    return round(value, 2)

def fetch_factory_items(factory_id: str, factory_name: str):
    print(f"\nFetching items for factory {factory_name} (ID: {factory_id})...")
    items_by_ean = {}
    offset = 0
    limit = 100
    
    while True:
        url = f"https://aftercool.de/api/products?account=JV&dataset=Lister&factory_id={factory_id}&limit={limit}&offset={offset}&include_row=1"
        cmd = [
            "curl", "-s", "-b", COOKIES_FILE,
            url,
            "--header", "Accept: application/json"
        ]
        out = subprocess.check_output(cmd)
        data = json.loads(out)
        
        total = data.get("total", 0)
        items = data.get("items", [])
        if not items:
            break
            
        for it in items:
            ean = (it.get("ean") or it.get("row", {}).get("EAN") or "").strip()
            raw_price = it.get("row", {}).get("Startpreis")
            price = parse_price(raw_price)
            
            if ean and price:
                uvp = process_uvp(price)
                items_by_ean[ean] = {
                    "ean": ean,
                    "sku": ean, # default sku is ean on JV
                    "product_id": it.get("product_id"),
                    "name": it.get("name") or it.get("row", {}).get("Artikelbeschreibung"),
                    "factory_id": factory_id,
                    "factory_name": factory_name,
                    "price": price,
                    "uvp": uvp,
                    "raw_price": raw_price,
                }
                
        offset += len(items)
        print(f"  [{factory_name}] Fetched offset {offset}/{total} (valid priced items so far: {len(items_by_ean)})")
        
        if offset >= total or len(items) < limit:
            break
            
        time.sleep(0.1)
        
    print(f"Finished {factory_name}: total {len(items_by_ean)} unique EANs with valid prices.")
    return items_by_ean

def main():
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    login()
    
    all_factory_items = {}
    for fact in FACTORIES:
        items = fetch_factory_items(fact["id"], fact["name"])
        all_factory_items[fact["id"]] = {
            "factory_id": fact["id"],
            "factory_name": fact["name"],
            "count": len(items),
            "items": items,
        }
        
    total_items = sum(f["count"] for f in all_factory_items.values())
    print(f"\n==========================================")
    print(f"TOTAL UNIQUE ITEMS COLLECTED: {total_items}")
    print(f"==========================================")
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_factory_items, f, ensure_ascii=False, indent=2)
    print(f"Saved all items to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
