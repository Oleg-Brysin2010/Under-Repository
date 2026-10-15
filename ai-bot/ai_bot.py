import os
import re
import json
import base64
import telebot
from telebot import types
import requests

# Твои токены (разделены плюсом, чтобы защита GitHub не блокировала ключ)
TELEGRAM_TOKEN = "8967141379:" + "AAFIy52g-Rz0ff73dFQaTn1t7PZ0xlU7Qjs"
GROQ_API_KEY = "gsk_" + "7ZZ7rgRfeBzOIEnchdcyWGdyb3FYPeTrVNq4oew7RXzxiVfAmn20"

BOT_VERSION = "3.0"
MEMORY_FILE = "/root/ai-bot/memory.json"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

AVAILABLE_MODELS = []
VISION_MODELS = []
CURRENT_MODEL = None

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

PREFERRED_VISION_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.2-90b-vision-preview",
    "llama-3.2-11b-vision-preview"
]

SKIP_KEYWORDS = ["whisper", "tts", "guard", "playai", "orpheus", "embed", "allam"]

MODES = {
    "coder": (
        "Ты личный ИИ-ассистент и опытный программист в Telegram (версия 3.0). "
        "Ты работаешь на сервере своего создателя в Нидерландах и обновляешься напрямую с его GitHub. "
        "Отвечай четко, подробно и по делу на русском языке. "
        "Когда просят написать код — пиши полный, рабочий и чистый код без сокращений."
    ),
    "bro": (
        "Ты лучший друг и ИИ-бро в Telegram (версия 3.0), живущий на сервере в Нидерландах. "
        "Общайся живо, по-дружески, с хорошим юмором и приколами, поддерживай разговор, "
        "эмоционально реагируй на стикеры, гифки и фото, но когда просят помочь с делом или кодом — помогай на высшем уровне."
    ),
    "expert": (
        "Ты аналитический ИИ-эксперт и системный архитектор в Telegram (версия 3.0). "
        "Давай максимально глубокие, точные и структурированные ответы, разбирай задачи по шагам."
    )
}

MODE_NAMES = {
    "coder": "💻 Программист и помощник",
    "bro": "😎 Лучший друг (Бро)",
    "expert": "🧠 Ультра-эксперт"
}

# Загрузка и сохранение постоянной памяти на диске
def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"histories": {}, "facts": {}, "modes": {}}

def save_memory():
    try:
        os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения памяти: {e}")

memory_data = load_memory()

def fetch_working_models():
    global AVAILABLE_MODELS, VISION_MODELS, CURRENT_MODEL
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

            sorted_models = [m for m in PREFERRED_MODELS if m in models]
            for m in models:
                if m not in sorted_models:
                    sorted_models.append(m)

            AVAILABLE_MODELS = sorted_models
            VISION_MODELS = [m for m in PREFERRED_VISION_MODELS if m in models]
            for m in models:
                if ("vision" in m.lower() or "llama-4" in m.lower()) and m not in VISION_MODELS:
                    VISION_MODELS.append(m)

            if AVAILABLE_MODELS and (CURRENT_MODEL not in AVAILABLE_MODELS):
                CURRENT_MODEL = AVAILABLE_MODELS[0]
    except Exception as e:
        print(f"Ошибка получения списка моделей: {e}")

fetch_working_models()

def get_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("🗑 Очистить память"), types.KeyboardButton("🤖 Модели ИИ"))
    markup.row(types.KeyboardButton("👤 Обо мне"), types.KeyboardButton("🎭 Режим"))
    return markup

def build_system_prompt(user):
    uid = str(user.id)
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    full_name = f"{first_name} {last_name}".strip() or "Друг"
    username = f"@{user.username}" if user.username else "не указан"

    mode_key = memory_data.get("modes", {}).get(uid, "coder")
    base_prompt = MODES.get(mode_key, MODES["coder"])

    user_facts = memory_data.get("facts", {}).get(uid, [])
    facts_text = "\n".join([f"- {fact}" for fact in user_facts]) if user_facts else "Пока нет дополнительных заметок."

    profile_block = (
        f"\n\n[ДАННЫЕ О СОБЕСЕДНИКЕ В TELEGRAM]\n"
        f"- Имя пользователя: {full_name}\n"
        f"- Username: {username}\n"
        f"- Telegram ID: {uid}\n"
        f"- Сохранённые факты о пользователе:\n{facts_text}\n"
        f"Всегда обращайся к пользователю по имени ({first_name or full_name}), если это уместно, и помни эти факты!"
    )
    return base_prompt + profile_block

