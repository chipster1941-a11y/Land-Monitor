import json
import os

OUT = "nextdoor_state.json"
# Cookie-Editor exports to merge in: file name -> site they belong to
SOURCES = {
    "nd_cookies.json": "nextdoor.com",
    "fb_cookies.json": "facebook.com",
}

SAMESITE = {
    "no_restriction": "None",
    "none": "None",
    "lax": "Lax",
    "strict": "Strict",
    "unspecified": "Lax",
}


def convert(c):
    return {
        "name": c["name"],
        "value": c["value"],
        "domain": c["domain"],
        "path": c.get("path", "/"),
        "expires": float(c.get("expirationDate", -1)),
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": bool(c.get("secure", False)),
        "sameSite": SAMESITE.get(str(c.get("sameSite", "unspecified")).lower(), "Lax"),
    }


# Start from the existing session file so nothing already saved is lost.
state = {"cookies": [], "origins": []}
if os.path.exists(OUT):
    with open(OUT, encoding="utf-8-sig") as f:
        state = json.load(f)

found_any = False
for filename, site in SOURCES.items():
    if not os.path.exists(filename):
        continue
    found_any = True
    with open(filename, encoding="utf-8-sig") as f:
        new_cookies = [convert(c) for c in json.load(f)]
    # Replace any old cookies for this site with the fresh ones.
    state["cookies"] = [c for c in state["cookies"] if site not in c.get("domain", "")]
    state["cookies"].extend(new_cookies)
    print(f"Added {len(new_cookies)} {site} cookies from {filename}.")

if not found_any:
    print("No nd_cookies.json or fb_cookies.json found in this folder. Nothing changed.")
else:
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    nd = sum("nextdoor.com" in c.get("domain", "") for c in state["cookies"])
    fb = sum("facebook.com" in c.get("domain", "") for c in state["cookies"])
    print(f"Done. {OUT} now has {nd} Nextdoor and {fb} Facebook cookies.")
