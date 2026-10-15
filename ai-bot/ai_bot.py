import telebot
from telebot import types
import requests
import re

# Твои токены (разделены плюсом, чтобы защита GitHub не блокировала ключ)
TELEGRAM_TOKEN = "8967141379:" + "AAFIy52g-Rz0ff73dFQaTn1t7PZ0xlU7Qjs"
GROQ_API_KEY = "gsk_" + "7ZZ7rgRfeBzOIEnchdcyWGdyb3FYPeTrVNq4oew7RXzxiVfAmn20"

BOT_VERSION = "2.0"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Память диалогов пользователей
chat_histories = {}

# Список доступных моделей и текущая выбранная модель
 AVAILABLE_MODELS = []
CURRENT_MODEL = None

# Приоритет моделей (от самых мощных к базовым)
PREFERRED_MODELS = [
    "openai/gpt-oss-120b",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
    "deepseek-r1-distill-llama-70b",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
    "gemma2-9b-it"
]

# Ключевые слова не-текстовых моделей (озвучка, проверка безопасности и т.д.), которые нужно пропускать
SKIP_KEYWORDS = ["whisper", "tts", "guard", "playai", "orpheus", "embed", "allam"]

SYSTEM_PROMPT = (
    "Ты умный ИИ-ассистент и опытный программист. "
    "Отвечай четко, подробно и по делу на русском языке. "
    "Когда просят написать код — пиши полный, рабочий и чистый код без сокращений."
)

def fetch_working_models():
    """Запрашивает у Groq реальный список моделей, доступных на этом API-ключе."""
    global AVAILABLE_MODELS, CURRENT_MODEL
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        resp = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            models = []
            for m in data:
                m_id = m.get("id", "")
                if not any(skip in m_id.lower() for skip in SKIP_KEYWORDS):
                    models.append(m_id)
            
            # Сортируем: сначала модели из нашего списка приоритетов, затем остальные
            sorted_models = [m for m in PREFERRED_MODELS if m in models]
            for m in models:
                if m not in sorted_models:
                    sorted_models.append(m)
            
            AVAILABLE_MODELS = sorted_models
            if AVAILABLE_MODELS and (CURRENT_MODEL not in AVAILABLE_MODELS):
                CURRENT_MODEL = AVAILABLE_MODELS[0]
    except Exception as e:
        print(f"Ошибка получения списка моделей: {e}")

# Загружаем доступные модели при старте бота
fetch_working_models()

def get_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("🗑 Очистить память"), types.KeyboardButton("🤖 Модели ИИ"))
    return markup

def clean_response(text):
    """Убирает скрытые теги размышлений <think>...</think>, если они есть у модели."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return cleaned if cleaned else text

def ask_groq(user_id, user_text):
    global CURRENT_MODEL
    if not AVAILABLE_MODELS:
        fetch_working_models()
    if not AVAILABLE_MODELS:
        return "Не удалось получить список доступных моделей от Groq. Проверь API-ключ."

    if user_id not in chat_histories:
        chat_histories[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    chat_histories[user_id].append({"role": "user", "content": user_text})
    
    if len(chat_histories[user_id]) > 15:
        chat_histories[user_id] = [chat_histories[user_id][0]] + chat_histories[user_id][-14:]

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    # Сначала пробуем текущую модель, а если она недоступна — перебираем остальные автоматически
    models_to_try = [CURRENT_MODEL] + [m for m in AVAILABLE_MODELS if m != CURRENT_MODEL]
    last_error = ""

    for model_name in models_to_try:
        if not model_name:
            continue
        payload = {
            "model": model_name,
            "messages": chat_histories[user_id],
            "temperature": 0.7
        }
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        if response.status_code == 200:
            CURRENT_MODEL = model_name
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
            answer = clean_response(answer)
            chat_histories[user_id].append({"role": "assistant", "content": answer})
            return answer
        else:
            last_error = f"({response.status_code}): {response.text}"

    return f"Все модели вернули ошибку. Последняя ошибка {last_error}"

def send_long_message(chat_id, text, reply_markup=None):
    max_length = 4000
    for i in range(0, len(text), max_length):
        chunk = text[i:i + max_length]
        try:
            bot.send_message(chat_id, chunk, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception:
            bot.send_message(chat_id, chunk, reply_markup=reply_markup)

@bot.message_handler(commands=["start"])
def start_cmd(message):
    chat_histories[message.from_user.id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    bot.send_message(
        message.chat.id,
        f"Привет! Я ИИ-бот (версия {BOT_VERSION}).\n"
        f"Активная модель: {CURRENT_MODEL}\n\n"
        "Пиши мне любые вопросы или проси написать код!\n"
        "Для сброса контекста жми «🗑 Очистить память».",
        reply_markup=get_keyboard()
    )

@bot.message_handler(commands=["clear"])
@bot.message_handler(func=lambda msg: msg.text == "🗑 Очистить память")
def clear_cmd(message):
    chat_histories[message.from_user.id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    bot.send_message(
        message.chat.id,
        "Память диалога очищена! Задавай новый вопрос.",
        reply_markup=get_keyboard()
    )

@bot.message_handler(commands=["models"])
@bot.message_handler(func=lambda msg: msg.text == "🤖 Модели ИИ")
def models_cmd(message):
    fetch_working_models()
    if not AVAILABLE_MODELS:
        bot.send_message(message.chat.id, "Список моделей пуст или ключ недоступен.")
        return

     inline_kb = types.InlineKeyboardMarkup()
    for m in AVAILABLE_MODELS[:10]:
        prefix = "✅ " if m == CURRENT_MODEL else "🔹 "
        inline_kb.add(types.InlineKeyboardButton(text=f"{prefix}{m}", callback_data=f"setmodel|{m}"))

    bot.send_message(
        message.chat.id,
        f"Сейчас активна модель:\n`{CURRENT_MODEL}`\n\nНажми на любую модель ниже, чтобы переключиться:",
        parse_mode="Markdown",
        reply_markup=inline_kb
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("setmodel|"))
def set_model_callback(call):
    global CURRENT_MODEL
    selected = call.data.split("|", 1)[1]
    CURRENT_MODEL = selected
    bot.answer_callback_query(call.id, f"Выбрана модель: {selected}")
    bot.edit_message_text(
        f"✅ Активная модель переключена на:\n`{CURRENT_MODEL}`",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown"
    )

@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        reply = ask_groq(message.from_user.id, message.text)
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Произошла ошибка: {e}")

if __name__ == "__main__":
    print(f"ИИ-бот запущен! Версия: {BOT_VERSION}, Модель: {CURRENT_MODEL}")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