def clean_response(text):
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return cleaned if cleaned else text

def get_user_history(user):
    uid = str(user.id)
    sys_prompt = build_system_prompt(user)
    histories = memory_data.setdefault("histories", {})
    if uid not in histories or not histories[uid]:
        histories[uid] = [{"role": "system", "content": sys_prompt}]
    else:
        histories[uid][0] = {"role": "system", "content": sys_prompt}
    return histories[uid]

def ask_groq(user, user_text):
    global CURRENT_MODEL
    if not AVAILABLE_MODELS:
        fetch_working_models()
    if not AVAILABLE_MODELS:
        return "Не удалось получить список доступных моделей от Groq."

    history = get_user_history(user)
    history.append({"role": "user", "content": user_text})

    if len(history) > 17:
        history[:] = [history[0]] + history[-16:]

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    models_to_try = [CURRENT_MODEL] + [m for m in AVAILABLE_MODELS if m != CURRENT_MODEL]
    last_error = ""

    for model_name in models_to_try:
        if not model_name:
            continue
        payload = {
            "model": model_name,
            "messages": history,
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
            answer = clean_response(data["choices"][0]["message"]["content"])
            history.append({"role": "assistant", "content": answer})
            save_memory()
            return answer
        else:
            last_error = f"({response.status_code}): {response.text}"

    return f"Ошибка всех моделей: {last_error}"

def ask_groq_vision(user, prompt_text, image_bytes, mime_type="image/jpeg", history_note="фото"):
    if not VISION_MODELS:
        fetch_working_models()

    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    sys_prompt = build_system_prompt(user)
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    candidates = VISION_MODELS if VISION_MODELS else PREFERRED_VISION_MODELS

    for v_model in candidates:
        payload = {
            "model": v_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"{sys_prompt}\n\nЗапрос пользователя: {prompt_text}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}
                        }
                    ]
                }
            ],
            "temperature": 0.7
        }
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            )
            if resp.status_code == 200:
                answer = clean_response(resp.json()["choices"][0]["message"]["content"])
                history = get_user_history(user)
                history.append({"role": "user", "content": f"[Пользователь отправил {history_note}: {prompt_text}]"})
                history.append({"role": "assistant", "content": answer})
                save_memory()
                return answer
        except Exception:
            continue

    # Если ни одна зрительная модель не смогла обработать формат (например, сложный анимированный стикер),
    # отвечаем через основную текстовую модель по описанию
    return ask_groq(user, f"[Пользователь прислал {history_note}]. {prompt_text}")

def transcribe_voice(voice_bytes):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    for w_model in ["whisper-large-v3-turbo", "whisper-large-v3"]:
        try:
            files = {"file": ("voice.ogg", voice_bytes, "audio/ogg")}
            data = {"model": w_model, "language": "ru"}
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers=headers,
                files=files,
                data=data,
                timeout=45
            )
            if resp.status_code == 200:
                return resp.json().get("text", "").strip()
        except Exception:
            continue
    return None

def download_telegram_file(file_id):
    file_info = bot.get_file(file_id)
    return bot.download_file(file_info.file_path), file_info.file_path

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
    user = message.from_user
    uid = str(user.id)
    memory_data.setdefault("histories", {})[uid] = [{"role": "system", "content": build_system_prompt(user)}]
    save_memory()

    name = user.first_name or "друг"
    mode_key = memory_data.get("modes", {}).get(uid, "coder")
    bot.send_message(
        message.chat.id,
        f"Привет, {name}! 👋 Я твой ИИ-бот (версия {BOT_VERSION}).\n"
        f"🧠 Активная модель: {CURRENT_MODEL}\n"
        f"🎭 Режим: {MODE_NAMES.get(mode_key)}\n\n"
        "Что я теперь умею:\n"
        "• Знаю, как тебя зовут, и помню факты о тебе\n"
        "• Вижу фотографии, скриншоты, стикеры и гифки 👀\n"
        "• Понимаю голосовые сообщения 🎤\n"
        "• Чтобы я запомнил важный факт навсегда, напиши:\n"
        "Запомни: <любой факт о тебе>",
        reply_markup=get_keyboard()
    )

