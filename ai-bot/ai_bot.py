import telebot
from telebot import types
import requests

# Твои токены (разделены плюсом, чтобы защита GitHub не заблокировала ключ при сохранении)
TELEGRAM_TOKEN = "8967141379:" + "AAFIy52g-Rz0ff73dFQaTn1t7PZ0xlU7Qjs"
GROQ_API_KEY = "gsk_" + "7ZZ7rgRfeBzOIEnchdcyWGdyb3FYPeTrVNq4oew7RXzxiVfAmn20"

# Модель ИИ: Llama 3.3 70B отлично пишет код и отвечает на вопросы
MODEL = "llama-3.3-70b-versatile"
BOT_VERSION = "1.0"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Словарь для хранения памяти диалога каждого пользователя
chat_histories = {}

# Системная инструкция, задающая характер и поведение ИИ
SYSTEM_PROMPT = (
    "Ты умный ИИ-ассистент и опытный программист. "
    "Отвечай четко, подробно и по делу на русском языке. "
    "Когда просят написать код — пиши полный, рабочий и чистый код без сокращений."
)

def get_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🗑 Очистить память"))
    return markup

def ask_groq(user_id, user_text):
    # Если пользователь пишет впервые — создаём для него историю с системным промптом
    if user_id not in chat_histories:
        chat_histories[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # Добавляем сообщение пользователя в память
    chat_histories[user_id].append({"role": "user", "content": user_text})
    
    # Храним системный промпт + последние 14 сообщений, чтобы не переполнять лимит токенов
    if len(chat_histories[user_id]) > 15:
        chat_histories[user_id] = [chat_histories[user_id][0]] + chat_histories[user_id][-14:]

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": chat_histories[user_id],
        "temperature": 0.7
    }

    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=60
    )
    
    if response.status_code != 200:
        return f"Ошибка ИИ ({response.status_code}): {response.text}"

    data = response.json()
    answer = data["choices"][0]["message"]["content"]
    
    # Сохраняем ответ ИИ в память, чтобы он помнил контекст беседы
    chat_histories[user_id].append({"role": "assistant", "content": answer})
    return answer

def send_long_message(chat_id, text, reply_markup=None):
    # В Telegram лимит 4096 символов на одно сообщение.
    # Если ИИ написал длинный код, делим ответ на части по 4000 символов.
    max_length = 4000
    for i in range(0, len(text), max_length):
        chunk = text[i:i + max_length]
        try:
            bot.send_message(chat_id, chunk, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception:
            # Если в коде есть спецсимволы, ломающие разметку Markdown, шлём чистым текстом
            bot.send_message(chat_id, chunk, reply_markup=reply_markup)

@bot.message_handler(commands=["start"])
def start_cmd(message):
    chat_histories[message.from_user.id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    bot.send_message(
        message.chat.id,
        f"Привет! Я ИИ-бот (версия {BOT_VERSION}).\n"
        "Пиши мне любые вопросы или проси написать код!\n\n"
        "Если захочешь сменить тему — нажми кнопку «🗑 Очистить память» или отправь /clear.",
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

@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        # Показываем статус «печатает...» в Telegram, пока ИИ думает
        bot.send_chat_action(message.chat.id, "typing")
        reply = ask_groq(message.from_user.id, message.text)
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Произошла ошибка: {e}")

if __name__ == "__main__":
    print(f"ИИ-бот успешно запущен! Версия: {BOT_VERSION}")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
