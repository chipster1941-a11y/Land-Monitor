import json
import browser_cookie3

def extract_facebook_session():
    print("Extracting live Facebook cookies directly from Firefox...")
    try:
        cj = browser_cookie3.firefox(domain_name=".facebook.com")
        
        cookies = []
        for c in cj:
            # Clean up expiration: must be -1 or a positive integer timestamp
            expires_val = -1
            if c.expires and float(c.expires) > 0:
                expires_val = int(c.expires)

            cookies.append({
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "expires": expires_val,
                "httpOnly": bool(c.has_nonstandard_attr("HttpOnly")),
                "secure": bool(c.secure),
                "sameSite": "Lax"
            })

        storage_state = {
            "cookies": cookies,
            "origins": []
        }

        with open("storage_state.json", "w", encoding="utf-8") as f:
            json.dump(storage_state, f, indent=2)

        print(f"Success! Saved {len(cookies)} valid Facebook cookies to storage_state.json")

    except Exception as e:
        print(f"Error extracting cookies: {e}")

if __name__ == "__main__":
    extract_facebook_session()