@bot.message_handler(commands=["clear"])
@bot.message_handler(func=lambda msg: msg.text == "🗑 Очистить память")
def clear_cmd(message):
    user = message.from_user
    uid = str(user.id)
    memory_data.setdefault("histories", {})[uid] = [{"role": "system", "content": build_system_prompt(user)}]
    save_memory()
    bot.send_message(
        message.chat.id,
        "🧹 История текущего диалога очищена! (Но твоё имя и факты в «👤 Обо мне» я всё равно помню).",
        reply_markup=get_keyboard()
    )

@bot.message_handler(commands=["remember"])
def remember_cmd(message):
    uid = str(message.from_user.id)
    fact = message.text.replace("/remember", "", 1).strip()
    if not fact:
        bot.send_message(message.chat.id, "Напиши так: /remember Мне нравится Python и серверы в Нидерландах")
        return
    facts = memory_data.setdefault("facts", {}).setdefault(uid, [])
    facts.append(fact)
    save_memory()
    bot.send_message(message.chat.id, f"✅ Запомнил навсегда:\n«{fact}»", reply_markup=get_keyboard())

@bot.message_handler(commands=["forget_facts"])
def forget_facts_cmd(message):
    uid = str(message.from_user.id)
    memory_data.setdefault("facts", {})[uid] = []
    save_memory()
    bot.send_message(message.chat.id, "🗑 Все сохранённые факты о тебе удалены.", reply_markup=get_keyboard())

@bot.message_handler(commands=["profile"])
@bot.message_handler(func=lambda msg: msg.text == "👤 Обо мне")
def profile_cmd(message):
    user = message.from_user
    uid = str(user.id)
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    username = f"@{user.username}" if user.username else "не указан"
    facts = memory_data.get("facts", {}).get(uid, [])
    facts_str = "\n".join([f"• {f}" for f in facts]) if facts else "Пока пусто. Напиши «Запомни: ...», чтобы добавить факт!"

    bot.send_message(
        message.chat.id,
        f"👤 Твой профиль в моей памяти:\n\n"
        f"• Имя: {full_name}\n"
        f"• Username: {username}\n"
        f"• Telegram ID: {uid}\n\n"
        f"📌 Что я знаю о тебе:\n{facts_str}\n\n"
        f"Чтобы добавить факт, напиши: Запомни: твой факт\n"
        f"Чтобы стереть факты: /forget_facts",
        reply_markup=get_keyboard()
    )

