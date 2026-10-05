import html
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from logging.handlers import RotatingFileHandler

# ==================== НАСТРОЙКИ ====================
# Все значения берутся из окружения (файл .env рядом с ботом)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")

TWITCH_LOGIN = os.environ.get("TWITCH_LOGIN", "fatsphynx").lower()
STREAMER_NAME = os.environ.get("STREAMER_NAME", "Сфинкса")  # в родительном падеже: "Стрим у ..."

# Чаты, которые подписаны всегда (id через запятую). Необязательно:
# бот сам запоминает любую группу, в которую его добавили.
CHAT_IDS = [x.strip() for x in os.environ.get("CHAT_IDS", "").split(",") if x.strip()]

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))  # как часто опрашивать Twitch (сек)
OFFLINE_GRACE = int(os.environ.get("OFFLINE_GRACE", "5"))  # сколько проверок подряд "оффлайн", чтобы считать стрим завершённым
SEND_PREVIEW = os.environ.get("SEND_PREVIEW", "1") == "1"  # присылать картинку-превью стрима
STATE_FILE = os.environ.get("STATE_FILE", "state.json")  # идёт ли стрим
CHATS_FILE = os.environ.get("CHATS_FILE", "chats.json")  # список подписанных чатов
LOG_FILE = os.environ.get("LOG_FILE", "twitch_bot.log")

STREAM_URL = f"https://www.twitch.tv/{TWITCH_LOGIN}"
START_TIME = time.time()
BOT_USERNAME = ""

log = logging.getLogger("twitch_bot")
TOKEN = {"value": None, "expires": 0}
TOKEN_LOCK = threading.Lock()
CHECK_LOCK = threading.Lock()
SUBS_LOCK = threading.Lock()
SUBS = set()  # id чатов, куда шлём уведомления
STATE = {
    "is_live": False,
    "offline_count": 0,
    "stream_id": None,
    "stream": None,
    "last_check": None,
    "last_error": None,
}


# ==================== ЛОГИРОВАНИЕ ====================
def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)


