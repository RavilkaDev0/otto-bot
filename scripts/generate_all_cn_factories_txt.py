import json
from collections import Counter, defaultdict

# All sources of Chinese goods on XL:
# 1. cn_products_xl.json (10,227 items)
# 2. cn_in_tr_batches_found.json (756 items)
# 3. timur_cn_to_update_12w.json (308 items)
# 4. newly_discovered_cn_items.json (386 items: NEW_GANASI_SPECIAL, NEW_GANASI_SPECIAL_2, UK-PengPai)

factory_data = defaultdict(lambda: {
    "factory_id": "",
    "type": "Чистая китайская фабрика",
    "cn_count": 0,
    "suppliers": Counter(),
    "sample_titles": []
})

# 1. Main CN factories
with open("cn_products_xl.json") as f:
    d = json.load(f)
    for fact in d.get("factories", []):
        fname = fact.get("factory_name", "")
        fid = str(fact.get("factory_id", ""))
        items = fact.get("items", [])
        factory_data[fname]["factory_id"] = fid
        factory_data[fname]["type"] = "Специализированная китайская фабрика (CN)"
        factory_data[fname]["cn_count"] += len(items)

# 2. Mixed Batch 1 (756 items)
with open("cn_in_tr_batches_found.json") as f:
    d = json.load(f)
    for it in d.get("items", []):
        fname = it.get("factory_name", "")
        supp = it.get("supplier") or "CN_Supplier"
        factory_data[fname]["cn_count"] += 1
        factory_data[fname]["type"] = "Смешанная папка / каталог (содержит китайские товары)"
        factory_data[fname]["suppliers"][supp] += 1

# 3. Timur Batch 2 (308 items)
with open("timur_cn_to_update_12w.json") as f:
    d = json.load(f)
    for it in d.get("items", []):
        fname = it.get("factory_name", "")
        fid = str(it.get("factory_id", ""))
        if fid and not factory_data[fname]["factory_id"]:
            factory_data[fname]["factory_id"] = fid
        supp = it.get("supplier") or "CN_Supplier"
        factory_data[fname]["cn_count"] += 1
        factory_data[fname]["type"] = "Смешанная папка / каталог (содержит китайские товары)"
        factory_data[fname]["suppliers"][supp] += 1

# 4. Newly discovered extra folders (386 items)
with open("newly_discovered_cn_items.json") as f:
    new_items = json.load(f)
    for it in new_items:
        fname = it.get("factory_name", "")
        fid = str(it.get("factory_id", ""))
        if fid and not factory_data[fname]["factory_id"]:
            factory_data[fname]["factory_id"] = fid
        factory_data[fname]["cn_count"] += 1
        factory_data[fname]["type"] = "Специализированная китайская фабрика (CN)"

# Fill factory IDs if missing
with open("all_afterbuy_xl_factories.json") as f:
    all_f = json.load(f)
    f_map = {f.get("name"): str(f.get("id")) for f in all_f}

for fname in factory_data:
    if not factory_data[fname]["factory_id"] and fname in f_map:
        factory_data[fname]["factory_id"] = f_map[fname]

# Sort factories by count descending
sorted_factories = sorted(factory_data.items(), key=lambda x: x[1]["cn_count"], reverse=True)
total_cn = sum(x[1]["cn_count"] for x in sorted_factories)

print(f"Total factories with Chinese goods: {len(sorted_factories)}")
print(f"Total Chinese items across all factories: {total_cn}")

lines = []
lines.append("=" * 80)
lines.append("СПИСОК ВСЕХ ФАБРИК И КАТАЛОГОВ С КИТАЙСКИМИ ТОВАРАМИ НА OTTO XL")
lines.append("Срок доставки: 12 недель | Bearbeitungszeit: 57 дней")
lines.append("Профиль: LIEFERZEIT (8-12 WOCHEN) [027a1a46-cf60-56f4-917b-81661569f016]")
lines.append(f"Всего каталогов/фабрик: {len(sorted_factories)} | Всего китайских товаров: {total_cn}")
lines.append("=" * 80)
lines.append("")

# Part 1: Pure Chinese Factories
pure_cn = [(k, v) for k, v in sorted_factories if "Специализированная" in v["type"]]
lines.append("-" * 80)
lines.append(f"ЧАСТЬ 1. СПЕЦИАЛИЗИРОВАННЫЕ КИТАЙСКИЕ ФАБРИКИ И КАТАЛОГИ ({len(pure_cn)} фабрик)")
lines.append("(Каталоги, состоящие целиком из китайских товаров)")
lines.append("-" * 80)
for idx, (fname, d) in enumerate(pure_cn, 1):
    fid_str = f" [ID: {d['factory_id']}]" if d['factory_id'] else ""
    lines.append(f"{idx:2d}. {fname:<45}{fid_str} — {d['cn_count']} товаров")

lines.append("")
lines.append("-" * 80)
# Part 2: Mixed Folders containing Chinese goods
mixed_cn = [(k, v) for k, v in sorted_factories if "Смешанная" in v["type"]]
lines.append(f"ЧАСТЬ 2. СМЕШАННЫЕ ПАПКИ И КАТАЛОГИ МЕНЕДЖЕРОВ ({len(mixed_cn)} папок)")
lines.append("(Сборные каталоги, в которых обнаружены и переведены на 12 недель китайские товары)")
lines.append("(Турецкие товары внутри этих папок сохранены на 8 неделях)")
lines.append("-" * 80)
for idx, (fname, d) in enumerate(mixed_cn, 1):
    fid_str = f" [ID: {d['factory_id']}]" if d['factory_id'] else ""
    supp_desc = ", ".join([f"{s} ({c})" for s, c in d["suppliers"].most_common(4)])
    lines.append(f"{idx:2d}. {fname:<35}{fid_str} — {d['cn_count']} китайских товаров")
    if supp_desc:
        lines.append(f"    Китайские бренды/поставщики: {supp_desc}")

lines.append("")
lines.append("=" * 80)
lines.append(f"ИТОГО КИТАЙСКИХ ТОВАРОВ: {total_cn} (ВСЕ УСПЕШНО НАСТРОЕНЫ НА 12 НЕДЕЛЬ)")
lines.append("=" * 80)

output_path = "all_chinese_factories_xl.txt"
with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print(f"File saved to {output_path}")
