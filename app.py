import os
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
# 2. CONFIGURATIONS
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
MY_USER_ID = int(os.environ.get("MY_USER_ID", "0"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

TIMEZONE = pytz.timezone("Asia/Kolkata")
OUTREACH_DELAY_SECONDS = 3600  

SYSTEM_INSTRUCTION = (
    "You are Alex, a close friend who texts casually like a human. "
    "CRITICAL RULE 1: Respond in VERY FEW WORDS. Strictly keep replies under 10 words per message. "
    "CRITICAL RULE 2: You speak casually and can use a light mix of English and Telugu/Teleglish phrases like 'em chesthunnv?' when appropriate. "
    "CRITICAL RULE 3: Keep answers brief, natural, and friendly."
)

ai_client = genai.Client(api_key=GEMINI_API_KEY)

async_chat_session = ai_client.aio.chats.create(
    model="gemini-2.5-flash-lite",
    config={
        "system_instruction": SYSTEM_INSTRUCTION,
        "temperature": 0.7,
        "max_output_tokens": 30
    }
)

is_active = True
outreach_task = None

meal_status = {
    "breakfast": False,
    "lunch": False,
    "dinner": False
}

# ==========================================
# 3. HELPER & LLM FUNCTIONS
# ==========================================
def get_current_time():
    return datetime.now(TIMEZONE).time()

def check_meal_window():
    now = get_current_time()
    if time(10, 0) <= now <= time(11, 0) and not meal_status["breakfast"]:
        return "breakfast"
    elif time(13, 0) <= now <= time(14, 0) and not meal_status["lunch"]:
        return "lunch"
    elif time(21, 0) <= now <= time(22, 0) and not meal_status["dinner"]:
        return "dinner"
    return None

async def query_llm(user_prompt: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            response = await async_chat_session.send_message(user_prompt)
            return response.text.strip()
        except Exception as e:
            print(f"Gemini API Error (Attempt {attempt + 1}/{retries}): {e}")
            await asyncio.sleep(2)
    return "Hey, tiny delay here! Em chesthunnav?"

# ==========================================
# 4. BACKGROUND TASKS & HANDLERS
# ==========================================
async def wait_and_send_outreach(bot):
    global is_active
    try:
        await asyncio.sleep(OUTREACH_DELAY_SECONDS)
        if is_active:
            outreach_prompts = [
                "Ask me 'em chesthunnv?' in under 5 words.",
                "Ask what I'm up to right now in under 6 words.",
                "Send a short casual check-in message in under 6 words."
            ]
            prompt = random.choice(outreach_prompts)
            reply = await query_llm(prompt)
            await bot.send_message(chat_id=MY_USER_ID, text=reply)
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
    global is_active, outreach_task

    # Print user ID to Render logs for debugging
    print(f"Received message from User ID: {update.effective_user.id}")

    if MY_USER_ID != 0 and update.effective_user.id != MY_USER_ID:
        print(f"Blocked message from unauthorized user ID: {update.effective_user.id}")
        return

    is_active = True
    if outreach_task and not outreach_task.done():
        outreach_task.cancel()

    user_text = update.message.text.strip().lower()
    goodbye_words = ["bye", "bye bye", "ttyl", "gotta go", "catch you later", "goodnight", "gn"]
    if any(word in user_text for word in goodbye_words):
        reply = await query_llm(f"The user said '{update.message.text}'. Say a short bye in under 5 words.")
        await update.message.reply_text(reply)
        reset_timer(context.bot)
        return

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
            f"The user said: '{update.message.text}'. Respond casually. STRICT LIMIT: Maximum 10 words total."
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

    # 1. Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. Build Telegram Bot
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

    # 3. Run Telegram Bot long polling directly on the main thread
    print("Starting Telegram Bot Polling...")
    app_bot.run_polling()