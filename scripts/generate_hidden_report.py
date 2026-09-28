import json

with open("hidden_tr_found_unmarked.json") as f:
    data = json.load(f)

lines = [
    f"СПИСОК НАЙДЕННЫХ ТУРЕЦКИХ ТОВАРОВ В ПАПКАХ БЕЗ МЕТКИ (TR) ДЛЯ XL (ВСЕГО {data['total_items']} ТОВАРОВ):",
    "===================================================================="
]

lines.append("\nРАЗБИВКА ПО ТУРЕЦКИМ БРЕНДАМ / ФАБРИКАМ:")
for brand, cnt in sorted(data["by_tr_brand"].items(), key=lambda x: x[1], reverse=True):
    lines.append(f"  * {brand.upper()} — {cnt} товаров")

lines.append("\nРАЗБИВКА ПО ПАПКАМ AFTERBUY, ГДЕ ОНИ НАХОДЯТСЯ:")
for fn, cnt in sorted(data["by_folder"].items(), key=lambda x: x[1], reverse=True):
    lines.append(f"  * {fn} — {cnt} товаров")

lines.append("====================================================================")
lines.append("Статус на OTTO XL: проверено через API — у них сейчас короткий срок (None/2-5 дней)")

with open("factories_xl_hidden_tr.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("Created factories_xl_hidden_tr.txt")
