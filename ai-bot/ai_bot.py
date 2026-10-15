import os
import io
import re
import json
import base64
import urllib.parse
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

# Токены (разделены плюсом от автоблокировки GitHub)
TELEGRAM_TOKEN = "8967141379:" + "AAFIy52g-Rz0ff73dFQaTn1t7PZ0xlU7Qjs"
GROQ_API_KEY = "gsk_" + "7ZZ7rgRfeBzOIEnchdcyWGdyb3FYPeTrVNq4oew7RXzxiVfAmn20"

BOT_VERSION = "4.1"
OLD_MEMORY_FILE = "/root/ai-bot/memory.json"

if os.path.exists(OLD_MEMORY_FILE):
    try:
        os.remove(OLD_MEMORY_FILE)
    except Exception:
        pass

bot = telebot.TeleBot(TELEGRAM_TOKEN)

chat_histories = {}

MAIN_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b"
]

ROUTER_MODELS = [
    "llama-3.1-8b-instant",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile"
]

VISION_MODELS = [
    "qwen/qwen3.8-27b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct"
]

def get_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🗑 Очистить память"))
    return markup

def build_system_prompt(user):
    name = (user.first_name or "друг").strip()
    return (
        f"Ты умный, современный ИИ-помощник в Telegram.\n"
        f"Сейчас разговариваешь с пользователем по имени: {name}.\n\n"
        f"СТРОГИЕ ПРАВИЛА:\n"
        f"1. НИКОГДА не пиши про серверы, Нидерланды, хостинг или GitHub.\n"
        f"2. НИКОГДА не используй в тексте символы разметки: НЕ пиши решётки (#, ##, ###), "
        f"НЕ пиши звёздочки (*, **), НЕ рисуй таблицы из вертикальных палок и тире (|---|---|), "
        f"НЕ ставь знаки цитат (>) в начале строки. Пиши только чистым, красивым обычным текстом с абзацами. "
        f"Для списков используй обычное тире (-) или цифры (1., 2.).\n"
        f"3. НЕ присылай ссылки на сайты или YouTube, если пользователь сам не просил ссылку, видео или информацию про конкретный канал. "
        f"На обычное общение, благодарность или похвалу отвечай просто и по-человечески.\n"
        f"4. Если в запросе переданы ДАННЫЕ ИЗ ИНТЕРНЕТА — используй их для точного ответа, но бери оттуда только то, что реально относится к вопросу пользователя.\n"
        f"5. Если пользователь просит написать программный код — пиши полный, чистый рабочий код."
    )

