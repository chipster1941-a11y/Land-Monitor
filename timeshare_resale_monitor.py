import json
import os
import re
import asyncio
from playwright.async_api import async_playwright

SEEN_FILE = "seen_timeshares.json"

# Constraints
MAX_PRICE = 1000.00
MAX_MAINTENANCE_FEE = 1400.00

# Target Keywords for Interval International & 2BR Lockouts
II_KEYWORDS = ["interval international", "marriott", "westin", "sheraton", "vistana", "hyatt", "disney", "dvc", "worldmark", "diamond"]
TARGET_FEATURES = ["lockout", "lock-out", "2 bedroom", "2bd", "2br", "lockoff", "lock-off"]

def load_seen_ids():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_ids(seen_ids):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen_ids), f, indent=2)

def parse_price(price_str: str) -> float:
    """Extract float price from string like '$800.00' or 'FREE'."""
    if not price_str or "free" in price_str.lower():
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", price_str)
    try:
        return float(cleaned)
    except ValueError:
        return 999999.0  # High default to skip invalid parses

async def scrape_redweek(page, seen_ids):
    listings = []
    # Search for lockout / 2bd resales on RedWeek
    url = "https://www.redweek.com/search?q=lockout+resale"
    print(f"Scraping RedWeek: {url}")
    await page.goto(url, wait_until="domcontentloaded")
    
    cards = await page.query_selector_all(".posting-card, .listing-item, .search-result")
    for card in cards:
        text = await card.inner_text()
        text_lower = text.lower()

        # Extract link & title
        link_elem = await card.query_selector("a")
        href = await link_elem.get_attribute("href") if link_elem else ""
        listing_id = f"redweek_{href}"

        if not href or listing_id in seen_ids:
            continue

        # Extract price
        price_elem = await card.query_selector(".price, .posting-price")
        price_text = await price_elem.inner_text() if price_elem else "$0"
        price = parse_price(price_text)

        # Apply Price Filter
        if price > MAX_PRICE:
            continue

        # Apply Maintenance Fee Check if fee listed in card text
        mf_match = re.search(r"maint(?:enance)?\s*fee:?\s*\$?([\d,]+)", text_lower)
        if mf_match:
            mf_val = float(mf_match.group(1).replace(",", ""))
            if mf_val > MAX_MAINTENANCE_FEE:
                continue

        # Feature Check (2BR / Lockout or Interval International)
        has_ii = any(k in text_lower for k in II_KEYWORDS)
        has_feat = any(f in text_lower for f in TARGET_FEATURES)

        if has_ii or has_feat:
            seen_ids.add(listing_id)
            full_url = f"https://www.redweek.com{href}" if href.startswith("/") else href
            listings.append({
                "source": "RedWeek",
                "title": text.split("\n")[0].strip(),
                "price": f"${price:.2f}",
                "url": full_url
            })
    return listings

async def scrape_tug(page, seen_ids):
    listings = []
    # TUG Marketplace Search for low cost / bargain resales
    url = "https://tug2.com/timesharemarketplace"
    print(f"Scraping TUG Marketplace: {url}")
    await page.goto(url, wait_until="domcontentloaded")

    rows = await page.query_selector_all("tr, .marketplace-listing, .ad-item")
    for row in rows:
        text = await row.inner_text()
        text_lower = text.lower()

        link_elem = await row.query_selector("a")
        href = await link_elem.get_attribute("href") if link_elem else ""
        listing_id = f"tug_{href}"

        if not href or listing_id in seen_ids:
            continue

        price_match = re.search(r"\$([\d,]+(?:\.\d{2})?)", text)
        price = float(price_match.group(1).replace(",", "")) if price_match else 0.0

        if price > MAX_PRICE:
            continue

        # Check for Interval International or 2BR/Lockout keywords
        has_ii = any(k in text_lower for k in II_KEYWORDS)
        has_feat = any(f in text_lower for f in TARGET_FEATURES)

        if has_ii or has_feat:
            seen_ids.add(listing_id)
            full_url = f"https://tug2.com{href}" if href.startswith("/") else href
            listings.append({
                "source": "TUG Marketplace",
                "title": text.split("\n")[0].strip(),
                "price": f"${price:.2f}",
                "url": full_url
            })
    return listings

async def main():
    seen_ids = load_seen_ids()
    all_new_listings = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            rw_results = await scrape_redweek(page, seen_ids)
            all_new_listings.extend(rw_results)
        except Exception as e:
            print(f"Error scraping RedWeek: {e}")

        try:
            tug_results = await scrape_tug(page, seen_ids)
            all_new_listings.extend(tug_results)
        except Exception as e:
            print(f"Error scraping TUG: {e}")

        await browser.close()

    save_seen_ids(seen_ids)

    print("\n--- MATCHING RESULTS ---")
    if all_new_listings:
        for item in all_new_listings:
            print(f"[{item['source']}] {item['title']} - {item['price']}")
            print(f"   URL: {item['url']}\n")
    else:
        print("No new matching timeshare resales found under $1,000.")

if __name__ == "__main__":
    asyncio.run(main())