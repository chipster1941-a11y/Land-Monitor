import json
import os
import re
import asyncio
from playwright.async_api import async_playwright

SEEN_FILE = "seen_timeshares.json"
MAX_PRICE = 1000.00
MAX_MAINTENANCE_FEE = 1400.00

II_BRANDS = [
    "marriott", "westin", "sheraton", "vistana", "hyatt", 
    "disney", "dvc", "welk", "diamond", "interval", "tahiti", "worldmark"
]

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

def extract_maintenance_fee(body_text: str) -> float:
    """Extract annual maintenance fee from the thread post text."""
    body_lower = body_text.lower()
    patterns = [
        r"maint(?:enance)?\s*(?:fee|dues)?\w*\s*:?\s*\$?([\d,]+(?:\.\d{2})?)",
        r"annual\s*dues\w*\s*:?\s*\$?([\d,]+(?:\.\d{2})?)",
        r"mf\w*\s*:?\s*\$?([\d,]+(?:\.\d{2})?)"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, body_lower)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                continue
    return 0.0

async def scrape_tug_bargains(page, seen_ids):
    listings = []
    url = "https://tugbbs.com/forums/forums/free-timeshares.55/"
    print(f"Scraping TUG Free/Bargain Forum: {url}")
    
    await page.goto(url, wait_until="networkidle")
    threads = await page.query_selector_all(".structItem--thread")
    
    candidates = []
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
        price = extract_price(title)
        if price > MAX_PRICE:
            continue

        has_ii_brand = any(brand in title_lower for brand in II_BRANDS)
        has_feature = any(feat in title_lower for feat in FEATURES)

        if has_ii_brand or has_feature:
            candidates.append({
                "listing_id": listing_id,
                "title": title.strip(),
                "price": price,
                "url": f"https://tugbbs.com{href}" if href.startswith("/") else href
            })

    # Detailed extraction step: Click into candidate posts to extract maintenance fees
    for cand in candidates:
        try:
            await page.goto(cand["url"], wait_until="domcontentloaded")
            first_post = await page.query_selector(".message-body")
            
            fee_val = 0.0
            if first_post:
                post_text = await first_post.inner_text()
                fee_val = extract_maintenance_fee(post_text)

            # Skip if maintenance fee exceeds threshold
            if fee_val > MAX_MAINTENANCE_FEE and fee_val > 0:
                print(f"Skipping '{cand['title']}' - Maintenance fee (${fee_val:.0f}) exceeds ${MAX_MAINTENANCE_FEE:.0f}")
                continue

            seen_ids.add(cand["listing_id"])
            listings.append({
                "source": "TUG Bargain Forum",
                "title": cand["title"],
                "price": f"${cand['price']:.0f}" if cand['price'] > 0 else "FREE / Bargain",
                "maint_fee": f"${fee_val:.0f}/year" if fee_val > 0 else "Not specified",
                "url": cand["url"]
            })
        except Exception as e:
            print(f"Error checking thread details for {cand['url']}: {e}")

    return listings

async def main():
    seen_ids = load_seen_ids()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
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
            print(f" Price:      {item['price']}")
            print(f" Maint Fee:  {item['maint_fee']}")
            print(f" URL:        {item['url']}\n")
    else:
        print("No new matching bargains found under the fee and price thresholds.")

if __name__ == "__main__":
    asyncio.run(main())