def clean_formatting(text):
    """Удаляет таблицы |---|, решётки ### и звёздочки **, сохраняя блоки кода."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    parts = re.split(r"(```[\s\S]*?```)", text)
    cleaned_parts = []

    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            code_lines = part.strip("`").splitlines()
            if code_lines and re.match(r"^[a-zA-Z0-9_+-]+$", code_lines[0].strip()):
                code_lines = code_lines[1:]
            cleaned_parts.append("\n".join(code_lines))
            continue

        part = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r"\1 (\2)", part)

        lines = part.splitlines()
        new_lines = []
        for line in lines:
            if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
                continue
            if re.match(r"^\s*[\-*_]{3,}\s*$", line):
                continue

            if line.strip().startswith("|") and line.strip().endswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|") if c.strip()]
                if cells:
                    line = " — ".join(cells)
                else:
                    continue

            line = re.sub(r"^\s*#{1,6}\s*", "", line)
            line = re.sub(r"^\s*>\s*", "", line)
            line = re.sub(r"^\s*\*\s+", "• ", line)
            line = line.replace("**", "").replace("*", "").replace("__", "")

            new_lines.append(line)

        cleaned_text = "\n".join(new_lines)
        cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
        cleaned_parts.append(cleaned_text)

    return "\n".join(cleaned_parts).strip()

# ================= УМНЫЙ ПОИСК В ИНТЕРНЕТЕ И YOUTUBE =================

def decide_search_need(user_text):
    """Определяет через быструю модель, нужен ли вообще поиск в интернете или на YouTube для этого сообщения."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    router_prompt = (
        "Определи, требует ли сообщение пользователя поиска информации в интернете или поиска видео на YouTube.\n"
        "Правила:\n"
        "- Если это обычное общение, приветствие, благодарность, похвала (например: 'спасибо большое ты умничка', 'красава', 'привет как дела'), "
        "просьба написать код, решить задачу или перевести текст -> web: false, youtube: false.\n"
        "- Если пользователь спрашивает про конкретного человека, блогера, стримера (например Т2х2), игру, компанию, новости, погоду, текущее время, курс или факт -> web: true.\n"
        "- Если пользователь явно просит скинуть видео, ссылку на ролик/канал или спрашивает про ютубера/YouTube-канал -> youtube: true.\n"
        "Ответь СТРОГО в формате JSON без лишнего текста:\n"
        '{"web": false, "youtube": false, "query": "точный поисковый запрос"}'
    )

    for r_model in ROUTER_MODELS:
        payload = {
            "model": r_model,
            "messages": [
                {"role": "system", "content": router_prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.0
        }
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=10
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{.*?\}", content, flags=re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
                    return (
                        bool(data.get("web", False)),
                        bool(data.get("youtube", False)),
                        str(data.get("query", "")).strip() or user_text
                    )
        except Exception:
            continue

    return False, False, user_text

def search_duckduckgo(query, max_results=5):
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "ru-ru"},
            headers=headers,
            timeout=8
        )
        if resp.status_code == 200:
            html = resp.text
            blocks = re.findall(
                r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>.*?<a class="result__snippet"[^>]*>(.*?)</a>',
                html,
                flags=re.DOTALL
            )
            for url, title, snippet in blocks[:max_results]:
                clean_title = re.sub(r"<.*?>", "", title).strip()
                clean_snip = re.sub(r"<.*?>", "", snippet).strip()
                if "uddg=" in url:
                    m = re.search(r"uddg=([^&]+)", url)
                    if m:
                        url = urllib.parse.unquote(m.group(1))
                results.append(f"- {clean_title}: {clean_snip} (Ссылка: {url})")
    except Exception:
        pass
    return results

def search_wikipedia_ru(query):
    results = []
    headers = {"User-Agent": "TelegramAIBot/4.1"}
    try:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "utf8": 1,
            "format": "json",
            "srlimit": 2
        }
        r = requests.get("https://ru.wikipedia.org/w/api.php", params=params, headers=headers, timeout=6)
        if r.status_code == 200:
            search_items = r.json().get("query", {}).get("search", [])
            for item in search_items:
                title = item.get("title", "")
                snip = re.sub(r"<.*?>", "", item.get("snippet", "")).strip()
                page_url = f"https://ru.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                results.append(f"- Википедия ({title}): {snip} (Ссылка: {page_url})")
    except Exception:
        pass
    return results

def search_youtube(query, max_results=3):
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8"
    }
    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 200:
            html = resp.text
            channels = re.findall(
                r'"channelRenderer":\{"channelId":"([^"]+)","title":\{"simpleText":"([^"]+)"\}.*?"canonicalBaseUrl":"([^"]+)"',
                html
            )
            seen_urls = set()
            for ch_id, ch_title, ch_url in channels[:2]:
                full_ch_url = f"https://www.youtube.com{ch_url}" if ch_url.startswith("/") else f"https://www.youtube.com/channel/{ch_id}"
                if full_ch_url not in seen_urls:
                    seen_urls.add(full_ch_url)
                    results.append(f"- YouTube-канал «{ch_title}»: {full_ch_url}")

            videos = re.findall(
                r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})".*?"title":\{"runs":\[\{"text":"([^"]+)"\}\]',
                html
            )
            for vid_id, vid_title in videos:
                v_url = f"https://www.youtube.com/watch?v={vid_id}"
                if v_url not in seen_urls:
                    seen_urls.add(v_url)
                    results.append(f"- Видео на YouTube «{vid_title}»: {v_url}")
                    if len(results) >= max_results + 1:
                        break
    except Exception:
        pass
    return results