# ==================== ПОДПИСАННЫЕ ЧАТЫ ====================
def load_chats():
    try:
        with open(CHATS_FILE, encoding="utf-8") as f:
            SUBS.update(str(x) for x in json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Не удалось прочитать %s: %s", CHATS_FILE, e)
    SUBS.update(CHAT_IDS)


def _save_chats():
    try:
        with open(CHATS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(SUBS), f)
    except Exception as e:
        log.warning("Не удалось сохранить %s: %s", CHATS_FILE, e)


def add_chat(chat_id):
    """Возвращает True, если чат был добавлен только что."""
    with SUBS_LOCK:
        cid = str(chat_id)
        if cid in SUBS:
            return False
        SUBS.add(cid)
        _save_chats()
    log.info("Чат подписан: %s (всего: %d)", cid, len(SUBS))
    return True


def remove_chat(chat_id):
    with SUBS_LOCK:
        cid = str(chat_id)
        if cid not in SUBS:
            return False
        SUBS.discard(cid)
        _save_chats()
    log.info("Чат отписан: %s (всего: %d)", cid, len(SUBS))
    return True


def migrate_chat(old_id, new_id):
    """Группа стала супергруппой — id поменялся."""
    with SUBS_LOCK:
        if str(old_id) in SUBS:
            SUBS.discard(str(old_id))
            SUBS.add(str(new_id))
            _save_chats()
            log.info("Чат %s переехал в %s", old_id, new_id)


def subscribers():
    with SUBS_LOCK:
        return list(SUBS)


# ==================== HTTP ====================
def http_json(url, data=None, headers=None, timeout=15):
    """Возвращает (status, json, error)."""
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        return e.code, None, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return 0, None, str(e)


# ==================== TWITCH API ====================
def twitch_token(force=False):
    """App access token (client credentials), кэшируется до истечения."""
    with TOKEN_LOCK:
        if not force and TOKEN["value"] and time.time() < TOKEN["expires"] - 60:
            return TOKEN["value"], None
        body = urllib.parse.urlencode(
            {
                "client_id": TWITCH_CLIENT_ID,
                "client_secret": TWITCH_CLIENT_SECRET,
                "grant_type": "client_credentials",
            }
        ).encode()
        status, data, err = http_json("https://id.twitch.tv/oauth2/token", data=body)
        if status == 200 and data and "access_token" in data:
            TOKEN["value"] = data["access_token"]
            TOKEN["expires"] = time.time() + int(data.get("expires_in", 3600))
            log.info("Получен новый токен Twitch")
            return TOKEN["value"], None
        return None, f"не удалось получить токен Twitch: {err}"


def get_stream():
    """Возвращает (stream | None если оффлайн, ошибка)."""
    token, err = twitch_token()
    if not token:
        return None, err

    url = "https://api.twitch.tv/helix/streams?" + urllib.parse.urlencode({"user_login": TWITCH_LOGIN})
    for attempt in (1, 2):
        headers = {"Client-Id": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}
        status, data, err = http_json(url, headers=headers)
        if status == 401 and attempt == 1:  # токен протух — обновим и повторим
            token, err = twitch_token(force=True)
            if not token:
                return None, err
            continue
        if status == 200 and data is not None:
            streams = data.get("data", [])
            live = [s for s in streams if s.get("type") == "live"]
            return (live[0] if live else None), None
        return None, err or "пустой ответ Twitch"
    return None, "не удалось обратиться к Twitch"


# ==================== TELEGRAM API ====================
def tg_api(method, payload=None, timeout=15):
    """При ошибке Telegram возвращает его ответ ({"ok": False, "error_code": ..., ...}),
    при сбое сети — None."""
    if not BOT_TOKEN:
        log.error("BOT_TOKEN не установлен!")
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json"} if payload else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        log.error("Ошибка TG API (%s): %s %s", method, e.code, body[:200])
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                parsed.setdefault("ok", False)
                parsed.setdefault("error_code", e.code)
                return parsed
        except Exception:
            pass
        return {"ok": False, "error_code": e.code, "description": body[:200]}
    except Exception as e:
        log.error("Ошибка TG API (%s): %s", method, e)
    return None


def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_api("sendMessage", payload)


def send_photo(chat_id, photo_url, caption, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_api("sendPhoto", payload, timeout=30)


def setup_bot_commands():
    tg_api(
        "setMyCommands",
        {
            "commands": [
                {"command": "stream", "description": "Идёт ли стрим сейчас"},
                {"command": "stop", "description": "Отключить уведомления в этом чате"},
                {"command": "start", "description": "Включить уведомления в этом чате"},
                {"command": "id", "description": "Показать id этого чата"},
            ]
        },
    )


def load_bot_username():
    global BOT_USERNAME
    res = tg_api("getMe")
    if res and res.get("ok"):
        BOT_USERNAME = res["result"].get("username", "")
        log.info("Бот: @%s", BOT_USERNAME)


def is_chat_admin(chat, msg):
    """В личке — всегда да. В группе — только админы."""
    if chat.get("type") == "private":
        return True
    sender_chat = msg.get("sender_chat")  # анонимный админ
    if sender_chat and sender_chat.get("id") == chat["id"]:
        return True
    user_id = (msg.get("from") or {}).get("id")
    if not user_id:
        return False
    res = tg_api("getChatMember", {"chat_id": chat["id"], "user_id": user_id})
    return bool(res and res.get("ok") and res["result"].get("status") in ("creator", "administrator"))


# ==================== ФОРМАТИРОВАНИЕ ====================
def esc(s):
    return html.escape(str(s), quote=False)


def stream_card(stream, header):
    title = stream.get("title") or "Без названия"
    game = stream.get("game_name")
    text = f"{header}\n\n📺 <b>{esc(title)}</b>\n"
    if game:
        text += f"🎮 {esc(game)}\n"
    if stream.get("viewer_count") is not None:
        text += f"👀 Зрителей: {stream['viewer_count']}\n"
    text += f"\n👉 {STREAM_URL}"
    kb = {"inline_keyboard": [[{"text": "▶️ Смотреть стрим", "url": STREAM_URL}]]}
    return text, kb


def preview_url(stream):
    thumb = stream.get("thumbnail_url")
    if not thumb:
        return None
    thumb = thumb.replace("{width}", "1280").replace("{height}", "720")
    return f"{thumb}?t={int(time.time())}"  # cache-buster, чтобы Telegram не взял старую картинку


def greeting_text():
    return (
        f"👋 Привет! Буду писать сюда, когда у {esc(STREAMER_NAME)} начнётся стрим на Twitch.\n\n"
        "/stream — идёт ли стрим сейчас\n"
        "/stop — отключить уведомления (для админов группы)\n"
        "/start — включить обратно"
    )


# ==================== УВЕДОМЛЕНИЯ ====================
def handle_send_error(chat_id, res):
    """Если бота выгнали/группа удалена — отписываем чат; если группа переехала — меняем id."""
    migrate_to = (res.get("parameters") or {}).get("migrate_to_chat_id")
    if migrate_to:
        migrate_chat(chat_id, migrate_to)
        return
    code = res.get("error_code")
    desc = (res.get("description") or "").lower()
    dead = (code == 403 and "rights" not in desc) or "chat not found" in desc or "was deleted" in desc
    if dead:
        remove_chat(chat_id)


def announce(stream):
    text, kb = stream_card(stream, f"🔴 <b>Стрим у {esc(STREAMER_NAME)}!</b>")
    chats = subscribers()
    log.info("Рассылка уведомления в %d чат(ов)", len(chats))
    for chat_id in chats:
        res = None
        if SEND_PREVIEW:
            url = preview_url(stream)
            if url:
                res = send_photo(chat_id, url, text, kb)
        if not (res and res.get("ok")):  # нет превью или не получилось — шлём обычным текстом
            res = send_message(chat_id, text, kb)
        ok = bool(res and res.get("ok"))
        log.info("Уведомление в %s: %s", chat_id, "ок" if ok else "ошибка")
        if res and not ok:
            handle_send_error(chat_id, res)
        time.sleep(0.1)  # не упираемся в лимиты Telegram


# ==================== СОСТОЯНИЕ СТРИМА ====================
def load_state():
    """Возвращает True, если состояние было сохранено раньше."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        STATE["is_live"] = bool(saved.get("is_live"))
        STATE["stream_id"] = saved.get("stream_id")
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        log.warning("Не удалось прочитать %s: %s", STATE_FILE, e)
        return False


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"is_live": STATE["is_live"], "stream_id": STATE["stream_id"]}, f)
    except Exception as e:
        log.warning("Не удалось сохранить состояние: %s", e)


# ==================== ПРОВЕРКА ====================
FIRST_RUN = {"baseline": False}


def run_check():
    """Одна проверка Twitch. Возвращает (stream | None, ошибка)."""
    with CHECK_LOCK:
        stream, err = get_stream()
        STATE["last_check"] = time.time()
        STATE["last_error"] = err
        if err:
            log.warning("Ошибка проверки: %s", err)
            return None, err
        STATE["stream"] = stream

        if stream:
            STATE["offline_count"] = 0
            if not STATE["is_live"]:
                STATE["is_live"] = True
                STATE["stream_id"] = stream.get("id")
                save_state()
                if FIRST_RUN["baseline"]:
                    # первый запуск без файла состояния: стрим уже идёт — не спамим, просто запоминаем
                    log.info("Первый запуск: стрим уже идёт, уведомление пропущено")
                else:
                    log.info("Стрим начался: %s", stream.get("title"))
                    announce(stream)
            FIRST_RUN["baseline"] = False
        else:
            FIRST_RUN["baseline"] = False
            if STATE["is_live"]:
                STATE["offline_count"] += 1
                # ждём несколько проверок подряд, чтобы короткий обрыв связи не считался новым стримом
                if STATE["offline_count"] >= OFFLINE_GRACE:
                    STATE["is_live"] = False
                    STATE["stream_id"] = None
                    STATE["offline_count"] = 0
                    save_state()
                    log.info("Стрим завершён")
        return stream, None


def monitor_thread():
    log.info("Слежу за %s (каждые %d с), подписанных чатов: %d", STREAM_URL, CHECK_INTERVAL, len(subscribers()))
    while True:
        try:
            run_check()
        except Exception:
            log.exception("Ошибка в мониторинге")
        time.sleep(CHECK_INTERVAL)


# ==================== ОБРАБОТКА ОБНОВЛЕНИЙ ====================
def handle_membership(ev):
    """Бота добавили в чат / удалили из чата / заблокировали."""
    chat = ev["chat"]
    status = (ev.get("new_chat_member") or {}).get("status")
    title = chat.get("title") or chat.get("username") or chat["id"]
    if status in ("member", "administrator"):
        if add_chat(chat["id"]):
            log.info("Бота добавили в чат: %s (%s)", title, chat["id"])
            if chat.get("type") != "private":
                send_message(chat["id"], greeting_text())
    elif status in ("left", "kicked"):
        log.info("Бота убрали из чата: %s (%s)", title, chat["id"])
        remove_chat(chat["id"])


def handle_message(msg):
    chat = msg["chat"]
    chat_id = chat["id"]

    if msg.get("migrate_to_chat_id"):
        migrate_chat(chat_id, msg["migrate_to_chat_id"])
        return

    parts = (msg.get("text") or "").split()
    if not parts or not parts[0].startswith("/"):
        return
    cmd, _, target = parts[0].partition("@")
    cmd = cmd.lower()
    # команда адресована другому боту (/id@other_bot) — не отвечаем
    if target and BOT_USERNAME and target.lower() != BOT_USERNAME.lower():
        return

    if cmd == "/id":
        send_message(chat_id, f"🆔 id этого чата: <code>{chat_id}</code>")

    elif cmd == "/start":
        add_chat(chat_id)
        send_message(chat_id, greeting_text())

    elif cmd == "/stop":
        if not is_chat_admin(chat, msg):
            send_message(chat_id, "⛔ Отключить уведомления могут только админы группы.")
        elif remove_chat(chat_id):
            send_message(chat_id, "🔕 Уведомления отключены. Включить обратно: /start")
        else:
            send_message(chat_id, "Уведомления тут и так отключены. Включить: /start")

    elif cmd == "/stream":
        # недавний результат берём из кэша, чтобы много чатов не долбили Twitch
        fresh = STATE["last_check"] and time.time() - STATE["last_check"] < 20 and not STATE["last_error"]
        if fresh:
            stream, err = STATE["stream"], None
        else:
            stream, err = run_check()
        if err:
            send_message(chat_id, f"⚠️ Не удалось проверить Twitch: <code>{esc(err)}</code>")
        elif stream:
            t, kb = stream_card(stream, f"🔴 <b>Сейчас стрим у {esc(STREAMER_NAME)}!</b>")
            send_message(chat_id, t, kb)
        else:
            kb = {"inline_keyboard": [[{"text": "🌐 Открыть канал", "url": STREAM_URL}]]}
            send_message(chat_id, f"⚫ У {esc(STREAMER_NAME)} сейчас оффлайн.", kb)


def handle_update(update):
    if "my_chat_member" in update:
        handle_membership(update["my_chat_member"])
    elif "message" in update:
        handle_message(update["message"])


def telegram_polling():
    log.info("Запущена обработка команд")
    setup_bot_commands()
    offset = 0
    while True:
        res = tg_api(
            "getUpdates",
            {"offset": offset, "timeout": 30, "allowed_updates": ["message", "my_chat_member"]},
            timeout=40,
        )
        if res and res.get("ok"):
            for update in res.get("result", []):
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception:
                    log.exception("Ошибка обработки обновления")
        else:
            time.sleep(5)


# ==================== ТОЧКА ВХОДА ====================
if __name__ == "__main__":
    setup_logging()
    missing = [n for n, v in (("BOT_TOKEN", BOT_TOKEN), ("TWITCH_CLIENT_ID", TWITCH_CLIENT_ID),
                              ("TWITCH_CLIENT_SECRET", TWITCH_CLIENT_SECRET)) if not v]
    if missing:
        log.error("Не заданы переменные окружения: %s", ", ".join(missing))
        raise SystemExit(1)

    load_chats()
    FIRST_RUN["baseline"] = not load_state()
    load_bot_username()
    threading.Thread(target=monitor_thread, name="monitor", daemon=True).start()
    threading.current_thread().name = "telegram"
    telegram_polling()
