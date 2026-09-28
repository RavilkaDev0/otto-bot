import json

all_cn_skus = set()

# 1. Main CN
with open("cn_products_xl.json") as f:
    d = json.load(f)
    for fact in d.get("factories", []):
        for it in fact.get("items", []):
            s = str(it.get("ean") or it.get("sku") or "").strip()
            if s: all_cn_skus.add(s)

cnt1 = len(all_cn_skus)

# 2. Mixed Batch 1
with open("cn_in_tr_batches_found.json") as f:
    d = json.load(f)
    for it in d.get("items", []):
        s = str(it.get("ean") or it.get("sku") or "").strip()
        if s: all_cn_skus.add(s)

cnt2 = len(all_cn_skus)

# 3. Timur Batch 2
with open("timur_cn_to_update_12w.json") as f:
    d = json.load(f)
    for it in d.get("items", []):
        s = str(it.get("ean") or it.get("sku") or "").strip()
        if s: all_cn_skus.add(s)

cnt3 = len(all_cn_skus)

# 4. Extra Ganasi/Evergo Batch 3
with open("extra_cn_385_to_update_12w.json") as f:
    d = json.load(f)
    for it in d.get("items", []):
        s = str(it.get("ean") or it.get("sku") or "").strip()
        if s: all_cn_skus.add(s)

cnt4 = len(all_cn_skus)

print(f"1. Основной каталог (50 фабрик): {cnt1}")
print(f"2. + Смешанные папки (партия 1): {cnt2 - cnt1} (всего: {cnt2})")
print(f"3. + Фабрики Тимура (партия 2): {cnt3 - cnt2} (всего: {cnt3})")
print(f"4. + Специальные папки Ganasi/Evergo (партия 3): {cnt4 - cnt3} (всего: {cnt4})")
print(f"ИТОГО УНИКАЛЬНЫХ КИТАЙСКИХ ТОВАРОВ: {len(all_cn_skus)}")
