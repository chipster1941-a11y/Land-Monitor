#!/usr/bin/env python3
"""
gdv_inventory_monitor.py
Automates login to Global Discovery Vacations (GDV), selects a travel month,
extracts nationwide condo inventory, checks against a persistent seen file,
and sends an HTML email alert when new listings appear.
"""

import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Any
from playwright.sync_api import sync_playwright

# Logging Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Persistent State Tracking File
SEEN_FILE = "seen_gdv_weeks.json"

# Credentials & Email Settings (Set via Environment Variables or GitHub Secrets)
GDV_MEMBER_ID = os.environ.get("GDV_MEMBER_ID", "YOUR_MEMBER_ID")
GDV_PASSWORD = os.environ.get("GDV_PASSWORD", "YOUR_PASSWORD")

EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))

# Target Month Label (e.g., "September, 2027")
TARGET_MONTH_LABEL = "September, 2027"


def load_seen_weeks() -> set:
    """Loads previously seen week IDs from JSON storage."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("seen_ids", []))
        except Exception as e:
            logging.error(f"Error loading {SEEN_FILE}: {e}")
            return set()
    return set()


def save_seen_weeks(seen_ids: set):
    """Saves updated seen week IDs to storage."""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"seen_ids": list(seen_ids)}, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving {SEEN_FILE}: {e}")


def send_email_alert(new_listings: List[Dict[str, Any]]):
    """Sends a formatted HTML email notification with new inventory."""
    if not EMAIL_SENDER or not EMAIL_RECEIVER or not EMAIL_PASSWORD:
        logging.warning("Email credentials not fully set. Printing findings to console instead.")
        for item in new_listings:
            print(f"NEW LISTING: {item}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"GDV Alert: {len(new_listings)} New Resort Week(s) Found for {TARGET_MONTH_LABEL}!"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    # Build HTML Table Body
    rows = ""
    for item in new_listings:
        rows += f"""
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;"><b>{item.get('resort_name')}</b></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{item.get('location')}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{item.get('dates')}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{item.get('credits_cost')}</td>
        </tr>
        """

    html_content = f"""
    <html>
      <body>
        <h2>New Global Discovery Vacations Inventory Available</h2>
        <p>The automated GDV monitor found new available resort weeks for <b>{TARGET_MONTH_LABEL}</b>:</p>
        <table style="border-collapse: collapse; width: 100%;">
          <thead>
            <tr style="background-color: #f2f2f2;">
              <th style="padding: 8px; border: 1px solid #ddd;">Resort</th>
              <th style="padding: 8px; border: 1px solid #ddd;">Location</th>
              <th style="padding: 8px; border: 1px solid #ddd;">Dates</th>
              <th style="padding: 8px; border: 1px solid #ddd;">Credits / Fee</th>
            </tr>
          </thead>
          <tbody>
            {rows}
          </tbody>
        </table>
        <p><a href="https://globaldiscoveryvacations.com/">Log into GDV to book.</a></p>
      </body>
    </html>
    """
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, [EMAIL_RECEIVER], msg.as_string())
        logging.info("Email alert successfully sent!")
    except Exception as e:
        logging.error(f"Failed to send email alert: {e}")


def run_gdv_scrape():
    """Main execution block using Playwright for browser automation."""
    seen_ids = load_seen_weeks()
    new_findings = []

    with sync_playwright() as p:
        # Set headless=False during testing to watch the execution visually
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        logging.info("Navigating to GDV Login Portal...")
        page.goto("https://globaldiscoveryvacations.com/agent/login.aspx", timeout=60000)

       # 1. Authenticate using exact GDV login selectors
        username_selector = "#ctl00_body_tbLoginAgentID"
        password_selector = "input[type='password']"
        submit_selector = "input[type='submit'], button[type='submit']"

        logging.info("Waiting for login inputs...")
        page.wait_for_selector(username_selector, timeout=15000)
        page.fill(username_selector, GDV_MEMBER_ID)
        page.fill(password_selector, GDV_PASSWORD)
        
        # Click login and explicitly wait for navigation off the login page
        with page.expect_navigation(timeout=30000):
            page.click(submit_selector)
            
        logging.info("Logged in successfully. Redirecting to Search grid...")

        # 2. After login, wait for the authenticated landing page to load
        logging.info("Waiting for member dashboard...")
        page.wait_for_load_state("networkidle")

        # Navigate to search via UI link if needed
        if "login" not in page.url.lower():
            logging.info(f"Successfully logged in! Current page: {page.url}")
            # Click Destinations or Search link on the dashboard
            dest_link = page.query_selector("a:has-text('Destinations'), a[href*='Search'], a[href*='destinations']")
            if dest_link:
                dest_link.click()
                page.wait_for_load_state("networkidle")

        # 3. Target the Month Filter Button on the active search page
        month_selector = "a[id*='lbFilter']"
        
        try:
            logging.info("Waiting for target month button...")
            page.wait_for_selector(month_selector, timeout=20000)
            page.click(month_selector, force=True)
            page.wait_for_load_state("networkidle")
            logging.info("Successfully selected month filter!")
        except Exception as err:
            logging.error(f"Failed to find month filter. Current URL: {page.url}")
            page.screenshot(path="debug_search_page.png")
            raise err

        # 4. Extract Nationwide Inventory (Destination left unselected)
        resort_cards = page.query_selector_all(".resort-card, .search-result-item, div#filter div.col-sm-12")

        for card in resort_cards:
            card_text = card.inner_text().strip()
            if not card_text:
                continue

            # Generate a unique hash ID based on listing details
            listing_id = str(hash(card_text[:120]))

            if listing_id not in seen_ids:
                seen_ids.add(listing_id)
                
                # Parse text lines into structured fields
                lines = [line.strip() for line in card_text.split("\n") if line.strip()]
                resort_name = lines[0] if len(lines) > 0 else "Unknown Resort"
                location = lines[1] if len(lines) > 1 else "Nationwide / See Map"
                dates = lines[2] if len(lines) > 2 else TARGET_MONTH_LABEL
                credits_cost = lines[3] if len(lines) > 3 else "Standard Credits"

                new_findings.append({
                    "resort_name": resort_name,
                    "location": location,
                    "dates": dates,
                    "credits_cost": credits_cost
                })

        browser.close()

    # 5. Alert and Persist State
    if new_findings:
        logging.info(f"Found {len(new_findings)} new matching listing(s)!")
        send_email_alert(new_findings)
        save_seen_weeks(seen_ids)
    else:
        logging.info("No new inventory detected for this run.")


if __name__ == "__main__":
    run_gdv_scrape()