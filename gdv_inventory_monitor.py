import os
import json
import time
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Credentials & Environment Settings
GDV_MEMBER_ID = os.getenv("GDV_MEMBER_ID", "")
GDV_PASSWORD = os.getenv("GDV_PASSWORD", "")
NEXTDOOR_SESSION_ID = os.getenv("NEXTDOOR_SESSION_ID", "")

SENDER_EMAIL = os.getenv("SENDER_EMAIL", os.getenv("EMAIL_SENDER", ""))
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", os.getenv("EMAIL_PASSWORD", ""))
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL", os.getenv("EMAIL_RECEIVER", ""))

SEEN_FILE = "seen_gdv_weeks.json"

# Regional search targets: (Month Label, Region Filter)
TARGET_MONTHS = [
    ("November, 2026", "Midwest / North Central"),
    ("December, 2026", "Florida / Southeast"),
    ("January, 2027", "Florida / Southeast"),
    ("February, 2027", "Florida / Southeast"),
    ("September, 2027", "Midwest / North Central")
]

def load_seen_items():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"Error loading {SEEN_FILE}: {e}")
    return set()

def save_seen_items(seen_items):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen_items), f, indent=2)
        logging.info(f"Saved {len(seen_items)} seen items to {SEEN_FILE}")
    except Exception as e:
        logging.error(f"Error saving {SEEN_FILE}: {e}")

def send_email_notification(new_matches):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECEIVER_EMAIL:
        logging.warning("Email credentials incomplete. Skipping email dispatch.")
        return

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = f"GDV & Inventory Monitor Alert: {len(new_matches)} New Listing Matches!"

    body = "New Inventory Matches Discovered:\n\n"
    for item in new_matches:
        body += f"Source: {item.get('source', 'GDV')}\n"
        body += f"Title: {item.get('title', 'Unknown')}\n"
        body += f"Month / Date: {item.get('month', 'N/A')}\n"
        body += f"Details: {item.get('details', 'N/A')}\n"
        body += f"Link: {item.get('link', 'N/A')}\n"
        body += "-" * 40 + "\n"

    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        logging.info("Successfully sent email notification.")
    except Exception as e:
        logging.error(f"Failed to send email notification: {e}")

def login_gdv(page):
    logging.info("Navigating to GDV Member Portal...")
    page.goto("https://globaldiscoveryvacations.com/members.aspx")
    page.wait_for_load_state("networkidle")

    # If redirected or presented with login fields
    if page.locator("input[name*='txtMemberNum']").count() > 0 or "login" in page.url.lower():
        logging.info("Submitting member login credentials...")
        page.fill("input[name*='txtMemberNum']", GDV_MEMBER_ID)
        page.fill("input[name*='txtPassword']", GDV_PASSWORD)
        page.click("input[type='submit'], input[name*='btnLogin'], button[id*='Login']")
        page.wait_for_load_state("networkidle")

    logging.info(f"Member login successful! Current URL: {page.url}")

def select_month_and_search(page, target_month_str):
    # Only perform initial navigation if not already on the portal page
    if "members.aspx" not in page.url.lower() and "condo" not in page.url.lower():
        logging.info("Navigating to condo portal...")
        page.goto("https://globaldiscoveryvacations.com/members.aspx")
        page.wait_for_load_state("networkidle")

    logging.info(f"Opening month dropdown for {target_month_str}...")
    
    # Open rpMonth / ddlMonth dropdown toggle
    dropdown_toggle = page.locator(".dropdown-toggle, a:has-text('Select Month'), #ddlMonth").first
    if dropdown_toggle.count() > 0 and dropdown_toggle.is_visible():
        dropdown_toggle.click()
        page.wait_for_timeout(1000)

    # Target the lbMonth LinkButton inside rpMonth dropdown items
    month_option = page.locator(f"a[id*='lbMonth']:has-text('{target_month_str}')").first

    if month_option.count() > 0:
        logging.info(f"Clicking lbMonth LinkButton for {target_month_str}...")
        href = month_option.get_attribute("href")
        
        if href and href.startswith("javascript:"):
            # Execute ASP.NET __doPostBack directly in browser context
            page.evaluate(href.replace("javascript:", ""))
        else:
            month_option.click(force=True)

        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
    else:
        logging.warning(f"Could not locate month option for '{target_month_str}'")

