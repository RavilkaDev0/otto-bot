import os, signal

killed = []
for pid in os.listdir("/proc"):
    if pid.isdigit() and int(pid) > 1:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().decode("utf-8", errors="ignore").replace("\x00", " ")
                if "update_tr_products.py" in cmd:
                    os.kill(int(pid), signal.SIGKILL)
                    killed.append((pid, cmd))
        except Exception:
            pass

if killed:
    for p, c in killed:
        print(f"KILLED: PID {p} -> {c}")
else:
    print("No update_tr_products.py process found.")
