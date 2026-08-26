#!/usr/bin/env python3
"""
freelancer_bot.py  v2.0  —  Full Auto-Apply Bot with Human Behaviour
═════════════════════════════════════════════════════════════════════
Logs into your Freelancer.com account, finds new matching jobs,
writes a tailored proposal for each one, and submits the bid —
all automatically, behaving exactly like a real human would.

HUMAN BEHAVIOUR BUILT IN:
  • Max 8–15 bids per day  (controlled by MAX_BIDS_PER_DAY)
  • 10–40 minute random gap between each bid
  • Reads each job page for 25–60 seconds before bidding
  • Types proposal character by character at human speed
  • Slightly varies bid amount each time
  • Only works during ACTIVE_HOURS (8am–8pm by default)
  • Randomly skips ~10% of jobs (humans are selective)
  • Saves browser session — stays logged in, no constant re-login

SETUP:
  1. pip install playwright requests
  2. python -m playwright install chromium
  3. Fill in your credentials in the CONFIG section below
  4. Run:  python freelancer_bot.py --login
     (logs in manually the first time, saves session)
  5. Run:  python freelancer_bot.py
     (runs the bot — login is automatic from saved session)

FIRST TIME:
  Run with --login flag. A browser window opens. Log in manually.
  The session is saved to freelancer_session.json so future runs
  are fully automatic (no window, runs in background).
"""

from __future__ import annotations
import sys, json, time, random, logging, smtplib, os
from datetime import datetime, date
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout
except ImportError:
    print("\n  Missing dependency. Run:\n")
    print("    pip install playwright")
    print("    python -m playwright install chromium\n")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Run: pip install requests")
    sys.exit(1)


# ════════════════════════════════════════════════════════════════════════════
#  CONFIG — FILL EVERYTHING HERE
# ════════════════════════════════════════════════════════════════════════════

FREELANCER_EMAIL    = "obodavekel466@gmail.com"    # your Freelancer.com login email
FREELANCER_PASSWORD = "OBOdavekel@46"             # your Freelancer.com password

TELEGRAM_TOKEN      = "8871384090:AAHJ92v3MeXgd2PKotnRjSL1q9NLGDZ3vvg"    # from @BotFather on Telegram
TELEGRAM_CHAT_ID    = "8350604369"    # run:  python freelancer_bot.py --get-chat-id

EMAIL_FROM          = ""    # your Gmail address
EMAIL_PASSWORD      = ""    # Gmail App Password (not your main password)
EMAIL_TO            = ""    # where to receive alerts

# ── Job search settings ───────────────────────────────────────────────────
SEARCH_KEYWORDS = [
    # ── General dev roles (broad, pulls the most jobs) ─────────────────────
    "python developer",
    "web developer",
    "software developer",
    "app developer",
    "backend developer",
    "frontend developer",
    "full stack developer",
    "javascript developer",
    "react developer",
    "node.js developer",
    "typescript developer",
    "java developer",
    # ── Specific frameworks / stacks ───────────────────────────────────────
    "PHP laravel developer",
    "PHP developer",
    "wordpress developer",
    "wordpress plugin",
    "shopify developer",
    "shopify app",
    "woocommerce developer",
    "django developer",
    "flask developer",
    "fastapi developer",
    "flutter developer",
    "react native developer",
    "vue.js developer",
    "angular developer",
    "next.js developer",
    "spring boot developer",
    "ruby rails developer",
    "golang developer",
    # ── Mobile ─────────────────────────────────────────────────────────────
    "mobile app developer",
    "android developer",
    "ios developer",
    "mobile app development",
    # ── Automation & bots ──────────────────────────────────────────────────
    "automation script",
    "python automation",
    "web automation",
    "browser automation",
    "trading bot",
    "metatrader expert advisor",
    "MT5 trading bot",
    "crypto trading bot",
    "telegram bot developer",
    "discord bot developer",
    "chatbot developer",
    "AI chatbot",
    # ── Web scraping / data ────────────────────────────────────────────────
    "web scraping",
    "web scraper",
    "data scraping",
    "data extraction",
    "data collection automation",
    "data entry automation",
    "automate data entry",
    "excel automation python",
    "google sheets automation",
    # ── AI / ML ────────────────────────────────────────────────────────────
    "AI developer",
    "machine learning developer",
    "openai integration",
    "GPT integration",
    "LLM developer",
    # ── APIs & integrations ────────────────────────────────────────────────
    "api developer",
    "api integration",
    "rest api developer",
    "webhook integration",
    "zapier integration",
    # ── Database ───────────────────────────────────────────────────────────
    "sql developer",
    "database developer",
    "mysql developer",
    "postgresql developer",
    # ── General build requests ─────────────────────────────────────────────
    "build a website",
    "build a mobile app",
    "build a web app",
    "website development",
    "app development",
    "software development",
    "saas development",
]

MIN_BUDGET_USD = 30     # skip jobs below this budget (not worth the effort)
MAX_BUDGET_USD = 3000   # skip jobs above this (usually need a big team)
MIN_BID_USD    = 50     # never bid below this — $20 bids look desperate

# ── Human behaviour settings ──────────────────────────────────────────────
MAX_BIDS_PER_DAY    = 20     # max bids placed per calendar day
MIN_BID_GAP_MIN     = 2      # minimum minutes between bids
MAX_BID_GAP_MIN     = 6      # maximum minutes between bids
READING_TIME_MIN    = 25     # seconds to "read" job page before bidding
READING_TIME_MAX    = 60     # max reading time
ACTIVE_HOUR_START   = 0      # 24/7 — no time restriction
ACTIVE_HOUR_END     = 24
SKIP_PROBABILITY    = 0.10   # 10% chance to skip a job randomly (looks human)
SCAN_INTERVAL_MIN   = 5      # minutes between scans (runs in background)

# ── Your profile ──────────────────────────────────────────────────────────
YOUR_NAME  = "Favour"

YOUR_BIO = """\
I'm a Python developer specialising in automation, trading bots, and full-stack
development with hands-on experience in MetaTrader 5 (MT5) algorithmic trading,
web scraping, PHP, HTML, mobile apps, SQL, and AI automation workflows.
I write clean, documented code and deliver on time.\
"""

# ── Application form defaults (used on Wellfound, LinkedIn, etc.) ─────────
PROFILE = {
    "phone"            : "+447502449946",
    "portfolio"        : "https://github.com/kene-favs",
    "english"          : "Yes",
    "authorized"       : "Yes",    # authorized to work
    "sponsorship"      : "No",     # require visa sponsorship
    "location"         : "Remote — available globally",
    "availability"     : "Immediately",
    "experience_years" : "3",
    "salary_expectation": "Negotiable based on project scope",
}


