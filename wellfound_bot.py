#!/usr/bin/env python3
"""
wellfound_bot.py  v1.0  —  Wellfound.com Auto-Apply Bot
════════════════════════════════════════════════════════
Logs into your Wellfound account, finds matching dev jobs,
and submits applications automatically with smart form answering.

SETUP:
  1. pip install playwright requests
  2. python -m playwright install chromium
  3. Run:  python wellfound_bot.py --login
     (logs in once, saves session)
  4. Run:  python wellfound_bot.py
     (runs the bot automatically)
"""

from __future__ import annotations
import sys, json, time, random, logging, os, re
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout
except ImportError:
    print("Run: pip install playwright && python -m playwright install chromium")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Run: pip install requests")
    sys.exit(1)


# ════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ════════════════════════════════════════════════════════════════════════════

WELLFOUND_EMAIL    = "obodavekel466@gmail.com"
WELLFOUND_PASSWORD = "OBOdavekel@46"

TELEGRAM_TOKEN   = "8871384090:AAHJ92v3MeXgd2PKotnRjSL1q9NLGDZ3vvg"
TELEGRAM_CHAT_ID = "8350604369"

YOUR_NAME = "Favour"

PROFILE = {
    "phone"             : "+447502449946",
    "portfolio"         : "https://github.com/kene-favs",
    "english"           : "Yes",
    "authorized"        : "Yes",
    "sponsorship"       : "No",
    "location"          : "Remote — available globally",
    "availability"      : "Immediately",
    "experience_years"  : "3",
    "salary_expectation": "Negotiable based on project scope",
}

# Job roles to search for on Wellfound
SEARCH_ROLES = [
    "Python Developer",
    "Backend Developer",
    "Full Stack Developer",
    "Automation Engineer",
    "Web Developer",
    "Software Engineer",
    "Mobile Developer",
    "Data Engineer",
    "API Developer",
]

MAX_APPLIES_PER_SESSION = 20
MIN_GAP_MIN = 2
MAX_GAP_MIN = 6
READING_TIME_MIN = 8
READING_TIME_MAX = 15
SKIP_PROBABILITY = 0.08

# Wellfound only recognises a fixed set of role slugs — use the ones that actually work
JOB_SEARCH_URLS = [
    "https://wellfound.com/jobs?role=engineer&remote=true",
    "https://wellfound.com/jobs?role=software-engineer&remote=true",
    "https://wellfound.com/jobs?role=backend-engineer&remote=true",
    "https://wellfound.com/jobs?role=mobile-engineer&remote=true",
    "https://wellfound.com/jobs",                                   # all jobs, no filter
]

# ════════════════════════════════════════════════════════════════════════════
#  PATHS & LOGGING
# ════════════════════════════════════════════════════════════════════════════