def extract_all_resort_cards(page, month_label):
    logging.info(f"Extracting resort cards from Page 1 for {month_label}...")
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    items = []
    # Query resort card containers
    cards = soup.select(".resort-card, .resortItem, div[id*='pnlResort']")
    if not cards:
        cards = soup.select("div[class*='resort']")

    for idx, card in enumerate(cards):
        title_el = card.select_one(".resort-name, .title, h3, h4, a[id*='lbResort']")
        title = title_el.get_text(strip=True) if title_el else f"Resort Listing #{idx+1}"
        
        link_el = card.select_one("a[href]")
        link = link_el["href"] if link_el else page.url

        details_el = card.select_one(".details, .location, .description")
        details = details_el.get_text(strip=True) if details_el else "N/A"

        item_id = f"gdv_{month_label}_{title}"

        items.append({
            "id": item_id,
            "source": "GDV Condo Portal",
            "title": title,
            "month": month_label,
            "details": details,
            "link": link
        })

    logging.info(f"Extracted {len(items)} items for {month_label}.")
    return items

def scrape_nextdoor(browser, seen_items):
    new_nd_items = []
    if not NEXTDOOR_SESSION_ID:
        print("NEXTDOOR_SESSION_ID missing. Skipping Nextdoor.")
        return new_nd_items

    try:
        logging.info("Scraping Nextdoor listings...")
        nd_context = browser.new_context()
        nd_context.add_cookies([{
            "name": "id",
            "value": NEXTDOOR_SESSION_ID,
            "domain": ".nextdoor.com",
            "path": "/"
        }])
        nd_page = nd_context.new_page()
        nd_page.goto("https://nextdoor.com/for_sale_and_free/")
        nd_page.wait_for_load_state("networkidle")

        # Extraction logic for Nextdoor feed
        soup = BeautifulSoup(nd_page.content(), "html.parser")
        nd_cards = soup.select("div[data-testid='feed-item']")

        for card in nd_cards:
            title_el = card.select_one("span, a")
            title = title_el.get_text(strip=True) if title_el else "Nextdoor Item"
            item_id = f"nd_{title}"

            if item_id not in seen_items:
                new_nd_items.append({
                    "id": item_id,
                    "source": "Nextdoor",
                    "title": title,
                    "month": "Current",
                    "details": "Nextdoor local feed item",
                    "link": "https://nextdoor.com/for_sale_and_free/"
                })

        print(f"Nextdoor section added {len(new_nd_items)} items to notification queue.")
        nd_page.close()
    except Exception as e:
        print(f"Error scraping Nextdoor: {e}")

    return new_nd_items

def run_scraper():
    seen_items = load_seen_items()
    new_matches = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            # 1. GDV Scraper Section
            login_gdv(page)

            for month_label, region_rule in TARGET_MONTHS:
                try:
                    select_month_and_search(page, month_label)
                    month_items = extract_all_resort_cards(page, month_label)

                    for item in month_items:
                        if item["id"] not in seen_items:
                            seen_items.add(item["id"])
                            new_matches.append(item)

                except Exception as e:
                    logging.error(f"Error processing month {month_label}: {e}")

            # 2. Nextdoor Scraper Section
            nd_matches = scrape_nextdoor(browser, seen_items)
            for nd_item in nd_matches:
                seen_items.add(nd_item["id"])
                new_matches.append(nd_item)

        except Exception as e:
            logging.error(f"Global execution error: {e}")
        finally:
            browser.close()

    logging.info(f"Scan complete. Total queue length for email dispatch: {len(new_matches)}")

    if new_matches:
        save_seen_items(seen_items)
        send_email_notification(new_matches)
    else:
        print("No new listings or price drops on this run.")

if __name__ == "__main__":
    run_scraper()