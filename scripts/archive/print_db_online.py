import json

with open("remaining_unupdated_tr_all.json") as f:
    data = json.load(f)

print("Folders with items marked Online in local DB:")
total_db_online = 0
for d in data:
    cnt = d.get("db_online_count", 0)
    if cnt > 0:
        total_db_online += cnt
        name = d["factory_name"]
        fid = d["factory_id"]
        tot = d["count"]
        print(f"  * {name} (ID: {fid}) -> {cnt} Online items | Total candidates: {tot}")

print(f"\nTotal Online in local DB across these folders: {total_db_online}")