BROWSER_PROFILE = Path("wellfound_profile")
SEEN_FILE       = Path("wellfound_seen.json")
BIDS_LOG_FILE   = Path("bids_log.json")       # shared with freelancer bot → same dashboard
DASHBOARD_FILE  = Path("dashboard.html")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-12s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("wellfound_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("WellfoundBot")


# ════════════════════════════════════════════════════════════════════════════
#  STATE
# ════════════════════════════════════════════════════════════════════════════

def load_seen() -> set:
    if SEEN_FILE.exists():
        try: return set(json.loads(SEEN_FILE.read_text()))
        except: pass
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def load_bids_log() -> list:
    if BIDS_LOG_FILE.exists():
        try: return json.loads(BIDS_LOG_FILE.read_text(encoding="utf-8"))
        except: pass
    return []

def save_bids_log(bids: list):
    BIDS_LOG_FILE.write_text(json.dumps(bids, indent=2, ensure_ascii=False), encoding="utf-8")

def record_application(title: str, company: str, url: str, platform: str = "Wellfound"):
    bids = load_bids_log()
    entry_id = f"wf_{int(time.time())}_{random.randint(100,999)}"
    bids.append({
        "id"        : entry_id,
        "platform"  : platform,
        "title"     : f"{title} @ {company}",
        "url"       : url,
        "bid_amount": 0,
        "budget_min": 0,
        "budget_max": 0,
        "posted"    : datetime.now().strftime("%H:%M %d/%m/%y"),
        "bid_date"  : datetime.now().strftime("%Y-%m-%d %H:%M"),
        "proposal"  : "Applied via Wellfound",
        "status"    : "pending",
    })
    save_bids_log(bids)
    generate_dashboard(bids)


# ════════════════════════════════════════════════════════════════════════════
#  DASHBOARD  (shared with freelancer bot)
# ════════════════════════════════════════════════════════════════════════════

def generate_dashboard(bids: list):
    total   = len(bids)
    won     = sum(1 for b in bids if b["status"] == "won")
    lost    = sum(1 for b in bids if b["status"] == "lost")
    pending = sum(1 for b in bids if b["status"] == "pending")
    earned  = sum(b.get("bid_amount", 0) for b in bids if b["status"] == "won")

    rows = ""
    for b in reversed(bids):
        sc = {"won": "#22c55e", "lost": "#ef4444", "pending": "#f59e0b"}.get(b["status"], "#888")
        badge = f'<span style="background:{sc};color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600">{b["status"].upper()}</span>'
        amt = f'${b["bid_amount"]}' if b.get("bid_amount") else "—"
        rows += f"""
        <tr>
          <td>{b["bid_date"]}</td>
          <td><b>{b["platform"]}</b></td>
          <td><a href="{b["url"]}" target="_blank" style="color:#6366f1;text-decoration:none">{b["title"][:60]}</a></td>
          <td style="text-align:center">{amt}</td>
          <td style="text-align:center">{badge}</td>
          <td style="text-align:center">
            <button onclick="setStatus('{b["id"]}','won')"  style="background:#22c55e;color:#fff;border:none;padding:3px 10px;border-radius:6px;cursor:pointer;margin:2px">✓ Won</button>
            <button onclick="setStatus('{b["id"]}','lost')" style="background:#ef4444;color:#fff;border:none;padding:3px 10px;border-radius:6px;cursor:pointer;margin:2px">✗ Lost</button>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoApply Dashboard — @thefavs0</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',sans-serif;background:#0f0f1a;color:#e2e8f0;min-height:100vh}}
  .header{{background:linear-gradient(135deg,#6366f1,#8b5cf6);padding:30px 40px}}
  .header h1{{font-size:26px;font-weight:700;letter-spacing:1px}}
  .header p{{opacity:.8;margin-top:4px;font-size:14px}}
  .stats{{display:flex;gap:20px;padding:30px 40px;flex-wrap:wrap}}
  .card{{background:#1e1e2e;border-radius:14px;padding:22px 28px;flex:1;min-width:160px;border:1px solid #2a2a3d}}
  .card .num{{font-size:36px;font-weight:800;margin-bottom:4px}}
  .card .lbl{{font-size:13px;opacity:.6;text-transform:uppercase;letter-spacing:.5px}}
  .card.green .num{{color:#22c55e}}.card.red .num{{color:#ef4444}}
  .card.amber .num{{color:#f59e0b}}.card.blue .num{{color:#6366f1}}.card.gold .num{{color:#fbbf24}}
  .section{{padding:0 40px 40px}}
  .section h2{{font-size:18px;margin-bottom:16px;color:#a5b4fc}}
  table{{width:100%;border-collapse:collapse;background:#1e1e2e;border-radius:14px;overflow:hidden;border:1px solid #2a2a3d}}
  th{{background:#2a2a3d;padding:12px 16px;text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:#a5b4fc}}
  td{{padding:12px 16px;border-bottom:1px solid #2a2a3d;font-size:14px;vertical-align:middle}}
  tr:last-child td{{border-bottom:none}}tr:hover td{{background:#252538}}
  .refresh{{display:inline-block;margin:0 40px 20px;padding:10px 22px;background:#6366f1;color:#fff;border-radius:8px;cursor:pointer;border:none;font-size:14px}}
  .ts{{padding:0 40px 10px;font-size:12px;opacity:.4}}
</style>
</head>
<body>
<div class="header">
  <h1>🚀 AutoApply Dashboard</h1>
  <p>Freelancer · Wellfound · LinkedIn — @thefavs0</p>
</div>
<div class="stats">
  <div class="card blue"> <div class="num">{total}</div>   <div class="lbl">Total Applied</div></div>
  <div class="card amber"><div class="num">{pending}</div> <div class="lbl">Pending</div></div>
  <div class="card green"><div class="num">{won}</div>     <div class="lbl">Won / Hired</div></div>
  <div class="card red">  <div class="num">{lost}</div>    <div class="lbl">Lost</div></div>
  <div class="card gold"> <div class="num">${earned}</div> <div class="lbl">Earned</div></div>
</div>
<div class="section">
  <h2>📋 All Applications</h2>
  <table>
    <thead><tr>
      <th>Date</th><th>Platform</th><th>Job / Company</th>
      <th style="text-align:center">Bid</th>
      <th style="text-align:center">Status</th>
      <th style="text-align:center">Update</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
<p class="ts">Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
<button class="refresh" onclick="location.reload()">↻ Refresh</button>
<script>
function setStatus(id, status) {{
  const data = JSON.parse(localStorage.getItem('statuses') || '{{}}');
  data[id] = status;
  localStorage.setItem('statuses', JSON.stringify(data));
  location.reload();
}}
window.addEventListener('load', () => {{
  const data = JSON.parse(localStorage.getItem('statuses') || '{{}}');
  document.querySelectorAll('td span').forEach(el => {{
    const row = el.closest('tr');
    if (!row) return;
    const btn = row.querySelector('button');
    if (!btn) return;
    const onclick = btn.getAttribute('onclick') || '';
    const m = onclick.match(/'([^']+)'/);
    if (m && data[m[1]]) {{
      el.textContent = data[m[1]].toUpperCase();
      el.style.background = data[m[1]] === 'won' ? '#22c55e' : '#ef4444';
    }}
  }});
}});
</script>
</body>
</html>"""
    DASHBOARD_FILE.write_text(html, encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ════════════════════════════════════════════════════════════════════════════

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram error: {e}")


# ════════════════════════════════════════════════════════════════════════════
#  HUMAN BEHAVIOUR
# ════════════════════════════════════════════════════════════════════════════

def human_delay(mn: float = 1.0, mx: float = 3.5):
    time.sleep(random.uniform(mn, mx))

def human_type(page: Page, locator, text: str):
    locator.click()
    human_delay(0.3, 0.7)
    for ch in text:
        locator.type(ch, delay=random.randint(35, 110))
        if random.random() < 0.04:
            time.sleep(random.uniform(0.2, 0.6))

def human_scroll(page: Page, times: int = 3):
    for _ in range(times):
        page.evaluate(f"window.scrollBy(0, {random.randint(200,500)})")
        time.sleep(random.uniform(1.2, 3.0))


# ════════════════════════════════════════════════════════════════════════════
#  SMART QUESTION ANSWERING
# ════════════════════════════════════════════════════════════════════════════

def answer_question(question: str) -> str:
    q = question.lower().strip()
    if any(x in q for x in ["english", "proficient in english"]):
        return PROFILE["english"]
    if any(x in q for x in ["authorized", "legally authorized", "right to work", "eligible"]):
        return PROFILE["authorized"]
    if any(x in q for x in ["sponsor", "sponsorship", "visa", "immigration"]):
        return PROFILE["sponsorship"]
    if any(x in q for x in ["years of experience", "how many years", "experience level"]):
        return PROFILE["experience_years"]
    if any(x in q for x in ["available", "start date", "when can you start", "notice"]):
        return PROFILE["availability"]
    if any(x in q for x in ["salary", "rate", "compensation", "pay", "hourly"]):
        return PROFILE["salary_expectation"]
    if any(x in q for x in ["location", "where are you", "timezone", "based"]):
        return PROFILE["location"]
    if any(x in q for x in ["portfolio", "github", "work sample", "link", "website"]):
        return PROFILE["portfolio"]
    if any(x in q for x in ["phone", "mobile", "contact number"]):
        return PROFILE["phone"]
    if any(x in q for x in ["why", "interest", "motivation", "why this", "why apply"]):
        return (
            "This role aligns perfectly with my background in Python development, "
            "automation, and full-stack engineering. I'm confident I can deliver "
            "quality work from day one and I'm available to start immediately."
        )
    if any(x in q for x in ["cover letter", "tell us about", "introduce yourself", "about you"]):
        return (
            f"Hi, I'm {YOUR_NAME} — a Python developer specialising in automation systems, "
            "trading bots, web scraping, and full-stack development. I have hands-on experience "
            "with MetaTrader 5, Playwright, PHP, Laravel, Flutter, React, Node.js, SQL, and "
            "AI/LLM integrations. I write clean, tested code and communicate clearly throughout. "
            f"Portfolio: {PROFILE['portfolio']}"
        )
    if any(x in q for x in ["remote", "comfortable working remote", "willing to work remote"]):
        return "Yes"
    if any(x in q for x in ["how did you hear", "referral", "source"]):
        return "Online job search"
    return "Yes"


# ════════════════════════════════════════════════════════════════════════════
#  STEALTH
# ════════════════════════════════════════════════════════════════════════════

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
window.chrome = {runtime: {}};
"""


# ════════════════════════════════════════════════════════════════════════════
#  LOGIN
# ════════════════════════════════════════════════════════════════════════════

def manual_login():
    log.info("Opening browser for Wellfound login — only needs to be done once.")
    BROWSER_PROFILE.mkdir(exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            slow_mo=60,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled",
                  "--disable-infobars"],
            ignore_default_args=["--enable-automation"],
        )
        page = context.new_page()
        page.add_init_script(STEALTH_SCRIPT)
        page.goto("https://wellfound.com/login", wait_until="domcontentloaded")
        human_delay(2, 3)

        # Try auto-fill
        try:
            email_field = page.locator('input[type="email"], input[name="email"]').first
            if email_field.is_visible(timeout=5000):
                email_field.fill(WELLFOUND_EMAIL)
                human_delay(0.5, 1)
                pw_field = page.locator('input[type="password"]').first
                pw_field.fill(WELLFOUND_PASSWORD)
                human_delay(0.5, 1)
                page.locator('button[type="submit"]').first.click()
                page.wait_for_url("**/jobs**", timeout=20000)
                log.info("✓ Auto-login successful — session saved.")
                context.close()
                return
        except Exception as e:
            log.warning(f"Auto-login failed ({e}). Log in manually.")

        input(">>> Log in manually in the browser, then press ENTER <<<")
        context.close()
    log.info("Login saved. Run:  python wellfound_bot.py  to start.")


# ════════════════════════════════════════════════════════════════════════════
#  FILL APPLICATION FORM
# ════════════════════════════════════════════════════════════════════════════

def fill_application_form(page: Page) -> bool:
    """Fill all visible form fields in the application. Returns True if submitted."""
    human_delay(2, 4)

    # Fill phone number
    for sel in ['input[type="tel"]', 'input[name*="phone" i]', 'input[placeholder*="phone" i]']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.triple_click()
                el.type(PROFILE["phone"], delay=60)
                break
        except: continue

    human_delay(0.5, 1)

    # Fill portfolio / website / GitHub
    for sel in ['input[name*="portfolio" i]', 'input[name*="website" i]',
                'input[name*="github" i]', 'input[placeholder*="portfolio" i]',
                'input[placeholder*="github" i]', 'input[placeholder*="website" i]']:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=1500):
                el.triple_click()
                el.type(PROFILE["portfolio"], delay=50)
                break
        except: continue

    human_delay(0.5, 1)

    # Handle all radio button questions
    try:
        questions = page.locator('[class*="question"], [class*="field"], fieldset').all()
        for q_el in questions[:15]:
            try:
                label_text = q_el.inner_text()
                answer = answer_question(label_text)
                # Find radio matching the answer
                radios = q_el.locator('input[type="radio"]').all()
                for radio in radios:
                    try:
                        radio_label = radio.evaluate(
                            "el => el.closest('label') ? el.closest('label').innerText "
                            ": (el.nextSibling ? el.nextSibling.textContent : '')"
                        ).strip()
                        if answer.lower() in radio_label.lower() or radio_label.lower() in answer.lower():
                            radio.click()
                            human_delay(0.3, 0.6)
                            break
                    except: continue
            except: continue
    except: pass

    human_delay(0.5, 1)

    # Handle textarea questions (cover letter / why interested / etc.)
    try:
        textareas = page.locator("textarea").all()
        for ta in textareas:
            try:
                if ta.is_visible(timeout=1500):
                    # Find the label for this textarea
                    label = page.evaluate(
                        """el => {
                            const id = el.id;
                            if (id) {
                                const lbl = document.querySelector('label[for="' + id + '"]');
                                if (lbl) return lbl.innerText;
                            }
                            const parent = el.closest('[class*="field"],[class*="question"],fieldset');
                            return parent ? parent.innerText : '';
                        }""",
                        ta.element_handle()
                    )
                    response = answer_question(label or "cover letter")
                    ta.click()
                    human_delay(0.4, 0.8)
                    ta.evaluate("el => el.value = ''")
                    for ch in response:
                        ta.type(ch, delay=random.randint(28, 85))
                        if random.random() < 0.03:
                            time.sleep(random.uniform(0.2, 0.5))
                    human_delay(0.5, 1)
            except: continue
    except: pass

    human_delay(1, 2)

    # Submit
    for sel in [
        'button[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Apply")',
        'button:has-text("Send Application")',
        'button:has-text("Submit Application")',
    ]:
        try:
            btn = page.locator(sel).last
            if btn.is_visible(timeout=3000):
                btn.scroll_into_view_if_needed()
                human_delay(0.8, 1.5)
                btn.click()
                log.info("  ✓ Application submitted!")
                return True
        except: continue

    log.warning("  Submit button not found.")
    return False


# ════════════════════════════════════════════════════════════════════════════
#  JOB SCRAPER  (Wellfound API / web)
# ════════════════════════════════════════════════════════════════════════════

def _dismiss_cookie_banner(page: Page):
    """Click Reject All or Agree on cookie/consent banners if visible."""
    for sel in ['button:has-text("Reject All")', 'button:has-text("Agree & Proceed")', 'button:has-text("Accept")']:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click()
                time.sleep(0.5)
                break
        except Exception:
            pass


def _scrape_jobs_from_page(page: Page) -> list:
    """Extract all job links from the current page DOM."""
    import re as _re
    _dismiss_cookie_banner(page)
    raw = page.evaluate("""
        () => {
            const seen = new Set();
            const results = [];
            const links = [...document.querySelectorAll('a[href]')];
            for (const a of links) {
                const href = a.href;
                // Match real job pages — must have path segment after roles/ or jobs/ that's 6+ chars
                const isJob = /wellfound\.com\/(company\/.+\/roles\/.{6,}|jobs\/.{6,})/i.test(href);
                // Skip known nav paths
                const isNav = /\/(jobs\/saved|jobs\/search|jobs\/applied|jobs\?|jobs#)/i.test(href);
                if (!isJob || isNav) continue;
                const clean = href.split('?')[0];
                if (seen.has(clean)) continue;
                seen.add(clean);
                let el = a;
                let title = '';
                let company = '';
                for (let i = 0; i < 6; i++) {
                    el = el.parentElement;
                    if (!el) break;
                    if (!title) {
                        const h = el.querySelector('h1,h2,h3,h4,[class*="title"],[class*="role"],[class*="name"]');
                        if (h) title = h.innerText.trim();
                    }
                    if (!company) {
                        const c = el.querySelector('[class*="company"],[class*="startup"],[class*="org"]');
                        if (c) company = c.innerText.trim().split('\\n')[0];
                    }
                    if (title && company) break;
                }
                if (!title) title = a.innerText.trim();
                results.push({ href: clean, title: title || 'Unknown Role', company: company || 'Unknown' });
            }
            return results;
        }
    """)
    jobs = []
    for item in (raw or [])[:80]:
        href    = item.get("href", "")
        title   = item.get("title", "").strip()
        company = item.get("company", "Unknown").strip()
        bad_titles = {"search for jobs", "jobs", "home", ""}
        if title.lower() in bad_titles or len(title) < 4:
            m = _re.search(r'/jobs/\d+-(.+)$|/roles/\d+-(.+)$', href)
            if m:
                slug = m.group(1) or m.group(2)
                title = slug.replace("-", " ").title()
            else:
                continue   # no job ID in URL — it's a nav link, skip it
        if href and title and len(title) > 3:
            jobs.append({"title": title, "company": company, "url": href, "id": href})
    return jobs


def _extract_api_jobs(data, collected: list):
    """Recursively pull job objects from Wellfound's GraphQL/REST API response."""
    if isinstance(data, dict):
        # Wellfound GraphQL shape: node with title + slug + startup
        if "title" in data and ("slug" in data or "liveJobUrl" in data or "remoteOk" in data):
            try:
                title   = data.get("title", "").strip()
                slug    = data.get("slug", "")
                company = ""
                startup = data.get("startup") or data.get("company") or {}
                if isinstance(startup, dict):
                    company = startup.get("name", "") or startup.get("slug", "")
                url = data.get("liveJobUrl") or data.get("jobUrl") or ""
                if not url and slug:
                    url = f"https://wellfound.com/jobs/{slug}"
                if url and title and len(title) > 3:
                    collected.append({"title": title, "company": company or "Unknown",
                                      "url": url.split("?")[0], "id": url.split("?")[0]})
            except Exception:
                pass
        for v in data.values():
            _extract_api_jobs(v, collected)
    elif isinstance(data, list):
        for item in data:
            _extract_api_jobs(item, collected)


def fetch_wellfound_jobs(page: Page) -> list:
    """Fetch jobs by intercepting Wellfound's internal API/GraphQL calls + DOM fallback."""
    all_jobs  = {}
    api_jobs  = []

    def _on_response(response):
        try:
            if response.status != 200:
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            url = response.url
            if not any(k in url for k in ["/graphql", "/api/", "wellfound.com"]):
                return
            data = response.json()
            before = len(api_jobs)
            _extract_api_jobs(data, api_jobs)
            gained = len(api_jobs) - before
            if gained:
                log.info(f"    API intercepted {gained} jobs from {url.split('wellfound.com')[1][:60]}")
        except Exception:
            pass

    page.on("response", _on_response)
    try:
        for url in JOB_SEARCH_URLS[:2]:   # only need 2 — API gives all results
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                human_delay(3, 5)
                # Scroll to trigger pagination API calls
                prev = len(api_jobs)
                for _ in range(20):
                    page.mouse.wheel(0, 600)
                    time.sleep(1.2)
                    if len(api_jobs) >= prev + 5:
                        prev = len(api_jobs)  # reset on new batch
                role_label = url.split('role=')[1].split('&')[0] if 'role=' in url else 'all-jobs'
                log.info(f"  {role_label}: {len(api_jobs)} API jobs so far")
            except Exception as e:
                log.warning(f"  Error on {url}: {e}")
    finally:
        page.remove_listener("response", _on_response)

    # Dedupe API jobs
    for j in api_jobs:
        all_jobs[j["id"]] = j

    # DOM fallback — scrape visible links too
    try:
        dom_jobs = _scrape_jobs_from_page(page)
        for j in dom_jobs:
            all_jobs.setdefault(j["id"], j)
        if dom_jobs:
            log.info(f"  DOM fallback added {len(dom_jobs)} links")
    except Exception:
        pass

    jobs = list(all_jobs.values())
    log.info(f"  Wellfound: {len(jobs)} unique jobs total.")
    return jobs


# ════════════════════════════════════════════════════════════════════════════
#  APPLY TO A JOB
# ════════════════════════════════════════════════════════════════════════════

def apply_to_job(page: Page, job: dict) -> bool:
    """Navigate to job page and submit application."""
    try:
        log.info(f"  Opening: {job['title']} @ {job['company']}")
        page.goto(job["url"], wait_until="domcontentloaded", timeout=25000)
        human_delay(2, 3)

        # Detect 404 / expired job — Wellfound shows homepage nav with "Sign Up" when job is gone
        page_text = page.inner_text("body")[:400]
        if any(x in page_text for x in ["Sign Up", "return home", "For Recruiters"]) and "Apply" not in page_text:
            log.warning(f"  Job no longer exists (404 page) — skipping.")
            return False

        _dismiss_cookie_banner(page)

        # Read the job
        read_time = random.uniform(READING_TIME_MIN, READING_TIME_MAX)
        log.info(f"  Reading for {read_time:.0f}s...")
        human_scroll(page, times=random.randint(2, 3))
        time.sleep(max(0, read_time - 8))

        # Scroll back to top — Apply button is usually in the header/sidebar
        page.evaluate("window.scrollTo(0, 0)")
        human_delay(0.5, 1)

        # Click Apply button
        apply_clicked = False
        for sel in [
            'button:has-text("Apply")',
            'button:has-text("Apply Now")',
            'button:has-text("Easy Apply")',
            'button:has-text("Quick Apply")',
            'a:has-text("Apply Now")',
            'a:has-text("Apply")',
            '[data-test*="apply"]',
            '[data-testid*="apply"]',
            '[class*="Apply"] button',
            '[class*="apply-button"]',
            '[class*="applyButton"]',
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.scroll_into_view_if_needed()
                    human_delay(0.5, 1.2)
                    btn.click()
                    apply_clicked = True
                    log.info(f"  Clicked Apply button ({sel}).")
                    break
            except: continue

        if not apply_clicked:
            # JS fallback — find any button/link whose text starts with "Apply"
            found = page.evaluate("""
                () => {
                    const btns = [...document.querySelectorAll('button, a, [role="button"]')];
                    const b = btns.find(el => /^apply/i.test((el.innerText||el.textContent||'').trim()));
                    if (b) { b.click(); return true; }
                    // Debug: return what buttons exist so we can diagnose
                    return btns.slice(0,20).map(el=>(el.innerText||el.textContent||'').trim().substring(0,40)).filter(t=>t);
                }
            """)
            if found is True:
                apply_clicked = True
                log.info("  Clicked Apply button (JS fallback).")
            else:
                log.warning(f"  Apply button not found. Buttons on page: {found}")
                return False
        human_delay(2, 3.5)

        # Check for external redirect
        if "wellfound.com" not in page.url:
            log.warning(f"  Redirected to external ATS ({page.url[:60]}) — skipping.")
            return False

        # Fill the form and submit
        return fill_application_form(page)

    except PlaywrightTimeout:
        log.warning(f"  Timeout on: {job['url']}")
        return False
    except Exception as e:
        log.warning(f"  Error applying: {e}")
        return False


# ════════════════════════════════════════════════════════════════════════════
#  MAIN BOT LOOP
# ════════════════════════════════════════════════════════════════════════════

def run():
    log.info("═" * 62)
    log.info("  Wellfound Auto-Apply Bot  v1.0")
    log.info(f"  Max applies/session : {MAX_APPLIES_PER_SESSION}")
    log.info(f"  Gap between applies : {MIN_GAP_MIN}–{MAX_GAP_MIN} minutes")
    log.info(f"  Telegram : {'✓' if TELEGRAM_TOKEN else '✗ not set'}")
    log.info("═" * 62)

    if not BROWSER_PROFILE.exists():
        log.error("No browser profile. Run:  python wellfound_bot.py --login  first.")
        sys.exit(1)

    seen = load_seen()

    send_telegram("🤖 <b>Wellfound Bot started.</b>\nSearching for dev jobs...")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            args=[
                "--headless=new",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1280,800",
                "--no-sandbox",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.add_init_script(STEALTH_SCRIPT)

        # Verify login
        page.goto("https://wellfound.com/jobs", wait_until="domcontentloaded", timeout=30000)
        human_delay(4, 6)   # give JS time to render after DOM loads
        if "login" in page.url.lower() or "signin" in page.url.lower():
            log.error("Not logged in. Run:  python wellfound_bot.py --login")
            send_telegram("⚠️ <b>Wellfound login needed.</b> Run: python wellfound_bot.py --login")
            context.close()
            sys.exit(1)

        log.info("✓ Logged in — running in background.")
        session_applies = 0
        empty_scan_streak = 0   # consecutive scans with 0 new jobs

        while True:
            # Session limit check
            if session_applies >= MAX_APPLIES_PER_SESSION:
                log.info(f"✅ {MAX_APPLIES_PER_SESSION} applications placed. Cooling down 3 hours...")
                send_telegram(
                    f"✅ <b>Wellfound: {MAX_APPLIES_PER_SESSION} applications placed!</b>\n"
                    "Cooling down 3 hours then starting fresh session."
                )
                time.sleep(3 * 3600)
                session_applies = 0
                seen = set()
                save_seen(seen)
                log.info("New session started.")

            # Fetch jobs
            log.info("Scanning Wellfound for dev jobs...")
            jobs = fetch_wellfound_jobs(page)
            new_jobs = [j for j in jobs if j["id"] not in seen]
            log.info(f"Found {len(jobs)} total, {len(new_jobs)} new")

            # Stuck detection: if 4+ scans in a row found nothing, clear seen list
            if len(new_jobs) == 0:
                empty_scan_streak += 1
                if empty_scan_streak >= 4:
                    log.info(f"⚠ {empty_scan_streak} consecutive empty scans — clearing seen list to retry old jobs.")
                    seen = set()
                    save_seen(seen)
                    empty_scan_streak = 0
                    new_jobs = jobs   # treat all as new this round
            else:
                empty_scan_streak = 0

            placed_this_scan = 0

            for job in new_jobs:
                seen.add(job["id"])
                save_seen(seen)

                if session_applies >= MAX_APPLIES_PER_SESSION:
                    break

                if random.random() < SKIP_PROBABILITY:
                    log.info(f"  ↷ Randomly skipping: {job['title'][:50]}")
                    continue

                log.info(f"  → Applying to: {job['title']} @ {job['company']}")
                success = apply_to_job(page, job)

                if success:
                    session_applies += 1
                    placed_this_scan += 1
                    record_application(job["title"], job["company"], job["url"])
                    send_telegram(
                        f"✅ <b>Applied on Wellfound!</b>\n\n"
                        f"<b>{job['title']}</b> @ {job['company']}\n"
                        f"🔗 <a href='{job['url']}'>View job</a>\n"
                        f"Session: {session_applies}/{MAX_APPLIES_PER_SESSION}"
                    )
                    log.info(f"  Session: {session_applies}/{MAX_APPLIES_PER_SESSION}")
                    # Human gap after success
                    if session_applies < MAX_APPLIES_PER_SESSION:
                        gap = random.randint(MIN_GAP_MIN * 60, MAX_GAP_MIN * 60)
                        log.info(f"  Waiting {gap//60}m {gap%60}s before next application...")
                        time.sleep(gap)
                else:
                    log.warning("  Application failed — trying next job.")
                    human_delay(4, 8)

            log.info(f"Scan done. Placed {placed_this_scan} application(s). "
                     f"Session: {session_applies}/{MAX_APPLIES_PER_SESSION}")

            if placed_this_scan == 0:
                wait = random.randint(60, 90)
                log.info(f"No applications placed — rescanning in {wait}s...\n")
            else:
                wait = random.randint(5, 8) * 60
                log.info(f"Next scan in {wait//60}m...\n")
            time.sleep(wait)


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--login" in args:
        manual_login()
        sys.exit(0)
    run()