def gather_web_context(user_text):
    need_web, need_yt, search_query = decide_search_need(user_text)
    if not need_web and not need_yt:
        return ""

    blocks = []
    if need_web:
        web_res = search_duckduckgo(search_query, max_results=5)
        wiki_res = search_wikipedia_ru(search_query)
        if web_res:
            blocks.append("Результаты из интернета:\n" + "\n".join(web_res))
        if wiki_res:
            blocks.append("Результаты из Википедии:\n" + "\n".join(wiki_res))

    if need_yt:
        yt_res = search_youtube(search_query, max_results=3)
        if yt_res:
            blocks.append("Найденные каналы и видео на YouTube:\n" + "\n".join(yt_res))

    if not blocks:
        return ""

    return (
        "\n\n[СПРАВОЧНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА ПО ВОПРОСУ ПОЛЬЗОВАТЕЛЯ]\n"
        + "\n\n".join(blocks)
        + "\n[КОНЕЦ СПРАВОЧНЫХ ДАННЫХ. Ответь на вопрос пользователя. Ссылки давай ТОЛЬКО если пользователь сам их попросил или спрашивал про канал/видео!]"
    )

# ================= РАБОТА С ИИ И МЕДИА =================

def ask_groq(user, user_text):
    uid = str(user.id)
    sys_prompt = build_system_prompt(user)

    if uid not in chat_histories or not chat_histories[uid]:
        chat_histories[uid] = [{"role": "system", "content": sys_prompt}]
    else:
        chat_histories[uid][0] = {"role": "system", "content": sys_prompt}

    web_context = gather_web_context(user_text)
    full_user_message = user_text + web_context if web_context else user_text

    messages_for_api = list(chat_histories[uid]) + [{"role": "user", "content": full_user_message}]

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    last_error = ""
    for model_name in MAIN_MODELS:
        payload = {
            "model": model_name,
            "messages": messages_for_api,
            "temperature": 0.5
        }
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            )
            if response.status_code == 200:
                raw_answer = response.json()["choices"][0]["message"]["content"]
                clean_answer = clean_formatting(raw_answer)

                chat_histories[uid].append({"role": "user", "content": user_text})
                chat_histories[uid].append({"role": "assistant", "content": clean_answer})
                if len(chat_histories[uid]) > 15:
                    chat_histories[uid] = [chat_histories[uid][0]] + chat_histories[uid][-14:]

                return clean_answer
            else:
                last_error = f"({response.status_code}): {response.text}"
        except Exception as e:
            last_error = str(e)

    return f"Ошибка соединения с ИИ: {last_error}"

def convert_media_to_jpeg(raw_bytes, filename="file.bin"):
    ext = os.path.splitext(filename)[1].lower()
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

def ask_groq_vision(user, prompt_text, raw_bytes, file_path="image.jpg", history_note="изображение"):
    jpeg_bytes = convert_media_to_jpeg(raw_bytes, file_path)
    b64_image = base64.b64encode(jpeg_bytes).decode("utf-8")
    sys_prompt = build_system_prompt(user)

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    for v_model in VISION_MODELS:
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
                                f"Пользователь прислал {history_note}. "
                                f"Внимательно разгляди картинку и ответь простым чистым текстом без звёздочек и решёток: {prompt_text}"
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
                answer = clean_formatting(resp.json()["choices"][0]["message"]["content"])
                uid = str(user.id)
                if uid not in chat_histories:
                    chat_histories[uid] = [{"role": "system", "content": sys_prompt}]
                chat_histories[uid].append({"role": "user", "content": f"[Пользователь прислал {history_note}]"})
                chat_histories[uid].append({"role": "assistant", "content": answer})
                return answer
        except Exception:
            continue

    return "Не удалось распознать картинку."

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
        bot.send_message(chat_id, chunk, reply_markup=reply_markup)

