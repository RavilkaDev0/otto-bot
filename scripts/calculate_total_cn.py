import json

all_cn_skus = set()

# 1. Main catalog
try:
    with open("cn_products_xl.json") as f:
        d = json.load(f)
        for fact in d.get("factories", []):
            for it in fact.get("items", []):
                s = str(it.get("ean") or it.get("sku") or "").strip()
                if s: all_cn_skus.add(s)
except Exception as e:
    print("cn_products_xl error:", e)

cnt_main = len(all_cn_skus)
print(f"1. Основной китайский каталог (50 фабрик CN): {cnt_main} товаров")

# 2. UK- папки
cnt_before_uk = len(all_cn_skus)
try:
    with open("uk_cn_products_xl.json") as f:
        d = json.load(f)
        for fact in d.get("factories", []):
            for it in fact.get("items", []):
                s = str(it.get("ean") or it.get("sku") or "").strip()
                if s: all_cn_skus.add(s)
except Exception:
    pass
print(f"2. Дополнительные UK- папки: +{len(all_cn_skus) - cnt_before_uk} новых уникальных")

# 3. Смешанные папки партия 1 (756 товаров)
cnt_before_tr_cn = len(all_cn_skus)
try:
    with open("cn_in_tr_batches_found.json") as f:
        d = json.load(f)
        for it in d.get("items", []):
            s = str(it.get("ean") or it.get("sku") or "").strip()
            if s: all_cn_skus.add(s)
except Exception as e:
    print("cn_in_tr error:", e)
print(f"3. Китайские товары из турецких папок (партия 1): +{len(all_cn_skus) - cnt_before_tr_cn} новых уникальных")

# 4. Фабрики Тимура партия 2 (308 товаров)
cnt_before_timur = len(all_cn_skus)
try:
    with open("timur_cn_to_update_12w.json") as f:
        d = json.load(f)
        for it in d.get("items", []):
            s = str(it.get("ean") or it.get("sku") or "").strip()
            if s: all_cn_skus.add(s)
except Exception as e:
    print("timur_cn error:", e)
print(f"4. Фабрики Тимура (партия 2): +{len(all_cn_skus) - cnt_before_timur} новых уникальных")

print(f"\n=======================================================")
print(f"ИТОГО ВСЕХ УНИКАЛЬНЫХ КИТАЙСКИХ ТОВАРОВ НА XL: {len(all_cn_skus)}")
print(f"=======================================================")
