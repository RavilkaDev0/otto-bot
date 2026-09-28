import json
from collections import defaultdict

with open("/home/Altera/workdir/otto-bot/tr_products_xl.json", "r", encoding="utf-8") as f:
    base_data = json.load(f)

with open("/home/Altera/workdir/otto-bot/extra_xl_tr_items.json", "r", encoding="utf-8") as f:
    extra_items = json.load(f)

all_items_by_factory = defaultdict(list)
seen_skus = set()

for f in base_data.get("factories", []):
    fname = f["factory_name"]
    for it in f["items"]:
        sku = str(it.get("ean") or it.get("sku")).strip()
        if sku not in seen_skus:
            seen_skus.add(sku)
            all_items_by_factory[fname].append(it)

added_extra = 0
for it in extra_items:
    sku = str(it.get("ean") or it.get("sku")).strip()
    if sku not in seen_skus:
        seen_skus.add(sku)
        fname = it.get("factory_name") or "OTHER_TR"
        all_items_by_factory[fname].append(it)
        added_extra += 1

print(f"Base items: {len(seen_skus) - added_extra}")
print(f"Added extra items: {added_extra}")
print(f"Total merged unique live items: {len(seen_skus)}")
print(f"Total factories: {len(all_items_by_factory)}")

merged_factories = []
total_count = 0
report_lines = [
    "СПИСОК ФАБРИК И ТОВАРОВ ИЗ ТУРЦИИ ДЛЯ АККАУНТА XL:",
    "============================================================"
]

idx = 1
for fname, items in sorted(all_items_by_factory.items(), key=lambda x: len(x[1]), reverse=True):
    merged_factories.append({
        "factory_name": fname,
        "factory_id": items[0].get("factory_id", 0),
        "count": len(items),
        "items": items
    })
    total_count += len(items)
    report_lines.append(f"{idx}. {fname} — {len(items)}")
    idx += 1

report_lines.append("============================================================")
report_lines.append(f"ИТОГО: {total_count} товаров в {len(all_items_by_factory)} фабриках / папках\n")

with open("/home/Altera/workdir/otto-bot/tr_products_xl.json", "w", encoding="utf-8") as f:
    json.dump({"account": "xl", "total_items": total_count, "factories": merged_factories}, f, ensure_ascii=False, indent=2)

with open("/home/Altera/workdir/otto-bot/factories_xl.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(report_lines))

print("Successfully updated tr_products_xl.json and factories_xl.txt!")
