import os
from playwright.sync_api import sync_playwright

def generate_facebook_session():
    print("Launching visible browser for Facebook login...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print("Navigating to Facebook login...")
        try:
            # wait_until='commit' prevents Playwright from crashing if network is slow
            page.goto("https://www.facebook.com/login", wait_until="commit", timeout=60000)
        except Exception as e:
            print(f"Initial load warning (proceeding anyway): {e}")

        print("\n" + "="*60)
        print("ACTION REQUIRED:")
        print("1. A Chrome browser window is open.")
        print("2. Log into your Facebook account and complete 2FA.")
        print("3. Once logged in, navigate to Marketplace in that browser window.")
        print("4. Return to this terminal window and press ENTER.")
        print("="*60 + "\n")

        input("Press ENTER here after logging into Facebook and seeing Marketplace...")

        # Save session cookies and storage state
        context.storage_state(path="storage_state.json")
        print("Successfully saved fresh 'storage_state.json'!")
        browser.close()

if __name__ == "__main__":
    generate_facebook_session()