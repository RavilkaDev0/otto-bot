import json
import re

with open("all_afterbuy_xl_factories.json") as f:
    all_f = json.load(f)

# Load existing factories in factories_xl.txt
existing_f = set()
with open("factories_xl.txt") as f:
    for line in f:
        if " — " in line and line[0].isdigit():
            fn = line.split(". ", 1)[1].split(" — ")[0].strip().lower()
            existing_f.add(fn)

print(f"Total known XL factories: {len(existing_f)}")

non_foreign_remaining = []
foreign_remaining = []

for f in all_f:
    name = f.get("name", "").strip()
    cnt = f.get("items_count", 0)
    if cnt == 0:
        continue
    low = name.lower()
    if low in existing_f:
        continue
    
    # Check if explicitly foreign
    is_foreign = False
    for code in ["(pl)", "(it)", "(cn)", "(de)", "(cz)", "(uk)", "(es)", "(fr)", "(nl)"]:
        if code in low:
            is_foreign = True
            break
    if "italy" in low or "poland" in low or "meble" in low:
        is_foreign = True

    if is_foreign:
        foreign_remaining.append((name, cnt))
    else:
        non_foreign_remaining.append((name, cnt, f.get("id")))

print(f"Foreign remaining (PL, IT, CN, etc.): {len(foreign_remaining)} factories")
print(f"NON-FOREIGN UNCLASSIFIED REMAINING: {len(non_foreign_remaining)} factories")
print("\n=== ALL NON-FOREIGN UNCLASSIFIED FACTORIES ===")
for n, c, fid in sorted(non_foreign_remaining, key=lambda x: x[1], reverse=True):
    print(f"  - {n} (items: {c}, ID: {fid})")

# Save non-foreign unclassified to a JSON file
with open("unclassified_factories_xl.json", "w", encoding="utf-8") as out:
    json.dump([{"name": n, "items_count": c, "id": fid} for n, c, fid in non_foreign_remaining], out, ensure_ascii=False, indent=2)
