import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ==========================================
# CONFIGURATION
# ==========================================
SEARCH_QUERY = "golf cart"
NEXTDOOR_SEARCH_URL = f"https://nextdoor.com/for_sale_and_free/?query={SEARCH_QUERY.replace(' ', '%20')}"

SEEN_FILE = "seen_carts.txt"

# Email environment variables (matching existing secret names)
EMAIL_SENDER = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")

# Nextdoor Session Cookie (from environment variable / secret)
NEXTDOOR_SESSION_ID = os.environ.get("NEXTDOOR_SESSION_ID", "")


def load_seen_ids():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen_id(item_id):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{item_id}\n")


# ==========================================
# SCRAPER: NEXTDOOR VIA PLAYWRIGHT
# ==========================================
def scrape_nextdoor(seen_ids):
    print(f"🛒 Checking Nextdoor for '{SEARCH_QUERY}' listings...")
    matches = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )

            # Robust Cookie Parser for Playwright
            if NEXTDOOR_SESSION_ID:
                cookies = []
                raw_cookie_str = NEXTDOOR_SESSION_ID.strip()

                if "=" in raw_cookie_str:
                    for item in raw_cookie_str.split(";"):
                        item = item.strip()
                        if not item or "=" not in item:
                            continue
                        
                        name, val = item.split("=", 1)
                        name = name.strip()
                        val = val.strip()

                        # Skip invalid/empty cookie names or standard HTTP attributes
                        if not name or name.lower() in ["path", "domain", "expires", "secure", "httponly", "samesite"]:
                            continue

                        cookies.append({
                            "name": name,
                            "value": val,
                            "domain": ".nextdoor.com",
                            "path": "/"
                        })
                else:
                    # Single token fallback
                    cookies.append({
                        "name": "ndp_session",
                        "value": raw_cookie_str,
                        "domain": ".nextdoor.com",
                        "path": "/"
                    })

                if cookies:
                    context.add_cookies(cookies)

            page = context.new_page()

            print(f" -> Navigating to Nextdoor search: {NEXTDOOR_SEARCH_URL}")
            page.goto(NEXTDOOR_SEARCH_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(4000)

            # DIAGNOSTICS
            print(f" -> Landed URL: {page.url}")
            print(f" -> Page Title: {page.title()}")

            soup = BeautifulSoup(page.content(), "html.parser")

            cards = (
                soup.select('a[href*="/for_sale_and_free/"]')
                or soup.select('a[href*="/p/"]')
                or soup.select('a[href*="/finds/"]')
                or soup.select('div[data-testid]')
            )

            print(f" -> Found {len(cards)} raw candidate cards on page.")

            for card in cards:
                href = card.get("href", "")
                if not href:
                    continue

                item_id = href.strip("/").split("/")[-1]
                full_id = f"nd_cart_{item_id}"

                if full_id in seen_ids:
                    continue

                full_url = f"https://nextdoor.com{href}" if href.startswith("/") else href

                text_content = card.get_text(separator=" ").strip()
                if not text_content or len(text_content) < 5:
                    continue

                lines = [line.strip() for line in text_content.split("\n") if line.strip()]
                title = lines[0] if lines else f"Golf Cart Listing ({item_id})"

                price = "Check Listing"
                for line in lines:
                    if "$" in line:
                        price = line
                        break

                item_data = {
                    "id": full_id,
                    "title": title[:100],
                    "price": price,
                    "location": "Nextdoor Local Area",
                    "link": full_url,
                    "source": "Nextdoor"
                }

                matches.append(item_data)
                seen_ids.add(full_id)
                save_seen_id(full_id)

            browser.close()

    except Exception as e:
        print(f"Error executing Playwright for Nextdoor: {e}")

    return matches


# ==========================================
# EMAIL NOTIFICATIONS
# ==========================================
def send_email_alert(matches):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email credentials missing. Skipping email send.")
        return

    count = len(matches)
    first_match = matches[0]

    subject = f"🛺 Golf Cart Alert: {first_match['price']} | {first_match['title']}"
    if count > 1:
        subject = f"🛺 {count} New Golf Cart Listings Found on Nextdoor!"

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
        <h2 style="background-color: #15803d; color: white; padding: 12px; border-radius: 6px; text-align: center;">
            🛺 New Golf Cart Alert ({count})
        </h2>
        <p>The following new golf cart listing(s) were found on Nextdoor:</p>
        <hr style="border: 0; border-top: 1px solid #ccc;">
    """

    for item in matches:
        html_content += f"""
        <div style="border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background-color: #f8fafc;">
            <span style="background-color: #16a34a; color: white; padding: 3px 8px; font-size: 12px; border-radius: 4px; font-weight: bold;">
                {item['source']}
            </span>
            <h3 style="margin: 10px 0 5px 0; color: #0f172a;">{item['title']}</h3>
            <p style="margin: 4px 0;"><strong>Price:</strong> <span style="color: #16a34a; font-weight: bold;">{item['price']}</span></p>
            <p style="margin: 4px 0;"><strong>Area:</strong> {item['location']}</p>
            <p style="margin: 12px 0 0 0;">
                <a href="{item['link']}" target="_blank" style="background-color: #15803d; color: white; text-decoration: none; padding: 8px 14px; border-radius: 5px; font-size: 14px; display: inline-block;">
                    View on Nextdoor &rarr;
                </a>
            </p>
        </div>
        """

    html_content += """
        <p style="font-size: 12px; color: #64748b; text-align: center; margin-top: 24px;">
            Nextdoor Golf Cart Monitor • Automated GitHub Action
        </p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"Email alert sent successfully for {count} golf cart match(es)!")
    except Exception as e:
        print(f"Failed to send email alert: {e}")


# ==========================================
# MAIN RUNNER
# ==========================================
def main():
    seen_ids = load_seen_ids()
    all_matches = scrape_nextdoor(seen_ids)

    print(f"\nScan complete. Total new golf cart matches found: {len(all_matches)}")

    if all_matches:
        send_email_alert(all_matches)
    else:
        print("No new golf cart listings found on this run.")


if __name__ == "__main__":
    main()