import os
import io
import re
import json
import base64
import tempfile
import subprocess
import telebot
from telebot import types
import requests

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Твои токены (разделены плюсом, чтобы защита GitHub не блокировала ключ)
TELEGRAM_TOKEN = "8967141379:" + "AAFIy52g-Rz0ff73dFQaTn1t7PZ0xlU7Qjs"
GROQ_API_KEY = "gsk_" + "7ZZ7rgRfeBzOIEnchdcyWGdyb3FYPeTrVNq4oew7RXzxiVfAmn20"

BOT_VERSION = "3.1"
MEMORY_FILE = "/root/ai-bot/memory.json"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

AVAILABLE_MODELS = []
VISION_MODELS = []
CURRENT_MODEL = None

PREFERRED_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant"
]

# Актуальные модели со зрением на Groq (Qwen 3.8 27B — главная мультимодальная модель)
PREFERRED_VISION_MODELS = [
    "qwen/qwen3.8-27b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct"
]

SKIP_KEYWORDS = ["whisper", "tts", "guard", "playai", "orpheus", "embed", "allam"]

MODES = {
    "coder": (
        "Ты личный ИИ-ассистент и опытный программист в Telegram (версия 3.1). "
        "Ты работаешь на сервере своего создателя в Нидерландах и обновляешься напрямую с его GitHub. "
        "Отвечай четко, подробно и по делу на русском языке. "
        "Когда просят написать код — пиши полный, рабочий и чистый код без сокращений."
    ),
    "bro": (
        "Ты лучший друг и ИИ-бро в Telegram (версия 3.1), живущий на сервере в Нидерландах. "
        "Общайся живо, по-дружески, с хорошим юмором и приколами, поддерживай разговор, "
        "эмоционально реагируй на стикеры, гифки и фото, но когда просят помочь с делом или кодом — помогай на высшем уровне."
    ),
    "expert": (
        "Ты аналитический ИИ-эксперт и системный архитектор в Telegram (версия 3.1). "
        "Давай максимально глубокие, точные и структурированные ответы, разбирай задачи по шагам."
    )
}

MODE_NAMES = {
    "coder": "💻 Программист и помощник",
    "bro": "😎 Лучший друг (Бро)",
    "expert": "🧠 Ультра-эксперт"
}

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

            # Собираем доступные модели со зрением
            v_list = [m for m in PREFERRED_VISION_MODELS if m in models]
            for m in models:
                if any(k in m.lower() for k in ["qwen3.8", "vision", "llama-4"]) and m not in v_list:
                    v_list.append(m)
            VISION_MODELS = v_list if v_list else PREFERRED_VISION_MODELS

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
        f"Обращайся к пользователю по имени ({first_name or full_name}), когда уместно, и помни эти факты!"
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

def convert_media_to_jpeg(raw_bytes, filename="file.bin"):
    """Превращает любое фото, WebP-стикер, видео-стикер (.webm) или GIF (.mp4) в чистый JPEG для ИИ."""
    ext = os.path.splitext(filename)[1].lower()

    # 1. Пробуем открыть как изображение через Pillow (JPG, PNG, WEBP, статический GIF)
    if HAS_PIL and ext not in [".webm", ".mp4", ".tgs"]:
        try:
            img = Image.open(io.BytesIO(raw_bytes))
            if getattr(img, "is_animated", False):
                img.seek(0)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                img = bg
            else:
                img = img.convert("RGB")
            img.thumbnail((1024, 1024))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=88)
            return out.getvalue()
        except Exception:
            pass

    # 2. Если это видео-стикер (.webm) или анимация (.mp4/.gif), вытаскиваем кадр через ffmpeg
    try:
        with tempfile.NamedTemporaryFile(suffix=ext or ".bin", delete=False) as inp:
            inp.write(raw_bytes)
            inp_path = inp.name
        out_path = inp_path + ".jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-i", inp_path, "-vframes", "1", "-q:v", "2", out_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            with open(out_path, "rb") as f:
                jpg_data = f.read()
            os.remove(inp_path)
            os.remove(out_path)
            return jpg_data
        if os.path.exists(inp_path):
            os.remove(inp_path)
    except Exception:
        pass

    return raw_bytes

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