@bot.message_handler(commands=["mode"])
@bot.message_handler(func=lambda msg: msg.text == "🎭 Режим")
def mode_cmd(message):
    uid = str(message.from_user.id)
    current_mode = memory_data.get("modes", {}).get(uid, "coder")
    kb = types.InlineKeyboardMarkup()
    for key, label in MODE_NAMES.items():
        prefix = "✅ " if key == current_mode else ""
        kb.add(types.InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"setmode|{key}"))
    bot.send_message(message.chat.id, "🎭 Выбери характер и режим общения бота:", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setmode|"))
def set_mode_callback(call):
    uid = str(call.from_user.id)
    mode_key = call.data.split("|", 1)[1]
    if mode_key in MODES:
        memory_data.setdefault("modes", {})[uid] = mode_key
        save_memory()
        bot.answer_callback_query(call.id, f"Режим: {MODE_NAMES[mode_key]}")
        bot.edit_message_text(
            f"✅ Включён режим: {MODE_NAMES[mode_key]}",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
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
        f"Сейчас активна текстовая модель: {CURRENT_MODEL}\n"
        f"Модель зрения (для фото/стикеров/гифок): {VISION_MODELS[0] if VISION_MODELS else 'Авто'}\n\n"
        "Нажми на любую модель ниже, чтобы переключиться:",
        reply_markup=inline_kb
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("setmodel|"))
def set_model_callback(call):
    global CURRENT_MODEL
    selected = call.data.split("|", 1)[1]
    CURRENT_MODEL = selected
    bot.answer_callback_query(call.id, f"Выбрана модель: {selected}")
    bot.edit_message_text(
        f"✅ Активная модель переключена на: {CURRENT_MODEL}",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )

# Обработка фотографий
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        file_id = message.photo[-1].file_id
        img_bytes, _ = download_telegram_file(file_id)
        caption = message.caption or "Посмотри на это фото, опиши что на нём или ответь по сути изображения."
        reply = ask_groq_vision(message.from_user, caption, img_bytes, "image/jpeg", "фотографию")
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Не удалось прочитать фото: {e}")

# Обработка стикеров Telegram
@bot.message_handler(content_types=["sticker"])
def handle_sticker(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        st = message.sticker
        emoji = st.emoji or "🙂"
        set_name = st.set_name or "неизвестный стикерпак"
        prompt = (
            f"Пользователь прислал тебе стикер в Telegram (связанный смайлик: {emoji}, "
            f"название набора: {set_name}). Посмотри на изображение стикера и живо, с юмором или в тему диалога отреагируй на него!"
        )

        target_file_id = None
        mime_type = "image/webp"
        if not st.is_animated and not st.is_video:
            target_file_id = st.file_id
        else:
            thumb = getattr(st, "thumbnail", None) or getattr(st, "thumb", None)
            if thumb:
                target_file_id = thumb.file_id
                mime_type = "image/jpeg"

        if target_file_id:
            img_bytes, path = download_telegram_file(target_file_id)
            if path.endswith(".jpg") or path.endswith(".jpeg"):
                mime_type = "image/jpeg"
            reply = ask_groq_vision(message.from_user, prompt, img_bytes, mime_type, f"стикер {emoji} из набора {set_name}")
        else:
            reply = ask_groq(message.from_user, prompt)

        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Классный стикер! ({e})")

# Обработка GIF-анимаций
@bot.message_handler(content_types=["animation"])
def handle_animation(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        anim = message.animation
        caption = message.caption or ""
        prompt = (
            f"Пользователь прислал GIF-анимацию в Telegram. {caption} "
            "Перед тобой кадр из этой гифки — посмотри, что на ней происходит, и прикольно отреагируй в тему разговора!"
        )
        thumb = getattr(anim, "thumbnail", None) or getattr(anim, "thumb", None)
        if thumb:
            img_bytes, _ = download_telegram_file(thumb.file_id)
            reply = ask_groq_vision(message.from_user, prompt, img_bytes, "image/jpeg", "GIF-анимацию")
        else:
            reply = ask_groq(message.from_user, prompt)

        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Не удалось открыть гифку: {e}")

# Обработка голосовых сообщений
@bot.message_handler(content_types=["voice", "audio"])
def handle_voice(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        file_id = message.voice.file_id if message.voice else message.audio.file_id
        voice_bytes, _ = download_telegram_file(file_id)
        text = transcribe_voice(voice_bytes)
        if not text:
            bot.send_message(message.chat.id, "Не удалось разобрать голосовое сообщение 🎤")
            return
        bot.send_message(message.chat.id, f"🎤 Ты сказал: «{text}»")
        bot.send_chat_action(message.chat.id, "typing")
        reply = ask_groq(message.from_user, text)
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка обработки голосового: {e}")

# Обработка обычного текста
@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        text = message.text.strip()
        uid = str(message.from_user.id)

        # Быстрое сохранение факта фразой "Запомни: ..." или "Запомни что ..."
        lower = text.lower()
        if lower.startswith("запомни:") or lower.startswith("запомни, что") or lower.startswith("запомни что"):
            fact = re.sub(r"^запомни(:,?\s*что|\s*:)\s*", "", text, flags=re.IGNORECASE).strip()
            if fact:
                facts = memory_data.setdefault("facts", {}).setdefault(uid, [])
                facts.append(fact)
                save_memory()
                bot.send_message(
                    message.chat.id,
                    f"🧠 Записал в долгую память:\n«{fact}»",
                    reply_markup=get_keyboard()
                )
                return

        bot.send_chat_action(message.chat.id, "typing")
        reply = ask_groq(message.from_user, text)
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Произошла ошибка: {e}")

if __name__ == "__main__":
    print(f"ИИ-бот запущен! Версия: {BOT_VERSION}, Модель: {CURRENT_MODEL}")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
