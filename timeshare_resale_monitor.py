import json
import os
import re
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.async_api import async_playwright

SEEN_FILE = "seen_timeshares.json"
MAX_PRICE = 1000.00
MAX_MAINTENANCE_FEE = 1400.00

II_BRANDS = [
    "marriott", "westin", "sheraton", "vistana", "hyatt", 
    "disney", "dvc", "welk", "diamond", "interval", "tahiti", "worldmark"
]

# Expanded to include 1-bedroom unit variations alongside 2BR/lockouts
FEATURES = [
    "lockout", "lock-out", "lockoff", "lock-off", 
    "2br", "2 bed", "2-bedroom", "2 bedroom",
    "1br", "1 bed", "1-bedroom", "1 bedroom"
]

def load_seen_ids():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_ids(seen_ids):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen_ids), f, indent=2)

def send_email_alert(new_listings):
    """Send HTML email via Gmail SMTP using repository secrets."""
    sender_email = os.environ.get("EMAIL_SENDER")
    sender_password = os.environ.get("EMAIL_PASSWORD")
    recipient_email = os.environ.get("EMAIL_RECEIVER", sender_email)

    if not sender_email or not sender_password:
        print("Warning: EMAIL_SENDER or EMAIL_PASSWORD environment variables not set. Skipping email alert.")
        return

    subject = f"Timeshare Alert: {len(new_listings)} New Listing(s) Found!"
    
    html_items = ""
    for item in new_listings:
        html_items += f"""
        <div style="border:1px solid #ccc; padding:12px; margin-bottom:12px; border-radius:6px;">
            <h3 style="margin-top:0;">{item['title']}</h3>
            <p><strong>Source:</strong> {item['source']}</p>
            <p><strong>Asking Price:</strong> {item['price']}</p>
            <p><strong>Maint Fee:</strong> {item['maint_fee']}</p>
            <p><a href="{item['url']}" target="_blank">View Listing</a></p>
        </div>
        """

    html_content = f"""
    <html>
      <body>
        <h2>New Timeshare Resales Matching Your Criteria</h2>
        {html_items}
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
        print(f"Successfully sent email notification to {recipient_email}")
    except Exception as e:
        print(f"Failed to send email alert: {e}")

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
    body_lower = body_text.lower()
    patterns = [
        r"(?:maint(?:enance)?|mf|dues|hoa|fee|fees|operating)\w*\s*(?:fee|dues)?\w*\s*:?\s*\$?([\d,]+(?:\.\d{2})?)",
        r"annual(?:ly)?\s*(?:dues|fee|cost|maint)\w*\s*:?\s*\$?([\d,]+(?:\.\d{2})?)",
        r"\$?([\d,]+(?:\.\d{2})?)\s*(?:per year|/yr|/year|annually|annual|/maint|maint)"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, body_lower)
        if match:
            try:
                val = float(match.group(1).replace(",", ""))
                if 100 <= val <= 5000:
                    return val
            except ValueError:
                continue
    return 0.0

async def scrape_tug_bargains(page, seen_ids):
    listings = []
    url = "https://tugbbs.com/forums/forums/free-timeshares.55/"
    print(f"Scraping TUG Free/Bargain Forum: {url}")
    
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_selector(".structItem--thread", timeout=15000)
    
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

    for cand in candidates:
        try:
            await page.goto(cand["url"], wait_until="domcontentloaded", timeout=30000)
            first_post = await page.query_selector(".message-body")
            
            fee_val = 0.0
            if first_post:
                post_text = await first_post.inner_text()
                fee_val = extract_maintenance_fee(post_text)

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
            print(f"Error checking TUG thread details for {cand['url']}: {e}")

    return listings

async def scrape_redweek_bargains(page, seen_ids):
    listings = []
    url = "https://www.redweek.com/featured/bargain-timeshares-for-sale"
    print(f"Scraping RedWeek Bargain Resales: {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        
        cards = await page.query_selector_all(".posting-item, .resort-item, article")
        if not cards:
            cards = await page.query_selector_all("a[href*='/timeshare-item/'], a[href*='/posting/']")

        for card in cards:
            title_elem = await card.query_selector("h3, h4, .title, .resort-name")
            link_elem = await card.query_selector("a") if not title_elem else title_elem
            
            if not link_elem:
                continue

            title = await link_elem.inner_text() if title_elem else await card.inner_text()
            title = title.strip().split("\n")[0]
            href = await link_elem.get_attribute("href")
            
            if not href:
                continue

            listing_id = f"redweek_{href}"
            if listing_id in seen_ids:
                continue

            card_text = await card.inner_text()
            card_lower = card_text.lower()

            price = extract_price(card_text)
            if price > MAX_PRICE and price != 0.0:
                continue

            has_ii_brand = any(brand in card_lower for brand in II_BRANDS)
            has_feature = any(feat in card_lower for feat in FEATURES)

            if has_ii_brand or has_feature:
                fee_val = extract_maintenance_fee(card_text)
                if fee_val > MAX_MAINTENANCE_FEE and fee_val > 0:
                    print(f"Skipping RedWeek '{title}' - Fee (${fee_val:.0f}) exceeds ${MAX_MAINTENANCE_FEE:.0f}")
                    continue

                full_url = f"https://www.redweek.com{href}" if href.startswith("/") else href
                seen_ids.add(listing_id)
                listings.append({
                    "source": "RedWeek Bargains",
                    "title": title,
                    "price": f"${price:.0f}" if price > 0 else "Bargain / See Listing",
                    "maint_fee": f"${fee_val:.0f}/year" if fee_val > 0 else "See listing details",
                    "url": full_url
                })
    except Exception as e:
        print(f"Error executing RedWeek scraper: {e}")

    return listings

async def main():
    seen_ids = load_seen_ids()
    results = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # Run TUG Scraper
        try:
            tug_results = await scrape_tug_bargains(page, seen_ids)
            results.extend(tug_results)
        except Exception as e:
            print(f"Error executing TUG scraper: {e}")

        # Run RedWeek Scraper
        try:
            redweek_results = await scrape_redweek_bargains(page, seen_ids)
            results.extend(redweek_results)
        except Exception as e:
            print(f"Error executing RedWeek scraper: {e}")

        await browser.close()

    save_seen_ids(seen_ids)

    print("\n================ MATCHING RESULTS ================")
    if results:
        for item in results:
            print(f"[{item['source']}] {item['title']}")
            print(f" Price:      {item['price']}")
            print(f" Maint Fee:  {item['maint_fee']}")
            print(f" URL:        {item['url']}\n")
        
        send_email_alert(results)
    else:
        print("No new matching bargains found under the fee and price thresholds.")

if __name__ == "__main__":
    asyncio.run(main())