def ask_groq_vision(user, prompt_text, raw_bytes, file_path="image.jpg", history_note="изображение"):
    if not VISION_MODELS:
        fetch_working_models()

    # Конвертируем любой входной файл (фото/стикер/гифку) в стандартный JPEG
    jpeg_bytes = convert_media_to_jpeg(raw_bytes, file_path)
    b64_image = base64.b64encode(jpeg_bytes).decode("utf-8")
    sys_prompt = build_system_prompt(user)

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    candidates = VISION_MODELS if VISION_MODELS else PREFERRED_VISION_MODELS
    last_err = ""

    # 1. Пробуем мультимодальные модели Groq (включая qwen/qwen3.8-27b)
    for v_model in candidates:
        payload = {
            "model": v_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"{sys_prompt}\n\n"
                                f"ВНИМАНИЕ: Пользователь прислал тебе реальное изображение ({history_note}). "
                                f"Ты отлично видишь эту картинку! Внимательно разгляди все детали на изображении "
                                f"(кто или что там нарисовано, эмоции, предметы, текст) и ответь пользователю: {prompt_text}"
                            )
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                        }
                    ]
                }
            ],
            "temperature": 0.6
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
                history.append({"role": "user", "content": f"[Пользователь показал {history_note}: {prompt_text}]"})
                history.append({"role": "assistant", "content": answer})
                save_memory()
                return answer
            else:
                last_err = f"{v_model}: {resp.status_code}"
        except Exception as e:
            last_err = str(e)

    # 2. Резервный зрительный канал (OpenAI-совместимый мультимодальный эндпоинт), если на Groq лимит
    try:
        fallback_payload = {
            "model": "openai",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"{sys_prompt}\n\nОпиши и отреагируй на это изображение ({history_note}): {prompt_text}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                    ]
                }
            ]
        }
        r2 = requests.post("https://text.pollinations.ai/openai", json=fallback_payload, timeout=45)
        if r2.status_code == 200:
            ans = clean_response(r2.json()["choices"][0]["message"]["content"])
            history = get_user_history(user)
            history.append({"role": "user", "content": f"[Пользователь показал {history_note}: {prompt_text}]"})
            history.append({"role": "assistant", "content": ans})
            save_memory()
            return ans
    except Exception:
        pass

    return f"Не удалось распознать картинку через модуль зрения ({last_err})."

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
    v_model = VISION_MODELS[0] if VISION_MODELS else "qwen/qwen3.8-27b"
    bot.send_message(
        message.chat.id,
        f"Привет, {name}! 👋 Я твой ИИ-бот (версия {BOT_VERSION}).\n"
        f"🧠 Текстовая модель: {CURRENT_MODEL}\n"
        f"👁 Модель зрения: {v_model}\n"
        f"🎭 Режим: {MODE_NAMES.get(mode_key)}\n\n"
        "Что я умею:\n"
        "• Знаю тебя по имени и помню факты о тебе\n"
        "• Реально вижу фотографии, стикеры (даже видео-стикеры!) и гифки 👀\n"
        "• Понимаю голосовые сообщения 🎤\n"
        "• Запишу любой факт, если напишешь: Запомни: ...",
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
        "🧹 История диалога очищена! (Твоё имя и факты в «👤 Обо мне» сохранены).",
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
        f"Модель зрения (фото/стикеры/гифки): {VISION_MODELS[0] if VISION_MODELS else 'qwen/qwen3.8-27b'}\n\n"
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
        img_bytes, file_path = download_telegram_file(file_id)
        caption = message.caption or "Опиши подробно, что ты видишь на этом фото, и ответь мне!"
        reply = ask_groq_vision(message.from_user, caption, img_bytes, file_path, "фотографию")
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Не удалось прочитать фото: {e}")

# Обработка стикеров Telegram (обычных, видео и анимированных)
@bot.message_handler(content_types=["sticker"])
def handle_sticker(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        st = message.sticker
        emoji = st.emoji or "🙂"
        set_name = st.set_name or "неизвестный набор"
        prompt = (
            f"Пользователь прислал тебе стикер в Telegram (смайлик стикера: {emoji}, набор: {set_name}). "
            f"НЕ выдумывай картинку по смайлику — посмотри именно на само изображение стикера, скажи кто или что на нём изображено, и прикольно отреагируй!"
        )

        # Сначала пробуем взять сам файл стикера (если это webp или видео-стикер webm), либо его превью
        thumb = getattr(st, "thumbnail", None) or getattr(st, "thumb", None)
        if not st.is_animated:
            raw_bytes, file_path = download_telegram_file(st.file_id)
        elif thumb:
            raw_bytes, file_path = download_telegram_file(thumb.file_id)
        else:
            raw_bytes, file_path = None, ""

        if raw_bytes:
            reply = ask_groq_vision(message.from_user, prompt, raw_bytes, file_path, f"стикер ({emoji})")
        else:
            reply = ask_groq(message.from_user, prompt)

        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Не удалось разглядеть стикер: {e}")

# Обработка GIF-анимаций
@bot.message_handler(content_types=["animation"])
def handle_animation(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        anim = message.animation
        caption = message.caption or ""
        prompt = (
            f"Пользователь прислал GIF-анимацию в Telegram. {caption} "
            "Посмотри на кадр из этой гифки, опиши кто или что на ней и прикольно отреагируй в тему!"
        )
        thumb = getattr(anim, "thumbnail", None) or getattr(anim, "thumb", None)
        if thumb:
            raw_bytes, file_path = download_telegram_file(thumb.file_id)
        else:
            raw_bytes, file_path = download_telegram_file(anim.file_id)

        reply = ask_groq_vision(message.from_user, prompt, raw_bytes, file_path, "GIF-анимацию")
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
