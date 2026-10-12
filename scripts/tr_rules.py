"""Shared detection rules and brand dictionaries for identifying Turkish products.
Works across both JV and XL accounts.
"""

import re
from scripts.tr_factories_list import VALID_TR_FACTORIES

# Clean Turkish factory names for folder matching
CLEAN_TR_FACTORIES = set()
for f in VALID_TR_FACTORIES:
    clean = re.sub(r"(_|\s)*\((TR|Tr)\)", "", f).strip().lower()
    clean = re.sub(r"(_|\s)*TR$", "", clean).strip()
    if len(clean) >= 3:
        CLEAN_TR_FACTORIES.add(clean)

# Verified Turkish furniture brands and supplier names
KNOWN_TR_BRANDS = {
    "merli", "alpino", "burobig", "vettero", "rixxe", "rixxehome", "triodoor",
    "trio", "erika", "evart", "duga", "tuare", "verengarden", "safa", "modelin",
    "cisa", "deco_hill", "kuka", "panda", "patroon", "sandalie", "brezza",
    "montro", "favory", "serhathome", "kaju", "eymense", "nott", "seyran",
    "gizetta", "vanitti", "luce_garden", "newsett", "alonex", "mse_bahce",
    "enter_sofa", "natura_outdoor", "zekioglu", "ada", "voguess", "vivanza",
    "woodface", "ulgen", "depotum", "bermonti", "wossew", "atolyekoltuk",
    "burosit", "bürosit", "acel", "saltanat", "saloni", "sandalya", "koltuk",
    "mobilya", "newberonni", "essence", "epika", "inci", "uyku", "mre"
}

ALL_TR_KEYWORDS = CLEAN_TR_FACTORIES | KNOWN_TR_BRANDS


def is_turkish_product(row: dict) -> tuple[bool, str]:
    """Inspect product row from Afterbuy and return (True, reason) if origin is Turkey."""
    all_imgs = f"{row.get('GalleryURL', '')} {row.get('PictureURL', '')} {row.get('pictureurls', '')}"
    title = str(row.get("Artikelbeschreibung") or "")
    desc = str(row.get("Description") or "") + " " + str(row.get("TranslatedDescription") or "")
    specifics = str(row.get("CustomItemSpecifics") or "")
    short_desc = str(row.get("Kurzbeschreibung") or row.get("ShortDescription") or "")
    combined_text = f"{title} {desc} {specifics} {short_desc}"

    # 1. Inspect image upload folder
    m = re.search(r"AfterbuyUploadService/([^/]+)/", all_imgs)
    if m:
        folder = m.group(1).strip()
        folder_low = folder.lower()
        if re.search(r"(_tr\b|/tr/|_tr_|\(tr\))", folder, re.I):
            return True, f"Image upload folder '{folder}' has TR tag"
        clean_folder = re.sub(r"^\d+_", "", folder_low).strip()
        for kw in ALL_TR_KEYWORDS:
            if clean_folder == kw or clean_folder.startswith(kw + "_") or clean_folder.endswith("_" + kw):
                return True, f"Image upload folder '{folder}' matches TR brand '{kw}'"

    # 2. Text markers in description, attributes and short description
    if re.search(r"\bcountry\s+TR\b", combined_text, re.I):
        return True, "'country TR' in product details"

    # Turkish furniture manufacturing cities (Inegol, Bursa, Kayseri, Istanbul, Ankara, Izmir, Adana, Gaziantep)
    m_city = re.search(r"\bcity\s+(INE|BUR|KAY|IST|ANK|IZM|ADA|GAZ)\b", combined_text, re.I)
    if m_city:
        return True, f"Turkish city code 'city {m_city.group(1).upper()}' in product details"

    # Explicit origin text
    if re.search(r"\b(made\s+in\s+turkey|hergestellt\s+in\s+(der\s+)?türkei)\b", combined_text, re.I):
        return True, "Explicit 'Made in Turkey' in product details"

    # 3. Tags in product title
    if re.search(r"(_TR\b|_TR_|\(TR\)|_\(TR\))", title, re.I):
        return True, "TR tag in product title"

    return False, ""
