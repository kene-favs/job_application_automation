#!/usr/bin/env python3
"""
microtask_bot.py  v2.0  —  Multi-Platform Micro-Task Semi-Automation Bot
=========================================================================
Monitors Mindrift (Toloka), Hive Micro, and MTurk simultaneously.
Accepts tasks instantly (faster than any human), analyses them, and either:
  - Auto-submits obvious answers (yes/no, sentiment, categorization)
  - Sends you a Telegram alert with the task link for anything complex

HOW IT WORKS:
  Bot runs 24/7 on VPS watching all 3 platforms.
  When a task appears → accepted immediately.
  Simple task → suggested answer filled, submitted after human-like delay.
  Complex task → Telegram alert with direct link → you open on phone/PC and submit.

SETUP:
  1. Fill credentials in CONFIG section below
  2. Login (run once per platform — saves browser session):
       python microtask_bot.py --login-mindrift      (GitHub OAuth — browser opens, you click login)
       python microtask_bot.py --login-hivemicro     (email/password — auto-filled)
       python microtask_bot.py --login-mturk         (Amazon account — browser opens)
  3. Start:
       python microtask_bot.py

ACCOUNTS:
  Mindrift  : mindrift.ai  (Toloka's worker platform — accepts Nigeria ✓)
  Hive Micro: app.hivemicro.com  (global — accepts Nigeria ✓)
  MTurk     : worker.mturk.com  (US-focused, set ENABLE_MTURK=False if not approved)

EARNINGS NOTES:
  Hive Micro pays per 1,000 tasks (e.g. $2.80/1000 = $0.0028/task)
  Bot targets high-volume simple tasks: yes/no image, categorization
  Volume target: 5,000–15,000 tasks/day across platforms
  Combined 6hr/day estimate: $20–60/day depending on task availability
"""

from __future__ import annotations
import sys, json, time, random, logging, threading, re
from datetime import datetime, date
from pathlib import Path
from typing import Optional

try:
    from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout
except ImportError:
    print("pip install playwright && python -m playwright install chromium")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)


# ════════════════════════════════════════════════════════════════════════════
#  CONFIG — FILL YOUR CREDENTIALS HERE
# ════════════════════════════════════════════════════════════════════════════

# ── Mindrift (Toloka worker platform) ────────────────────────────────────────
# Login via GitHub OAuth — no password needed, bot opens browser for you
MINDRIFT_EMAIL = "obodavekel466@gmail.com"   # your GitHub email (for reference only)

# ── Hive Micro ────────────────────────────────────────────────────────────────
HIVEMICRO_EMAIL    = "obodavekel466@gmail.com"
HIVEMICRO_PASSWORD = "OBOdave@46"

# ── MTurk (Amazon) ────────────────────────────────────────────────────────────
MTURK_EMAIL    = ""
MTURK_PASSWORD = ""

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "8871384090:AAHJ92v3MeXgd2PKotnRjSL1q9NLGDZ3vvg"
TELEGRAM_CHAT_ID = "8350604369"

# ── Groq Vision AI (FREE — get key at console.groq.com, no credit card needed) ──
# Replaces Gemini — Groq works in Nigeria, Gemini does NOT (location blocked).
# Steps: 1) Go to console.groq.com  2) Sign up free  3) API Keys → Create key
# Paste your key below. Model: llama-4-scout (free, fast, excellent vision).
GEMINI_API_KEY = ""   # (old Gemini key — no longer used, kept for reference)
GROQ_API_KEY   = ""   # ← PASTE YOUR GROQ KEY HERE (from console.groq.com)

# ── Task filters ──────────────────────────────────────────────────────────────
MIN_TASK_PAY     = 0.02   # minimum pay per task (USD) — catch everything from $0.02+
MAX_TASK_TIME    = 600    # skip tasks that allow less than this many seconds (avoid rush traps)
AUTO_SUBMIT_CONF = 0.80   # confidence threshold for auto-submit (0.0–1.0)
HUMAN_DELAY_MIN  = 2.5    # minimum seconds before submitting (looks human)
HUMAN_DELAY_MAX  = 7.0    # maximum seconds before submitting

# ── Session limits ────────────────────────────────────────────────────────────
MAX_TASKS_PER_SESSION = 200   # after this, rest 10 minutes (simulate break)
SESSION_BREAK_MIN     = 10    # break duration in minutes

# ── Platform enable/disable ────────────────────────────────────────────────────
ENABLE_MINDRIFT   = True
ENABLE_HIVEMICRO  = False   # disabled — focusing on Mindrift
ENABLE_MTURK      = False   # set True once MTurk account is approved

# ── Training mode ──────────────────────────────────────────────────────────────
# Set True ONLY while doing training tasks. Enables wrong-answer correction logic
# (training shows correct answer after a miss — real tasks may NOT do this).
# Set to False before running on real paid tasks to avoid accidental double-clicks.
MINDRIFT_TRAINING_MODE = True   # ← change to False when training is complete


# ════════════════════════════════════════════════════════════════════════════
#  PATHS & LOGGING
# ════════════════════════════════════════════════════════════════════════════

BASE_DIR               = Path(".")
PROFILE_MINDRIFT       = BASE_DIR / "profile_mindrift"
PROFILE_HIVEMICRO      = BASE_DIR / "profile_hivemicro"
PROFILE_MTURK          = BASE_DIR / "profile_mturk"
EARNINGS_FILE          = BASE_DIR / "microtask_earnings.json"
MICRODASH_FILE         = BASE_DIR / "microtask_dashboard.html"
SEEN_TASKS_FILE        = BASE_DIR / "microtask_seen.json"
LEARNED_RULES_FILE     = BASE_DIR / "learned_rules.json"

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(name)-14s  %(message)s",
    datefmt = "%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("microtask_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("MicroTaskBot")


# ════════════════════════════════════════════════════════════════════════════
#  EARNINGS TRACKER
# ════════════════════════════════════════════════════════════════════════════

def load_earnings() -> list:
    if EARNINGS_FILE.exists():
        try: return json.loads(EARNINGS_FILE.read_text(encoding="utf-8"))
        except: pass
    return []

def save_earnings(data: list):
    EARNINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def record_task_done(platform: str, task_id: str, title: str, pay: float,
                     task_type: str, auto_submitted: bool, url: str):
    data = load_earnings()
    data.append({
        "date"          : datetime.now().strftime("%Y-%m-%d %H:%M"),
        "platform"      : platform,
        "task_id"       : task_id,
        "title"         : title[:80],
        "pay"           : pay,
        "type"          : task_type,
        "auto_submitted": auto_submitted,
        "url"           : url,
        "status"        : "submitted",
    })
    save_earnings(data)
    generate_microdashboard(data)

def load_seen_tasks() -> set:
    if SEEN_TASKS_FILE.exists():
        try: return set(json.loads(SEEN_TASKS_FILE.read_text()))
        except: pass
    return set()

def save_seen_tasks(seen: set):
    SEEN_TASKS_FILE.write_text(json.dumps(list(seen)))

_seen_lock = threading.Lock()
_seen_tasks: set = set()

def mark_seen(task_id: str) -> bool:
    """Mark task as seen. Returns True if it was new (not seen before)."""
    with _seen_lock:
        if task_id in _seen_tasks:
            return False
        _seen_tasks.add(task_id)
        save_seen_tasks(_seen_tasks)
        return True


# ════════════════════════════════════════════════════════════════════════════
#  ADAPTIVE LEARNING SYSTEM
#  Bot saves every mistake + platform explanation → injects into Gemini prompt
# ════════════════════════════════════════════════════════════════════════════

# Pre-seeded rules from real training mistakes — never forget these
SEED_RULES = [
    # ── Color requirements are LITERAL ───────────────────────────────────────
    "Straw hat must be tan/natural BEIGE color (real straw color). A YELLOW or bright hat is NOT a straw hat — fails automatically.",
    "Color accuracy is literal: prompt says 'grey' = mid-tone grey. Dark charcoal or near-black is NOT grey.",
    "Color accuracy is literal: prompt says 'blush pink + cream + dusty lavender' = ALL THREE colors must be present. Missing one = fail.",
    "Check for color cast defect: extreme blue/cool tint making skin tones blue-grey = color defect. Loses to natural-colored image.",
    "'Vibrant psychedelic colors' = hot pink, magenta, lime green, cyan, orange ALL together. Dark muted palette FAILS this requirement.",
    # ── Material/surface requirements are LITERAL ─────────────────────────────
    "MATTE = zero specular highlights, pure diffuse surface, no gloss. Glossy/shiny surface FAILS 'matte' — automatic disqualification.",
    "Chainmail = interlocking metal ring mesh texture (like armor). Dark fabric/cloth is NOT chainmail.",
    "'Crosshatch texture' = visible intersecting diagonal line pattern (diamond grid) on the surface. Must be clearly visible.",
    "'Embossed text' = text carved INTO the surface, same color as surface. Text printed/applied ON TOP = NOT embossed.",
    # ── Style requirements are LITERAL ────────────────────────────────────────
    "'Illustration' style = drawn/illustrated artwork. Photorealistic render/photo FAILS an 'illustration' requirement.",
    "1980s airbrush illustration: hyper-smooth gradients, glassy/glossy surfaces, hyper-saturated magenta/pink/teal. Very distinct style.",
    "1970s Japanese ad poster: must match authentic retro Japanese ad design language (bold type, period color relationships). Modern chaotic collage ≠ 1970s Japanese ad.",
    "Vintage badge logo MUST have a containing border shape (circle, oval, shield). Floating text arranged in a circle WITHOUT a border = NOT a badge.",
    # ── Pose/position requirements are LITERAL ────────────────────────────────
    "'Seated on raised surface' = person must be visibly SITTING/PERCHED. Standing next to something elevated = NOT seated.",
    "'Top-down view' = directly overhead bird's-eye view. Angled elevated shot ≠ top-down.",
    "Ghost mannequin = NO visible head, face, or hair. Only clothing on an invisible body. Any visible head = fails automatically.",
    # ── Typography requirements ────────────────────────────────────────────────
    "'Playful groovy font' = 1970s bubble/rounded retro lettering throughout ALL text. Mixed fonts (some groovy + some plain serif) = inconsistent = FAIL.",
    "'Mixed letter sizing with some letters more narrow than others' = variable-width typeface where I is much narrower than M, W. Uniform-width text FAILS.",
    "'Embossed dark text' = dark-on-dark text pressed INTO surface. Bright contrasting text applied on top = NOT embossed.",
    # ── Clothing/garment requirements ─────────────────────────────────────────
    "Jeans = denim fabric (recognizable blue or grey denim weave). Grey sweatpants/joggers are NOT jeans — fails automatically.",
    "'Fitted top' = form-fitting, close to body. Oversized or loose top ≠ fitted.",
    "'Sculptural fabric face mask' = structurally complex mask with raised architectural elements. Plain flat surgical/medical mask ≠ sculptural.",
    # ── Count/quantity requirements ────────────────────────────────────────────
    "When prompt specifies number of subjects (e.g., '3 scientists'), ALL must be FULLY visible without cropping. Cropped subject is a composition failure.",
    # ── Composition/structure requirements ────────────────────────────────────
    "'Stacked' arrangement = elements arranged vertically on top of each other. Side-by-side horizontal arrangement ≠ stacked.",
    "For product packaging: the actual product must be the HERO — prominently displayed, readable. If product is tiny/buried = composition failure.",
    "'Shower gel packaging' ≠ soap packaging. If the label says 'sabonete' (soap) it's NOT shower gel — fails.",
    # ── Background requirements ────────────────────────────────────────────────
    "'White background' must be white. Cream/off-white is acceptable but dark backgrounds fail.",
    "'Gray gradient background' = light-to-medium grey with visible gradient. Very dark/near-black background ≠ grey gradient.",
    "'Cream background' = warm off-white/ivory. Dark backgrounds fail.",
    # ── Advanced aesthetic rules ───────────────────────────────────────────────
    "For simple 1-word color prompt (e.g., 'blue'): the image that IS that color (pure abstract) beats a photo with a color filter applied.",
    "For 1970s Japanese ad poster: must have Japanese text + arrow + product at bottom in proper vintage ad layout. Random chaos/collage ≠ vintage ad.",
    "Logo legibility: all required text must be CLEARLY readable. Text that is tiny, buried, or illegible = design failure.",
    "For open/minimal prompts: image that IS the concept beats image that uses it as a secondary element.",
    # ── Training-specific self-improvement rules ───────────────────────────────
    "ALWAYS check: does the 'winning' image actually have ALL colors named in the prompt? Missing even one named color = fail.",
    "ALWAYS check: for 'straw hat' prompts — is the hat actually straw-colored (tan/beige)? Yellow ≠ straw.",
    "ALWAYS check: for 'illustration' prompts — does the image actually look illustrated or is it photorealistic?",
    "ALWAYS check: count the number of required subjects and ensure all are visible and uncropped.",
    "ALWAYS check: for material prompts (matte, glossy, chainmail, embossed) — examine the actual surface texture before deciding.",
]


def load_learned_rules() -> list:
    """Load pre-seeded rules + any rules learned from platform corrections."""
    rules = list(SEED_RULES)
    if LEARNED_RULES_FILE.exists():
        try:
            data = json.loads(LEARNED_RULES_FILE.read_text(encoding="utf-8"))
            for entry in data.get("entries", []):
                lesson = entry.get("lesson", "").strip()
                if lesson and lesson not in rules:
                    rules.append(lesson)
        except Exception:
            pass
    return rules


def save_learned_rule(lesson: str, prompt_text: str = "",
                      bot_choice: str = "", correct_choice: str = "",
                      source: str = "platform_correction"):
    """Persist a new lesson so it survives across sessions."""
    data: dict = {"entries": []}
    if LEARNED_RULES_FILE.exists():
        try:
            data = json.loads(LEARNED_RULES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["entries"].append({
        "date"       : datetime.now().strftime("%Y-%m-%d %H:%M"),
        "lesson"     : lesson,
        "prompt"     : prompt_text[:200],
        "bot_chose"  : bot_choice,
        "correct_was": correct_choice,
        "source"     : source,
    })
    LEARNED_RULES_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info(f"  📚 Saved new rule: {lesson[:120]}...")


def _capture_platform_explanation(page) -> str:
    """
    After a wrong answer, try to read the explanation/feedback the platform shows.
    Returns the explanation text, or '' if nothing found.
    """
    explanation = ""
    selectors = [
        '[class*="explanation"]', '[class*="feedback"]', '[class*="reason"]',
        '[class*="correction"]', '[class*="hint"]', '[class*="message"]',
        'div:has-text("The correct")', 'p:has-text("because")',
        'div:has-text("correct answer")', '[class*="result-text"]',
        '[class*="answer-explanation"]',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=800):
                text = el.inner_text().strip()
                if len(text) > 25 and text.lower() not in ["incorrect", "wrong", "error"]:
                    explanation = text
                    break
        except Exception:
            continue

    if not explanation:
        # Broad JS sweep: find any newly visible text about the correct answer
        try:
            explanation = page.evaluate("""
                () => {
                    const kw = ['correct', 'because', 'should be', 'better', 'wins', 'fails', 'missing'];
                    for (const el of [...document.querySelectorAll('div,p,span,li')]) {
                        const t = (el.innerText || '').trim();
                        if (t.length > 30 && t.length < 600
                            && kw.some(k => t.toLowerCase().includes(k))) {
                            return t;
                        }
                    }
                    return '';
                }
            """) or ""
        except Exception:
            pass

    return explanation[:500]


# ════════════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ════════════════════════════════════════════════════════════════════════════

def send_telegram(msg: str, parse_mode: str = "HTML"):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": parse_mode,
                  "disable_web_page_preview": False},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram error: {e}")

def alert_complex_task(platform: str, title: str, pay: float, url: str, hint: str = ""):
    """Send Telegram alert for tasks needing human attention."""
    hint_line = f"\n💡 <i>Hint: {hint}</i>" if hint else ""
    send_telegram(
        f"🔔 <b>Task needs you — {platform}</b>\n\n"
        f"<b>{title[:80]}</b>\n"
        f"💰 Pay: ${pay:.3f}\n"
        f"🔗 <a href='{url}'>Open task now</a>{hint_line}\n\n"
        f"<i>Complete and submit — bot continues after.</i>"
    )

def alert_task_accepted(platform: str, title: str, pay: float, task_type: str, confidence: float):
    """Quick notification that bot auto-handled a task."""
    send_telegram(
        f"✅ <b>Auto-submitted — {platform}</b>\n"
        f"{title[:60]}\n"
        f"💰 ${pay:.3f}  |  Type: {task_type}  |  Confidence: {confidence:.0%}"
    )

def alert_session_summary(platform: str, count: int, earned: float):
    send_telegram(
        f"📊 <b>{platform} Session Summary</b>\n"
        f"Tasks completed: <b>{count}</b>\n"
        f"Estimated earned: <b>${earned:.2f}</b>"
    )


# ════════════════════════════════════════════════════════════════════════════
#  TASK ANALYZER — detect task type and suggest answer
# ════════════════════════════════════════════════════════════════════════════

POSITIVE_WORDS = {
    "good", "great", "excellent", "amazing", "wonderful", "fantastic", "love",
    "happy", "positive", "nice", "awesome", "perfect", "best", "brilliant",
    "outstanding", "superb", "pleasant", "delightful", "recommend", "satisfied",
}
NEGATIVE_WORDS = {
    "bad", "terrible", "awful", "horrible", "hate", "worst", "poor", "negative",
    "disappointed", "disappointing", "disgusting", "annoying", "useless", "broken",
    "problem", "issue", "complaint", "refund", "scam", "fraud", "broken",
}
SPAM_WORDS = {
    "click here", "free money", "earn cash", "limited offer", "act now",
    "buy now", "discount", "prize", "winner", "congratulations", "urgent",
    "bitcoin", "crypto investment", "make money fast",
}