def answer_application_question(question: str) -> str:
    """Return a smart, safe answer for any common application form question."""
    q = question.lower().strip()

    if any(x in q for x in ["english", "proficient in english", "english speaking"]):
        return PROFILE["english"]
    if any(x in q for x in ["authorized", "legally authorized", "right to work", "eligible to work"]):
        return PROFILE["authorized"]
    if any(x in q for x in ["sponsor", "sponsorship", "visa", "immigration"]):
        return PROFILE["sponsorship"]
    if any(x in q for x in ["years of experience", "how many years", "experience level"]):
        return PROFILE["experience_years"]
    if any(x in q for x in ["available", "start date", "when can you start", "notice period"]):
        return PROFILE["availability"]
    if any(x in q for x in ["salary", "rate", "compensation", "hourly rate", "expected pay"]):
        return PROFILE["salary_expectation"]
    if any(x in q for x in ["location", "where are you", "where do you live", "timezone"]):
        return PROFILE["location"]
    if any(x in q for x in ["portfolio", "github", "work sample", "previous work", "link"]):
        return PROFILE["portfolio"]
    if any(x in q for x in ["phone", "mobile", "contact number", "telephone"]):
        return PROFILE["phone"]
    if any(x in q for x in ["why", "interest", "why this role", "why apply", "motivation"]):
        return (
            "This role aligns perfectly with my background in Python development, "
            "automation, and full-stack engineering. I'm confident I can deliver "
            "quality work from day one and I'm ready to start immediately."
        )
    if any(x in q for x in ["cover letter", "tell us about yourself", "introduce yourself"]):
        return (
            f"Hi, I'm {YOUR_NAME}, a Python developer specialising in automation, "
            "trading bots, web scraping, and full-stack web development. I have hands-on "
            "experience with MetaTrader 5, Playwright, PHP, Laravel, Flutter, React, "
            "SQL, and AI/LLM integrations. I deliver clean, documented, tested code "
            "and communicate clearly throughout. Portfolio: " + PROFILE["portfolio"]
        )
    if any(x in q for x in ["reference", "referral", "how did you hear"]):
        return "Online job search"
    if any(x in q for x in ["remote", "willing to work remote", "comfortable remote"]):
        return "Yes"
    if any(x in q for x in ["full time", "part time", "contract", "freelance"]):
        return "Yes, I'm available for contract and freelance work"

    # Unknown question — safe neutral answer
    return "Yes"

# ── Default bid delivery period ───────────────────────────────────────────
DEFAULT_DELIVERY_DAYS = 7   # how many days you promise delivery


# ════════════════════════════════════════════════════════════════════════════
#  PATHS & LOGGING
# ════════════════════════════════════════════════════════════════════════════
BROWSER_PROFILE   = Path("browser_profile")   # persistent Chrome profile — stays logged in
SEEN_FILE         = Path("jobs_seen.json")
STATE_FILE        = Path("bot_state.json")   # tracks daily bid count
BIDS_LOG_FILE     = Path("bids_log.json")    # full bid history for dashboard
DASHBOARD_FILE    = Path("dashboard.html")   # auto-generated dashboard
QUIZ_PENDING_FILE = Path("quiz_pending.json") # jobs with quiz — bot retries after you answer

logging.basicConfig(
    level   = logging.INFO,
    format  = '%(asctime)s  %(name)-12s  %(message)s',
    datefmt = '%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("freelancer_bot.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("FreelancerBot")


# ════════════════════════════════════════════════════════════════════════════
#  STATE  (seen jobs + daily bid count)
# ════════════════════════════════════════════════════════════════════════════

def load_seen() -> set:
    if SEEN_FILE.exists():
        try: return set(json.loads(SEEN_FILE.read_text()))
        except: pass
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def load_state() -> dict:
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text())
        except: pass
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))

def load_quiz_pending() -> list:
    if QUIZ_PENDING_FILE.exists():
        try: return json.loads(QUIZ_PENDING_FILE.read_text(encoding="utf-8"))
        except: pass
    return []

def save_quiz_pending(jobs: list):
    QUIZ_PENDING_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")

def add_quiz_pending(job: dict) -> bool:
    """Add job to pending list. Returns True if newly added (first time), False if already there."""
    jobs = load_quiz_pending()
    if not any(j["id"] == job["id"] for j in jobs):
        jobs.append(job)
        save_quiz_pending(jobs)
        return True   # newly added → send alert
    return False       # already in list → don't alert again

def remove_quiz_pending(job_id: str):
    jobs = [j for j in load_quiz_pending() if j["id"] != job_id]
    save_quiz_pending(jobs)

def bids_today(state: dict) -> int:
    today = str(date.today())
    return state.get(today, 0)

def record_bid(state: dict) -> dict:
    today = str(date.today())
    state[today] = state.get(today, 0) + 1
    save_state(state)
    return state


# ════════════════════════════════════════════════════════════════════════════
#  FREELANCER.COM SCRAPER  (finds new jobs via API)
# ════════════════════════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent"        : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
    "Accept"            : "application/json, text/plain, */*",
    "Accept-Language"   : "en-US,en;q=0.9",
    "Referer"           : "https://www.freelancer.com/jobs/",
    "freelancer-ewf-ref": "/jobs/",
}

API_URL = "https://www.freelancer.com/api/projects/0.1/projects/active"

# Browser session cookies — populated after login so API calls are authenticated
_session_cookies: dict = {}

def capture_cookies(context) -> None:
    """Extract browser cookies and store them for use in requests API calls."""
    global _session_cookies
    try:
        raw = context.cookies()
        _session_cookies = {
            c["name"]: c["value"]
            for c in raw
            if "freelancer.com" in c.get("domain", "")
        }
        log.info(f"  Captured {len(_session_cookies)} session cookies for API calls.")
    except Exception as e:
        log.warning(f"  Could not capture cookies: {e}")


