import json

SRC = "nd_cookies.json"
OUT = "nextdoor_state.json"

SAMESITE = {
    "no_restriction": "None",
    "none": "None",
    "lax": "Lax",
    "strict": "Strict",
    "unspecified": "Lax",
}

with open(SRC, encoding="utf-8-sig") as f:
    raw = json.load(f)

cookies = []
for c in raw:
    cookies.append({
        "name": c["name"],
        "value": c["value"],
        "domain": c["domain"],
        "path": c.get("path", "/"),
        "expires": float(c.get("expirationDate", -1)),
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": bool(c.get("secure", False)),
        "sameSite": SAMESITE.get(str(c.get("sameSite", "unspecified")).lower(), "Lax"),
    })

with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"cookies": cookies, "origins": []}, f, indent=2)

print(f"Done. Wrote {len(cookies)} cookies to {OUT}.")