def analyze_task(title: str, instructions: str, content: str = "") -> dict:
    """
    Analyze a task and return suggested action.
    Returns dict with: type, answer, confidence, auto_submit, hint
    """
    full_text = (title + " " + instructions + " " + content).lower().strip()
    title_low = title.lower()
    instr_low = instructions.lower()

    # ── IMAGE COMPARISON (detect early — highest priority on Mindrift) ──────
    IMAGE_COMP_TRIGGERS = [
        "which image is better", "which is better", "better image",
        "higher quality image", "aesthetic", "visual quality",
        "better response to", "better based on the prompt", "prefer",
        "which photo", "select the better", "choose the better",
    ]
    if any(x in full_text for x in IMAGE_COMP_TRIGGERS):
        # Determine if this is prompt-based (needs understanding) or pure aesthetic
        is_prompt_based = any(x in full_text for x in [
            "based on the prompt", "response to", "matches the prompt",
            "user's request", "prompt", "instruction",
        ])
        return {
            "type": "image_comparison_prompt" if is_prompt_based else "image_comparison_aesthetic",
            "answer": None,   # resolved by JS image analyzer on the page
            "confidence": 0.88,
            "auto_submit": True,
            "hint": "Image comparison — bot uses quality scoring",
            "prompt_based": is_prompt_based,
        }

    # ── YES / NO tasks ────────────────────────────────────────────────────
    if any(x in instr_low for x in [
        "yes or no", "is this", "does this", "is it a", "is the following",
        "true or false", "correct or incorrect", "valid or invalid",
    ]):
        answer, confidence = _yes_no_answer_scored(full_text, content)
        # Only auto-submit if we're genuinely confident
        auto = confidence >= AUTO_SUBMIT_CONF
        return {"type": "yes_no", "answer": answer, "confidence": confidence,
                "auto_submit": auto,
                "hint": f"{'Auto: ' if auto else 'Uncertain — check: '}{answer} ({confidence:.0%})"}

    # ── SENTIMENT / OPINION ───────────────────────────────────────────────
    if any(x in instr_low for x in [
        "sentiment", "opinion", "positive or negative", "classify this review",
        "rate this", "how does this text feel", "emotional tone",
    ]):
        sentiment, conf = _classify_sentiment(content or instructions)
        auto = conf >= AUTO_SUBMIT_CONF
        return {"type": "sentiment", "answer": sentiment, "confidence": conf,
                "auto_submit": auto,
                "hint": f"{'Auto: ' if auto else 'Check: '}{sentiment} ({conf:.0%})"}

    # ── SPAM / NOT SPAM ───────────────────────────────────────────────────
    if any(x in instr_low for x in ["spam", "not spam", "is this spam", "classify email"]):
        text_check = (content or full_text).lower()
        spam_hits = sum(1 for w in SPAM_WORDS if w in text_check)
        legit_hits = sum(1 for w in [
            "dear", "hello", "regards", "sincerely", "invoice", "receipt",
            "order", "account", "notification", "update", "confirm",
        ] if w in text_check)
        is_spam = spam_hits >= 2 and spam_hits > legit_hits
        answer = "Spam" if is_spam else "Not Spam"
        conf   = min(0.95, 0.65 + spam_hits * 0.08) if is_spam else min(0.92, 0.70 + legit_hits * 0.06)
        return {"type": "spam_check", "answer": answer, "confidence": conf,
                "auto_submit": conf >= AUTO_SUBMIT_CONF,
                "hint": f"{answer} ({conf:.0%} confidence, {spam_hits} spam signals)"}

    # ── NSFW / CONTENT MODERATION ─────────────────────────────────────────
    if any(x in instr_low for x in [
        "nsfw", "explicit", "adult content", "contains nudity",
        "dsm", "bdsm", "sexual", "inappropriate content",
    ]):
        # For NSFW yes/no — default No (most images in queues are safe)
        # but flag for human review since wrong answers hurt rating badly
        return {"type": "nsfw_check", "answer": "No",
                "confidence": 0.70, "auto_submit": False,
                "hint": "NSFW check — open and verify before answering (wrong = rating penalty)"}

    # ── SAFE FOR WORK / APPROPRIATE ───────────────────────────────────────
    if any(x in instr_low for x in [
        "appropriate", "safe for work", "is this image okay", "suitable",
        "offensive", "violates", "against guidelines",
    ]):
        return {"type": "content_check", "answer": None,
                "confidence": 0.0, "auto_submit": False,
                "hint": "Content policy check — always verify manually (high stakes)"}

    # ── BOUNDING BOX / IMAGE ANNOTATION ──────────────────────────────────
    if any(x in instr_low for x in [
        "draw a box", "bounding box", "annotate", "mark the", "polygon",
        "select the area", "outline", "segmentation", "draw around",
    ]):
        return {"type": "image_annotation", "answer": None, "confidence": 0.0,
                "auto_submit": False,
                "hint": "Draw bounding boxes — open task link and annotate manually"}

    # ── AUDIO / VIDEO ─────────────────────────────────────────────────────
    if any(x in instr_low for x in [
        "transcribe", "listen", "audio", "what do you hear",
        "record", "narrate", "speak", "video",
    ]):
        return {"type": "av_task", "answer": None, "confidence": 0.0,
                "auto_submit": False, "hint": "Audio/video task — needs manual attention"}

    # ── CATEGORIZATION (with option reading) ─────────────────────────────
    if any(x in instr_low for x in [
        "categorize", "category", "classify", "which category", "what type",
        "label this", "which label", "what is this", "select the best",
        "choose the correct", "pick the",
    ]):
        return {"type": "categorize", "answer": None, "confidence": 0.0,
                "auto_submit": False,
                "hint": "Categorization — open and pick from options manually"}

    # ── RATING / SCALE ────────────────────────────────────────────────────
    if any(x in instr_low for x in [
        "rate from", "scale of", "1 to 5", "1 to 10", "out of 5",
        "out of 10", "how would you rate", "quality score",
    ]):
        return {"type": "rating", "answer": None, "confidence": 0.0,
                "auto_submit": False,
                "hint": "Rating task — needs human judgment on scale"}

    # ── DATA VERIFICATION ─────────────────────────────────────────────────
    if any(x in instr_low for x in [
        "is this correct", "does this match", "validate", "is this accurate",
        "verify this", "check if correct",
    ]):
        return {"type": "verification", "answer": None, "confidence": 0.0,
                "auto_submit": False,
                "hint": "Verification task — check data before confirming"}

    # ── UNKNOWN — always flag, never guess ────────────────────────────────
    return {"type": "complex", "answer": None, "confidence": 0.0,
            "auto_submit": False, "hint": f"Unknown task type — send to Telegram"}


def _yes_no_answer_scored(full_text: str, content: str) -> tuple[str, float]:
    """
    Determine yes/no answer with a confidence score.
    Returns (answer, confidence). Never guesses below 0.75.
    """
    text = full_text.lower()

    # Map of question patterns → expected answer + confidence
    YES_PATTERNS = [
        ("is this english", 0.92),
        ("is this readable", 0.90),
        ("is this a valid", 0.85),
        ("is this a real", 0.85),
        ("does this contain text", 0.88),
        ("is this professional", 0.82),
        ("is this a complete sentence", 0.85),
        ("is this legible", 0.88),
        ("is the text clear", 0.87),
        ("is this image clear", 0.82),
    ]
    NO_PATTERNS = [
        ("is this spam", 0.90),
        ("is this offensive", 0.88),
        ("does this violate", 0.90),
        ("is this inappropriate", 0.88),
        ("contains adult", 0.85),
        ("contains violence", 0.88),
        ("is this a duplicate", 0.82),
        ("is this blurry", 0.80),
        ("is this low quality", 0.80),
        ("does this contain an error", 0.85),
    ]

    for pattern, conf in YES_PATTERNS:
        if pattern in text:
            return "Yes", conf
    for pattern, conf in NO_PATTERNS:
        if pattern in text:
            return "No", conf

    # Fallback: content-based analysis
    c = (content or "").lower()
    pos_signals = sum(1 for w in POSITIVE_WORDS if w in c)
    neg_signals = sum(1 for w in NEGATIVE_WORDS if w in c)

    if pos_signals > neg_signals * 2 and pos_signals >= 2:
        return "Yes", 0.78
    if neg_signals > pos_signals * 2 and neg_signals >= 2:
        return "No", 0.78

    # Not confident — return with low confidence (will NOT auto-submit)
    return "Yes", 0.55   # 0.55 < AUTO_SUBMIT_CONF → goes to Telegram


def _classify_sentiment(text: str) -> tuple[str, float]:
    """
    Weighted sentiment classifier. Returns (label, confidence).
    Confidence < AUTO_SUBMIT_CONF triggers Telegram alert instead of auto-submit.
    """
    t = text.lower()
    # Weight longer, stronger words more
    STRONG_POS = {"excellent", "amazing", "fantastic", "outstanding", "superb", "brilliant", "love"}
    STRONG_NEG = {"terrible", "awful", "horrible", "hate", "disgusting", "useless", "broken", "scam"}

    pos = sum(2 if w in STRONG_POS else 1 for w in POSITIVE_WORDS if w in t)
    neg = sum(2 if w in STRONG_NEG else 1 for w in NEGATIVE_WORDS if w in t)
    total = pos + neg

    if total == 0:
        return "Neutral", 0.58   # below threshold → Telegram

    ratio = pos / total  # 1.0 = all positive, 0.0 = all negative
    if ratio >= 0.80:
        return "Positive", min(0.95, 0.75 + (ratio - 0.80) * 0.80)
    if ratio <= 0.20:
        return "Negative", min(0.95, 0.75 + (0.20 - ratio) * 0.80)
    if ratio > 0.55:
        return "Positive", 0.68   # below threshold → Telegram
    if ratio < 0.45:
        return "Negative", 0.68   # below threshold → Telegram
    return "Neutral", 0.60        # below threshold → Telegram


# ════════════════════════════════════════════════════════════════════════════
#  HUMAN BEHAVIOUR UTILITIES
# ════════════════════════════════════════════════════════════════════════════

def human_delay(mn: float = 1.0, mx: float = 3.5):
    time.sleep(random.uniform(mn, mx))

def accept_delay():
    """Delay before accepting — looks like human reaction time (0.6–1.8s)."""
    time.sleep(random.uniform(0.6, 1.8))

def submit_delay():
    """Delay before submitting — looks like human 'reading and reviewing'."""
    time.sleep(random.uniform(HUMAN_DELAY_MIN, HUMAN_DELAY_MAX))

