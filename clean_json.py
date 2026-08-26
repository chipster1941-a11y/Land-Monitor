import json

with open("storage_state.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for cookie in data.get("cookies", []):
    exp = cookie.get("expires")
    if exp is not None and float(exp) > 0:
        cookie["expires"] = int(float(exp))
    else:
        cookie["expires"] = -1
    
    cookie["secure"] = bool(cookie.get("secure"))
    cookie["httpOnly"] = bool(cookie.get("httpOnly"))

with open("storage_state.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

print("storage_state.json sanitized successfully!")