def fetch_jobs(keyword: str, limit: int = 40) -> list:
    params = {
        "keyword"         : keyword,
        "limit"           : limit,
        "offset"          : 0,
        "full_description": "true",
        "compact"         : "false",
        "sort_field"      : "time_updated",
        "sort_direction"  : "desc",
        "project_types[]" : ["fixed", "hourly"],
        "job_details"     : "true",
    }
    try:
        r = requests.get(
            API_URL,
            headers=HEADERS,
            params=params,
            cookies=_session_cookies,   # authenticated — uses logged-in browser session
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        projects = data.get("result", {}).get("projects", [])
        if not projects and not _session_cookies:
            log.warning(f"  API returned 0 results for '{keyword}' — cookies not yet captured.")
        return projects
    except Exception as e:
        log.warning(f"Fetch error '{keyword}': {e}")
        return []


def parse_job(raw: dict) -> Optional[dict]:
    try:
        pid      = str(raw.get("id", ""))
        title    = raw.get("title", "").strip()
        desc     = raw.get("description", "").strip()
        seo      = raw.get("seo_url", pid)
        url      = f"https://www.freelancer.com/projects/{seo}"
        currency = raw.get("currency", {}).get("sign", "$")
        budget   = raw.get("budget", {})
        bmin     = float(budget.get("minimum") or 0)
        bmax     = float(budget.get("maximum") or 0)
        avg      = (bmin + bmax) / 2 if bmax else bmin
        ptype    = raw.get("type", "fixed")
        skills   = [j.get("name","") for j in raw.get("jobs", [])]
        posted   = datetime.fromtimestamp(raw.get("time_submitted",0)).strftime('%H:%M %d/%m/%y')

        # Skip Preferred Freelancer jobs — requires paid PF membership
        upgrades = raw.get("upgrades", {})
        if upgrades.get("qualified") or upgrades.get("pfonly") or upgrades.get("preferred"):
            log.debug(f"Skip PF-only job: {title[:50]}")
            return None
        # Also catch it in the title/description
        if "preferred freelancer" in title.lower() or "preferred freelancer" in desc[:300].lower():
            log.debug(f"Skip PF-only job (text match): {title[:50]}")
            return None

        return dict(id=pid, title=title, description=desc, url=url,
                    currency=currency, budget_min=bmin, budget_max=bmax,
                    avg_budget=avg, type=ptype, skills=skills, posted=posted)
    except Exception as e:
        log.debug(f"Skip malformed job: {e}")
        return None


# ── Relevance filter — job must match your actual skills ─────────────────────

# These must appear in the JOB TITLE — not buried in description
TITLE_TECH_TERMS = [
    # Python ecosystem
    "python", "django", "flask", "fastapi",
    # Trading / MT
    "metatrader", "mt5", "mt4", "mql", "trading bot", "algo trading", "expert advisor",
    # Scraping / automation
    "web scraping", "scraper", "crawler", "selenium", "playwright",
    "automation", "automate", "bot development", "build a bot", "build a script",
    # PHP / CMS
    "php", "laravel", "wordpress",
    # Mobile
    "flutter", "android", "ios app", "mobile app", "react native",
    # Web — broad: "website", "web app", "web dev" etc.
    "website", "web app", "web application", "web development", "web developer",
    "full stack", "fullstack", "frontend", "backend",
    "api developer", "api integration", "rest api",
    "site builder",
    # JavaScript ecosystem
    "javascript", "node.js", "nodejs", "node js", "react", "vue", "angular",
    "next.js", "nextjs", "typescript",
    # AI / ML
    "chatbot", "llm", "openai", "gpt", "ai automation", "machine learning",
    # Data
    "data engineer", "data pipeline", "etl",
    "sql", "mysql", "postgres", "mongodb", "database developer",
    "data extraction", "data processing", "data collection",
    # General dev
    "software developer", "software engineer", "app developer", "app development",
    "programmer", "coder",
    "developer needed", "developer required", "developer for",
    "build a website", "build an app", "build an application",
    "fix my code", "fix my website", "fix my app", "debug",
    # HTML / CSS
    "html developer", "html website", "html page", "html css",
    # Other languages
    "java developer", "java spring", "java backend",
    "c# developer", ".net developer", "ruby on rails",
    # Specific platforms
    "shopify developer", "shopify store", "shopify app",
    "woocommerce", "magento",
    # Script
    "python script", "automation script", "bash script",
    # Data entry (automated)
    "data entry automation", "automate data entry",
    "excel automation", "google sheets automation",
    "spreadsheet automation", "data processing automation",
    # Loose — safe because blocked list catches non-tech uses
    "mobile", "app development", "app developer", "build an app", "create an app",
    "bot", "chatbot", "build a bot",
    "plugin", "extension", "add-on", "widget",
    "integration", "api integration",
    "software", "system development", "custom software",
    "saas", "web platform", "platform development",
]

# Job title must NOT contain any of these — hard block, no exceptions
BLOCKED_TITLE_WORDS = [
    # Marketing & social
    "instagram", "tiktok", "facebook", "twitter", "social media",
    "influencer", "outreach", "dm outreach", "lead generation", "cold email",
    # SEO & content
    "seo", "search engine optimisation", "search engine optimization",
    "content writer", "copywriter", "blog", "article writer",
    "link building", "backlink", "keyword research",
    # Design
    "logo", "graphic design", "illustration", "photoshop", "figma",
    "brand kit", "branding", "ui design", "ux design", "banner",
    "flyer", "poster", "animation", "video edit",
    # Marketing (non-dev)
    "marketing automation", "email marketing", "email campaign",
    "growth hacking", "affiliate marketing", "digital marketing",
    "sales automation", "sales funnel", "cold outreach",
    # Finance & admin
    "audit", "accountant", "bookkeeping", "tax", "payroll",
    "salesforce", "pardot", "hubspot", "crm",
    "virtual assistant", "customer service", "customer support",
    "copy paste", "form filling", "handwritten", "manual typing", "typing job",
    # Non-tech services
    "translation", "transcription", "transcript", "proofreading",
    "medical", "clinical", "healthcare", "nurse", "doctor",
    "legal document", "paralegal",
    "forex signal", "crypto signal", "trading signal",
    "dropshipping", "amazon fba", "shopify dropship",
    "youtube", "podcast", "voice over",
    "salon", "beauty", "fashion", "jewellery", "jewelry",
    "real estate", "property", "leasing",
    "recruiter", "hr manager", "talent acquisition",
]


import re as _re

# High-confidence tech terms — if even ONE appears in description, it's a dev job
DESC_TECH_HIGH = [
    "python", "javascript", "typescript", "react", "node.js", "nodejs",
    "django", "flask", "fastapi", "php", "laravel",
    "flutter", "android studio", "swift", "kotlin",
    "mysql", "postgresql", "mongodb", "redis",
    "graphql", "docker", "kubernetes", "aws", "gcp", "azure",
    "selenium", "playwright", "beautifulsoup",
    "metatrader", "mt5", "mql5", "expert advisor",
    "tensorflow", "pytorch", "openai", "langchain", "llm",
    "java spring", "c# .net", "ruby on rails", "golang",
]
# Medium-confidence — need 2+ of these
DESC_TECH_MED = [
    "api", "rest api", "webhook", "database", "sql", "backend",
    "frontend", "full stack", "automation", "scraping", "git",
    "github", "deployment", "cloud", "server", "authentication",
    "oauth", "crud", "microservice", "framework", "library",
]

# Title verbs + nouns combo — catches "Build a Booking App", "Create a Trading System" etc.
_BUILD_VERBS = r'\b(build|create|develop|make|write|fix|automate|integrate|design|set up|setup)\b'
_TECH_NOUNS  = r'\b(app|application|website|bot|tool|script|system|platform|software|api|plugin|extension|dashboard|portal|database|widget|saas|mvp)\b'

def is_relevant(job: dict) -> bool:
    """Return True if the job matches the user's tech skills."""
    title_lower = job["title"].lower()
    desc_lower  = (job.get("description") or "")[:800].lower()

    # Hard block — if title has any of these, skip immediately
    for bad in BLOCKED_TITLE_WORDS:
        if bad in title_lower:
            return False

    # 1. Title contains a known tech term
    for term in TITLE_TECH_TERMS:
        if term in title_lower:
            return True

    # 2. Title has build-verb + tech-noun combo ("Build a Booking App", "Fix my Dashboard")
    if _re.search(_BUILD_VERBS, title_lower) and _re.search(_TECH_NOUNS, title_lower):
        return True

    # 3. Description has ONE high-confidence tech term (python, react, etc.)
    if any(t in desc_lower for t in DESC_TECH_HIGH):
        return True

    # 4. Description has TWO medium-confidence tech terms
    med_hits = sum(1 for t in DESC_TECH_MED if t in desc_lower)
    if med_hits >= 2:
        return True

    return False


def scan_jobs() -> list:
    all_jobs: dict[str, dict] = {}
    total_raw = 0
    parsed_ok  = 0
    budget_ok  = 0
    sample_budget_fails: list = []
    sample_relevance_fails: list = []

    for kw in SEARCH_KEYWORDS:
        raw_results = fetch_jobs(kw)
        total_raw += len(raw_results)
        for raw in raw_results:
            job = parse_job(raw)
            if job is None:
                continue
            parsed_ok += 1

            # Budget check — if avg is 0 (no budget set), use min; if still 0, allow it anyway
            avg = job["avg_budget"] if job["avg_budget"] > 0 else job["budget_min"]
            budget_passes = (avg == 0) or (MIN_BUDGET_USD <= avg <= MAX_BUDGET_USD)
            if not budget_passes:
                if len(sample_budget_fails) < 3:
                    sample_budget_fails.append(f"${avg:.0f} — {job['title'][:40]}")
                continue
            budget_ok += 1

            if is_relevant(job):
                all_jobs[job["id"]] = job
            else:
                if len(sample_relevance_fails) < 5:
                    sample_relevance_fails.append(job["title"][:55])

        time.sleep(random.uniform(0.8, 1.5))

    log.info(
        f"  API={total_raw} → parsed={parsed_ok} → budget_ok={budget_ok} → relevant={len(all_jobs)}"
    )
    if sample_budget_fails:
        log.info(f"  Budget rejects (sample): {sample_budget_fails}")
    if sample_relevance_fails:
        log.info(f"  Relevance rejects (sample): {sample_relevance_fails}")

    return list(all_jobs.values())


# ════════════════════════════════════════════════════════════════════════════
#  PROPOSAL GENERATOR
# ════════════════════════════════════════════════════════════════════════════

def detect_tech(text: str) -> list:
    t = text.lower()
    found = []
    checks = {
        "MetaTrader/MT5"  : ["metatrader","mt5","mt4","mql","expert advisor"],
        "Trading bot"     : ["trading bot","algo","algorithmic","trade auto"],
        "Python"          : ["python"],
        "Web scraping"    : ["scraping","scraper","crawl","beautifulsoup"],
        "PHP"             : ["php","wordpress","laravel"],
        "Full stack"      : ["full stack","fullstack","frontend","backend"],
        "Mobile app"      : ["mobile","android","ios","flutter","react native"],
        "AI / automation" : ["ai","automation","bot","chatbot","llm","gpt"],
        "Data entry"      : ["data entry","spreadsheet","excel","csv"],
        "SQL / database"  : ["sql","mysql","postgres","mongodb","database"],
        "JavaScript"      : ["javascript","react","vue","node","angular"],
    }
    for label, keywords in checks.items():
        if any(k in t for k in keywords):
            found.append(label)
    return found


def calculate_bid_amount(job: dict) -> int:
    """
    Bid near the top of the budget — 85–90% of max.
    e.g. $30–250  → bid ~$215–225
         $100–500 → bid ~$430–450
         $250 flat → bid $215–225
    """
    bmin = job["budget_min"]
    bmax = job["budget_max"]

    if bmax > 0 and bmax > bmin * 1.2:
        # Wide range — bid 85–90% of max (near ceiling, serious bid)
        bid = bmax * random.uniform(0.85, 0.90)
    elif bmax > 0:
        # Tight range — bid at or just below max
        bid = bmax * random.uniform(0.88, 0.95)
    else:
        # No max listed — bid just above minimum
        bid = bmin * random.uniform(1.05, 1.10)

    snapped = round(bid / 5) * 5
    return max(int(snapped), MIN_BID_USD)


def generate_proposal(job: dict) -> str:
    title = job["title"]
    desc  = (job["description"] or "")[:600]
    tech  = detect_tech(title + " " + desc)
    tech_line = ", ".join(tech[:4]) if tech else "the required stack"

    d = (title + " " + desc).lower()

    if any(x in d for x in ["metatrader","mt5","mt4","trading bot","algo"]):
        opener = (
            "I build MetaTrader 5 trading bots and automation systems from scratch. "
            "I've built complete arbitrage bots, signal bots, and MT5 EAs with "
            "real-time price feeds, order management, and risk controls."
        )
    elif any(x in d for x in ["scraping","scraper","crawl","extract data"]):
        opener = (
            "Web scraping and data extraction is a core part of my work. "
            "I've built scrapers for JavaScript-heavy sites, sites with Cloudflare "
            "protection, and large-scale data pipelines."
        )
    elif any(x in d for x in ["automate","automation","script","bot"]):
        opener = (
            "Python automation is exactly what I do. "
            "I've built schedulers, workflow bots, API integrations, and "
            "file processing systems that run 24/7 reliably."
        )
    elif any(x in d for x in ["mobile","android","ios","flutter"]):
        opener = "I develop mobile applications end-to-end and can handle this project from design to deployment."
    elif any(x in d for x in ["php","wordpress","website","web app","html"]):
        opener = "I'm a full-stack web developer with strong PHP, HTML, and web application experience."
    elif any(x in d for x in ["data entry","spreadsheet","excel","csv"]):
        opener = (
            "I handle data entry projects accurately and at speed — "
            "and wherever possible I automate the process in Python to eliminate human error."
        )
    elif any(x in d for x in ["ai","llm","gpt","chatbot","openai"]):
        opener = "I build AI automation systems and have integrated GPT, Claude, and other LLMs into real-world workflows."
    else:
        opener = "I've reviewed your project carefully and I have exactly the skills to deliver what you need."

    proposal = f"""Hi,

{opener}

For this project I would:
• Start with a detailed scope discussion to make sure we're 100% aligned
• Deliver working, tested code — not something that only runs once
• Keep you updated at each stage
• Be available for questions and adjustments after delivery

Relevant experience for this specific project: {tech_line}

I can start today. What's your preferred timeline?

Best,
{YOUR_NAME}"""

    return proposal


# ════════════════════════════════════════════════════════════════════════════
#  HUMAN BEHAVIOUR UTILITIES
# ════════════════════════════════════════════════════════════════════════════

def human_delay(min_sec: float = 1.0, max_sec: float = 3.5):
    """Random pause like a human thinking."""
    time.sleep(random.uniform(min_sec, max_sec))


def human_type(page: Page, selector: str, text: str):
    """Type text character by character at human typing speed."""
    el = page.locator(selector).first
    el.click()
    human_delay(0.3, 0.8)
    for char in text:
        el.type(char, delay=random.randint(30, 110))
        # Occasional longer pause (human thinking / correction)
        if random.random() < 0.04:
            time.sleep(random.uniform(0.3, 0.8))


def human_scroll(page: Page, times: int = 3):
    """Scroll down slowly like reading the page."""
    for _ in range(times):
        amount = random.randint(200, 500)
        page.evaluate(f"window.scrollBy(0, {amount})")
        time.sleep(random.uniform(1.5, 4.0))


def is_active_hour() -> bool:
    """Only work during configured active hours."""
    h = datetime.now().hour
    return ACTIVE_HOUR_START <= h < ACTIVE_HOUR_END


def random_bid_gap():
    """Wait a random human-like gap between bids."""
    gap = random.randint(MIN_BID_GAP_MIN * 60, MAX_BID_GAP_MIN * 60)
    log.info(f"  Waiting {gap//60}m {gap%60}s before next bid...")
    time.sleep(gap)


# ════════════════════════════════════════════════════════════════════════════
#  BROWSER SESSION
# ════════════════════════════════════════════════════════════════════════════

def save_session(context):
    pass   # persistent profile saves automatically — nothing to do here


# ════════════════════════════════════════════════════════════════════════════
#  FREELANCER LOGIN  (run once with --login flag)
# ════════════════════════════════════════════════════════════════════════════

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
window.chrome = {runtime: {}};
"""

EMAIL_SELECTORS = [
    'input[name="loginUsername"]',
    'input[name="username"]',
    'input[name="email"]',
    'input[type="email"]',
    'input[placeholder*="email" i]',
    'input[placeholder*="username" i]',
    '#email', '#username',
]
PASSWORD_SELECTORS = [
    'input[name="loginPassword"]',
    'input[name="password"]',
    'input[type="password"]',
    'input[placeholder*="password" i]',
    '#password',
]

def _try_fill(page: Page, selectors: list, value: str) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                el.click()
                human_delay(0.3, 0.6)
                el.fill(value)
                return True
        except:
            continue
    return False


def manual_login():
    """Open browser with persistent profile so login is saved permanently."""
    log.info("Opening browser for login — this only needs to be done once.")
    BROWSER_PROFILE.mkdir(exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            slow_mo=60,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = context.new_page()
        page.add_init_script(STEALTH_SCRIPT)
        page.goto("https://www.freelancer.com/login", wait_until="domcontentloaded")
        human_delay(2, 3)

        if FREELANCER_EMAIL and FREELANCER_PASSWORD:
            email_ok    = _try_fill(page, EMAIL_SELECTORS, FREELANCER_EMAIL)
            human_delay(0.6, 1.2)
            password_ok = _try_fill(page, PASSWORD_SELECTORS, FREELANCER_PASSWORD)
            human_delay(0.6, 1.2)

            if email_ok and password_ok:
                try:
                    page.locator('fl-button[type="submit"], button[type="submit"]').first.click()
                    page.wait_for_url("**dashboard**", timeout=20000)
                    log.info("✓ Auto-login successful — session saved to browser_profile/")
                    context.close()
                    log.info("Login complete. Run:  python freelancer_bot.py  to start.")
                    return
                except Exception as e:
                    log.warning(f"Submit failed ({e}). Finish login manually.")
            else:
                log.info("Could not find login fields. Log in manually in the browser window.")

        input(">>> Finish logging in manually, then press ENTER here <<<")
        context.close()
    log.info("Login complete. Run:  python freelancer_bot.py  to start.")


# ════════════════════════════════════════════════════════════════════════════
#  PLACE BID  (the core browser automation)
# ════════════════════════════════════════════════════════════════════════════

def _close_messages_panel(page: Page):
    """Close the Freelancer Messages chat panel that covers the bid button."""
    try:
        # Try clicking the chevron/down arrow that minimizes the Messages tray
        for sel in [
            "fl-chat-box .minimize-btn",
            "fl-messaging-tray button[aria-label*='close' i]",
            "fl-messaging-tray button[aria-label*='minimize' i]",
            ".messaging-tray .close-btn",
            "fl-messaging-tray .icon-arrow-down",
            # The chevron is a button inside the Messages panel header
            "fl-messaging-tray fl-button",
            ".messaging-tray-list__header button",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    time.sleep(0.5)
                    log.info("  Closed Messages panel.")
                    return
            except Exception:
                continue

        # JS fallback — click any button inside the Messages panel header
        page.evaluate("""
            () => {
                const panel = document.querySelector('fl-messaging-tray, .messaging-tray');
                if (panel) {
                    const btn = panel.querySelector('button, fl-button');
                    if (btn) btn.click();
                }
            }
        """)
        time.sleep(0.4)
    except Exception:
        pass


def _try_accept_agreement(page: Page) -> bool:
    """Auto-accept any NDA or project agreement blocking the bid form."""
    for sel in [
        'button:has-text("I Agree")',
        'button:has-text("I agree")',
        'button:has-text("Accept")',
        'button:has-text("Agree & Continue")',
        'button:has-text("Sign Agreement")',
        'button:has-text("Accept NDA")',
        'fl-button:has-text("I Agree")',
        'fl-button:has-text("Accept")',
        'fl-button:has-text("Agree")',
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.scroll_into_view_if_needed()
                human_delay(0.5, 1.0)
                btn.click()
                log.info("  Auto-accepted NDA/agreement — continuing to bid.")
                return True
        except Exception:
            continue
    # JS fallback
    try:
        found = page.evaluate("""
            () => {
                const btns = [...document.querySelectorAll('button, fl-button, [role="button"]')];
                const b = btns.find(el =>
                    /^(i agree|accept|agree|sign nda|accept nda|agree & continue)/i
                    .test((el.innerText || el.textContent || '').trim())
                );
                if (b) { b.click(); return true; }
                return false;
            }
        """)
        if found:
            log.info("  Auto-accepted NDA/agreement (JS) — continuing to bid.")
            return True
    except Exception:
        pass
    return False


def _find_and_click_bid_button(page: Page) -> bool:
    """Try every known method to find and click the Place Bid button."""
    import re as _re

    # First close the Messages panel — it covers the bid button in the sidebar
    _close_messages_panel(page)
    human_delay(0.5, 1.0)

    # Method 1 — Playwright get_by_role (works with Angular rendered buttons)
    try:
        btn = page.get_by_role("button", name=_re.compile(r"place bid|bid now|submit a bid", _re.I)).first
        if btn.is_visible(timeout=4000):
            btn.scroll_into_view_if_needed()
            human_delay(0.4, 0.9)
            btn.click()
            log.info("  Clicked bid button (role method).")
            return True
    except Exception:
        pass

    # Method 2 — fl-button Angular component (text filter)
    try:
        btn = page.locator("fl-button").filter(has_text=_re.compile(r"Place Bid|Bid Now", _re.I)).first
        if btn.is_visible(timeout=4000):
            btn.scroll_into_view_if_needed()
            human_delay(0.4, 0.9)
            btn.click()
            log.info("  Clicked bid button (fl-button method).")
            return True
    except Exception:
        pass

    # Method 3 — any button / anchor containing bid text
    for sel in [
        "button:has-text('Place Bid')",
        "button:has-text('Bid Now')",
        "a:has-text('Place Bid')",
        "a:has-text('Bid Now')",
        "[fltrackingid*='placeBid' i]",
        "[data-link='placeBid']",
        "app-project-view-action-bar button",
        ".action-bar button",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.scroll_into_view_if_needed()
                human_delay(0.4, 0.9)
                btn.click()
                log.info(f"  Clicked bid button ({sel}).")
                return True
        except Exception:
            continue

    # Method 4 — JavaScript: find any clickable element whose text says "place bid" or "bid now"
    try:
        found = page.evaluate("""
            () => {
                const all = [...document.querySelectorAll('button, fl-button, a, [role="button"]')];
                const btn = all.find(el => /place bid|bid now/i.test((el.innerText || el.textContent || '').trim()));
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            }
        """)
        if found:
            log.info("  Clicked bid button (JS fallback).")
            return True
    except Exception:
        pass

    # Nothing worked — log all buttons on the page so we can diagnose
    try:
        all_btns = page.evaluate("""
            () => {
                const els = [...document.querySelectorAll('button, fl-button, a[role="button"], [role="button"]')];
                return els.map(el => (el.innerText || el.textContent || '').trim().replace(/\\s+/g,' ')).filter(t => t.length > 0 && t.length < 80);
            }
        """)
        log.info(f"  DEBUG — all clickable text on page: {all_btns[:30]}")
    except Exception as e:
        log.info(f"  DEBUG — could not read buttons: {e}")

    return False


def place_bid(page: Page, job: dict, proposal: str, bid_amount: int) -> bool:
    """Navigate to job page and submit a bid. Returns True on success."""
    try:
        log.info(f"  Opening job: {job['title'][:60]}")
        # Wait for any in-progress navigation to settle before starting a new one
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        try:
            page.goto(job["url"], wait_until="domcontentloaded", timeout=25000)
        except Exception as nav_err:
            if "interrupted" in str(nav_err).lower() or "navigation" in str(nav_err).lower():
                # Page redirected mid-navigation — wait and check where we landed
                time.sleep(2)
                if page.url == job["url"] or job["url"].split("/")[-1] in page.url:
                    pass   # landed correctly despite the error
                else:
                    log.warning(f"  Navigation interrupted → landed on {page.url[:70]} — skipping")
                    return False
            else:
                raise

        # Wait for Angular to finish rendering the page
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        # Force Angular to re-render — triggers layout recalculation
        page.evaluate("""
            () => {
                window.dispatchEvent(new Event('resize'));
                window.dispatchEvent(new Event('scroll'));
                document.dispatchEvent(new Event('visibilitychange'));
            }
        """)
        human_delay(2, 4)

        # Simulate reading the job
        read_time = random.uniform(READING_TIME_MIN, READING_TIME_MAX)
        log.info(f"  Reading job for {read_time:.0f}s...")
        human_scroll(page, times=random.randint(2, 4))
        time.sleep(max(0, read_time - 10))   # remaining read time after scrolling

        # Scroll back up — bid button is usually near the top on desktop
        page.evaluate("window.scrollTo(0, 0)")
        human_delay(1, 2)

        # Detect account-gated pages — skip if any blocking message found
        try:
            block_reason = page.evaluate("""
                () => {
                    const body = document.body.innerText || document.body.textContent || '';
                    if (/upgrade to bid|buy bids|get bids|purchase bids/i.test(body))
                        return 'bid credits';
                    if (/open to select freelancers only|minimum balance|add funds/i.test(body))
                        return 'minimum balance required';
                    if (/preferred freelancer program|only preferred freelancers|pf badge/i.test(body))
                        return 'preferred freelancer only';
                    if (/membership required|upgrade your membership/i.test(body))
                        return 'membership required';
                    return null;
                }
            """)
            if block_reason:
                log.warning(f"  Skipping — {block_reason} (account restriction).")
                return False
        except Exception:
            pass

        # Try to click the bid button first — if it's visible the quiz gate is open
        clicked = _find_and_click_bid_button(page)

        if not clicked:
            # Step 1 — maybe an NDA/agreement is blocking — try to auto-accept it
            if _try_accept_agreement(page):
                human_delay(1.5, 2.5)
                clicked = _find_and_click_bid_button(page)

        if not clicked:
            # Step 2 — check if a real pre-screening quiz is the blocker
            try:
                has_quiz = page.evaluate("""
                    () => {
                        const all = [...document.querySelectorAll('button, a, fl-button')];
                        return all.some(el => {
                            const t = (el.innerText || el.textContent || '').trim();
                            return /view \\d+ (more )?question|answer \\d+ question|screening question|pre-screening/i.test(t);
                        });
                    }
                """)
                if has_quiz:
                    is_new = add_quiz_pending(job)
                    if is_new:
                        log.warning("  Action needed — alerting via Telegram.")
                        bmin = job["budget_min"]
                        bmax = job["budget_max"]
                        budget_str = f"${bmin:.0f}–${bmax:.0f}" if bmax else f"${bmin:.0f}"
                        send_telegram(
                            f"📝 <b>Action Needed!</b>\n\n"
                            f"<b>{job['title']}</b>\n"
                            f"💰 Budget: {budget_str}\n"
                            f"🔗 <a href='{job['url']}'>Open job — sign agreement or answer quiz</a>\n\n"
                            f"<i>Once done, the bot will automatically bid on the next scan.</i>"
                        )
                    else:
                        log.info("  Already alerted — waiting for you to complete it.")
                else:
                    try:
                        ss_path = f"bid_debug_{int(time.time())}.png"
                        page.screenshot(path=ss_path)
                        log.warning(f"  Could not find bid button — screenshot saved: {ss_path}")
                    except Exception:
                        log.warning("  Could not find bid button.")
            except Exception:
                log.warning("  Could not find bid button.")
            return False

        human_delay(1.5, 3)

        # Wait for bid form/panel to appear after clicking bid button
        human_delay(2, 3.5)

        # Fill bid amount — Freelancer's bid form uses fl-input internally
        amount_selectors = [
            'input[name="amount"]',
            'fl-input[label*="amount" i] input',
            'fl-input[label*="bid" i] input',
            'input[placeholder*="amount" i]',
            'input[placeholder*="bid" i]',
            '#bidAmount',
            'input[type="number"]',
        ]
        for sel in amount_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.triple_click()
                    human_delay(0.2, 0.5)
                    el.type(str(bid_amount), delay=random.randint(60, 150))
                    log.info(f"  Entered bid amount: ${bid_amount}")
                    break
            except:
                continue

        human_delay(0.8, 1.5)

        # Fill delivery period
        period_selectors = [
            'input[name="period"]',
            'fl-input[label*="day" i] input',
            'fl-input[label*="deliver" i] input',
            'input[placeholder*="day" i]',
            'input[placeholder*="deliver" i]',
        ]
        for sel in period_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.triple_click()
                    human_delay(0.2, 0.4)
                    days = DEFAULT_DELIVERY_DAYS + random.choice([-1, 0, 0, 1, 2])
                    el.type(str(days), delay=random.randint(60, 120))
                    break
            except:
                continue

        human_delay(1, 2)

        # Fill proposal — Freelancer uses fl-textarea which wraps a real <textarea>
        desc_selectors = [
            'fl-textarea textarea',
            'textarea[name="description"]',
            'textarea[name="proposal"]',
            'textarea[placeholder*="proposal" i]',
            'textarea[placeholder*="describe" i]',
            'textarea[placeholder*="cover letter" i]',
            'textarea[placeholder*="bid" i]',
            '.bid-form__description textarea',
            'div[contenteditable="true"]',   # rich text editor fallback
        ]
        typed = False
        for sel in desc_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.scroll_into_view_if_needed()
                    el.click()
                    human_delay(0.5, 1)
                    # Clear any existing text
                    el.evaluate("el => el.value = ''")
                    human_delay(0.2, 0.5)
                    for char in proposal:
                        el.type(char, delay=random.randint(28, 95))
                        if random.random() < 0.03:
                            time.sleep(random.uniform(0.3, 0.7))
                    typed = True
                    log.info(f"  Typed proposal ({len(proposal)} chars).")
                    break
            except:
                continue

        if not typed:
            log.warning("  Could not find proposal textarea.")
            return False

        human_delay(2, 4)

        # Submit — look for the submit button inside the bid form
        import re as _re2
        submitted = False
        try:
            btn = page.get_by_role("button", name=_re2.compile(r"place bid|submit bid|bid now", _re2.I)).last
            if btn.is_visible(timeout=3000):
                btn.scroll_into_view_if_needed()
                human_delay(0.5, 1.5)
                btn.click()
                submitted = True
                log.info("  ✓ Bid submitted!")
        except Exception:
            pass

        if not submitted:
            for sel in [
                "fl-button:has-text('Place Bid')",
                "fl-button:has-text('Submit Bid')",
                "fl-button[type='submit']",
                "button[type='submit']",
                "[data-link='submitBid']",
            ]:
                try:
                    btn = page.locator(sel).last
                    if btn.is_visible(timeout=2000):
                        btn.scroll_into_view_if_needed()
                        human_delay(0.5, 1.5)
                        btn.click()
                        submitted = True
                        log.info("  ✓ Bid submitted!")
                        break
                except Exception:
                    continue

        if not submitted:
            log.warning("  Submit button not found.")
            return False

        # Wait to confirm submission went through
        human_delay(3, 5)
        return True

    except PlaywrightTimeout:
        log.warning(f"  Timeout on job page: {job['url']}")
        return False
    except Exception as e:
        log.warning(f"  Error placing bid: {e}")
        return False


# ════════════════════════════════════════════════════════════════════════════
#  ALERT SYSTEM
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


def send_email(subject: str, body: str):
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        return
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_FROM, EMAIL_PASSWORD)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    except Exception as e:
        log.warning(f"Email error: {e}")


def load_bids_log() -> list:
    if BIDS_LOG_FILE.exists():
        try: return json.loads(BIDS_LOG_FILE.read_text(encoding="utf-8"))
        except: pass
    return []

def save_bids_log(bids: list):
    BIDS_LOG_FILE.write_text(json.dumps(bids, indent=2, ensure_ascii=False), encoding="utf-8")

def record_bid_log(job: dict, bid_amount: int, proposal: str, platform: str = "Freelancer"):
    bids = load_bids_log()
    bids.append({
        "id"          : job["id"],
        "platform"    : platform,
        "title"       : job["title"],
        "url"         : job["url"],
        "bid_amount"  : bid_amount,
        "budget_min"  : job["budget_min"],
        "budget_max"  : job["budget_max"],
        "posted"      : job["posted"],
        "bid_date"    : datetime.now().strftime("%Y-%m-%d %H:%M"),
        "proposal"    : proposal,
        "status"      : "pending",   # pending / won / lost
    })
    save_bids_log(bids)
    generate_dashboard(bids)

def generate_dashboard(bids: list):
    total   = len(bids)
    won     = sum(1 for b in bids if b["status"] == "won")
    lost    = sum(1 for b in bids if b["status"] == "lost")
    pending = sum(1 for b in bids if b["status"] == "pending")
    earned  = sum(b["bid_amount"] for b in bids if b["status"] == "won")

    rows = ""
    for b in reversed(bids):
        status_color = {"won": "#22c55e", "lost": "#ef4444", "pending": "#f59e0b"}.get(b["status"], "#888")
        status_badge = f'<span style="background:{status_color};color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600">{b["status"].upper()}</span>'
        rows += f"""
        <tr>
          <td>{b["bid_date"]}</td>
          <td><b>{b["platform"]}</b></td>
          <td><a href="{b["url"]}" target="_blank" style="color:#6366f1;text-decoration:none">{b["title"][:55]}</a></td>
          <td style="text-align:center">${b["bid_amount"]}</td>
          <td style="text-align:center">{status_badge}</td>
          <td style="text-align:center">
            <button onclick="setStatus('{b["id"]}','won')"   style="background:#22c55e;color:#fff;border:none;padding:3px 10px;border-radius:6px;cursor:pointer;margin:2px">✓ Won</button>
            <button onclick="setStatus('{b["id"]}','lost')"  style="background:#ef4444;color:#fff;border:none;padding:3px 10px;border-radius:6px;cursor:pointer;margin:2px">✗ Lost</button>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoApply Dashboard — @thefavs0</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Segoe UI',sans-serif; background:#0f0f1a; color:#e2e8f0; min-height:100vh; }}
  .header {{ background:linear-gradient(135deg,#6366f1,#8b5cf6); padding:30px 40px; }}
  .header h1 {{ font-size:26px; font-weight:700; letter-spacing:1px; }}
  .header p  {{ opacity:.8; margin-top:4px; font-size:14px; }}
  .stats {{ display:flex; gap:20px; padding:30px 40px; flex-wrap:wrap; }}
  .card {{ background:#1e1e2e; border-radius:14px; padding:22px 28px; flex:1; min-width:160px; border:1px solid #2a2a3d; }}
  .card .num  {{ font-size:36px; font-weight:800; margin-bottom:4px; }}
  .card .lbl  {{ font-size:13px; opacity:.6; text-transform:uppercase; letter-spacing:.5px; }}
  .card.green .num {{ color:#22c55e; }}
  .card.red   .num {{ color:#ef4444; }}
  .card.amber .num {{ color:#f59e0b; }}
  .card.blue  .num {{ color:#6366f1; }}
  .card.gold  .num {{ color:#fbbf24; }}
  .section {{ padding:0 40px 40px; }}
  .section h2 {{ font-size:18px; margin-bottom:16px; color:#a5b4fc; }}
  table {{ width:100%; border-collapse:collapse; background:#1e1e2e; border-radius:14px; overflow:hidden; border:1px solid #2a2a3d; }}
  th {{ background:#2a2a3d; padding:12px 16px; text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:.5px; color:#a5b4fc; }}
  td {{ padding:12px 16px; border-bottom:1px solid #2a2a3d; font-size:14px; vertical-align:middle; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:#252538; }}
  .refresh {{ display:inline-block; margin:0 40px 20px; padding:10px 22px; background:#6366f1; color:#fff; border-radius:8px; cursor:pointer; border:none; font-size:14px; }}
  .refresh:hover {{ background:#4f46e5; }}
  .ts {{ padding:0 40px 10px; font-size:12px; opacity:.4; }}
</style>
</head>
<body>
<div class="header">
  <h1>🚀 AutoApply Dashboard</h1>
  <p>Real-time bid tracker — @thefavs0</p>
</div>
<div class="stats">
  <div class="card blue">  <div class="num">{total}</div>   <div class="lbl">Total Bids</div></div>
  <div class="card amber"> <div class="num">{pending}</div> <div class="lbl">Pending</div></div>
  <div class="card green"> <div class="num">{won}</div>     <div class="lbl">Won</div></div>
  <div class="card red">   <div class="num">{lost}</div>    <div class="lbl">Lost</div></div>
  <div class="card gold">  <div class="num">${earned}</div> <div class="lbl">Earned</div></div>
</div>
<div class="section">
  <h2>📋 All Bids</h2>
  <table>
    <thead><tr>
      <th>Date</th><th>Platform</th><th>Job Title</th>
      <th style="text-align:center">My Bid</th>
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
  fetch('update_status.json?' + Date.now())
    .catch(() => {{}});
  // Write to localStorage so the page reflects the change immediately
  const key = 'status_' + id;
  localStorage.setItem(key, status);
  location.reload();
}}
// On load, apply any locally saved statuses
document.querySelectorAll('tr[data-id]').forEach(row => {{
  const s = localStorage.getItem('status_' + row.dataset.id);
  if (s) {{
    const badge = row.querySelector('.badge');
    if (badge) badge.textContent = s.toUpperCase();
  }}
}});
</script>
</body>
</html>"""

    DASHBOARD_FILE.write_text(html, encoding="utf-8")


def alert_bid_placed(job: dict, bid_amount: int, proposal: str):
    bmin = job["budget_min"]
    bmax = job["budget_max"]
    budget_str = f"{job['currency']}{bmin:.0f}–{bmax:.0f}" if bmax else f"{job['currency']}{bmin:.0f}"

    tg = (
        f"✅ <b>BID PLACED!</b>\n\n"
        f"<b>{job['title']}</b>\n"
        f"💰 Job budget: {budget_str}  |  My bid: ${bid_amount}\n"
        f"📅 Posted: {job['posted']}\n"
        f"🔗 <a href='{job['url']}'>View job</a>\n\n"
        f"<i>Waiting for client response...</i>"
    )
    send_telegram(tg)

    email_body = f"""BID PLACED — Freelancer.com
{'='*60}
TITLE    : {job['title']}
JOB URL  : {job['url']}
JOB BUDGET: {budget_str}
MY BID   : ${bid_amount}
POSTED   : {job['posted']}

PROPOSAL SENT:
{'-'*40}
{proposal}
"""
    send_email(f"[Bot] Bid placed: {job['title'][:55]}", email_body)


def alert_daily_summary(bids_placed: int, jobs_skipped: int):
    send_telegram(
        f"📊 <b>Daily Summary</b>\n"
        f"Bids placed today: <b>{bids_placed}</b>\n"
        f"Jobs skipped (budget/relevance): {jobs_skipped}\n"
        f"Limit: {MAX_BIDS_PER_DAY}/day"
    )


# ════════════════════════════════════════════════════════════════════════════
#  GET TELEGRAM CHAT ID
# ════════════════════════════════════════════════════════════════════════════

def get_chat_id():
    if not TELEGRAM_TOKEN:
        print("Fill in TELEGRAM_TOKEN first.")
        return
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            timeout=10
        ).json()
        updates = r.get("result", [])
        if not updates:
            print("No messages found. Send ANY message to your bot first, then run this again.")
            return
        for u in updates:
            chat = u.get("message", {}).get("chat", {})
            cid  = chat.get("id")
            name = chat.get("first_name", "?")
            if cid:
                print(f"\n✓ Your chat_id = {cid}  (name: {name})")
                print(f'  Set:  TELEGRAM_CHAT_ID = "{cid}"\n')
                return
    except Exception as e:
        print(f"Error: {e}")


# ════════════════════════════════════════════════════════════════════════════
#  MAIN BOT LOOP
# ════════════════════════════════════════════════════════════════════════════

def run():
    log.info("═" * 62)
    log.info("  Freelancer Auto-Apply Bot  v2.0")
    log.info(f"  Max bids/day : {MAX_BIDS_PER_DAY}")
    log.info(f"  Active hours : {ACTIVE_HOUR_START}:00 – {ACTIVE_HOUR_END}:00")
    log.info(f"  Gap between bids: {MIN_BID_GAP_MIN}–{MAX_BID_GAP_MIN} minutes")
    log.info(f"  Telegram : {'✓' if TELEGRAM_TOKEN else '✗ not set'}")
    log.info(f"  Email    : {'✓' if EMAIL_FROM else '✗ not set'}")
    log.info("═" * 62)

    if not BROWSER_PROFILE.exists():
        log.error("No browser profile found. Run:  python freelancer_bot.py --login  first.")
        sys.exit(1)

    seen  = load_seen()
    state = load_state()

    send_telegram(
        "🤖 <b>Freelancer Bot started.</b>\n"
        f"Will place up to {MAX_BIDS_PER_DAY} bids/day, {MIN_BID_GAP_MIN}–{MAX_BID_GAP_MIN} min apart."
    )

    with sync_playwright() as p:
        # --headless=new = Chrome's modern headless mode
        # Renders Angular/JS fully (unlike old --headless), avoids bot detection,
        # and needs no visible window — no minimising required.
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1,1",       # 1×1 pixel — on-screen so Angular renders fully
                "--window-position=0,0",   # at corner, NOT off-screen
                "--no-sandbox",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = context.new_page()
        # Viewport is full size — Chrome renders to this regardless of window size
        page.set_viewport_size({"width": 1280, "height": 800})
        page.add_init_script(STEALTH_SCRIPT)

        # Verify we're still logged in
        page.goto("https://www.freelancer.com/dashboard", wait_until="networkidle", timeout=30000)
        human_delay(2, 4)
        current_url = page.url.lower()
        if "login" in current_url or "signup" in current_url:
            log.error("Not logged in. Run:  python freelancer_bot.py --login  again.")
            send_telegram("⚠️ <b>Login needed.</b> Run: python freelancer_bot.py --login")
            context.close()
            sys.exit(1)

        log.info("✓ Logged in — running in background (no visible window).")
        capture_cookies(context)   # grab auth cookies so API calls work

        bids_this_run = 0
        session_bids  = 0   # counts bids in current 20-bid session

        while True:
            # ── Session limit — after 20 successful bids, cooldown 3 hrs then reset ──
            if session_bids >= MAX_BIDS_PER_DAY:
                cooldown = 3 * 3600
                log.info(f"✅ {MAX_BIDS_PER_DAY} bids placed this session. "
                         f"Cooling down for 3 hours before starting fresh session...")
                send_telegram(
                    f"✅ <b>{MAX_BIDS_PER_DAY} bids placed!</b>\n"
                    f"Bot cooling down for 3 hours, then starting a new session."
                )
                time.sleep(cooldown)
                session_bids = 0
                seen = set()   # reset seen jobs so fresh jobs are visible
                save_seen(seen)
                log.info("New session started — scanning for jobs again.")

            placed_this_scan = 0

            # ── Quiz re-alert on every scan until you answer them ────────
            quiz_jobs = load_quiz_pending()
            if quiz_jobs:
                job_list = "\n".join([f"• {j['title'][:55]}\n  🔗 {j['url']}" for j in quiz_jobs[:8]])
                send_telegram(
                    f"⏳ <b>ACTION NEEDED — {len(quiz_jobs)} quiz/agreement job(s) waiting!</b>\n\n"
                    f"{job_list}\n\n"
                    f"<i>Open each link, answer the quiz or sign the IP agreement. "
                    f"Bot will bid automatically on next scan.</i>"
                )

            # ── Retry quiz-pending jobs first (you answered the quiz) ─────
            if quiz_jobs:
                log.info(f"Retrying {len(quiz_jobs)} quiz-pending job(s)...")
                for qjob in quiz_jobs[:]:
                    if session_bids >= MAX_BIDS_PER_DAY:
                        break
                    log.info(f"  → Retrying quiz job: {qjob['title'][:55]}")
                    proposal   = generate_proposal(qjob)
                    bid_amount = calculate_bid_amount(qjob)
                    success    = place_bid(page, qjob, proposal, bid_amount)
                    if success:
                        remove_quiz_pending(qjob["id"])
                        state = record_bid(state)
                        placed_this_scan += 1
                        bids_this_run    += 1
                        session_bids     += 1
                        record_bid_log(qjob, bid_amount, proposal, platform="Freelancer")
                        alert_bid_placed(qjob, bid_amount, proposal)
                        log.info(f"  ✓ Quiz job bid placed — removed from pending.")
                        if session_bids < MAX_BIDS_PER_DAY:
                            random_bid_gap()
                    else:
                        # Check WHY it failed — remove if expired/locked/PF/closed
                        try:
                            body = page.inner_text("body")[:800].lower()
                            dead_signals = [
                                "sign up", "return home", "page not found",   # 404
                                "preferred freelancer program", "only preferred freelancers",  # PF
                                "project is closed", "no longer available", "bidding is closed",
                                "project has been awarded", "already awarded",
                                "bidding has ended", "project ended",
                            ]
                            if any(s in body for s in dead_signals):
                                log.info(f"  Job expired/closed/PF — removing from pending list.")
                                remove_quiz_pending(qjob["id"])
                            else:
                                log.info(f"  Quiz still pending (not answered yet) — will retry next scan.")
                        except Exception:
                            log.info(f"  Quiz still pending (not answered yet) — will retry next scan.")
                        human_delay(3, 6)

            # ── Scan for new jobs ─────────────────────────────────────────
            log.info(f"Scanning {len(SEARCH_KEYWORDS)} keywords for new jobs...")
            jobs = scan_jobs()
            new_jobs = [j for j in jobs if j["id"] not in seen]
            log.info(f"Found {len(jobs)} total, {len(new_jobs)} new")

            for job in new_jobs:
                # Mark as seen regardless of whether we bid
                seen.add(job["id"])
                save_seen(seen)

                # Re-check session limit mid-scan
                if session_bids >= MAX_BIDS_PER_DAY:
                    log.info("Session limit reached mid-scan — cooldown starting.")
                    break

                # Random skip (human behaviour)
                if random.random() < SKIP_PROBABILITY:
                    log.info(f"  ↷ Randomly skipping: {job['title'][:50]}")
                    continue

                # Place the bid
                proposal   = generate_proposal(job)
                bid_amount = calculate_bid_amount(job)

                log.info(f"  → Bidding ${bid_amount} on: {job['title'][:55]}")
                success = place_bid(page, job, proposal, bid_amount)

                if success:
                    state = record_bid(state)
                    placed_this_scan += 1
                    bids_this_run    += 1
                    session_bids     += 1
                    record_bid_log(job, bid_amount, proposal, platform="Freelancer")
                    alert_bid_placed(job, bid_amount, proposal)
                    save_session(context)

                    log.info(f"  Session progress: {session_bids}/{MAX_BIDS_PER_DAY} bids")

                    # Human-like gap before next bid (only after SUCCESS)
                    if session_bids < MAX_BIDS_PER_DAY:
                        random_bid_gap()
                else:
                    # Failed — Freelancer blocked it or button not found
                    # Just move on to the next job immediately, no gap
                    log.warning(f"  Bid failed (blocked/unavailable) — trying next job.")
                    human_delay(3, 6)

            log.info(f"Scan done. Placed {placed_this_scan} bid(s). "
                     f"Session: {session_bids}/{MAX_BIDS_PER_DAY}")

            if placed_this_scan == 0:
                # Track consecutive empty scans — if bids keep failing, monthly limit may be hit
                empty_streak = state.get("consecutive_empty_scans", 0) + 1
                state["consecutive_empty_scans"] = empty_streak
                save_state(state)
                if empty_streak == 4:
                    send_telegram(
                        "🚫 <b>Bids not going through for 4 scans in a row!</b>\n\n"
                        "You may have hit your Freelancer monthly bid limit.\n"
                        "Check your account — if bids are exhausted, the bot will keep finding "
                        "jobs but can't bid until your limit resets."
                    )
                wait = random.randint(60, 90)
                log.info(f"No bids placed — rescanning in {wait}s...\n")
            else:
                state["consecutive_empty_scans"] = 0
                save_state(state)
                # At least 1 successful bid — normal scan interval
                wait = SCAN_INTERVAL_MIN * 60 + random.randint(-60, 120)
                log.info(f"Next scan in {wait//60}m {wait%60}s...\n")
            time.sleep(wait)


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--get-chat-id" in args:
        get_chat_id()
        sys.exit(0)

    if "--login" in args:
        manual_login()
        sys.exit(0)

    if "--clear-quiz" in args:
        save_quiz_pending([])
        print("✓ Quiz pending list cleared.")
        sys.exit(0)

    run()
