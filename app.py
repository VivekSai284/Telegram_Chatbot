import os
import re
import asyncio
import logging
import random
import threading
from datetime import datetime, time
import pytz
from flask import Flask
from google import genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==========================================
# 1. FLASK WEB SERVER (Health Check)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting Flask server on port {port}...")
    app.run(host="0.0.0.0", port=port)

# ==========================================
# 2. CONFIGURATIONS & STATE
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
MY_USER_ID = int(os.environ.get("MY_USER_ID", "0"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

TIMEZONE = pytz.timezone("Asia/Kolkata")

SYSTEM_INSTRUCTION = (
    "You are Alex, a close friend who texts casually like a human. "
    "CRITICAL RULE 1: Respond in VERY FEW WORDS. Strictly keep replies under 10 words per message. "
    "CRITICAL RULE 2: You speak casually and can use a light mix of English and Telugu/Teleglish phrases when appropriate. "
    "CRITICAL RULE 3: DO NOT ask 'Em chesthunnv?'. Talk about what you are doing, ask distinct questions, or reference past things discussed earlier in the chat. "
    "CRITICAL RULE 4: Do not use special characters, punctuation, exclamations except question marks when asking a question."
)

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Active chat session holding conversation history
chat_session = ai_client.aio.chats.create(
    model="gemini-3.5-flash-lite",
    config={
        "system_instruction": SYSTEM_INSTRUCTION,
        "temperature": 0.85,
        "max_output_tokens": 30
    }
)

is_active = True
outreach_task = None
awaiting_final_bye = False  # Set to True when the bot just sent a 'bye'

meal_status = {
    "breakfast": False,
    "lunch": False,
    "dinner": False
}

# ==========================================
# 3. HELPER & LLM FUNCTIONS
# ==========================================
def clean_response(text: str) -> str:
    """Removes all special characters, symbols, and punctuation except '?'."""
    cleaned = re.sub(r'[^a-zA-Z0-9\s?]', '', text)
    return re.sub(r'\s+', ' ', cleaned).strip()

def get_current_time():
    return datetime.now(TIMEZONE).time()

def check_meal_window():
    now = get_current_time()
    if time(8, 0) <= now <= time(12, 0) and not meal_status["breakfast"]:
        return "breakfast"
    elif time(13, 0) <= now <= time(15, 0) and not meal_status["lunch"]:
        return "lunch"
    elif time(20, 0) <= now <= time(22, 0) and not meal_status["dinner"]:
        return "dinner"
    return None

async def query_llm(user_prompt: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            response = await chat_session.send_message(user_prompt)
            raw_text = response.text.strip()
            return clean_response(raw_text)
        except Exception as e:
            print(f"Gemini API Error (Attempt {attempt + 1}/{retries}): {e}")
            await asyncio.sleep(2)
    return "Hey tiny delay here what are you up to"

# ==========================================
# 4. BACKGROUND TASKS & HANDLERS
# ==========================================
async def wait_and_send_outreach(bot):
    global is_active
    try:
        # Pick random delay between 1 hour (3600s) and 4 hours (14400s)
        delay_seconds = random.randint(3600, 14400)
        print(f"Next outreach scheduled in {delay_seconds // 60} minutes.")
        await asyncio.sleep(delay_seconds)

        if is_active:
            outreach_prompts = [
                "Send a short casual text sharing what you are up to right now in under 6 words.",
                "Send a friendly check-in referencing something fun or random in under 6 words.",
                "Ask a short, casual context-aware question based on previous messages in under 6 words without saying em chesthunnv."
            ]
            prompt = random.choice(outreach_prompts)
            reply = await query_llm(prompt)
            await bot.send_message(chat_id=MY_USER_ID, text=reply)
            reset_timer(bot)
    except asyncio.CancelledError:
        pass

def reset_timer(bot):
    global outreach_task
    if outreach_task and not outreach_task.done():
        outreach_task.cancel()
    outreach_task = asyncio.create_task(wait_and_send_outreach(bot))

async def daily_good_morning_loop(bot):
    while True:
        now = datetime.now(TIMEZONE)
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target.replace(day=now.day + 1)
        seconds_until_8am = (target - now).total_seconds()
        await asyncio.sleep(seconds_until_8am)
        for key in meal_status:
            meal_status[key] = False
        if is_active:
            reply = await query_llm("Send me a brief Good Morning text in under 5 words.")
            await bot.send_message(chat_id=MY_USER_ID, text=reply)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active, outreach_task, awaiting_final_bye

    print(f"Received message from User ID: {update.effective_user.id}")

    if MY_USER_ID != 0 and update.effective_user.id != MY_USER_ID:
        print(f"Blocked message from unauthorized user ID: {update.effective_user.id}")
        return

    is_active = True
    user_text = update.message.text.strip().lower()
    goodbye_words = ["bye", "bye bye", "ttyl", "gotta go", "catch you later", "goodnight", "gn"]
    is_goodbye = any(word in user_text for word in goodbye_words)

    # RULE: If bot said 'bye' and user says 'bye' back -> DO NOT reply, just stop & schedule outreach
    if awaiting_final_bye and is_goodbye:
        awaiting_final_bye = False
        print("Second bye received from user. Stopping conversation without replying.")
        reset_timer(context.bot)
        return

    # RULE: If user says 'bye' for the first time -> Bot replies 'bye'
    if is_goodbye:
        awaiting_final_bye = True
        reply = await query_llm(f"The user said '{update.message.text}'. Say a short casual bye in under 4 words.")
        await update.message.reply_text(reply)
        return

    # User sent a normal message (even if awaiting_final_bye was True), so clear goodbye state & continue
    awaiting_final_bye = False

    # Meal tracking
    if any(k in user_text for k in ["ate", "eaten", "had food", "had breakfast", "had lunch", "had dinner", "finished eating", "thinna"]):
        current_meal = check_meal_window()
        if current_meal:
            meal_status[current_meal] = True
        elif time(6, 0) <= get_current_time() <= time(12, 0):
            meal_status["breakfast"] = True
        elif time(12, 0) <= get_current_time() <= time(18, 0):
            meal_status["lunch"] = True
        elif time(18, 0) <= get_current_time() <= time(23, 59):
            meal_status["dinner"] = True

    await context.bot.send_chat_action(chat_id=MY_USER_ID, action="typing")
    pending_meal = check_meal_window()

    if pending_meal:
        instruction = (
            f"The user said: '{update.message.text}'. "
            f"Acknowledge what they said, but also ask if they have eaten their {pending_meal} yet. "
            f"STRICT LIMIT: Keep response under 10 words total."
        )
    else:
        instruction = (
            f"The user said: '{update.message.text}'. Respond casually, bringing up previous context or random topics. "
            f"STRICT LIMIT: Maximum 10 words total."
        )

    response_text = await query_llm(instruction)
    await update.message.reply_text(response_text)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active, outreach_task
    is_active = False
    if outreach_task and not outreach_task.done():
        outreach_task.cancel()
    await update.message.reply_text("Paused! Text me anytime to resume.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_active
    is_active = True
    await update.message.reply_text("Hey! Online and ready.")

async def post_init(application: Application):
    asyncio.create_task(daily_good_morning_loop(application.bot))

# ==========================================
# 5. ENTRY POINT
# ==========================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    app_bot = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .post_init(post_init)
        .build()
    )

    app_bot.add_handler(CommandHandler("start", start_command))
    app_bot.add_handler(CommandHandler("stop", stop_command))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Starting Telegram Bot Polling...")
    app_bot.run_polling()