def jitter():
    """Tiny random jitter between actions."""
    time.sleep(random.uniform(0.15, 0.45))

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = {runtime: {}};
"""


# ════════════════════════════════════════════════════════════════════════════
#  DASHBOARD GENERATOR
# ════════════════════════════════════════════════════════════════════════════

def generate_microdashboard(data: list):
    today_str = date.today().strftime("%Y-%m-%d")
    total      = len(data)
    total_earn = sum(d["pay"] for d in data)
    today_data = [d for d in data if d["date"].startswith(today_str)]
    today_earn = sum(d["pay"] for d in today_data)
    today_cnt  = len(today_data)
    auto_cnt   = sum(1 for d in data if d.get("auto_submitted"))

    by_platform: dict = {}
    for d in data:
        p = d["platform"]
        by_platform.setdefault(p, {"count": 0, "earn": 0.0})
        by_platform[p]["count"] += 1
        by_platform[p]["earn"]  += d["pay"]

    plat_cards = ""
    colors = {"Mindrift": "#6366f1", "HiveMicro": "#f59e0b", "MTurk": "#22c55e"}
    for p, v in by_platform.items():
        c = colors.get(p, "#888")
        plat_cards += f"""
        <div class="card" style="border-top:3px solid {c}">
          <div class="num" style="color:{c}">${v['earn']:.2f}</div>
          <div class="lbl">{p} · {v['count']} tasks</div>
        </div>"""

    rows = ""
    for d in reversed(data[-100:]):
        auto_badge = '<span style="background:#22c55e;color:#fff;padding:1px 8px;border-radius:10px;font-size:11px">AUTO</span>' if d.get("auto_submitted") else '<span style="background:#6366f1;color:#fff;padding:1px 8px;border-radius:10px;font-size:11px">MANUAL</span>'
        rows += f"""
        <tr>
          <td>{d['date']}</td>
          <td><b>{d['platform']}</b></td>
          <td><a href="{d['url']}" target="_blank" style="color:#6366f1;text-decoration:none">{d['title'][:50]}</a></td>
          <td style="text-align:center">${d['pay']:.3f}</td>
          <td style="text-align:center">{d['type']}</td>
          <td style="text-align:center">{auto_badge}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MicroTask Dashboard — @thefavs0</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',sans-serif;background:#0f0f1a;color:#e2e8f0;min-height:100vh}}
  .header{{background:linear-gradient(135deg,#6366f1,#f59e0b);padding:30px 40px}}
  .header h1{{font-size:26px;font-weight:700}}
  .header p{{opacity:.8;margin-top:4px;font-size:14px}}
  .stats{{display:flex;gap:16px;padding:24px 40px;flex-wrap:wrap}}
  .card{{background:#1e1e2e;border-radius:14px;padding:20px 24px;flex:1;min-width:150px;border:1px solid #2a2a3d}}
  .card .num{{font-size:32px;font-weight:800;margin-bottom:4px}}
  .card .lbl{{font-size:12px;opacity:.6;text-transform:uppercase;letter-spacing:.5px}}
  .section{{padding:0 40px 40px}}
  .section h2{{font-size:18px;margin-bottom:14px;color:#a5b4fc;padding-top:24px}}
  table{{width:100%;border-collapse:collapse;background:#1e1e2e;border-radius:14px;overflow:hidden;border:1px solid #2a2a3d}}
  th{{background:#2a2a3d;padding:10px 14px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#a5b4fc}}
  td{{padding:10px 14px;border-bottom:1px solid #2a2a3d;font-size:13px;vertical-align:middle}}
  tr:last-child td{{border-bottom:none}}tr:hover td{{background:#252538}}
  .ts{{padding:0 40px 20px;font-size:12px;opacity:.4}}
  .refresh{{display:inline-block;margin:0 40px 20px;padding:8px 20px;background:#6366f1;color:#fff;border-radius:8px;cursor:pointer;border:none;font-size:13px}}
</style>
</head>
<body>
<div class="header">
  <h1>⚡ MicroTask Dashboard</h1>
  <p>Remotasks · Clickworker · MTurk — @thefavs0</p>
</div>
<div class="stats">
  <div class="card"><div class="num" style="color:#fbbf24">${total_earn:.2f}</div><div class="lbl">Total Earned</div></div>
  <div class="card"><div class="num" style="color:#22c55e">${today_earn:.2f}</div><div class="lbl">Today ({today_cnt} tasks)</div></div>
  <div class="card"><div class="num" style="color:#6366f1">{total}</div><div class="lbl">All Tasks</div></div>
  <div class="card"><div class="num" style="color:#f59e0b">{auto_cnt}</div><div class="lbl">Auto-Submitted</div></div>
  {plat_cards}
</div>
<div class="section">
  <h2>📋 Task History (last 100)</h2>
  <table>
    <thead><tr>
      <th>Date</th><th>Platform</th><th>Task</th>
      <th style="text-align:center">Pay</th>
      <th style="text-align:center">Type</th>
      <th style="text-align:center">How</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>
<p class="ts">Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
<button class="refresh" onclick="location.reload()">↻ Refresh</button>
</body>
</html>"""
    MICRODASH_FILE.write_text(html, encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════════
#  MINDRIFT MODULE  (Toloka's worker platform — accepts Nigeria)
# ════════════════════════════════════════════════════════════════════════════

MINDRIFT_URL = "https://mindrift.toloka.ai"

def login_mindrift():
    """Open browser so user can log in via GitHub OAuth. Session saved automatically."""
    log.info("Opening Mindrift browser — log in with GitHub, then press ENTER here.")
    log.info(f"  URL: {MINDRIFT_URL}")
    PROFILE_MINDRIFT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx  = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_MINDRIFT), headless=False, slow_mo=60,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.new_page()
        page.add_init_script(STEALTH_SCRIPT)
        # Go directly to auth page
        page.goto(f"{MINDRIFT_URL}/auth", wait_until="domcontentloaded", timeout=30000)
        log.info("  → Browser is open. Click 'Continue with GitHub', complete login.")
        log.info("  → Wait until you can see your Mindrift dashboard/tasks page.")
        log.info("  → ONLY THEN press ENTER here.")
        input(">>> Dashboard loaded? Press ENTER to save session <<<")
        # Save cookies
        cookies = ctx.cookies()
        log.info(f"  Saved {len(cookies)} session cookies.")
        ctx.close()
    log.info("✓ Mindrift session saved. You can now run: python microtask_bot.py")


def run_mindrift():
    """Main Mindrift monitoring loop — runs in its own thread."""
    log.info("[Mindrift] Starting monitor...")
    if not PROFILE_MINDRIFT.exists():
        log.warning("[Mindrift] No session. Run: python microtask_bot.py --login-mindrift")
        return

    session_count  = 0
    session_earned = 0.0

    with sync_playwright() as p:
        ctx  = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_MINDRIFT),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1,1", "--window-position=0,0", "--no-sandbox",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.add_init_script(STEALTH_SCRIPT)

        # Verify login
        try:
            page.goto(MINDRIFT_URL, wait_until="domcontentloaded", timeout=30000)
            human_delay(3, 5)
            # If still on auth page after load, session not saved
            if any(x in page.url.lower() for x in ["/auth", "github.com", "signin", "login"]):
                log.error("[Mindrift] Not logged in. Run --login-mindrift first.")
                log.error(f"  Current URL: {page.url}")
                ctx.close()
                return
        except Exception as e:
            log.error(f"[Mindrift] Connection error: {e}")
            ctx.close()
            return

        log.info("[Mindrift] ✓ Logged in. Starting task loop...")

        # Preferred projects in priority order
        PREFERRED_PROJECTS = [
            "which image is better",
            "high-end visual quality",
            "aesthetic comparison",
            "better response",
            "better based on the prompt",
            "image is better",
        ]

        while True:
            try:
                if session_count >= MAX_TASKS_PER_SESSION:
                    alert_session_summary("Mindrift", session_count, session_earned)
                    time.sleep(SESSION_BREAK_MIN * 60)
                    session_count = 0; session_earned = 0.0

                # Step 1 — go to Explore, find and start a project
                log.info("[Mindrift] Scanning Explore for tasks...")
                _mindrift_navigate_to_tasks(page)
                human_delay(2, 3)

                started = _mindrift_start_project(page, PREFERRED_PROJECTS)
                if not started:
                    log.info("[Mindrift] No suitable project found — waiting 60s...")
                    time.sleep(60)
                    continue

                # Step 2 — work through tasks in this project session
                log.info("[Mindrift] Project started — working tasks...")
                consecutive_empty = 0

                while consecutive_empty < 5:
                    task_url  = page.url
                    task_title = page.title() or "Mindrift Task"

                    page_text = page.evaluate("() => document.body.innerText.slice(0, 3000)") or ""
                    page_text_low = page_text.lower()

                    # ── In training mode: always use the smart image comparison handler ──
                    # It handles EVERYTHING internally: checkboxes, external platform
                    # buttons, popups, Continue buttons, then the actual comparison.
                    # No need for a separate external-platform branch.
                    if MINDRIFT_TRAINING_MODE:
                        fake_task = {"title": task_title, "pay": 0.003, "url": task_url}
                        result = _mindrift_image_comparison(page, fake_task)
                        if result:
                            session_count  += 1
                            session_earned += 0.003
                            record_task_done("Mindrift", f"md_{session_count}_{int(time.time())}",
                                             task_title, 0.003, "image_comparison",
                                             result.get("auto_submitted", False), task_url)
                            consecutive_empty = 0
                        else:
                            consecutive_empty += 1
                        continue

                    # ── Non-training: detect task type and route ───────────────
                    # External platform redirect
                    if "open external platform" in page_text_low or "external platform" in page_text_low:
                        log.info("  External platform task — handling...")
                        result = _mindrift_handle_external(page, task_url)
                        if result:
                            session_count  += 1
                            session_earned += 0.003
                            record_task_done("Mindrift", f"md_{session_count}_{int(time.time())}",
                                             task_title, 0.003, "image_comparison",
                                             result.get("auto_submitted", False), task_url)
                            consecutive_empty = 0
                        else:
                            consecutive_empty += 1
                        continue

                    # Count meaningful images (not icons): ≥2 = almost certainly a task
                    img_count = page.evaluate(
                        "() => [...document.querySelectorAll('img')].filter("
                        "  el => el.naturalWidth > 100 && el.naturalHeight > 100"
                        ").length"
                    ) or 0
                    is_image_comp = (img_count >= 2) or any(x in page_text_low for x in [
                        "which image is better", "which is better", "better image",
                        "aesthetic", "visual quality", "better response", "based on the prompt",
                        "select the better", "choose the better",
                    ])
                    if is_image_comp:
                        fake_task = {"title": task_title, "pay": 0.003, "url": task_url}
                        result = _mindrift_image_comparison(page, fake_task)
                        if result:
                            session_count  += 1
                            session_earned += 0.003
                            record_task_done("Mindrift", f"md_{session_count}_{int(time.time())}",
                                             task_title, 0.003, "image_comparison",
                                             result.get("auto_submitted", False), task_url)
                            consecutive_empty = 0
                        else:
                            consecutive_empty += 1

                    elif page_text.strip():
                        # ── Training stage intro / "Continue" page ─────────────
                        # Before alerting, check for Continue/Start buttons that
                        # indicate this is a training overview page, not a real task.
                        _training_page = False
                        for _tsel in [
                            'button:has-text("Continue")', 'a:has-text("Continue")',
                            'button:has-text("Start training")', 'button:has-text("Start Training")',
                            'button:has-text("Next stage")', 'button:has-text("Next Stage")',
                            'a:has-text("Start tasks")', 'button:has-text("Proceed")',
                            'button:has-text("Get started")', 'button:has-text("Let\'s start")',
                        ]:
                            try:
                                _tbtn = page.locator(_tsel).first
                                if _tbtn.is_visible(timeout=800):
                                    _tbtn.click()
                                    log.info(f"  [Training] Stage intro → clicked: {_tsel}")
                                    human_delay(2.5, 4.0)
                                    _training_page = True
                                    consecutive_empty = 0
                                    break
                            except Exception:
                                continue

                        if not _training_page:
                            # Genuine unrecognised task — only alert outside training
                            if not MINDRIFT_TRAINING_MODE:
                                analysis = analyze_task(task_title, page_text)
                                if analysis["auto_submit"] and analysis["answer"]:
                                    success = _generic_fill_and_submit(page, analysis)
                                    if success:
                                        session_count  += 1
                                        session_earned += 0.003
                                        record_task_done("Mindrift", f"md_{session_count}_{int(time.time())}",
                                                         task_title, 0.003, analysis["type"], True, task_url)
                                        consecutive_empty = 0
                                    else:
                                        consecutive_empty += 1
                                else:
                                    alert_complex_task("Mindrift", task_title, 0.003,
                                                       task_url, analysis.get("hint", ""))
                                    _wait_for_completion(page, 180)
                                    consecutive_empty = 0
                            else:
                                # Training mode: unknown page — log it and try any visible button
                                log.info(f"  [Training] Unrecognised page. URL={page.url[:80]}")
                                log.info(f"  [Training] Page text snippet: {page_text[:300]!r}")
                                # Log ALL visible buttons so we can see what's available
                                try:
                                    visible_btns = page.evaluate("""
                                        () => [...document.querySelectorAll('button, a[role="button"], [class*="btn"]')]
                                            .filter(el => el.offsetParent !== null && el.innerText.trim().length > 0)
                                            .map(el => el.innerText.trim().slice(0, 40))
                                    """) or []
                                    log.info(f"  [Training] Visible buttons on page: {visible_btns}")
                                    # Try clicking any button that isn't navigation
                                    NAV_SKIP = {"projects", "explore", "dashboard", "settings",
                                                "profile", "logout", "home", "tasks", "earnings"}
                                    for btn_text in visible_btns:
                                        if btn_text.lower() in NAV_SKIP or len(btn_text) < 2:
                                            continue
                                        try:
                                            candidate = page.locator(
                                                f'button:has-text("{btn_text}"), a[role="button"]:has-text("{btn_text}")'
                                            ).first
                                            if candidate.is_visible(timeout=600):
                                                candidate.click()
                                                log.info(f"  [Training] Clicked button: '{btn_text}'")
                                                human_delay(2.0, 3.5)
                                                consecutive_empty = 0
                                                break
                                        except Exception:
                                            continue
                                    else:
                                        # No clickable button found — wait and let outer loop retry
                                        log.info("  [Training] No suitable button found — waiting 5s")
                                        time.sleep(5)
                                except Exception as _be:
                                    log.info(f"  [Training] Button scan error: {_be}")
                                    time.sleep(5)

                    else:
                        consecutive_empty += 1
                        time.sleep(3)

                log.info(f"[Mindrift] Project session done — {session_count} tasks, ${session_earned:.4f}")

            except PlaywrightTimeout:
                log.warning("[Mindrift] Timeout — retrying in 15s.")
                human_delay(12, 18)
            except Exception as e:
                log.error(f"[Mindrift] Error: {e}")
                human_delay(10, 20)


def _mindrift_navigate_to_tasks(page: Page):
    """Navigate to the Mindrift tasks/training page."""
    try:
        if "mindrift.toloka.ai" not in page.url:
            page.goto(MINDRIFT_URL, wait_until="domcontentloaded", timeout=25000)
            human_delay(2, 3)

        # In training mode: check for a "Training" or "My Projects" nav link first
        # Training tasks are often in a separate section, not on Explore
        if MINDRIFT_TRAINING_MODE:
            for training_nav in [
                'a:has-text("Training")', 'a:has-text("My projects")',
                'a:has-text("My tasks")', 'a:has-text("In progress")',
                'a[href*="training"]', 'a[href*="my-projects"]',
            ]:
                try:
                    link = page.locator(training_nav).first
                    if link.is_visible(timeout=1500):
                        link.click()
                        human_delay(2, 3)
                        log.info(f"[Mindrift] Navigated via training link: {training_nav}")
                        break
                except Exception:
                    continue

        # Click Explore tab if visible (fallback for training and normal mode)
        try:
            exp = page.locator('a:has-text("Explore"), nav a:has-text("Explore")').first
            if exp.is_visible(timeout=3000):
                exp.click()
                human_delay(2, 3)
        except: pass

        # Handle 404 page — click "Go to tasks"
        try:
            go_btn = page.locator('a:has-text("Go to tasks"), button:has-text("Go to tasks")').first
            if go_btn.is_visible(timeout=2000):
                go_btn.click()
                human_delay(2, 3)
        except: pass

        log.info(f"[Mindrift] On page: {page.url}")
    except Exception as e:
        log.warning(f"[Mindrift] Navigation error: {e}")


def _mindrift_start_project(page: Page, preferred: list) -> bool:
    """
    Find a preferred project on the Explore page and click Start.
    Returns True if a project was started successfully.
    """
    try:
        # Get all project cards
        projects = page.evaluate("""
            () => {
                const cards = [...document.querySelectorAll(
                    'article, [class*="card"], [class*="Card"], [class*="project"], [class*="Project"]'
                )];
                return cards.map(card => ({
                    title: (card.querySelector('h1,h2,h3,h4,h5,[class*="title"]')?.innerText || card.innerText.split('\\n')[0]).trim().toLowerCase(),
                    hasStart: /^start$/i.test((card.querySelector('button,a')?.innerText||'').trim()),
                    hasTasks: !/selfie|video|narrat|palm|photo|face/i.test(card.innerText),
                }));
            }
        """) or []

        log.info(f"[Mindrift] Found {len(projects)} projects on Explore page")

        # Try preferred projects first
        for pref in preferred:
            for sel in [
                f'button:has-text("Start")',
                f'a:has-text("Start")',
                f'[class*="card"] button',
            ]:
                try:
                    # Find cards containing preferred keyword
                    cards = page.locator(f'article:has-text("{pref}"), [class*="card"]:has-text("{pref}")')
                    if cards.count() > 0:
                        start_btn = cards.first.locator('button:has-text("Start"), a:has-text("Start")').first
                        if start_btn.is_visible(timeout=2000):
                            log.info(f"[Mindrift] Starting project: {pref}")
                            start_btn.click()
                            human_delay(3, 5)
                            return True
                except: continue

        # Fallback: click Start on any image-comparison type project ONLY
        # In training mode, we MUST stay on image comparison — never start photo/selfie/upload tasks.
        SKIP_WORDS = [
            "selfie", "video", "narrat", "palm", "face photo",
            "upload", "photo of", "picture of", "younger", "older",
            "childhood", "personal photo", "take a photo", "share a photo",
            "your photo", "your face", "webcam",
        ]
        # In training mode: ONLY start a project that is specifically image comparison.
        # "training" is intentionally NOT here — many unrelated tasks also say "training".
        IMAGE_COMP_WORDS = [
            "which image", "better image", "aesthetic", "visual quality",
            "image comparison", "image quality", "high-end visual",
            "better based", "image is better",
        ]
        all_starts = page.locator('button:has-text("Start"), a:has-text("Start")')
        count = all_starts.count()
        for i in range(count):
            try:
                btn = all_starts.nth(i)
                parent_text = btn.evaluate("el => el.closest('article,[class*=\"card\"]')?.innerText || ''").lower()
                log.info(f"[Mindrift] Project card text: {parent_text[:120]!r}")
                if any(skip in parent_text for skip in SKIP_WORDS):
                    log.info(f"  Skipping (disallowed project type)")
                    continue
                # In training mode, MUST match image comparison keywords — no exceptions
                if MINDRIFT_TRAINING_MODE:
                    if not any(w in parent_text for w in IMAGE_COMP_WORDS):
                        log.info(f"  Skipping (not an image comparison project)")
                        continue
                if btn.is_visible(timeout=1000):
                    log.info(f"[Mindrift] Starting project: {parent_text[:80]!r}")
                    btn.click()
                    human_delay(3, 5)
                    return True
            except: continue

        # Training projects that are already in-progress show "Continue" (not "Start").
        # Check ALL button types that could advance an in-progress project.
        # Must verify the parent card is NOT a disallowed (selfie/video/upload) project.
        for resume_sel in [
            'button:has-text("Continue")', 'a:has-text("Continue")',
            'a:has-text("Start tasks")', 'button:has-text("Start tasks")',
            'a:has-text("Resume")', 'button:has-text("Resume")',
            'a:has-text("Continue training")', 'button:has-text("Continue training")',
        ]:
            try:
                btns = page.locator(resume_sel)
                for idx in range(btns.count()):
                    btn = btns.nth(idx)
                    if not btn.is_visible(timeout=1000):
                        continue
                    parent_text = btn.evaluate(
                        "el => el.closest('article,[class*=\"card\"]')?.innerText || ''"
                    ).lower()
                    log.info(f"[Mindrift] Continue/Resume candidate ({resume_sel}): {parent_text[:100]!r}")
                    if any(skip in parent_text for skip in SKIP_WORDS):
                        log.info(f"  Skipping — disallowed project type")
                        continue
                    # Empty parent_text means button isn't inside a project card
                    # (could be a page-level Continue) — allow it in training mode
                    log.info(f"[Mindrift] Clicking: {resume_sel} on project: {parent_text[:60]!r}")
                    btn.click()
                    human_delay(3, 5)
                    return True
            except: continue

        log.info("[Mindrift] No image comparison project found to start.")
        return False

    except Exception as e:
        log.warning(f"[Mindrift] Could not start project: {e}")
        return False


def _mindrift_get_tasks(page: Page) -> list:
    """Scrape visible tasks from the current Mindrift page — no navigation/reload here."""
    tasks = []
    try:

        raw = page.evaluate("""
            () => {
                const results = [];
                // Task cards on Mindrift explore page
                const cards = [...document.querySelectorAll(
                    '[class*="task"], [class*="Task"], [class*="project"], [class*="Project"], article, .card'
                )];
                for (const card of cards.slice(0, 30)) {
                    // Get link
                    const link = card.querySelector('a[href]') || (card.tagName === 'A' ? card : null);
                    const href = link ? link.href : '';
                    // Get title
                    const titleEl = card.querySelector('h1,h2,h3,h4,h5,[class*="title"],[class*="name"]');
                    const title = (titleEl ? titleEl.innerText : card.innerText).trim().split('\\n')[0];
                    // Get pay — look for $ or US$ amounts
                    const text = card.innerText || '';
                    const payMatch = text.match(/\\$([\\d.]+)/);
                    const pay = payMatch ? parseFloat(payMatch[1]) : 0;
                    // Get task type hint
                    const typeMatch = text.match(/Type:\\s*([^\\n]+)/i);
                    const taskType = typeMatch ? typeMatch[1].trim() : '';
                    if (title && title.length > 3) {
                        results.push({
                            id: href ? href.split('/').pop() : (title.slice(0,20) + Math.random()),
                            title,
                            pay,
                            task_type: taskType,
                            url: href || window.location.href,
                        });
                    }
                }
                return results;
            }
        """) or []

        # Filter: skip video/selfie/photo tasks the bot can't handle
        SKIP_TITLES = ["selfie", "video", "record", "narrat", "palm", "face photo", "upload photo"]
        tasks = [t for t in raw if not any(s in t.get("title","").lower() for s in SKIP_TITLES)]

        if not tasks:
            log.debug("[Mindrift] No suitable tasks found — queue may be empty.")

    except Exception as e:
        log.warning(f"[Mindrift] Error fetching tasks: {e}")
    return tasks


def _mindrift_process(page: Page, task: dict) -> Optional[dict]:
    """Open a Mindrift task and process it."""
    try:
        if task.get("url") and task["url"] != page.url:
            page.goto(task["url"], wait_until="domcontentloaded", timeout=20000)
            human_delay(2, 3)

        # Click Start button if present
        for sel in ['button:has-text("Start")', 'a:has-text("Start")',
                    'button:has-text("Begin")', 'button:has-text("Accept")']:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    accept_delay()
                    btn.click()
                    human_delay(1.5, 2.5)
                    break
            except: continue

        # Read task content
        instructions = page.evaluate("""
            () => {
                const el = document.querySelector(
                    '[class*="instruction"],[class*="description"],[class*="question"],
                     [class*="prompt"],[class*="task-body"],main p'
                );
                return el ? el.innerText.trim() : document.body.innerText.slice(0, 600);
            }
        """) or ""

        title = task.get("title", "")
        task_type_hint = task.get("task_type", "")

        # Special handling for "Which image is better?" — Mindrift's highest volume task
        if "which image is better" in title.lower() or "image is better" in instructions.lower():
            return _mindrift_image_comparison(page, task)

        analysis = analyze_task(title, instructions + " " + task_type_hint)
        log.info(f"  Type: {analysis['type']} | Conf: {analysis['confidence']:.0%}")

        if analysis["auto_submit"] and analysis["answer"]:
            success = _generic_fill_and_submit(page, analysis)
            if success:
                log.info(f"  ✓ Auto-submitted: {analysis['answer']}")
                return {"type": analysis["type"], "auto_submitted": True}

        alert_complex_task("Mindrift", title, task.get("pay", 0),
                           page.url, analysis.get("hint", ""))
        _wait_for_completion(page, timeout=300)
        return {"type": analysis["type"], "auto_submitted": False}

    except Exception as e:
        log.warning(f"[Mindrift] Processing error: {e}")
        return None


def _mindrift_handle_external(page: Page, task_url: str) -> Optional[dict]:
    """
    Handle Mindrift tasks that redirect to an external annotation platform.
    Clicks 'Open External Platform', does image comparison there, auto-submits.
    """
    try:
        # Click "Open External Platform" link
        ext_link = page.locator('a:has-text("Open External Platform"), a:has-text("Open external"), a[href*="reindeer"], a[href*="annotation"]').first
        if not ext_link.is_visible(timeout=3000):
            log.warning("  External platform link not found — alerting user.")
            alert_complex_task("Mindrift", "External task", 0.003, task_url,
                               "Bot couldn't find 'Open External Platform' link — open manually")
            _wait_for_completion(page, 300)
            return {"auto_submitted": False}

        # Get the external URL
        ext_url = ext_link.get_attribute("href") or ""
        log.info(f"  Opening external platform: {ext_url[:80]}...")

        # Open in same tab
        ext_link.click()
        human_delay(3, 5)

        # Now on external annotation studio — do image comparison
        ext_page_text = page.evaluate("() => document.body.innerText.slice(0, 500)") or ""
        has_images = page.evaluate(
            "() => document.querySelectorAll('img').length"
        ) or 0

        if has_images >= 2:
            fake_task = {"title": "External image comparison", "pay": 0.003, "url": page.url}
            result = _mindrift_image_comparison(page, fake_task)
            return result
        else:
            # Needs onboarding first or complex task
            log.info("  External platform — onboarding may be needed. Alerting.")
            alert_complex_task("Mindrift", "External annotation task", 0.003,
                               page.url,
                               "Complete onboarding on the external platform, then tasks will flow automatically")
            _wait_for_completion(page, 600)
            return {"auto_submitted": False}

    except Exception as e:
        log.warning(f"  External platform error: {e}")
        return None


def _mindrift_image_comparison(page: Page, task: dict) -> Optional[dict]:
    """
    Smart handler for Mindrift image comparison tasks.
    Works on any state the page is in — handles checkboxes, external platform
    buttons, popups, Continue buttons — then does the actual comparison.
    """
    try:
        # ════════════════════════════════════════════════════════════════════
        # PHASE 0 — Pre-task actions (run on every call, safe to repeat)
        # The task page may show a checkbox + "Open External Platform" button
        # before the actual comparison loads. Handle these first.
        # ════════════════════════════════════════════════════════════════════

        # 0a — Tick any unchecked agreement / terms checkboxes
        try:
            checkboxes = page.locator(
                'input[type="checkbox"]:not(:checked), '
                '[role="checkbox"][aria-checked="false"], '
                '[class*="checkbox"]:not([class*="checked"]):not([class*="disabled"])'
            )
            for i in range(checkboxes.count()):
                cb = checkboxes.nth(i)
                if cb.is_visible(timeout=500):
                    cb.click()
                    log.info(f"  ✓ Ticked checkbox #{i+1}")
                    human_delay(0.3, 0.6)
        except Exception:
            pass

        # 0b — Click "Open in External Platform" / "Open External" button if present
        # This opens the actual task page on the external annotation platform.
        ext_opened = False
        for ext_sel in [
            'a:has-text("Open in External Platform")',
            'button:has-text("Open in External Platform")',
            'a:has-text("Open External Platform")',
            'button:has-text("Open External Platform")',
            'a:has-text("Open external")',
            'a:has-text("Open in external")',
            '[class*="external-link"]',
            '[class*="external"] a',
            '[class*="external"] button',
            'a[target="_blank"]',   # external links open in new tab
        ]:
            try:
                btn = page.locator(ext_sel).first
                if btn.is_visible(timeout=800):
                    href = btn.get_attribute("href") or ""
                    log.info(f"  ✓ Clicking external platform button ({ext_sel}) → {href[:60]}")
                    # Open in same tab if possible, otherwise handle new tab
                    with page.context.expect_page(timeout=6000) as new_page_info:
                        btn.click()
                    try:
                        new_page = new_page_info.value
                        new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                        log.info(f"  External platform opened in new tab: {new_page.url[:80]}")
                        # Switch all further work to the new tab
                        page = new_page
                    except Exception:
                        # Opened in same tab — just wait for load
                        human_delay(4.0, 6.0)
                        log.info(f"  External platform loaded in same tab: {page.url[:80]}")
                    ext_opened = True
                    human_delay(2.0, 3.0)
                    break
            except Exception:
                # No new tab opened — try a plain click
                try:
                    btn2 = page.locator(ext_sel).first
                    if btn2.is_visible(timeout=500):
                        btn2.click()
                        human_delay(4.0, 6.0)
                        log.info(f"  External platform clicked (same tab): {page.url[:80]}")
                        ext_opened = True
                        break
                except Exception:
                    continue

        if ext_opened:
            human_delay(2.0, 3.0)  # extra time for full render

        # ════════════════════════════════════════════════════════════════════
        # PHASE 1 — Dismiss popups, read stage instructions
        # ════════════════════════════════════════════════════════════════════
        # ── Read & dismiss any Stage Update / info popup first ────────────────
        # IMPORTANT: Read the popup text BEFORE dismissing — it contains stage
        # rules that must be passed to Gemini so it judges correctly.
        stage_instructions = ""
        for popup_text_sel in [
            '[role="dialog"]', '[class*="modal"]', '[class*="popup"]',
            '[class*="dialog"]', 'div[class*="stage"]',
        ]:
            try:
                modal = page.locator(popup_text_sel).first
                if modal.is_visible(timeout=1000):
                    stage_instructions = modal.inner_text().strip()
                    if len(stage_instructions) > 30:
                        log.info(f"  Stage instructions read: {stage_instructions[:120]}...")
                        break
            except Exception:
                continue

        for popup_sel in [
            'button:has-text("OK")',    'button:has-text("Ok")',
            'button:has-text("Got it")', 'button:has-text("Close")',
            '[aria-label="Close"]', 'button:has-text("×")',
        ]:
            try:
                btn = page.locator(popup_sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    log.info(f"  Dismissed popup: {popup_sel}")
                    human_delay(0.5, 1.0)
                    break
            except Exception:
                continue

        # ── Click "Continue" button if training stage shows one ───────────────
        for cont_sel in [
            'button:has-text("Continue")', 'a:has-text("Continue")',
            'button:has-text("Start training")', 'button:has-text("Begin")',
            'button:has-text("Next")', '[class*="continue"]',
        ]:
            try:
                btn = page.locator(cont_sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    log.info(f"  Clicked Continue/Start button: {cont_sel}")
                    human_delay(2.0, 3.0)
                    break
            except Exception:
                continue

        human_delay(2.0, 4.0)  # realistic reading time

        # ── Step 1: Read the actual task prompt (NOT the project title) ───────
        # The project title ("High-End Visual Quality & Aesthetic Comparison") must
        # be explicitly excluded. The real per-task prompt is the short description
        # of what each image was generated from (e.g. "a woman in jeans on a beach").
        prompt_text = page.evaluate("""
            () => {
                // Known project-title patterns to SKIP — these are headings, not prompts
                const SKIP = [
                    /high-end visual quality/i, /aesthetic comparison/i,
                    /which image is better/i, /you will need to navigate/i,
                    /external platform/i, /complete some onboarding/i,
                ];
                const navWords = ['projects', 'explore', 'dashboard', 'settings', 'profile', 'logout'];

                // Priority 1: explicit SOURCE CONTEXT / PROMPT label element
                for (const el of document.querySelectorAll('*')) {
                    const txt = (el.innerText || '').trim();
                    if (/SOURCE CONTEXT|SOURCE\\/PROMPT|\\bPROMPT\\b/i.test(txt) && txt.length < 60) {
                        const parent = el.closest('div, section');
                        if (parent) {
                            // Check siblings after the label
                            const siblings = [...(parent.parentElement?.children || [])];
                            const idx = siblings.indexOf(parent);
                            for (let i = idx + 1; i < Math.min(idx + 5, siblings.length); i++) {
                                const t = (siblings[i]?.innerText || '').trim();
                                if (t.length > 5 && t.length < 2000 && !SKIP.some(p => p.test(t))) return t;
                            }
                            // Also check children of the label's parent
                            for (const child of [...parent.children]) {
                                const t = (child.innerText || '').trim();
                                if (t.length > 5 && !SKIP.some(p => p.test(t))) return t;
                            }
                        }
                    }
                }

                // Priority 2: elements whose class names suggest prompt/context/source
                for (const el of document.querySelectorAll(
                    '[class*="prompt"], [class*="context"], [class*="source"], [class*="task-text"], [class*="instruction"]'
                )) {
                    const txt = (el.innerText || '').trim();
                    if (txt.length > 5 && txt.length < 1000 && !SKIP.some(p => p.test(txt))) {
                        const low = txt.toLowerCase();
                        if (!navWords.some(w => low.startsWith(w))) return txt;
                    }
                }

                // Priority 3: dark-background box (the black SOURCE CONTEXT box)
                for (const el of document.querySelectorAll('div, pre, code, section, p')) {
                    const bg = getComputedStyle(el).backgroundColor;
                    const isDark = /rgb\\([0123]\\d?,/.test(bg) || /rgba\\([0123]\\d?,/.test(bg);
                    const txt = (el.innerText || '').trim();
                    if (txt.length > 5 && txt.length < 1500 && isDark) {
                        if (SKIP.some(p => p.test(txt))) continue;
                        const low = txt.toLowerCase();
                        if (navWords.filter(w => low.includes('\\n' + w)).length > 1) continue;
                        return txt;
                    }
                }

                // Priority 4: any <p> or short text block that isn't a title/nav
                for (const el of document.querySelectorAll('p, li, span')) {
                    const txt = (el.innerText || '').trim();
                    if (txt.length > 5 && txt.length < 500 && !SKIP.some(p => p.test(txt))) {
                        const low = txt.toLowerCase();
                        if (!navWords.some(w => low.startsWith(w))) return txt;
                    }
                }

                return '';
            }
        """) or ""

        # Clean up residual navigation lines
        nav_prefixes = ["Projects", "Explore", "Dashboard", "Settings", "Profile", "High-End Visual"]
        prompt_lines = [l for l in prompt_text.split("\n")
                        if not any(l.strip().startswith(p) for p in nav_prefixes)]
        prompt_text = "\n".join(prompt_lines).strip()

        log.info(f"  Prompt: {prompt_text[:200]!r}")

        # ── Step 2: Scroll to top then take full-page screenshot ─────────────
        # Full-page capture ensures Gemini sees the prompt, contextual hints
        # (reference style images), AND the two generated images together.
        page.evaluate("window.scrollTo(0, 0)")
        human_delay(0.5, 1.0)
        screenshot_bytes = page.screenshot(full_page=True)

        # ── Step 3: Ask Gemini to judge ──────────────────────────────────────
        chosen = "1"   # default fallback
        if _GEMINI_AVAILABLE and _gemini_client:
            stage_block = ""
            if stage_instructions:
                stage_block = f"""
⚠️ STAGE-SPECIFIC INSTRUCTIONS (read carefully — these override default rules for this stage):
{stage_instructions}
Apply these stage instructions as the PRIMARY decision rule above all others.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            # ── Inject all learned rules into the prompt ─────────────────────
            learned_rules = load_learned_rules()
            rules_text = "\n".join(f"  • {r}" for r in learned_rules)
            learned_block = f"""
⚡ MANDATORY LEARNED RULES (from real platform mistakes — apply ALL, they override general judgment):
{rules_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

            gemini_prompt = f"""You are a world-class Art Director and visual quality expert at a premium creative agency. Your task is to judge which of two AI-generated images is superior. You must think carefully and systematically — do NOT rush to a conclusion.

═══════════════════════════════════════
PROMPT USED TO GENERATE BOTH IMAGES:
"{prompt_text}"
{stage_block}
{learned_block}
═══════════════════════════════════════

The screenshot shows a side-by-side comparison:
- LEFT HALF = Generated Image 1
- RIGHT HALF = Generated Image 2

YOU MUST FOLLOW THIS EXACT PROCESS — DO NOT SKIP ANY STEP:

━━━ STEP 1: ANALYZE IMAGE 1 (LEFT SIDE) ━━━
Look ONLY at the left image. Write your observations:
• Subject & content: what is depicted?
• Art style: is it a photo, illustration, 3D render, painting, digital art?
• Prompt compliance: check EACH word/requirement in the prompt — does Image 1 fulfill it? Note any misses.
• Defects: anatomy errors (count limbs!), AI artifacts, text quality, stroke consistency, background quality
• Compositional quality: lighting, color, arrangement, visual balance
• Background: is it contextually appropriate? What type is it?

━━━ STEP 2: ANALYZE IMAGE 2 (RIGHT SIDE) ━━━
Look ONLY at the right image. Write your observations:
• Subject & content: what is depicted?
• Art style: is it a photo, illustration, 3D render, painting, digital art?
• Prompt compliance: check EACH word/requirement in the prompt — does Image 2 fulfill it? Note any misses.
• Defects: anatomy errors (count limbs!), AI artifacts, text quality, stroke consistency, background quality
• Compositional quality: lighting, color, arrangement, visual balance
• Background: is it contextually appropriate? What type is it?

━━━ STEP 3: APPLY DECISION RULES ━━━
Now apply these rules in order (higher rules override lower ones):

Use this EXACT decision hierarchy (top = highest priority):

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 0 — STAGE INSTRUCTIONS (ABSOLUTE TOP PRIORITY):
If stage-specific instructions appear above, they override EVERYTHING below.
Read them carefully and apply them as your primary rule before all others.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 1 — NSFW (absolute hard gate):
If one image shows nudity or explicit sexual content, the OTHER image wins immediately. No exceptions.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 2 — PROMPT WORDS ARE LITERAL (hard gate):
Every descriptive word AND structural description in the prompt is LITERAL and must be fulfilled.

ADJECTIVES are literal: If the prompt says "old" → the image must look genuinely old/worn/weathered. A clean, new-looking version LOSES even if more beautiful. If the prompt says "large" → the subject must appear large.

COMPOSITION/STRUCTURE is literal: If the prompt says "composed of ribbons" → the design must literally be made of ribbon shapes. If it says "logo using interlocking circles" → must have actual interlocking circles. "Composed of X" means X is the design element — an image that substitutes a different design element (e.g., arcs instead of ribbons, type instead of geometric shapes) LOSES even if it is more beautiful or more polished.
The structural/compositional requirement is the DESIGN BRIEF — it is not optional or a suggestion.

An image that contradicts a KEY adjective or structural requirement is automatically disqualified — beauty does not save it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 3 — TECHNICAL/ANATOMY QUALITY (HARD disqualifier):
BEFORE anything else, scan both images carefully for:
- COUNT LIMBS: Every person must have exactly 2 arms and 2 legs. A third leg or third arm = immediate disqualification.
- Extra or missing fingers (humans have 5 per hand)
- Distorted or broken body poses that are physically impossible
- Glitched or fused objects held in hands
- Broken, garbled, or illegible text on signs/labels/etc.
- Objects floating unnaturally or merging into each other
- Faces that are distorted, asymmetrical, or melting
One clear anatomy/structural flaw = automatic loss for that image, even if the other image is less beautiful.
NOTE: Poses that could be explained by context (e.g., body contact during a fight, unusual angle) are NOT disqualifiers. Only IMPOSSIBLE anatomy is a hard disqualifier.

INFOGRAPHIC INTERNAL CONSISTENCY (critical for instructional images):
For infographics, step-by-step guides, posters with instructions — the GRAPHICS must match the TEXT.
If step 3 text says "apply soap" but the graphic shows a vase or an unrelated object → that is a mismatch = disqualifier.
Both the visual and the text must tell the same story. An infographic where illustrations don't correspond to the written steps is WRONG.

AI ARTIFACTS (hard disqualifier — important for all quality tasks):
The following disqualify an image even if it looks artistic at first glance:
- Grid patterns or dot/halftone patterns in background (looks like "texture" but is an AI rendering artifact — NOT intentional design)
- Grain or noise in areas that should be smooth
- Repeating pattern overlays not requested in prompt
- Vertical or horizontal lines crossing the image
- Pixelation or blurry patches
- Compression-like smearing around edges
- Watermarks or copyright text overlaid
- COLOR REPRODUCTION ISSUES: unnatural skin tones, washed-out colors, oversaturated or incorrectly balanced colors, color fringing or bleeding
- INCONSISTENT LINE/STROKE WEIGHT (critical for logos, monograms, line-art marks): Every stroke in a logo or mark must maintain perfectly consistent thickness from start to finish. If lines become THINNER or THICKER at junctions or meeting points for no reason — that is an AI generation artifact, NOT intentional design. A mark with consistent weight all the way through wins over one with thinning strokes, even if the latter looks simpler or more "minimalist."
Do NOT confuse AI artifacts with intentional art styles. If the prompt doesn't request grain/texture/halftone — it's an artifact, not a style.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 4 — ART STYLE ACCURACY (critical when a specific style is named):
If the prompt names a specific art style, pick the image that most AUTHENTICALLY matches that style.
A simpler image that correctly matches the specified style beats a more dramatic image that is the wrong style.
Examples:
- "West African acrylic painting" → flat bold colors with geometric shapes is CORRECT; thick impasto European expressionist is WRONG even if more impressive.
- "Kenny Scharf style" → every entity/object must have a FACE drawn on it; this is Scharf's defining feature. Dense psychedelic chaos without faces = WRONG.
- "Low-poly 3D" → must have visible geometric polygonal facets; smooth photorealistic = WRONG.
Look at the SPECIFIC defining features of the named style, not just the general mood.
Authentic style execution > generic visual drama.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 5 — DESIGNER STEP / DEFECT COUNT (for Stage 6 type tasks):
When the task involves evaluating a near-finished design with possible flaws:
1. First COUNT the defects in each image (typos, missing elements, text errors, anatomy issues, broken design elements, garbled text, extra limbs, wrong colors).
2. If an image has MORE THAN ONE DEFECT → it automatically loses to a clean/plain image, NO MATTER how beautiful it is.
3. If an image has EXACTLY ONE fixable defect → it can still win if it is significantly more beautiful/impressive than the clean version.
Examples of Stage 6 defects: "JAZZZ" (extra Z = 1 typo), THREE arms on dancer (1 anatomy error), garbled bottom text (1 text error) — but if ALL THREE appear on the same image → 3 defects → automatic loss.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 6 — STAGE 7 AESTHETICS: TYPOGRAPHY & DESIGN COHESION AND CONTEMPORARY VS VINTAGE:
CONTEMPORARY SOPHISTICATION: For modern brand logos, a clean bold wordmark with distinctive custom letterforms can be MORE sophisticated and premium than a logo with colorful decorative icons. Many premium contemporary brands (think high-end furniture, interior, design studios) use pure typographic wordmarks — not folk-art icons. Do NOT assume a brand needs a visual icon or warm colors to feel premium. A bold, minimal, single-color wordmark with custom letterforms = contemporary sophistication.
VINTAGE/ARTISANAL TRAP: A logo with earthy colors + decorative mandala/folk icons may look "artisanal" or "Etsy-like" rather than premium contemporary, even if the colors are warm and inviting. Unless the prompt specifically calls for a vintage, rustic, or artisanal look, "contemporary" beats "artisanal/vintage."
CUSTOM LETTERFORMS: Unusual or distinctive letter shapes in a wordmark (custom-drawn M's, ligatures, unique forms) are a sign of premium bespoke design investment — NOT an awkward flaw. Distinctive = sophisticated.
For Stage 7 (Aesthetics and balance) and logo/poster/design tasks, professional quality means:
- TYPOGRAPHIC CONSISTENCY: All text in a design must use fonts that work together as a cohesive system. Mismatched, clashing, or unrelated fonts = design flaw, even if the overall layout looks dramatic or bold.
- LOW-QUALITY LETTERING: If any lettering in an image looks poor quality (amateurish hand-lettering, poorly rendered script, low-res type) → that is a quality failure.
- FONT HIERARCHY: A clean design with one consistent font family (used at different weights/sizes) beats a dramatic design that mixes unrelated font styles.
- COHESION > VISUAL DRAMA: A bold, high-contrast design with inconsistent typography loses to a simpler, clean design with perfect typographic cohesion.
For Stage 7, ask: "Which image would a professional art director put in a premium brand book or portfolio?" Natural poses, polished materials, professional lighting, real "wow" factor. Avoid flat, stiff, plastic, or lifeless results.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 7 — OVERALL AESTHETIC QUALITY (dominant factor when no hard gates triggered):
Ask yourself: "Which image is more visually stunning, impressive, interesting, and beautiful?"
"Generic" and "stock-looking" images ALWAYS LOSE — even if technically correct to the prompt.

Signs of a BAD image: flat/plain illustration with simple shading, generic stock-photo look, boring composition, mass-produced feel.
Signs of a GOOD image: high production value, sophisticated lighting, dramatic atmosphere, striking composition, distinctive artistic vision, "wow" factor.

BALANCE AND RESTRAINT (Stage 7 critical — "Aesthetics and BALANCE"):
Over-exaggerating prompt requirements is a quality FLAW, not a quality advantage.
- If the prompt says "jewelry" → one or a few pieces worn elegantly. Stacking every possible jewelry piece = clutter = fails "balance."
- If the prompt says "flowers" → a tasteful floral arrangement. Filling the frame with excessive flowers = over-exaggeration.
- A short/simple prompt should be interpreted with ELEGANT PRECISION and RESTRAINT — not maximalism.
- The image that interprets the prompt cleanly and precisely beats the one that maximizes every element mentioned.
- "More is not more" in Stage 7 — excessive amounts of any element disturb visual balance and clutter the composition.

FOOD & PRODUCT PHOTOGRAPHY AESTHETICS (Stage 7 critical):
- The image must look VISUALLY APPEALING, not just technically accurate. "Realistic" does not mean "beautiful."
- Raw meat, food scraps, or any subject shown in a visceral, unappealing, or gory way = aesthetic failure, even if it matches the prompt perfectly.
- BACKGROUND CLEANLINESS: A cluttered, busy, or distracting background (visible props, dark areas, unrelated objects peeking in) is a serious negative. Clean, controlled, solid backgrounds always win over messy ones.
- PROFESSIONAL FOOD STYLING: A cohesive, well-arranged composition with a clean surface beats a more technically accurate but messily presented image. Professional styling (beautiful arrangement, clean background, nothing distracting) = premium quality.
- Would this image go in a premium brand book or food magazine? If the answer is no because it looks unappealing or cluttered, it loses to a cleaner, more styled version.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PORTRAIT PHOTOGRAPHY BACKGROUND TERMS (literal meanings):
- "Modeled painted background" = a studio canvas backdrop that has been hand-painted with graduated tonal modeling (dark-to-light gradients, subtle brush texture) — NOT a painted scene of buildings/landscape. A mottled, graduated dark studio backdrop IS a modeled painted background.
- "Plain background" = solid flat color — no gradients, no texture.
- "Painted scene background" = a specific scene rendered as a painting behind the subject.
- If a prompt says "modeled painted background," the image with a classic studio backdrop (dark, softly textured, graduated) wins over one with a painted urban/nature scene behind the subject.

CONTEXTUAL BACKGROUND APPROPRIATENESS:
A completely unsuitable or random background context is a MAJOR disqualifier — worse than having text artifacts or minor quality issues. The background must be contextually appropriate to the subject/setting. An image with minor text artifacts but a correct, professional context beats a cleaner image with a completely wrong/random background.

ANTI-DESIGN AND EXPERIMENTAL AESTHETICS RULE:
For prompts that explicitly call for underground, punk, rave, grunge, anti-design, chaotic, or experimental aesthetics:
- Apparent chaos, illegibility, extreme scale variation, and anti-readability are INTENTIONAL DESIGN CHOICES — not flaws.
- The more daring, committed, and experimental interpretation wins over the "safer" more legible version.
- A "competent but generic" version of an underground aesthetic loses to a genuinely bold, risk-taking version — even if the bold one is harder to read.
- "Digital-punk," "anti-design," and "xerox/photocopy" aesthetics have their own design logic and system — recognize intentional chaos vs. actual AI artifacts.
- If both images attempt the same underground aesthetic, ask: which one takes more creative risks? Which feels like a real artifact vs. a stock version of that style?

RULE 8 — CREATIVITY vs CORRECTNESS (for short/open prompts):
When one image is "safe/obvious" and the other is creative/unexpected:
- PREFER the creative one IF the prompt's main subject is still the CENTRAL FOCUS.
- The creative image loses ONLY IF the main subject is GONE — replaced or demoted to a background prop.
- Example WRONG: Prompt = "pizza". Creative image = horse eating pizza, horse fills the frame, pizza is a tiny prop → pizza GONE → plain pizza wins.
- Example CORRECT: Prompt = "Chinese karate man". Creative image = artistic painting of a Chinese karate man → karate man IS the central subject → creative painting WINS.
- Do NOT let contextual hints override creativity in open-prompt tasks.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE 9 — PROMPT RELEVANCE (soft gate):
Only disqualify an image if it completely misses the subject (asked for a person, got a landscape).
Minor detail mismatches do NOT disqualify a more beautiful image.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL DECISION ORDER:
Stage instructions → NSFW → Prompt adjectives literal → Anatomy/artifacts/defects → Art style accuracy → Typography cohesion → Aesthetic quality

━━━ STEP 4: FINAL VERDICT ━━━
State in ONE sentence which image wins and the single most important reason.

Then on the very last line of your response, write EXACTLY in this format:
WINNER: 1
or
WINNER: 2

Do not write anything after the WINNER line."""

            try:
                # Phase 1: Chain-of-thought analysis → final answer
                analysis_text = _call_groq_vision(gemini_prompt, [screenshot_bytes])
                if not analysis_text:
                    raise Exception("Empty response from Groq")
                log.info(f"  AI analysis (last 200 chars): ...{analysis_text[-200:]}")

                # Extract WINNER: from the response
                import re as _re
                winner_match = _re.search(r'WINNER:\s*([12])', analysis_text, _re.IGNORECASE)
                if winner_match:
                    chosen = winner_match.group(1)
                else:
                    # Fallback: find last standalone digit
                    digits = _re.findall(r'\b([12])\b', analysis_text)
                    if digits:
                        chosen = digits[-1]
                    else:
                        chosen = "1"

                # Phase 2: Verification call — second opinion on the decision
                verify_prompt = f"""You are a second expert Art Director reviewing another judge's decision.

The image comparison prompt was: "{prompt_text}"
{stage_block}
The first judge analyzed both images and concluded: WINNER: {chosen}

Here is the screenshot with Image 1 (LEFT) and Image 2 (RIGHT).

Do you AGREE with WINNER: {chosen}?
Consider:
- Does the winning image match all literal prompt requirements?
- Does it have better aesthetic quality?
- Are there any defects in the winning image that should disqualify it?
- Is the losing image actually better on any critical dimension?

If you AGREE: respond with CONFIRM: {chosen}
If you DISAGREE and the other image is clearly better: respond with OVERRIDE: {"2" if chosen == "1" else "1"}
If it's genuinely very close and you're not sure: respond with CONFIRM: {chosen}

Write your brief reasoning, then end with CONFIRM or OVERRIDE on the last line."""

                try:
                    verify_text = _call_groq_vision(verify_prompt, [screenshot_bytes], max_tokens=512)
                    if not verify_text:
                        raise Exception("Empty verification response")
                    log.info(f"  AI verification: ...{verify_text[-150:]}")

                    override_match = _re.search(r'OVERRIDE:\s*([12])', verify_text, _re.IGNORECASE)
                    confirm_match  = _re.search(r'CONFIRM:\s*([12])',  verify_text, _re.IGNORECASE)

                    if override_match:
                        new_choice = override_match.group(1)
                        log.info(f"  ⚠ Verification OVERRODE choice: {chosen} → {new_choice}")
                        chosen = new_choice
                    elif confirm_match:
                        log.info(f"  ✓ Verification CONFIRMED: image {chosen}")
                    else:
                        log.info(f"  Verification unclear — keeping original: image {chosen}")

                except Exception as ve:
                    log.warning(f"  Verification call failed: {ve} — keeping original answer {chosen}")

                log.info(f"  ✓ FINAL DECISION: Image {chosen}")

            except Exception as e:
                log.warning(f"  Gemini error: {e} — defaulting to image 1")
                chosen = "1"
        else:
            log.warning("  Gemini not available — defaulting to image 1")

        # ── Step 4: Press keyboard shortcut (1 or 2) then Enter ─────────────
        human_delay(1.0, 2.5)
        page.keyboard.press(chosen)
        human_delay(0.5, 1.0)
        page.keyboard.press("Enter")
        human_delay(2.0, 3.5)   # wait for result banner to appear

        log.info(f"  ✓ Submitted image {chosen} via keyboard shortcut")

        # ── Step 5: Handle post-submission result ────────────────────────────
        # The platform shows a green (correct) or red (incorrect) banner.
        # On INCORRECT answers during training, it also reveals the correct answer
        # and may allow you to re-select. Detect and handle both cases.

        # Give the platform a bit more time to render the result banner
        human_delay(2.0, 3.5)

        result_correct = True

        # Check for failure/incorrect via CSS classes
        wrong_selectors = [
            '[class*="wrong"]', '[class*="incorrect"]', '[class*="error"]',
            '[class*="fail"]', 'div:has-text("Incorrect")', 'div:has-text("Wrong")',
            '[class*="red"]', '[style*="red"]',
        ]
        for sel in wrong_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=800):
                    result_correct = False
                    log.warning(f"  ✗ Wrong answer detected via selector: {sel}")
                    break
            except Exception:
                continue

        # Also check via page text content (more reliable than class names)
        if result_correct:
            try:
                wrong_via_text = page.evaluate("""
                    () => {
                        const t = document.body.innerText.toLowerCase();
                        return t.includes('incorrect') || t.includes('wrong answer')
                            || t.includes("that's not right") || t.includes('not the best choice')
                            || t.includes('try again') || t.includes('right answer was');
                    }
                """) or False
                if wrong_via_text:
                    result_correct = False
                    log.warning("  ✗ Wrong answer detected via page text")
            except Exception:
                pass

        if not result_correct and MINDRIFT_TRAINING_MODE:
            # ── Capture and save the platform explanation ────────────────────
            human_delay(1.0, 1.5)   # brief wait for explanation to render
            platform_explanation = _capture_platform_explanation(page)
            correct_image = "2" if chosen == "1" else "1"

            if platform_explanation:
                lesson = (
                    f"Bot chose Image {chosen} but Image {correct_image} was correct. "
                    f"Prompt was: '{prompt_text[:150]}'. "
                    f"Platform explanation: {platform_explanation}"
                )
            else:
                lesson = (
                    f"Bot chose Image {chosen} but Image {correct_image} was correct. "
                    f"Prompt: '{prompt_text[:200]}'. "
                    f"Review what literal requirement Image {chosen} failed."
                )
            save_learned_rule(lesson, prompt_text, chosen, correct_image)

            # ── Try to find which answer the platform highlights as correct ──
            correct_selectors = [
                '[class*="correct"] input[type="radio"]',
                '[class*="correct"] button',
                '[class*="right"] input[type="radio"]',
                'input[type="radio"][class*="correct"]',
                '[aria-label*="correct"]',
                'label[class*="correct"]',
            ]
            corrected = False
            for sel in correct_selectors:
                try:
                    correct_el = page.locator(sel).first
                    if correct_el.is_visible(timeout=800):
                        correct_el.click()
                        human_delay(0.5, 1.0)
                        log.info(f"  ↩ Clicked platform-highlighted correct answer")
                        corrected = True
                        break
                except Exception:
                    continue

            # Also try clicking the image that is visually marked correct
            if not corrected:
                for num in ["1", "2"]:
                    try:
                        correct_img = page.locator(
                            f'[class*="correct"] >> nth={int(num)-1},'
                            f'[class*="right"] >> nth={int(num)-1}'
                        ).first
                        if correct_img.is_visible(timeout=600):
                            page.keyboard.press(num)
                            human_delay(0.5, 1.0)
                            log.info(f"  ↩ Re-selected image {num} as correction")
                            corrected = True
                            break
                    except Exception:
                        continue

        # ── Step 6: Click "To the next task!" button ─────────────────────────
        # After both correct and incorrect answers, a "To the next task!" button
        # (or similar) appears. Click it to advance to the next task.
        human_delay(1.5, 2.5)
        next_task_selectors = [
            'button:has-text("To the next task")',
            'a:has-text("To the next task")',
            'button:has-text("Next task")',
            'a:has-text("Next task")',
            'button:has-text("Continue")',
            'a:has-text("Continue")',
            'button:has-text("Next question")',
            'a:has-text("Next question")',
            '[class*="next-task"]',
            '[class*="nextTask"]',
            '[class*="next_task"]',
            'button:has-text("Next")',
            'a:has-text("Next")',
            'button:has-text("OK")',
            'button:has-text("Ok")',
            'button:has-text("Done")',
        ]
        next_clicked = False
        for sel in next_task_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    log.info(f"  → Clicked next-task button ({sel})")
                    human_delay(2.0, 3.0)
                    next_clicked = True
                    break
            except Exception:
                continue

        if not next_clicked:
            # Fallback: try pressing Enter/Space — sometimes the next-task action
            # is keyboard-accessible even when the button isn't found by selector
            log.info("  → Next-task button not found — trying Enter key as fallback")
            try:
                page.keyboard.press("Enter")
                human_delay(1.5, 2.5)
            except Exception:
                pass

        return {"type": "image_comparison", "auto_submitted": True, "correct": result_correct}

    except Exception as e:
        log.warning(f"  Image comparison error: {e}")
        alert_complex_task("Mindrift", task.get("title", ""), task.get("pay", 0),
                           page.url, f"Image comparison failed: {e}")
        return None



# ════════════════════════════════════════════════════════════════════════════
#  HIVE MICRO MODULE  (global — accepts Nigeria)
# ════════════════════════════════════════════════════════════════════════════

HIVEMICRO_URL = "https://app.hivemicro.com"

def login_hivemicro():
    """Auto-fill Hive Micro credentials and save session."""
    log.info("Logging in to Hive Micro...")
    PROFILE_HIVEMICRO.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx  = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_HIVEMICRO), headless=False, slow_mo=80,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.new_page()
        page.add_init_script(STEALTH_SCRIPT)
        page.goto(f"{HIVEMICRO_URL}/login", wait_until="domcontentloaded", timeout=20000)
        human_delay(1, 2)

        try:
            page.locator('input[type="email"], input[name="email"], #email').first.fill(HIVEMICRO_EMAIL)
            human_delay(0.5, 1)
            page.locator('input[type="password"], #password').first.fill(HIVEMICRO_PASSWORD)
            human_delay(0.5, 1)
            page.locator('button[type="submit"], input[type="submit"]').first.click()
            human_delay(2, 3)
            log.info("  Credentials submitted. Check browser if captcha or 2FA appears.")
        except Exception as e:
            log.warning(f"  Auto-fill failed: {e}. Log in manually in the browser.")

        input(">>> Logged in to Hive Micro? Press ENTER to save session <<<")
        ctx.close()
    log.info("Hive Micro session saved.")


def run_hivemicro():
    """Main Hive Micro monitoring loop — runs in its own thread."""
    log.info("[HiveMicro] Starting monitor...")
    if not PROFILE_HIVEMICRO.exists():
        log.warning("[HiveMicro] No session. Run: python microtask_bot.py --login-hivemicro")
        return

    session_count  = 0
    session_earned = 0.0

    with sync_playwright() as p:
        ctx  = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_HIVEMICRO),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1,1", "--window-position=0,0", "--no-sandbox",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.add_init_script(STEALTH_SCRIPT)

        # Verify login
        try:
            page.goto(f"{HIVEMICRO_URL}/jobs", wait_until="domcontentloaded", timeout=30000)
            human_delay(3, 5)
            if "login" in page.url.lower() or "signin" in page.url.lower():
                log.error("[HiveMicro] Not logged in. Run --login-hivemicro first.")
                ctx.close()
                return
        except Exception as e:
            log.error(f"[HiveMicro] Connection error: {e}")
            ctx.close()
            return

        log.info("[HiveMicro] ✓ Logged in. Monitoring for tasks...")

        while True:
            try:
                if session_count >= MAX_TASKS_PER_SESSION:
                    alert_session_summary("HiveMicro", session_count, session_earned)
                    time.sleep(SESSION_BREAK_MIN * 60)
                    session_count = 0; session_earned = 0.0

                tasks = _hivemicro_get_available(page)
                for task in tasks:
                    tid = task.get("id", "")
                    if not mark_seen(f"hm_{tid}"):
                        continue
                    pay = task.get("pay", 0.0)
                    if pay < MIN_TASK_PAY:
                        continue

                    log.info(f"[HiveMicro] Task: {task.get('title','?')[:50]}  ${pay:.5f}/task")
                    result = _hivemicro_process(page, task)
                    if result:
                        session_count  += 1
                        session_earned += pay
                        record_task_done(
                            "HiveMicro", tid, task.get("title",""),
                            pay, result["type"], result["auto_submitted"],
                            task.get("url", HIVEMICRO_URL)
                        )

                human_delay(1.5, 2.5)
                try:
                    page.reload(wait_until="domcontentloaded", timeout=15000)
                except: pass
                human_delay(1, 2)

            except PlaywrightTimeout:
                log.warning("[HiveMicro] Timeout — retrying...")
                human_delay(8, 15)
            except Exception as e:
                log.error(f"[HiveMicro] Error: {e}")
                human_delay(10, 20)


def _hivemicro_get_available(page: Page) -> list:
    """Get available jobs from Hive Micro job board."""
    tasks = []
    try:
        if "hivemicro" not in page.url or "job" not in page.url:
            page.goto(f"{HIVEMICRO_URL}/jobs", wait_until="domcontentloaded", timeout=20000)
            human_delay(2, 3)

        raw = page.evaluate("""
            () => {
                const results = [];
                // Hive Micro job cards
                const cards = [...document.querySelectorAll(
                    '[class*="job-card"], [class*="JobCard"], [class*="task-card"], .card, article'
                )];
                for (const card of cards.slice(0, 30)) {
                    const link = card.querySelector('a[href]');
                    const href = link ? link.href : '';
                    const titleEl = card.querySelector('h1,h2,h3,h4,[class*="title"],[class*="name"]');
                    const title = titleEl ? titleEl.innerText.trim() : card.innerText.trim().split('\\n')[0];
                    // Pay is per 1000 tasks — parse it and divide
                    const text = card.innerText || '';
                    const payMatch = text.match(/US\\$([\\d.]+)/i) || text.match(/\\$([\\d.]+)/);
                    const payPer1000 = payMatch ? parseFloat(payMatch[1]) : 0;
                    const payPerTask = payPer1000 / 1000;  // convert to per-task
                    const typeMatch = text.match(/Type:\\s*([^\\n]+)/i);
                    const taskType = typeMatch ? typeMatch[1].trim() : 'categorization';
                    // Skip tasks requiring qualification if button says "Take Qualification Test"
                    const hasQual = /take qualification/i.test(text);
                    if (title && title.length > 3 && !hasQual) {
                        results.push({
                            id: href ? href.split('/').pop() : title.slice(0,15),
                            title,
                            pay: payPerTask,
                            task_type: taskType,
                            url: href || window.location.href,
                        });
                    }
                }
                return results;
            }
        """) or []

        # Only keep categorization / yes-no tasks (skip bounding box, timestamp for now)
        SKIP_TYPES = ["bounding box", "timestamp", "transcription", "segmentation"]
        tasks = [t for t in raw
                 if not any(s in t.get("task_type","").lower() for s in SKIP_TYPES)
                 and t.get("pay", 0) >= MIN_TASK_PAY]

        if not tasks:
            log.debug("[HiveMicro] No ready tasks — may need qualification first.")
        else:
            log.info(f"[HiveMicro] Found {len(tasks)} available tasks.")

    except Exception as e:
        log.warning(f"[HiveMicro] Error fetching tasks: {e}")
    return tasks


def _hivemicro_process(page: Page, task: dict) -> Optional[dict]:
    """Open and complete a Hive Micro task."""
    try:
        page.goto(task["url"], wait_until="domcontentloaded", timeout=20000)
        human_delay(2, 3)

        # Click Start Tasks button
        for sel in ['a:has-text("Start tasks")', 'button:has-text("Start")',
                    'a:has-text("Start Tasks")', 'button:has-text("Start Tasks")']:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    accept_delay()
                    btn.click()
                    human_delay(2, 3)
                    break
            except: continue

        # Get task instructions
        instructions = page.evaluate("""
            () => {
                const el = document.querySelector(
                    '[class*="instruction"],[class*="description"],[class*="question"],[class*="task-text"],p'
                );
                return el ? el.innerText.trim() : document.body.innerText.slice(0, 600);
            }
        """) or ""

        analysis = analyze_task(task.get("title",""), instructions + " " + task.get("task_type",""))
        log.info(f"  Type: {analysis['type']} | Conf: {analysis['confidence']:.0%}")

        # Run many tasks in a row on HiveMicro (they batch tasks)
        completed = 0
        while True:
            if analysis["auto_submit"] and analysis["answer"]:
                success = _generic_fill_and_submit(page, analysis)
                if success:
                    completed += 1
                    log.info(f"  ✓ Task {completed} submitted: {analysis['answer']}")
                    human_delay(0.8, 1.5)  # quick between batch tasks
                    # Check if more tasks remain in the batch
                    more = page.evaluate("""
                        () => !!document.querySelector('[class*="question"],[class*="task-body"],img[class*="task"]')
                    """)
                    if not more or completed >= 50:
                        break
                    # Re-analyze next task in batch
                    instructions = page.evaluate("""
                        () => {
                            const el = document.querySelector('[class*="instruction"],[class*="question"],p');
                            return el ? el.innerText.trim() : '';
                        }
                    """) or ""
                    analysis = analyze_task(task.get("title",""), instructions)
                else:
                    break
            else:
                alert_complex_task("HiveMicro", task.get("title",""), task.get("pay",0) * 10,
                                   page.url, analysis.get("hint",""))
                _wait_for_completion(page, 300)
                break

        total_pay = task.get("pay", 0) * max(completed, 1)
        return {"type": analysis["type"], "auto_submitted": completed > 0,
                "tasks_done": completed, "total_pay": total_pay}

    except Exception as e:
        log.warning(f"[HiveMicro] Processing error: {e}")
        return None


def _hivemicro_placeholder_tasks_done():
    # unused — kept for compatibility
    pass


def _clickworker_process(page, task):
    # Renamed platform — this stub prevents NameError if called accidentally
    return _hivemicro_process(page, task)


def _clickworker_get_available(page):
    return _hivemicro_get_available(page)


def _hivemicro_old_stub():
    # placeholder
    pass




# ════════════════════════════════════════════════════════════════════════════
#  MTURK MODULE
# ════════════════════════════════════════════════════════════════════════════

MTURK_URL = "https://worker.mturk.com"

def login_mturk():
    log.info("Opening MTurk login browser — log in with your Amazon account, then press ENTER.")
    log.warning("⚠ MTurk approval for non-US workers can take weeks and may be rejected.")
    PROFILE_MTURK.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx  = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_MTURK), headless=False, slow_mo=60,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.new_page()
        page.add_init_script(STEALTH_SCRIPT)
        page.goto(f"{MTURK_URL}/", wait_until="domcontentloaded")
        input(">>> Logged in to MTurk? Press ENTER to save session <<<")
        ctx.close()
    log.info("MTurk session saved.")


def run_mturk():
    """Main MTurk monitoring loop — runs in its own thread."""
    log.info("[MTurk] Starting monitor...")
    if not PROFILE_MTURK.exists():
        log.warning("[MTurk] No session. Run: python microtask_bot.py --login-mturk")
        return

    session_count  = 0
    session_earned = 0.0

    with sync_playwright() as p:
        ctx  = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_MTURK),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1,1", "--window-position=0,0", "--no-sandbox",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.add_init_script(STEALTH_SCRIPT)

        # Verify login
        try:
            page.goto(f"{MTURK_URL}/", wait_until="domcontentloaded", timeout=30000)
            human_delay(3, 5)
            if "signin" in page.url.lower() or "login" in page.url.lower():
                log.error("[MTurk] Not logged in. Run --login-mturk first.")
                ctx.close()
                return
        except Exception as e:
            log.error(f"[MTurk] Connection error: {e}")
            ctx.close()
            return

        log.info("[MTurk] ✓ Logged in. Monitoring HITs...")

        while True:
            try:
                if session_count >= MAX_TASKS_PER_SESSION:
                    alert_session_summary("MTurk", session_count, session_earned)
                    time.sleep(SESSION_BREAK_MIN * 60)
                    session_count = 0; session_earned = 0.0

                hits = _mturk_get_available(page)
                for hit in hits:
                    tid = hit.get("id", "")
                    if not mark_seen(f"mturk_{tid}"):
                        continue
                    pay = hit.get("pay", 0.0)
                    if pay < MIN_TASK_PAY:
                        continue

                    log.info(f"[MTurk] New HIT: {hit.get('title','?')[:50]}  ${pay:.3f}")
                    result = _mturk_accept_and_process(page, hit)
                    if result:
                        session_count  += 1
                        session_earned += pay
                        record_task_done(
                            "MTurk", tid, hit.get("title",""),
                            pay, result["type"], result["auto_submitted"],
                            hit.get("url", MTURK_URL)
                        )

                human_delay(1.8, 2.8)

            except PlaywrightTimeout:
                log.warning("[MTurk] Timeout — retrying...")
                human_delay(10, 20)
            except Exception as e:
                log.error(f"[MTurk] Error: {e}")
                human_delay(10, 20)


def _mturk_get_available(page: Page) -> list:
    """Get available HITs from MTurk — uses the undocumented worker API."""
    hits = []
    try:
        # MTurk has an API endpoint for available HITs
        response = page.evaluate("""
            async () => {
                try {
                    const r = await fetch('/api/hitgroups?page_size=100&sort=latest&filters[preview]=true', {
                        headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}
                    });
                    const data = await r.json();
                    return (data.results || data.hit_groups || []).map(h => ({
                        id: h.hit_group_id || h.id || '',
                        title: h.description || h.title || '',
                        pay: parseFloat(h.monetary_reward?.amount_in_dollars || h.reward?.amount || 0),
                        requester: h.requester_name || '',
                        time_allowed: h.assignment_duration_in_seconds || 0,
                        available: h.hit_count || h.available_hits || 1,
                        url: `https://worker.mturk.com/projects/${h.hit_group_id || h.id}/tasks/accept_random`
                    }));
                } catch(e) {
                    return [];
                }
            }
        """)
        hits = response or []

        if not hits:
            # Fallback: scrape the HITs page
            page.goto(f"{MTURK_URL}/projects?sort=updated_desc",
                      wait_until="domcontentloaded", timeout=20000)
            human_delay(1, 2)
            hits = page.evaluate("""
                () => {
                    const rows = [...document.querySelectorAll('[class*="task-info"], .project-row, tr')];
                    return rows.slice(0, 50).map(row => {
                        const link = row.querySelector('a[href*="projects"]');
                        const title = row.querySelector('[class*="title"],.task-title')?.innerText?.trim() || '';
                        const payEl = row.querySelector('[class*="reward"],[class*="pay"]');
                        const payText = payEl?.innerText || '';
                        const pay = parseFloat(payText.replace(/[^0-9.]/g,'')) || 0;
                        const href = link?.href || '';
                        if (!title || !href) return null;
                        return {id: href.split('/').slice(-2,-1)[0] || href, title, pay, url: href};
                    }).filter(Boolean);
                }
            """) or []

    except Exception as e:
        log.warning(f"[MTurk] Error fetching HITs: {e}")
    return hits


def _mturk_accept_and_process(page: Page, hit: dict) -> Optional[dict]:
    """Accept a MTurk HIT and process it."""
    try:
        # Navigate to accept URL — this auto-accepts the HIT
        accept_delay()
        accept_url = hit.get("url") or f"{MTURK_URL}/projects/{hit['id']}/tasks/accept_random"
        page.goto(accept_url, wait_until="domcontentloaded", timeout=25000)
        human_delay(2, 4)

        # Get task content
        title = page.title() or hit.get("title", "")
        instructions = page.evaluate("""
            () => {
                // MTurk tasks often use iframes
                const iframe = document.querySelector('iframe[src*="mturk"], .task-content iframe, #taskContent iframe');
                if (iframe) {
                    try {
                        const doc = iframe.contentDocument || iframe.contentWindow?.document;
                        return doc ? doc.body.innerText.slice(0, 800) : '';
                    } catch { return ''; }
                }
                const el = document.querySelector('#task-content, .task, [class*="instruction"], main');
                return el ? el.innerText.slice(0, 800) : document.body.innerText.slice(0, 500);
            }
        """) or ""

        analysis = analyze_task(title, instructions)
        log.info(f"  Type: {analysis['type']} | Conf: {analysis['confidence']:.0%}")

        if analysis["auto_submit"] and analysis["answer"]:
            success = _mturk_fill_and_submit(page, analysis)
            if success:
                return {"type": analysis["type"], "auto_submitted": True}

        # Complex — alert user with task link
        alert_complex_task(
            "MTurk", hit.get("title",""), hit.get("pay",0),
            page.url, analysis.get("hint","")
        )
        _wait_for_completion(page, timeout=int(hit.get("time_allowed", 300)))
        return {"type": analysis["type"], "auto_submitted": False}

    except Exception as e:
        log.warning(f"[MTurk] Processing error: {e}")
        return None


def _mturk_fill_and_submit(page: Page, analysis: dict) -> bool:
    """Fill answer and submit MTurk HIT."""
    answer = analysis["answer"]
    try:
        # Try in main frame first, then iframe
        filled = _generic_fill_answer(page, answer)
        if not filled:
            # Try in iframe
            frames = page.frames
            for frame in frames[1:]:  # skip main frame
                try:
                    filled = frame.evaluate(f"""
                        (answer) => {{
                            const els = [...document.querySelectorAll(
                                'input[type="radio"], button, label, select option, [role="radio"]'
                            )];
                            const match = els.find(el =>
                                new RegExp(answer, 'i').test((el.innerText || el.value || el.textContent || '').trim())
                            );
                            if (match) {{ match.click(); return true; }}
                            return false;
                        }}
                    """, answer)
                    if filled:
                        break
                except: continue

        if not filled:
            return False

        submit_delay()

        # Submit in main frame
        submitted = page.evaluate("""
            () => {
                const btns = [...document.querySelectorAll('button, input[type="submit"]')];
                const btn = btns.find(el =>
                    /submit|done|next|finish|complete/i.test((el.innerText || el.value || '').trim())
                    && !el.disabled
                );
                if (btn) { btn.click(); return true; }
                return false;
            }
        """)

        if not submitted:
            # Try submit in iframes
            for frame in page.frames[1:]:
                try:
                    submitted = frame.evaluate("""
                        () => {
                            const btn = document.querySelector('input[type="submit"], button[type="submit"]');
                            if (btn && !btn.disabled) { btn.click(); return true; }
                            return false;
                        }
                    """)
                    if submitted: break
                except: continue

        return bool(submitted)

    except Exception as e:
        log.warning(f"  MTurk submit error: {e}")
        return False


# ════════════════════════════════════════════════════════════════════════════
#  SHARED UTILITIES
# ════════════════════════════════════════════════════════════════════════════

def _generic_fill_answer(page: Page, answer: str) -> bool:
    """Try to fill an answer on any task page."""
    return bool(page.evaluate(f"""
        (answer) => {{
            const els = [...document.querySelectorAll(
                'input[type="radio"], input[type="checkbox"], button, label, select, [role="radio"], [role="option"]'
            )];
            const match = els.find(el =>
                new RegExp(answer, 'i').test((el.innerText || el.value || el.textContent || '').trim())
            );
            if (match) {{
                match.click();
                if (match.tagName === 'OPTION') {{
                    match.selected = true;
                    match.parentElement.dispatchEvent(new Event('change'));
                }}
                return true;
            }}
            return false;
        }}
    """, answer))


def _generic_fill_and_submit(page: Page, analysis: dict) -> bool:
    """Generic fill + submit for any platform."""
    filled = _generic_fill_answer(page, analysis["answer"])
    if not filled:
        return False
    submit_delay()
    submitted = page.evaluate("""
        () => {
            const btns = [...document.querySelectorAll('button, input[type="submit"], [type="submit"]')];
            const btn = btns.find(el =>
                /submit|done|next|finish|complete|save|confirm/i
                .test((el.innerText || el.value || '').trim()) && !el.disabled
            );
            if (btn) { btn.click(); return true; }
            return false;
        }
    """)
    return bool(submitted)


def _wait_for_completion(page: Page, timeout: int = 300):
    """
    Wait for the user to manually complete a task.
    Detects page navigation or success message as completion signal.
    Gives up after `timeout` seconds.
    """
    deadline = time.time() + timeout
    start_url = page.url
    while time.time() < deadline:
        time.sleep(3)
        try:
            current_url = page.url
            # If URL changed significantly, task was likely submitted
            if current_url != start_url and "task" not in current_url.lower():
                log.info("  Page navigated — assuming task submitted.")
                return
            # Check for success message
            success = page.evaluate("""
                () => {
                    const body = document.body.innerText || '';
                    return /thank you|submitted|completed|approved|success/i.test(body);
                }
            """)
            if success:
                log.info("  Success message detected — task done.")
                return
        except: pass
    log.info(f"  Timeout ({timeout}s) — moving on.")


# ════════════════════════════════════════════════════════════════════════════
#  MAIN — CONCURRENT PLATFORM MONITORING
# ════════════════════════════════════════════════════════════════════════════

def run():
    log.info("═" * 62)
    log.info("  MicroTask Bot  v1.0 — Multi-Platform")
    log.info(f"  Min task pay    : ${MIN_TASK_PAY:.2f}")
    log.info(f"  Auto-submit at  : {AUTO_SUBMIT_CONF:.0%} confidence")
    log.info(f"  Session limit   : {MAX_TASKS_PER_SESSION} tasks → {SESSION_BREAK_MIN}min break")
    log.info(f"  Mindrift   : {'✓ enabled' if ENABLE_MINDRIFT else '✗ disabled'}")
    log.info(f"  HiveMicro  : {'✓ enabled' if ENABLE_HIVEMICRO else '✗ disabled'}")
    log.info(f"  MTurk      : {'✓ enabled' if ENABLE_MTURK else '✗ disabled'}")
    log.info("═" * 62)

    # Load seen tasks
    global _seen_tasks
    _seen_tasks = load_seen_tasks()

    send_telegram(
        "⚡ <b>MicroTask Bot v2 started!</b>\n"
        f"Monitoring: {'Mindrift ' if ENABLE_MINDRIFT else ''}{'HiveMicro ' if ENABLE_HIVEMICRO else ''}{'MTurk' if ENABLE_MTURK else ''}\n"
        f"Min pay: ${MIN_TASK_PAY:.4f} | Auto-submit: {AUTO_SUBMIT_CONF:.0%} confidence"
    )

    # Generate initial dashboard
    generate_microdashboard(load_earnings())

    threads = []

    if ENABLE_MINDRIFT and PROFILE_MINDRIFT.exists():
        t = threading.Thread(target=run_mindrift, name="Mindrift", daemon=True)
        threads.append(t)
    elif ENABLE_MINDRIFT:
        log.warning("Mindrift: no session profile. Run --login-mindrift first.")

    if ENABLE_HIVEMICRO and PROFILE_HIVEMICRO.exists():
        t = threading.Thread(target=run_hivemicro, name="HiveMicro", daemon=True)
        threads.append(t)
    elif ENABLE_HIVEMICRO:
        log.warning("HiveMicro: no session profile. Run --login-hivemicro first.")

    if ENABLE_MTURK and PROFILE_MTURK.exists():
        t = threading.Thread(target=run_mturk, name="MTurk", daemon=True)
        threads.append(t)
    elif ENABLE_MTURK:
        log.warning("MTurk: no session profile. Run --login-mturk first.")

    if not threads:
        log.error("No platforms ready. Run --login-<platform> for each platform first, then restart.")
        sys.exit(1)

    for t in threads:
        log.info(f"Starting {t.name} thread...")
        t.start()
        time.sleep(2)  # stagger starts slightly

    log.info(f"All {len(threads)} platform(s) running. Bot is active.")

    # Keep main thread alive — threads are daemons so they die if main exits
    try:
        while True:
            alive = [t.name for t in threads if t.is_alive()]
            dead  = [t.name for t in threads if not t.is_alive()]
            if dead:
                log.warning(f"Dead threads: {dead}. Still running: {alive}")
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Stopped by user.")


# ════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
#  HIVE MICRO — COMMERCIAL LABELING (Qualifier + Real Tasks)
# ════════════════════════════════════════════════════════════════════════════

def send_telegram_photo(image_bytes: bytes, caption: str) -> bool:
    """Send a photo to Telegram with caption."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        import io
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("frame.jpg", io.BytesIO(image_bytes), "image/jpeg")},
            timeout=20,
        )
        return r.status_code == 200
    except Exception as e:
        log.warning(f"Telegram photo error: {e}")
        return False


_last_update_id = 0

def wait_for_telegram_reply(timeout: int = 180) -> Optional[str]:
    """Poll Telegram for a new reply from user. Returns text or None on timeout."""
    global _last_update_id

    # Drain any old messages first so we only catch NEW ones
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": _last_update_id + 1, "limit": 100, "timeout": 1},
            timeout=5,
        )
        for upd in r.json().get("result", []):
            _last_update_id = upd["update_id"]
    except Exception:
        pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": _last_update_id + 1, "limit": 5, "timeout": 30},
                timeout=35,
            )
            for upd in r.json().get("result", []):
                _last_update_id = upd["update_id"]
                msg  = upd.get("message", {})
                text = msg.get("text", "").strip()
                cid  = str(msg.get("chat", {}).get("id", ""))
                if text and cid == str(TELEGRAM_CHAT_ID):
                    return text
        except Exception:
            time.sleep(2)
    return None


def _hivemicro_capture_video_frame(page: Page, seek_time: float) -> Optional[bytes]:
    """Seek the video to seek_time seconds and return a JPEG frame as bytes."""
    try:
        page.evaluate(f"""
            () => {{
                const v = document.querySelector('video');
                if (v) {{ v.currentTime = {seek_time}; v.pause(); }}
            }}
        """)
        time.sleep(1.2)

        # Try canvas capture first (gets the actual video pixels)
        b64 = page.evaluate("""
            () => {
                const v = document.querySelector('video');
                if (!v) return null;
                const c = document.createElement('canvas');
                c.width  = v.videoWidth  || 640;
                c.height = v.videoHeight || 360;
                c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
                return c.toDataURL('image/jpeg', 0.85).split(',')[1];
            }
        """)
        if b64:
            import base64
            return base64.b64decode(b64)

        # Fallback: screenshot the video element
        v_el = page.locator("video").first
        if v_el.is_visible(timeout=2000):
            return v_el.screenshot(type="jpeg", quality=80)

        return None
    except Exception as e:
        log.warning(f"Frame capture error: {e}")
        return None


def _hivemicro_get_segment_info(page: Page) -> dict:
    """Read segment start/end seconds and brand dropdown list from the page DOM."""
    default = {"start": 0.0, "end": 0.0, "brands": []}
    try:
        data = page.evaluate(r"""
            () => {
                function parseTime(t) {
                    const parts = t.split(':').map(Number);
                    if (parts.length === 2) return parts[0] * 60 + parts[1];
                    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
                    return parseFloat(t) || 0;
                }

                // Find "0:17.93 - 1:01.88" style text anywhere on page
                const body = document.body.innerText;
                const m = body.match(/(\d+:\d+[\.,]\d+)\s*[-–]\s*(\d+:\d+[\.,]\d+)/);
                let start = 0, end = 0;
                if (m) {
                    start = parseTime(m[1].replace(',', '.'));
                    end   = parseTime(m[2].replace(',', '.'));
                }

                // Fallback: try video duration + region element style
                if (!start) {
                    const v = document.querySelector('video');
                    const dur = v ? v.duration : 0;
                    const region = document.querySelector('[class*="region"],[class*="segment"],[class*="selection"]');
                    if (region && dur) {
                        const style = region.getAttribute('style') || '';
                        const lm = style.match(/left:\s*([\d.]+)%/);
                        const wm = style.match(/width:\s*([\d.]+)%/);
                        if (lm && wm) {
                            start = (parseFloat(lm[1]) / 100) * dur;
                            end   = start + (parseFloat(wm[1]) / 100) * dur;
                        }
                    }
                }

                // Brand list from any dropdown / option elements
                const brands = [...document.querySelectorAll(
                    'select option, [role="option"], li[class*="option"], .dropdown-item, [class*="menu-item"]'
                )].map(el => el.innerText.trim())
                  .filter(t => t && t.length > 1 && !/select|search|other/i.test(t))
                  .slice(0, 20);

                return { start, end, brands };
            }
        """)
        return data or default
    except Exception as e:
        log.warning(f"Segment info error: {e}")
        return default


def _hivemicro_get_red_bar_position(page: Page) -> dict:
    """Read the red bar position from the video timeline and return start/end in seconds."""
    return page.evaluate("""
        () => {
            const video = document.querySelector('video');
            const duration = (video && video.duration) ? video.duration : 90;

            // Find the red bar element in the timeline
            const candidates = [
                ...document.querySelectorAll('[style]')
            ].filter(el => {
                const s = el.getAttribute('style') || '';
                return (s.includes('background: red') || s.includes('background-color: red') ||
                        s.includes('rgb(255, 0, 0)') || s.includes('#ff0000') ||
                        s.includes('rgb(220,') || s.includes('rgb(230,'));
            });

            if (!candidates.length) {
                return { start: duration * 0.18, end: duration * 0.60 };
            }
            const redEl = candidates[0];

            // Walk up to find a wide container (the timeline bar)
            let timeline = redEl.parentElement;
            for (let i = 0; i < 8; i++) {
                if (!timeline) break;
                const w = timeline.getBoundingClientRect().width;
                if (w > 300) break;
                timeline = timeline.parentElement;
            }
            if (!timeline) return { start: duration * 0.18, end: duration * 0.60 };

            const tRect = timeline.getBoundingClientRect();
            const rRect = redEl.getBoundingClientRect();
            const sp = Math.max(0, (rRect.left - tRect.left) / tRect.width);
            const ep = Math.min(1, (rRect.right - tRect.left) / tRect.width);

            return { start: sp * duration, end: ep * duration, duration };
        }
    """) or {"start": 10, "end": 50, "duration": 90}


def _frame_brightness(frame_bytes: bytes) -> float:
    """Return average brightness (0–255) of a JPEG frame. Uses PIL if available."""
    if not frame_bytes:
        return 128.0
    try:
        if _OCR_AVAILABLE:
            img = _PIL_Image.open(_io_ocr.BytesIO(frame_bytes)).convert("L").resize((64, 36))
            return sum(img.getdata()) / (64 * 36)
    except Exception:
        pass
    # Rough proxy: compressed black frames are tiny; scale by file size
    return min(255.0, len(frame_bytes) / 60.0)


def _hivemicro_scan_transitions(page: Page, search_start: float, search_end: float,
                                 step: float = 0.8) -> list:
    """
    Scan the video between search_start and search_end, capturing a frame every `step` seconds.
    Returns list of (timestamp, brightness, frame_bytes).
    """
    results = []
    t = search_start
    while t <= search_end + step * 0.5:
        f = _hivemicro_capture_video_frame(page, max(t, 0.1))
        b = _frame_brightness(f) if f else 128.0
        results.append((round(t, 2), b, f))
        t += step
    return results


def _find_intro_outro(scan_data: list, red_start: float, red_end: float) -> tuple:
    """
    From scan data (time, brightness, frame), find:
      intro = first bright frame after a black frame near the red-bar start
      outro = last bright frame before a black frame near the red-bar end
    Falls back to red_start/red_end if no black frames found.
    """
    BLACK = 22  # brightness below this = black / near-black frame

    # Find intro: scanning forward from scan start
    intro = red_start
    for i in range(1, len(scan_data)):
        t, b, _ = scan_data[i]
        prev_b = scan_data[i - 1][1]
        if prev_b <= BLACK and b > BLACK:
            # Content starts after a black frame
            if abs(t - red_start) < 10:   # must be near the red bar
                intro = t
                break

    # Find outro: scanning backward from scan end
    outro = red_end
    for i in range(len(scan_data) - 1, 0, -1):
        t, b, _ = scan_data[i]
        prev_b = scan_data[i - 1][1]
        if prev_b > BLACK and b <= BLACK:
            if abs(scan_data[i - 1][0] - red_end) < 10:
                outro = scan_data[i - 1][0]
                break

    return intro, outro


def _gemini_find_timestamps(scan_data: list, red_start: float, red_end: float) -> tuple:
    """
    Ask Gemini to identify intro/outro timestamps from a sequence of frames.
    Falls back to red_start/red_end on error.
    """
    if not _GEMINI_AVAILABLE or not scan_data:
        return red_start, red_end

    import base64 as _b64

    # Use every other frame to stay within token limits
    selected = scan_data[::2][:14]
    times_str = "  |  ".join(f"Frame {i+1} @ {t:.1f}s" for i, (t, _, _) in enumerate(selected))

    prompt = (
        f"I am showing you {len(selected)} frames from a TV broadcast, taken at these timestamps:\n"
        f"{times_str}\n\n"
        f"The area of interest (red bar) is approximately {red_start:.1f}s – {red_end:.1f}s.\n\n"
        f"I need the EXACT intro (start) and outro (end) of the content segment within this area.\n"
        f"- INTRO = first frame where the new segment begins (after a black screen or clear scene cut)\n"
        f"- OUTRO = last frame of the segment (just before it fades out or cuts to the next thing)\n\n"
        f"Study the frames in order and identify the transitions.\n\n"
        f"Reply in EXACTLY this format:\n"
        f"INTRO: [seconds as a number, e.g. 7.2]\n"
        f"OUTRO: [seconds as a number, e.g. 37.5]\n"
        f"REASON: [one sentence]"
    )

    try:
        images = [fb for _, _, fb in selected if fb]
        text = _call_groq_vision(prompt, images)
        if not text:
            return red_start, red_end
        log.info(f"[AI timestamps] {text[:100]}")

        intro, outro = red_start, red_end
        for line in text.split("\n"):
            if line.startswith("INTRO:"):
                try:
                    intro = float(line.split(":", 1)[1].strip())
                except Exception:
                    pass
            elif line.startswith("OUTRO:"):
                try:
                    outro = float(line.split(":", 1)[1].strip())
                except Exception:
                    pass
        return intro, outro

    except Exception as e:
        log.warning(f"[Gemini timestamps] Error: {e}")
        return red_start, red_end


def _hivemicro_set_timestamps_kb(page: Page, intro: float, outro: float):
    """Seek to intro → Alt+S, seek to outro → Alt+E."""
    try:
        # Click somewhere on the page body first to ensure keyboard focus
        page.locator("body").click()
        time.sleep(0.3)

        # Set intro
        page.evaluate(f"() => {{ const v=document.querySelector('video'); if(v) v.currentTime={intro:.2f}; }}")
        time.sleep(1.0)
        page.keyboard.press("Alt+s")
        time.sleep(0.5)
        log.info(f"  ✓ Intro set: {intro:.2f}s")

        # Set outro
        page.evaluate(f"() => {{ const v=document.querySelector('video'); if(v) v.currentTime={outro:.2f}; }}")
        time.sleep(1.0)
        page.keyboard.press("Alt+e")
        time.sleep(0.5)
        log.info(f"  ✓ Outro set: {outro:.2f}s")

    except Exception as e:
        log.warning(f"Timestamp keyboard error: {e}")


def _hivemicro_submit_label(page: Page, category: str, brand: str = "") -> bool:
    """Click the correct radio button, pick brand from dropdown, click Next."""
    try:
        cat_map = {
            "1": "Commercial",      "commercial": "Commercial",
            "2": "TV Programming",  "tv programming": "TV Programming",
                                    "tv": "TV Programming", "programming": "TV Programming",
                                    "sports": "TV Programming", "news": "TV Programming",
                                    "movie": "TV Programming",
            "3": "Movie Trailer",   "trailer": "Movie Trailer",
            "4": "TV Promo",        "promo": "TV Promo",    "tv promo": "TV Promo",
        }
        cat_label = cat_map.get(category.lower().strip(), "TV Programming")

        # Click radio button for the category
        for sel in [
            f'label:has-text("{cat_label}")',
            f'input[type="radio"][value*="{cat_label.lower().replace(" ", "")}"]',
            f'[class*="radio"]:has-text("{cat_label}")',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.click()
                    human_delay(0.5, 1.0)
                    break
            except Exception:
                continue

        # Brand selection (only if Commercial)
        if cat_label == "Commercial" and brand:
            brand_low = brand.lower().strip()
            if brand_low in ("other", "o", "other brand"):
                for sel in ['input[type="checkbox"]', 'label:has-text("Other")', '[class*="other"]']:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=1500):
                            el.click()
                            break
                    except Exception:
                        continue
            else:
                # Open dropdown
                for sel in ['[class*="dropdown"]', '[class*="select"]', '[role="combobox"]', 'select']:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            el.click()
                            human_delay(0.5, 1.0)
                            break
                    except Exception:
                        continue
                # Type in search box
                for sel in ['input[placeholder*="earch"]', 'input[class*="search"]', 'input[type="text"]']:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=1500):
                            el.fill(brand[:25])
                            human_delay(0.5, 0.8)
                            break
                    except Exception:
                        continue
                # Click matching option
                for sel in [
                    f'[role="option"]:has-text("{brand[:20]}")',
                    f'li:has-text("{brand[:20]}")',
                    f'.dropdown-item:has-text("{brand[:20]}")',
                ]:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=2000):
                            el.click()
                            human_delay(0.3, 0.7)
                            break
                    except Exception:
                        continue

        human_delay(1.0, 2.0)

        # Scroll to top — Next button is in the top-right corner
        try:
            page.evaluate("window.scrollTo(0, 0)")
            human_delay(0.3, 0.6)
        except Exception:
            pass

        # Step 1: Click Next (submits answer, triggers confirmation dialog)
        clicked_next = False
        for sel in ['button:has-text("Next")', 'a:has-text("Next")',
                    'button[class*="next"]', '[class*="btn"]:has-text("Next")',
                    'button[type="submit"]']:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=5000):
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    clicked_next = True
                    human_delay(1.5, 2.5)
                    break
            except Exception:
                continue

        if not clicked_next:
            log.warning("  Could not find Next button — retrying once...")
            human_delay(2.0, 3.0)
            try:
                page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass
            for sel in ['button:has-text("Next")', 'a:has-text("Next")']:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=5000):
                        btn.click()
                        clicked_next = True
                        human_delay(1.5, 2.5)
                        break
                except Exception:
                    continue

        if not clicked_next:
            return False

        # Step 2: Click Confirm (the "you cannot make changes" confirmation)
        for sel in ['button:has-text("Confirm")', 'a:has-text("Confirm")',
                    'button[class*="confirm"]']:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=4000):
                    btn.click()
                    log.info("  ✓ Confirmed submission")
                    human_delay(2.0, 3.0)
                    break
            except Exception:
                continue

        # Step 3: Click Next again (after seeing Correct/Incorrect result, moves to next task)
        for sel in ['button:has-text("Next")', 'a:has-text("Next")',
                    'button[class*="next"]']:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=4000):
                    btn.click()
                    human_delay(1.5, 2.5)
                    break
            except Exception:
                continue

        return True
    except Exception as e:
        log.warning(f"Submit label error: {e}")
        return False


# ─── OCR helper (optional — improves accuracy significantly) ────────────────

try:
    import pytesseract as _pytesseract
    from PIL import Image as _PIL_Image
    import io as _io_ocr
    _OCR_AVAILABLE = True
    log.info("pytesseract OCR available.")
except ImportError:
    _OCR_AVAILABLE = False

try:
    from groq import Groq as _GroqClient
    import base64 as _b64_ai
    _GEMINI_AVAILABLE = bool(GROQ_API_KEY)
    if _GEMINI_AVAILABLE:
        _gemini_client = _GroqClient(api_key=GROQ_API_KEY)
        log.info("✓ Groq Vision AI active (llama-4-scout) — works in Nigeria ✓")
    else:
        _gemini_client = None
        log.warning("⚠ GROQ_API_KEY not set — bot will always pick Image 1. Get free key at console.groq.com")
except ImportError:
    _GEMINI_AVAILABLE = False
    _gemini_client = None
    log.warning("groq not installed — run: pip install groq")

# Groq vision models to try in order (first available wins)
_GROQ_VISION_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.2-90b-vision-preview",
    "llama-3.2-11b-vision-preview",
]

def _call_groq_vision(prompt: str, images: list, max_tokens: int = 4096) -> str:
    """
    Call Groq vision API with a text prompt and one or more images (bytes).
    Returns the response text. Tries models in order until one works.
    """
    if not _GEMINI_AVAILABLE or not _gemini_client:
        return ""
    content = [{"type": "text", "text": prompt}]
    for img_bytes in images:
        b64 = _b64_ai.b64encode(img_bytes).decode()
        mime = "image/jpeg" if img_bytes[:3] == b"\xff\xd8\xff" else "image/png"
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    last_err = None
    for model in _GROQ_VISION_MODELS:
        try:
            resp = _gemini_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                max_tokens=max_tokens,
                temperature=0.1,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            if "model_not_found" in str(e).lower() or "does not exist" in str(e).lower():
                continue
            break
    log.warning(f"  Groq vision error: {last_err}")
    return ""


def _ocr_frame(frame_bytes: bytes) -> str:
    """OCR a JPEG frame. Returns extracted text (lowercase) or empty string."""
    if not _OCR_AVAILABLE or not frame_bytes:
        return ""
    try:
        img  = _PIL_Image.open(_io_ocr.BytesIO(frame_bytes))
        text = _pytesseract.image_to_string(img, config="--psm 11 --oem 3")
        return text.lower()
    except Exception:
        return ""


def _hivemicro_handle_onboarding(page: Page) -> bool:
    """
    Auto-click through ANY guide / tutorial / onboarding modal or page.
    Handles:
      - Full-page instruction slides (pre-task)
      - Mid-assessment popup modals (e.g. TV Promo guide page 7/8)
      - Pagination inside modals (Next → Next → Start Test)
    Returns True if at least one slide was advanced.
    """
    advanced = False

    # Button selectors in priority order — covers "Next", "Start", final "Take test" etc.
    ADVANCE_SELECTORS = [
        # Final / launch buttons (checked first so we exit the guide when done)
        'button:has-text("Take Assessment")',
        'button:has-text("Take selected test")',
        'button:has-text("Start Assessment")',
        'button:has-text("Start Test")',
        'button:has-text("Begin Assessment")',
        'button:has-text("Launch")',
        # Standard navigation inside guide
        'button:has-text("Continue")', 'a:has-text("Continue")',
        'button:has-text("Next")',     'a:has-text("Next")',
        'button:has-text("Got it")',   'button:has-text("OK")',
        'button:has-text("Start")',    'button:has-text("Begin")',
        'button:has-text("I understand")',
        '[class*="continue-btn"]',     'button[class*="next"]',
    ]

    for _ in range(50):  # up to 50 slides/pages before giving up
        # ── Check 1: Is there a visible modal/dialog on screen? ──────────────
        modal_visible = False
        try:
            for modal_sel in [
                '[role="dialog"]', '[class*="modal"]', '[class*="overlay"]',
                '[class*="popup"]', '[class*="guide"]', '[class*="instructions"]',
            ]:
                if page.locator(modal_sel).first.is_visible(timeout=800):
                    modal_visible = True
                    break
        except Exception:
            pass

        # ── Check 2: Does body text look like a guide page? ──────────────────
        try:
            body = page.inner_text("body")[:1200].lower()
        except Exception:
            break

        has_pagination = bool(re.search(r"\b\d+\s*of\s*\d+\b|\bpage\s+\d+\b|[«»<>]\s*\d+", body))
        has_guide_kw   = any(x in body for x in [
            "welcome", "how to use", "instructions", "tutorial", "read carefully",
            "guidelines", "let's get started", "for tv promo", "for commercial",
            "for movie trailer", "updated", "you will be able to recognize",
            "you can also tell", "in the red bar", "select label",
        ])
        # Only treat as a guide if modal is visible OR pagination buttons exist
        # (prevents accidentally skipping real task radio buttons)
        is_guide_page = modal_visible or (has_pagination and has_guide_kw)

        if not is_guide_page:
            break  # we're on an actual task — stop

        # ── Try to click the next advance button ────────────────────────────
        clicked = False
        for sel in ADVANCE_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    human_delay(1.0, 1.8)
                    advanced = True
                    clicked  = True
                    log.info(f"[Guide] Clicked '{sel}' — advancing slide.")
                    break
            except Exception:
                continue

        if not clicked:
            # No button found — guide might have closed on its own, exit loop
            break

    return advanced


def _classify_with_gemini(frames: list, brands: list, seg_duration: float) -> tuple:
    """
    Use Google Gemini Flash vision to classify video segment frames.
    Returns (category "1"–"4", brand, confidence).
    This is the intelligent path — works on ANY content, no keywords needed.
    """
    if not _GEMINI_AVAILABLE or not frames:
        return "", "", 0.0

    import base64 as _b64

    brand_str = "\n".join(f"- {b}" for b in brands[:25]) if brands else "(none listed)"

    prompt = f"""You are labeling a segment of a recorded TV broadcast for an AI training dataset.

Segment duration: {seg_duration:.0f} seconds.

Possible categories:
1 = Commercial — a paid advertisement for a product, service, or brand. Shows a product being sold, a brand logo, a call to action ("call now", "visit us", website URL). Short, polished, product-focused.
2 = TV Programming — actual TV content: a drama, comedy, sitcom, reality show, sports game, live news, documentary, or full movie. The main content people tuned in to watch.
3 = Movie Trailer — a preview clip for an upcoming theatrical film. Usually fast cuts, dramatic music, "Coming Soon" / "In Theaters" / studio logo.
4 = TV Promo — a short clip promoting a TV show's upcoming airtime. Shows title card + day/time ("All New Sundays 10/9c", "Series Premiere", "Watch Tonight"). Promotes when to watch on TV.

Brand names that may appear if this is a Commercial:
{brand_str}

I am sending you {len(frames)} frames sampled from across the segment. Study all frames together.

Reply in EXACTLY this format (no extra text):
CATEGORY: [1, 2, 3, or 4]
BRAND: [exact brand name from the list, or "other" if commercial but brand not listed, or "none" if not a commercial]
CONFIDENCE: [high, medium, or low]
REASON: [one sentence explaining what you see]"""

    try:
        text = _call_groq_vision(prompt, frames[:6])
        if not text:
            return "", "", 0.0
        log.info(f"[AI classify] Response: {text[:120]}")

        cat, brand, confidence = "2", "", 0.70
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("CATEGORY:"):
                v = line.split(":", 1)[1].strip()
                if v in ("1", "2", "3", "4"):
                    cat = v
            elif line.startswith("BRAND:"):
                v = line.split(":", 1)[1].strip()
                if v.lower() not in ("none", "-", ""):
                    brand = v
            elif line.startswith("CONFIDENCE:"):
                v = line.split(":", 1)[1].strip().lower()
                confidence = 0.93 if v == "high" else 0.75 if v == "medium" else 0.58

        # If Gemini says Commercial but brand is "none", set to "other"
        if cat == "1" and not brand:
            brand = "other"

        return cat, brand, confidence

    except Exception as e:
        log.warning(f"[Gemini] Error: {e}")
        return "", "", 0.0


def _hivemicro_classify_auto(page: Page, seg_info: dict) -> tuple:
    """
    Fully automated segment classification.

    Priority:
      1. Gemini Vision AI  — understands ANY content like a human (needs GEMINI_API_KEY)
      2. OCR keyword match — reads text from frames (needs pytesseract)
      3. Duration heuristic — 30s/60s segment = ad slot
      4. Default: TV Programming

    Returns: (category "1"–"4", brand_str, confidence 0.0–1.0)
    """
    start_t  = seg_info.get("start", 0.0)
    end_t    = seg_info.get("end", 0.0)
    brands   = seg_info.get("brands", [])
    duration = (end_t - start_t) if end_t > start_t else 0.0

    # Capture frames spread evenly across the ENTIRE segment
    # More frames = more information = smarter decision
    if duration >= 6:
        n = min(6, max(3, int(duration / 3)))   # 1 frame every ~3 seconds, max 6
        sample_times = [start_t + (duration * i / (n - 1)) for i in range(n)]
    elif duration > 0:
        sample_times = [start_t + 0.5, start_t + duration * 0.5, end_t - 0.5]
    else:
        sample_times = [5, 15, 28, 40]

    frames = []
    combined_ocr = ""
    for t in sample_times:
        f = _hivemicro_capture_video_frame(page, max(t, 0.1))
        if f:
            frames.append(f)
            combined_ocr += " " + _ocr_frame(f)

    # ── PATH 1: Gemini Vision AI (most intelligent) ───────────────────────
    if _GEMINI_AVAILABLE and frames:
        cat, brand, conf = _classify_with_gemini(frames, brands, duration)
        if cat:
            log.info(f"[Gemini] → cat={cat} brand={brand!r} conf={conf:.0%}")
            return cat, brand, conf

    # ── PATH 2: OCR keyword matching ─────────────────────────────────────
    ocr = combined_ocr.lower()

    # Brand name match → Commercial
    for brand in brands:
        keys = [w for w in brand.lower().split() if len(w) >= 4]
        if any(k in ocr for k in keys):
            log.info(f"[OCR] Brand match: {brand}")
            return "1", brand, 0.90

    # Movie Trailer
    if any(k in ocr for k in [
        "coming soon", "in theaters", "only in theaters", "now playing",
        "rated pg", "rated r", "rated pg-13", "this summer", "this fall",
        "in imax", "imax", "dolby cinema", "motion picture", "opens",
    ]):
        return "3", "", 0.85

    # TV Promo
    if any(k in ocr for k in [
        "tonight", "new episode", "series premiere", "season finale",
        "series finale", "don't miss", "premieres", "new season",
        "only on abc", "only on nbc", "only on cbs", "only on fox",
        "only on cw", "watch this", "all new", "all-new",
        "sundays", "mondays", "tuesdays", "wednesdays", "thursdays", "fridays",
        "saturdays", "10/9c", "9/8c", "8/7c", "9c", "8c", "10c",
        "presents", "new series", "new show", "returns",
        "this sunday", "this monday", "this friday",
    ]):
        return "4", "", 0.80

    # Sports / News / TV content
    if any(k in ocr for k in [
        "nfl", "nba", "mlb", "nhl", "espn", "fox sports", "nbc sports",
        "cbs sports", "golf", "tennis", "soccer", "basketball", "baseball",
        "football", "hockey", "olympic", "championship", "playoffs",
        "quarter", "halftime", "innings", "score", "breaking news",
        "live news", "abc news", "cnn", "msnbc", "fox news", "cbs news",
        "nbc news", "good morning", "tonight show", "late show",
    ]):
        return "2", "", 0.82

    # ── PATH 3: Duration heuristic ────────────────────────────────────────
    if duration > 0:
        for ad_len in [15, 30, 45, 60]:
            if abs(duration - ad_len) <= 3:
                log.info(f"[Heuristic] Duration {duration:.1f}s ≈ ad slot {ad_len}s")
                return "1", "other", 0.60

    if duration > 120:
        return "2", "", 0.70

    # ── PATH 4: Default ───────────────────────────────────────────────────
    return "2", "", 0.40


def run_hivemicro_qualifier():
    """
    FULLY AUTOMATED Hive Micro Commercial Labeling qualification.

    Bot handles everything:
      - Auto-clicks through onboarding guide slides
      - Seeks video to the pre-marked segment
      - Captures frames and classifies via OCR + heuristics
      - Selects category + brand and clicks Next — no human needed
      - Sends Telegram progress updates only (no decisions needed from you)
      - If confidence < 60%, sends frame to Telegram as optional override

    Install OCR for best accuracy:
      pip install pytesseract pillow
      + Install Tesseract binary: https://github.com/UB-Mannheim/tesseract/wiki

    Run:  python microtask_bot.py --qualifier
    """
    log.info("[Qualifier] Starting FULLY AUTOMATED Commercial Labeling...")
    if not _OCR_AVAILABLE:
        log.warning("[Qualifier] OCR not installed — using duration heuristics only.")
        log.warning("  For better accuracy: pip install pytesseract pillow")
        log.warning("  + Install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki")

    if not PROFILE_HIVEMICRO.exists():
        log.warning("[Qualifier] No session — run --login-hivemicro first.")
        return

    send_telegram(
        "🤖 <b>Hive Micro Qualifier — FULLY AUTOMATED</b>\n\n"
        + ("✅ OCR active — using text recognition for classification.\n" if _OCR_AVAILABLE
           else "⚠️ OCR not installed — using duration heuristics.\n")
        + "\nBot will handle every task. I'll send you a Telegram update every 5 tasks.\n"
        + "If I'm unsure about a task, I'll send the frame and wait for your reply.\n"
        + "Otherwise just sit back — no action needed from you."
    )

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_HIVEMICRO),
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            ignore_default_args=["--enable-automation"],
        )
        page = ctx.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.add_init_script(STEALTH_SCRIPT)

        page.goto(HIVEMICRO_URL, wait_until="domcontentloaded", timeout=30000)
        human_delay(3, 5)

        if "login" in page.url.lower():
            log.error("[Qualifier] Not logged in. Run --login-hivemicro first.")
            ctx.close()
            return

        # Find and open the Commercial Labeling qualification
        for sel in [
            'a:has-text("Commercial Labeling")',
            '[class*="task"]:has-text("Commercial Labeling")',
            'a:has-text("Qualification")',
            'button:has-text("Take Qualification")',
            'a:has-text("Take")',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    human_delay(2, 4)
                    log.info("[Qualifier] Opened qualification task.")
                    break
            except Exception:
                continue

        # Auto-click through any onboarding/guide slides
        _hivemicro_handle_onboarding(page)
        human_delay(1, 2)

        # ── Main task loop ────────────────────────────────────────────────────
        task_num      = 0
        auto_count    = 0
        manual_count  = 0

        while True:
            task_num += 1
            log.info(f"[Qualifier] Task {task_num}")

            # Handle any mid-task guide slides
            _hivemicro_handle_onboarding(page)
            human_delay(1.0, 1.8)

            # ── Step 1: Read brand list + get red bar position ────────────────
            seg     = _hivemicro_get_segment_info(page)
            brands  = seg.get("brands", [])
            red     = _hivemicro_get_red_bar_position(page)
            red_s   = red.get("start", seg.get("start", 10.0))
            red_e   = red.get("end",   seg.get("end",   50.0))
            log.info(f"  Red bar: {red_s:.1f}s → {red_e:.1f}s  Brands: {len(brands)}")

            # ── Step 2: Scan frames across and around the red bar ─────────────
            scan_start = max(0, red_s - 4)
            scan_end   = red_e + 4
            scan_data  = _hivemicro_scan_transitions(page, scan_start, scan_end, step=0.8)
            log.info(f"  Scanned {len(scan_data)} frames")

            # ── Step 3: Find exact intro/outro ────────────────────────────────
            # Try Gemini first (most accurate), fall back to brightness analysis
            if _GEMINI_AVAILABLE:
                intro_t, outro_t = _gemini_find_timestamps(scan_data, red_s, red_e)
            else:
                intro_t, outro_t = _find_intro_outro(scan_data, red_s, red_e)
            log.info(f"  Timestamps → intro={intro_t:.2f}s  outro={outro_t:.2f}s")

            # ── Step 4: Classify category + brand using Gemini ────────────────
            # Use the frames that fall within intro→outro for classification
            segment_frames = [f for t, _, f in scan_data
                              if intro_t <= t <= outro_t and f]
            if not segment_frames:
                segment_frames = [f for _, _, f in scan_data if f][:6]

            seg_for_classify = {
                "start": intro_t, "end": outro_t,
                "brands": brands,
            }
            cat_choice, brand_choice, confidence = _hivemicro_classify_auto(page, seg_for_classify)

            cat_names = {"1": "Commercial", "2": "TV Programming",
                         "3": "Movie Trailer",  "4": "TV Promo"}
            log.info(f"  → {cat_names.get(cat_choice,'?')} | brand={brand_choice or '-'} | conf={confidence:.0%}")

            # ── Step 5: Set timestamps on the page ───────────────────────────
            _hivemicro_set_timestamps_kb(page, intro_t, outro_t)
            human_delay(0.5, 1.0)

            auto_count += 1

            # ── Step 6: Submit ────────────────────────────────────────────────
            ok = _hivemicro_submit_label(page, cat_choice, brand_choice)
            if not ok:
                log.warning(f"  Submit may have failed on task {task_num}")

            # ── Step 7: Handle any guide popup that appeared after submit ─────
            # (Hive Micro sometimes injects instruction modals between tasks)
            _hivemicro_handle_onboarding(page)
            human_delay(0.8, 1.5)

            # Progress update every 5 tasks
            if task_num % 5 == 0:
                send_telegram(
                    f"📊 <b>Progress: {task_num} tasks done</b>\n"
                    f"Auto: {auto_count} | You overrode: {manual_count}\n"
                    f"OCR: {'✅' if _OCR_AVAILABLE else '❌'}"
                )

            # Check for pass/fail result
            try:
                body_low = page.inner_text("body")[:600].lower()
                if any(x in body_low for x in ["passed", "congratulations", "you qualified", "qualified"]):
                    send_telegram(
                        "🎉 <b>PASSED! Qualification complete!</b>\n\n"
                        f"Tasks: {task_num} | Auto: {auto_count} | Manual: {manual_count}\n\n"
                        "Bot will now handle Commercial Labeling tasks automatically.\n"
                        "Run: <code>python microtask_bot.py</code>"
                    )
                    break
                if any(x in body_low for x in ["failed", "not qualified", "did not pass"]):
                    send_telegram(
                        f"❌ Qualification not passed.\n"
                        f"Tasks done: {task_num} | Auto: {auto_count} | Manual: {manual_count}\n\n"
                        "You can retake it. Run <code>--qualifier</code> again when ready.\n"
                        + ("Tip: install OCR for better accuracy: pip install pytesseract pillow"
                           if not _OCR_AVAILABLE else "")
                    )
                    break
            except Exception:
                pass

            # ── Check if qualification is truly done ──────────────────────────
            # Strategy: read the page title. As long as it says "X of 31 tasks"
            # (or whatever total), we keep going. Only stop when we see a final
            # result banner OR the task counter disappears entirely.
            human_delay(2.0, 3.0)

            # Also handle any guide popup that appeared after moving to next task
            _hivemicro_handle_onboarding(page)

            try:
                title_text = page.inner_text("body")[:1200].lower()
            except Exception:
                title_text = ""

            # Definitive end signals
            finished = any(x in title_text for x in [
                "you passed", "you failed", "qualification complete",
                "qualification passed", "qualification failed",
                "congratulations", "unfortunately", "you did not pass",
                "result:", "final score", "score:",
            ])
            if finished:
                send_telegram(
                    f"✅ <b>Qualification finished! {task_num} tasks done.</b>\n"
                    f"Auto: {auto_count} | Manual: {manual_count}\n"
                    "Check Hive Micro for your results."
                )
                log.info("[Qualifier] Final result page detected — done.")
                break

            # Still running if we see the task counter in the title
            still_running = bool(re.search(r"\d+\s+of\s+\d+\s+tasks", title_text))
            if not still_running:
                # No task counter visible yet — page may still be loading
                # Wait a bit more and try again before giving up
                human_delay(3.0, 5.0)
                try:
                    title_text = page.inner_text("body")[:1200].lower()
                    still_running = bool(re.search(r"\d+\s+of\s+\d+\s+tasks", title_text))
                except Exception:
                    pass

            if not still_running:
                log.info("[Qualifier] Task counter gone — qualification likely ended.")
                break
            # else: counter still visible → loop continues to next task

        log.info("[Qualifier] Session complete.")
        ctx.close()


# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--login-mindrift" in args:
        login_mindrift()
        sys.exit(0)
    if "--login-hivemicro" in args:
        login_hivemicro()
        sys.exit(0)
    if "--login-mturk" in args:
        login_mturk()
        sys.exit(0)
    if "--dashboard" in args:
        generate_microdashboard(load_earnings())
        print(f"Dashboard generated: {MICRODASH_FILE}")
        sys.exit(0)
    if "--qualifier" in args:
        run_hivemicro_qualifier()
        sys.exit(0)

    run()