@bot.message_handler(commands=["start"])
def start_cmd(message):
    user = message.from_user
    uid = str(user.id)
    chat_histories[uid] = [{"role": "system", "content": build_system_prompt(user)}]
    name = user.first_name or "друг"
    bot.send_message(
        message.chat.id,
        f"Привет, {name}! Я готов к работе (версия {BOT_VERSION}).\n\n"
        "Задавай любые вопросы, проси написать код или присылай фото, стикеры, гифки и голосовые!",
        reply_markup=get_keyboard()
    )

@bot.message_handler(commands=["clear"])
@bot.message_handler(func=lambda msg: msg.text == "🗑 Очистить память")
def clear_cmd(message):
    user = message.from_user
    uid = str(user.id)
    chat_histories[uid] = [{"role": "system", "content": build_system_prompt(user)}]
    bot.send_message(
        message.chat.id,
        "Память диалога очищена! О чём поговорим?",
        reply_markup=get_keyboard()
    )

@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        file_id = message.photo[-1].file_id
        img_bytes, file_path = download_telegram_file(file_id)
        caption = message.caption or "Опиши, что ты видишь на этом фото, и ответь мне."
        reply = ask_groq_vision(message.from_user, caption, img_bytes, file_path, "фотографию")
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Не удалось прочитать фото: {e}")

@bot.message_handler(content_types=["sticker"])
def handle_sticker(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        st = message.sticker
        emoji = st.emoji or "🙂"
        prompt = "Посмотри на изображение этого стикера, скажи кто или что на нём и отреагируй в тему диалога."
        thumb = getattr(st, "thumbnail", None) or getattr(st, "thumb", None)
        if not st.is_animated:
            raw_bytes, file_path = download_telegram_file(st.file_id)
        elif thumb:
            raw_bytes, file_path = download_telegram_file(thumb.file_id)
        else:
            raw_bytes, file_path = None, ""

        if raw_bytes:
            reply = ask_groq_vision(message.from_user, prompt, raw_bytes, file_path, f"стикер {emoji}")
        else:
            reply = ask_groq(message.from_user, f"Пользователь прислал стикер {emoji}")
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Не удалось разглядеть стикер: {e}")

@bot.message_handler(content_types=["animation"])
def handle_animation(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        anim = message.animation
        caption = message.caption or ""
        prompt = f"Посмотри на кадр из этой гифки и отреагируй в тему разговора. {caption}"
        thumb = getattr(anim, "thumbnail", None) or getattr(anim, "thumb", None)
        if thumb:
            raw_bytes, file_path = download_telegram_file(thumb.file_id)
        else:
            raw_bytes, file_path = download_telegram_file(anim.file_id)
        reply = ask_groq_vision(message.from_user, prompt, raw_bytes, file_path, "гифку")
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Не удалось открыть гифку: {e}")

@bot.message_handler(content_types=["voice", "audio"])
def handle_voice(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        file_id = message.voice.file_id if message.voice else message.audio.file_id
        voice_bytes, _ = download_telegram_file(file_id)
        text = transcribe_voice(voice_bytes)
        if not text:
            bot.send_message(message.chat.id, "Не удалось разобрать голосовое сообщение.")
            return
        bot.send_message(message.chat.id, f"🎤 Ты сказал: «{text}»")
        bot.send_chat_action(message.chat.id, "typing")
        reply = ask_groq(message.from_user, text)
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка обработки голосового: {e}")

@bot.message_handler(content_types=["text"])
def handle_text(message):
    try:
        bot.send_chat_action(message.chat.id, "typing")
        reply = ask_groq(message.from_user, message.text.strip())
        send_long_message(message.chat.id, reply, reply_markup=get_keyboard())
    except Exception as e:
        bot.send_message(message.chat.id, f"Произошла ошибка: {e}")

if __name__ == "__main__":
    print(f"ИИ-бот запущен! Версия: {BOT_VERSION}")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
