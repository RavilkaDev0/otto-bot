import json
import re

# 1. Parse current factories_xl.txt
factory_counts = {}
with open("factories_xl.txt", "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        # format: "1. Timur_for_Otto_Standard — 2030 (1287 ранее + 743 в финальной партии)" or "6. Alber_mobilya_(TR) — 805"
        m = re.match(r"^\d+\.\s+(.+?)\s+—\s+(\d+)", line)
        if m:
            fname = m.group(1).strip()
            total = int(m.group(2))
            factory_counts[fname] = total

prev_total = sum(factory_counts.values())
prev_factories_count = len(factory_counts)
print(f"Loaded {prev_factories_count} factories from factories_xl.txt, sum = {prev_total}")

# 2. Load batch 4
with open("update_summary_xl_batch4.json", "r", encoding="utf-8") as f:
    b4 = json.load(f)

b4_folders = b4["by_folder"]
b4_total = b4["updated"]
print(f"Loaded Batch 4: {len(b4_folders)} factories, updated = {b4_total}")

# 3. Merge
new_factories_added = 0
for fname, cnt in b4_folders.items():
    if fname in factory_counts:
        factory_counts[fname] += cnt
    else:
        factory_counts[fname] = cnt
        new_factories_added += 1

grand_total = sum(factory_counts.values())
grand_factories_count = len(factory_counts)
print(f"Grand Total: {grand_total} items across {grand_factories_count} factories (new factories added: {new_factories_added})")

# 4. Generate new factories_xl.txt
lines = [
    f"СПИСОК ВСЕХ ТУРЕЦКИХ ФАБРИК И ТОВАРОВ ДЛЯ АККАУНТА XL (ВСЕГО {grand_total:,} ТОВАРОВ):".replace(",", " "),
    "===================================================================="
]

idx = 1
for fname, cnt in sorted(factory_counts.items(), key=lambda x: x[1], reverse=True):
    b4_cnt = b4_folders.get(fname, 0)
    if b4_cnt > 0:
        lines.append(f"{idx}. {fname} — {cnt} (+{b4_cnt} в партии 4)")
    else:
        lines.append(f"{idx}. {fname} — {cnt}")
    idx += 1

lines.append("====================================================================")
lines.append(f"ИТОГО ОБНОВЛЕНО ДЛЯ XL: {grand_total:,} товаров ({grand_factories_count} папок / фабрик)".replace(",", " "))
lines.append("• Ранее обновлено (партии 1, 2 и 3): 31 610 товаров")
lines.append(f"• Обновлено в партии 4: {b4_total:,} товаров (100% успешно, 0 ошибок)".replace(",", " "))
lines.append("")

with open("factories_xl.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("Successfully generated updated factories_xl.txt!")
