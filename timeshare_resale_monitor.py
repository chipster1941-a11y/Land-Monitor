import json
import os
import re
import asyncio
from playwright.async_api import async_playwright

SEEN_FILE = "seen_timeshares.json"

# Max price limit ($1,000)
MAX_PRICE = 1000.00

# High-value Interval International (II) brand keywords
II_BRANDS = [
    "marriott", "westin", "sheraton", "vistana", "hyatt", 
    "disney", "dvc", "welk", "diamond", "interval", "tahiti", "worldmark"
]

# Features your parents want
FEATURES = ["lockout", "lock-out", "lockoff", "lock-off", "2br", "2 bed", "2-bedroom", "2 bedroom"]

def load_seen_ids():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_ids(seen_ids):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen_ids), f, indent=2)

def extract_price(text: str) -> float:
    """Detects price or treats 'FREE' / 'BARGAIN' as $0."""
    text_lower = text.lower()
    if "free" in text_lower or "$0" in text_lower:
        return 0.0
    
    match = re.search(r"\$([\d,]+)", text)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return 0.0
    return 0.0

async def scrape_tug_bargains(page, seen_ids):
    listings = []
    # TUG Free & Bargain Timeshares forum board
    url = "https://tugbbs.com/forums/forums/free-timeshares.55/"
    print(f"Scraping TUG Free/Bargain Forum: {url}")
    
    await page.goto(url, wait_until="networkidle")
    
    # Select forum thread rows
    threads = await page.query_selector_all(".structItem--thread")
    
    for thread in threads:
        title_elem = await thread.query_selector(".structItem-title a[data-tp-primary]")
        if not title_elem:
            continue
            
        title = await title_elem.inner_text()
        href = await title_elem.get_attribute("href")
        listing_id = f"tug_{href}"

        if listing_id in seen_ids:
            continue

        title_lower = title.lower()

        # Check for price
        price = extract_price(title)
        if price > MAX_PRICE:
            continue

        # Check for II brand or 2BR lockout features
        has_ii_brand = any(brand in title_lower for brand in II_BRANDS)
        has_feature = any(feat in title_lower for feat in FEATURES)

        # Catch threads that match either brand OR desired unit features
        if has_ii_brand or has_feature:
            seen_ids.add(listing_id)
            full_url = f"https://tugbbs.com{href}" if href.startswith("/") else href
            
            listings.append({
                "source": "TUG Bargain Forum",
                "title": title.strip(),
                "price": f"${price:.0f}" if price > 0 else "FREE / Bargain",
                "url": full_url
            })

    return listings

async def main():
    seen_ids = load_seen_ids()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Emulate standard browser context to avoid bot detection
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            results = await scrape_tug_bargains(page, seen_ids)
        except Exception as e:
            print(f"Error executing scraper: {e}")
            results = []

        await browser.close()

    save_seen_ids(seen_ids)

    print("\n================ MATCHING RESULTS ================")
    if results:
        for item in results:
            print(f"[{item['source']}] {item['title']}")
            print(f" Price: {item['price']}")
            print(f" URL:   {item['url']}\n")
    else:
        print("No new matching bargains found on this run.")

if __name__ == "__main__":
    asyncio.run(main())