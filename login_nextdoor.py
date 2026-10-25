from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(user_agent=UA)
    page = context.new_page()
    page.goto("https://nextdoor.com/login/")
    input("Log in in the browser window, then press Enter here...")
    context.storage_state(path="nextdoor_state.json")
    browser.close()