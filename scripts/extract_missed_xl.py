import json
import re

with open("/app/all_afterbuy_xl_factories.json") as f:
    xl_factories = json.load(f)

with open("/app/factories_xl.txt") as f:
    lines = f.readlines()

already_updated = set()
for l in lines:
    m = re.match(r"^\d+\.\s+([^\s—]+(?:\s+[^\s—]+)*)\s+—", l)
    if m:
        already_updated.add(m.group(1).strip().lower())

with open("/app/factories.txt") as f:
    lines = f.readlines()

jv_factories = set()
for l in lines:
    m = re.match(r"^\d+\.\s+([^\s—]+(?:\s+[^\s—]+)*)\s+—", l)
    if m:
        name = re.sub(r"\s+\([^)]+\)", "", m.group(1)).strip().lower()
        jv_factories.add(name)

# Find all TR or Timur or JV factories that were NOT in factories_xl.txt
missed = []
for f in xl_factories:
    name = f.get("name", "").strip()
    low = name.lower()
    clean_low = re.sub(r"\s+\([^)]+\)", "", low).strip()
    items_count = f.get("items_count", 0)
    
    if items_count == 0:
        continue
        
    is_in_jv = any(j in low or low in j for j in jv_factories)
    is_tr_tag = "(tr)" in low or "_tr" in low or "timur" in low
    
    if (is_in_jv or is_tr_tag) and low not in already_updated and clean_low not in already_updated:
        missed.append({
            "id": f.get("id"),
            "name": name,
            "items_count": items_count,
            "is_in_jv": is_in_jv,
            "is_tr_tag": is_tr_tag
        })

print(f"=== MISSED FACTORIES ON XL WITH ITEMS > 0: {len(missed)} ===")
total_items = sum(m["items_count"] for m in missed)
print(f"Total raw items in these factories: {total_items}\n")

# Save to file
with open("/app/xl_missed_factories_list.json", "w") as f:
    json.dump(missed, f, indent=2)

for i, m in enumerate(sorted(missed, key=lambda x: x["items_count"], reverse=True), 1):
    tag = "[IN JV]" if m["is_in_jv"] else ""
    print(f"{i:2d}. {m['name']} — {m['items_count']} items (ID: {m['id']}) {tag}")
