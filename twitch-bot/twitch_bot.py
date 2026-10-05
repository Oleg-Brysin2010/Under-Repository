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

# Кому доверяет бот (только эти люди могут добавлять его в группы и управлять им).
# Можно задать и по @username, и по числовому id (id надёжнее: username можно сменить).
ADMIN_USERNAMES = {
    x.strip().lstrip("@").lower()
    for x in os.environ.get("ADMIN_USERNAMES", "fatsphynx,ghchjfjtfh").split(",")
    if x.strip()
}
ADMIN_IDS = {x.strip() for x in os.environ.get("ADMIN_IDS", "5399489280").split(",") if x.strip()}

# Чаты, которые подписаны всегда (id через запятую). Необязательно.
CHAT_IDS = [x.strip() for x in os.environ.get("CHAT_IDS", "").split(",") if x.strip()]

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))  # как часто опрашивать Twitch (сек)
OFFLINE_GRACE = int(os.environ.get("OFFLINE_GRACE", "5"))  # проверок подряд "оффлайн", чтобы считать стрим завершённым
ALERT_AFTER = int(os.environ.get("ALERT_AFTER", "10"))  # после скольких неудачных проверок подряд писать админам
SEND_PREVIEW = os.environ.get("SEND_PREVIEW", "1") == "1"  # присылать картинку-превью стрима
STATE_FILE = os.environ.get("STATE_FILE", "state.json")  # идёт ли стрим
CHATS_FILE = os.environ.get("CHATS_FILE", "chats.json")  # подписанные чаты
ADMINS_FILE = os.environ.get("ADMINS_FILE", "admins.json")  # id админов, которых бот узнал по username
LOG_FILE = os.environ.get("LOG_FILE", "twitch_bot.log")

STREAM_URL = f"https://www.twitch.tv/{TWITCH_LOGIN}"
START_TIME = time.time()
BOT_USERNAME = ""

log = logging.getLogger("twitch_bot")
TOKEN = {"value": None, "expires": 0}
TOKEN_LOCK = threading.Lock()
CHECK_LOCK = threading.Lock()
SUBS_LOCK = threading.Lock()
ADMINS_LOCK = threading.Lock()
SUBS = {}  # id чата -> название
LEARNED_IDS = set()
STATE = {
    "is_live": False,
    "offline_count": 0,
    "stream_id": None,
    "stream": None,
    "last_check": None,
    "last_error": None,
    "fail_count": 0,
    "alerted": False,
}

ADMIN_COMMANDS = ("/start", "/stop", "/status", "/chats", "/test", "/say")

ANON_BOT_ID = 1087968824  # служебный аккаунт GroupAnonymousBot: так Telegram показывает "анонимного админа"
ANON_CACHE = {}  # chat_id -> (время проверки, результат)


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


# ==================== АДМИНЫ ====================
def load_admins():
    try:
        with open(ADMINS_FILE, encoding="utf-8") as f:
            LEARNED_IDS.update(str(x) for x in json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Не удалось прочитать %s: %s", ADMINS_FILE, e)


def learn_admin(user_id):
    """Запоминаем id человека, который написал с админским username."""
    with ADMINS_LOCK:
        uid = str(user_id)
        if uid in LEARNED_IDS or uid in ADMIN_IDS:
            return
        LEARNED_IDS.add(uid)
        try:
            with open(ADMINS_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(LEARNED_IDS), f)
        except Exception as e:
            log.warning("Не удалось сохранить %s: %s", ADMINS_FILE, e)
    log.info("Узнал админа: id=%s", uid)


def is_admin(user):
    """user — объект from из Telegram."""
    if not user or user.get("is_bot"):
        return False
    uid = str(user.get("id"))
    if uid in ADMIN_IDS or uid in LEARNED_IDS:
        return True
    uname = (user.get("username") or "").lower()
    if uname and uname in ADMIN_USERNAMES:
        learn_admin(uid)
        return True
    return False


def admin_ids():
    with ADMINS_LOCK:
        return sorted(ADMIN_IDS | LEARNED_IDS)


def is_anonymous_user(user):
    return bool(user) and user.get("id") == ANON_BOT_ID


def anonymous_admins_trusted(chat_id):
    """Если человек пишет или добавляет бота «от имени группы» (анонимный админ), Telegram скрывает,
    кто это. Смотрим список админов чата: доверяем, только если ВСЕ админы с включённой анонимностью — наши."""
    now = time.time()
    cached = ANON_CACHE.get(chat_id)
    if cached and now - cached[0] < 300:
        return cached[1]
    res = tg_api("getChatAdministrators", {"chat_id": chat_id})
    if not (res and res.get("ok")):
        return False
    anon = [m for m in res.get("result", []) if m.get("is_anonymous")]
    trusted = bool(anon) and all(is_admin(m.get("user")) for m in anon)
    ANON_CACHE[chat_id] = (now, trusted)
    return trusted


def admin_in_chat(chat_id):
    """True, если в чате состоит хотя бы один админ бота (по числовому id)."""
    for uid in admin_ids():
        try:
            res = tg_api("getChatMember", {"chat_id": chat_id, "user_id": int(uid)})
        except ValueError:
            continue
        if not (res and res.get("ok")):
            continue
        member = res.get("result") or {}
        status = member.get("status")
        if status in ("creator", "administrator", "member"):
            return True
        if status == "restricted" and member.get("is_member", True):
            return True
    return False


def who(user):
    if not user:
        return "неизвестно"
    name = ("@" + user["username"]) if user.get("username") else (user.get("first_name") or "без имени")
    return f"{name} (id {user.get('id')})"


def notify_admins(text):
    """Личное сообщение всем известным админам (они должны хотя бы раз нажать /start у бота в личке)."""
    for uid in admin_ids():
        res = send_message(uid, text)
        if not (res and res.get("ok")):
            log.info("Не удалось написать админу %s (он ещё не нажимал /start у бота?)", uid)


# ==================== ПОДПИСАННЫЕ ЧАТЫ ====================
def load_chats():
    try:
        with open(CHATS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, list):  # старый формат: просто список id
            SUBS.update({str(x): "" for x in saved})
        elif isinstance(saved, dict):
            SUBS.update({str(k): v for k, v in saved.items()})
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Не удалось прочитать %s: %s", CHATS_FILE, e)
    for cid in CHAT_IDS:
        SUBS.setdefault(cid, "")


def _save_chats():
    try:
        with open(CHATS_FILE, "w", encoding="utf-8") as f:
            json.dump(SUBS, f, ensure_ascii=False)
    except Exception as e:
        log.warning("Не удалось сохранить %s: %s", CHATS_FILE, e)


def add_chat(chat_id, title=""):
    """Возвращает True, если чат был добавлен только что."""
    with SUBS_LOCK:
        cid = str(chat_id)
        if cid in SUBS:
            if title and SUBS[cid] != title:
                SUBS[cid] = title
                _save_chats()
            return False
        SUBS[cid] = title
        _save_chats()
    log.info("Чат подписан: %s %s (всего: %d)", cid, title, len(SUBS))
    return True


def remove_chat(chat_id):
    with SUBS_LOCK:
        cid = str(chat_id)
        if cid not in SUBS:
            return False
        SUBS.pop(cid)
        _save_chats()
    log.info("Чат отписан: %s (всего: %d)", cid, len(SUBS))
    return True


def migrate_chat(old_id, new_id):
    """Группа стала супергруппой — id поменялся."""
    with SUBS_LOCK:
        title = SUBS.pop(str(old_id), None)
        if title is not None:
            SUBS[str(new_id)] = title
            _save_chats()
            log.info("Чат %s переехал в %s", old_id, new_id)


def subscribers():
    with SUBS_LOCK:
        return list(SUBS.keys())


def subscribed_with_titles():
    with SUBS_LOCK:
        return dict(SUBS)


def is_subscribed(chat_id):
    with SUBS_LOCK:
        return str(chat_id) in SUBS


def chat_title(chat):
    if chat.get("title"):
        return chat["title"]
    if chat.get("username"):
        return "@" + chat["username"]
    return chat.get("first_name") or str(chat.get("id"))


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
def keys_configured():
    return bool(
        TWITCH_CLIENT_ID
        and TWITCH_CLIENT_SECRET
        and TWITCH_CLIENT_ID != "temp"
        and TWITCH_CLIENT_SECRET != "temp"
    )


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
    # В общем меню — только безобидная команда; админские команды не светим
    tg_api("setMyCommands", {"commands": [{"command": "stream", "description": "Идёт ли стрим сейчас"}]})


def load_bot_username():
    global BOT_USERNAME
    res = tg_api("getMe")
    if res and res.get("ok"):
        BOT_USERNAME = res["result"].get("username", "")
        log.info("Бот: @%s", BOT_USERNAME)


# ==================== ФОРМАТИРОВАНИЕ ====================
def esc(s):
    return html.escape(str(s), quote=False)


def fmt_uptime(seconds):
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    return f"{d}д {h}ч {m}м" if d else f"{h}ч {m}м"


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
        "/stream — идёт ли стрим сейчас"
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


def broadcast_text(text):
    """Отправить текст во все подписанные чаты. Возвращает (отправлено, ошибок)."""
    ok_count = fail_count = 0
    for chat_id in subscribers():
        res = send_message(chat_id, text)
        if res and res.get("ok"):
            ok_count += 1
        else:
            fail_count += 1
            if res:
                handle_send_error(chat_id, res)
        time.sleep(0.1)
    return ok_count, fail_count


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
            STATE["fail_count"] += 1
            # Если Twitch долго недоступен — сообщаем админам один раз (пока ключи не настроены, молчим)
            if keys_configured() and STATE["fail_count"] >= ALERT_AFTER and not STATE["alerted"]:
                STATE["alerted"] = True
                notify_admins(
                    f"⚠️ Twitch не отвечает уже {STATE['fail_count']} проверок подряд.\n"
                    f"<code>{esc(err)}</code>"
                )
            return None, err

        if STATE["alerted"]:
            notify_admins("✅ Связь с Twitch восстановлена.")
        STATE["fail_count"] = 0
        STATE["alerted"] = False
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


# ==================== КОМАНДЫ АДМИНОВ ====================
def cmd_status():
    if STATE["last_check"]:
        last = fmt_uptime(time.time() - STATE["last_check"]) + " назад"
    else:
        last = "ещё не было"
    if not keys_configured():
        keys = "не настроены ❌ (стоят заглушки)"
    else:
        keys = "заданы ✅"
    if STATE["last_error"]:
        live = "❓ неизвестно (ошибка Twitch)"
    else:
        live = "🔴 идёт" if STATE["is_live"] else "⚫ оффлайн"
    text = (
        f"{'🟡' if STATE['last_error'] else '🟢'} <b>Бот работает</b>\n\n"
        f"• <b>Аптайм:</b> {fmt_uptime(time.time() - START_TIME)}\n"
        f"• <b>Канал:</b> {esc(TWITCH_LOGIN)} — {live}\n"
        f"• <b>Ключи Twitch:</b> {keys}\n"
        f"• <b>Последняя проверка:</b> {last}\n"
        f"• <b>Подписанных чатов:</b> {len(subscribers())}\n"
        f"• <b>Админов известно:</b> {len(admin_ids())}"
    )
    if STATE["last_error"]:
        text += f"\n• <b>Последняя ошибка:</b> <code>{esc(STATE['last_error'])}</code>"
    return text


def cmd_chats():
    items = subscribed_with_titles()
    if not items:
        return "Подписанных чатов нет."
    lines = [f"• {esc(t or '(без названия)')} — <code>{cid}</code>" for cid, t in items.items()]
    return f"📋 <b>Подписанные чаты ({len(items)}):</b>\n\n" + "\n".join(lines)


def cmd_test(chat_id):
    fake = {
        "title": "Тестовый стрим — так будет выглядеть уведомление",
        "game_name": "Just Chatting",
        "viewer_count": 123,
    }
    text, kb = stream_card(fake, f"🔴 <b>Стрим у {esc(STREAMER_NAME)}!</b>")
    send_message(chat_id, "🧪 <i>Это тест, реального стрима нет:</i>")
    send_message(chat_id, text, kb)


# ==================== ОБРАБОТКА ОБНОВЛЕНИЙ ====================
def handle_membership(ev):
    """Бота добавили в чат / удалили из чата / заблокировали."""
    chat = ev["chat"]
    chat_id = chat["id"]
    frm = ev.get("from")
    new_status = (ev.get("new_chat_member") or {}).get("status")
    old_status = (ev.get("old_chat_member") or {}).get("status")
    title = chat_title(chat)

    if new_status in ("left", "kicked"):
        log.info("Бота убрали из чата: %s (%s)", title, chat_id)
        remove_chat(chat_id)
        return

    added = new_status in ("member", "administrator") and old_status in (None, "left", "kicked")
    if not added or chat.get("type") == "private":
        return  # смена прав в чате или личка (в личке подписка идёт через /start)
    if is_subscribed(chat_id):
        return

    if (
        is_admin(frm)
        or (is_anonymous_user(frm) and anonymous_admins_trusted(chat_id))
        or admin_in_chat(chat_id)  # в чате есть админ бота — остаёмся, кто бы ни добавил
    ):
        if add_chat(chat_id, title):
            log.info("Бота добавил админ %s в чат %s (%s)", who(frm), title, chat_id)
            send_message(chat_id, greeting_text())
    else:
        # чужой человек добавил бота — выходим
        log.warning("Бота добавил посторонний %s в чат %s (%s) — выхожу", who(frm), title, chat_id)
        send_message(chat_id, "🔒 Это закрытый бот: его могут добавлять только разработчики. Выхожу.")
        tg_api("leaveChat", {"chat_id": chat_id})
        remove_chat(chat_id)
        notify_admins(f"🚫 {esc(who(frm))} добавил меня в «{esc(title)}» (<code>{chat_id}</code>). Я вышел из чата.")


def handle_message(msg):
    chat = msg["chat"]
    chat_id = chat["id"]
    private = chat.get("type") == "private"

    if msg.get("migrate_to_chat_id"):
        migrate_chat(chat_id, msg["migrate_to_chat_id"])
        return

    text = msg.get("text") or ""
    parts = text.split()
    if not parts or not parts[0].startswith("/"):
        return
    cmd, _, target = parts[0].partition("@")
    cmd = cmd.lower()
    # команда адресована другому боту (/id@other_bot) — не отвечаем
    if target and BOT_USERNAME and target.lower() != BOT_USERNAME.lower():
        return

    user = msg.get("from")
    # «от имени группы»: sender_chat совпадает с самим чатом (анонимный админ)
    anon_sender = (msg.get("sender_chat") or {}).get("id") == chat_id or is_anonymous_user(user)
    admin = is_admin(user) or (anon_sender and anonymous_admins_trusted(chat_id))
    subscribed = is_subscribed(chat_id)
    title = chat_title(chat)

    # --- Доступ ---
    if cmd in ("/stream", "/id"):
        if not (admin or subscribed):
            log.warning("Игнорирую %s от %s в неподписанном чате %s", cmd, who(user), chat_id)
            return
    elif cmd in ADMIN_COMMANDS:
        if not admin:
            log.warning("Игнорирую %s от постороннего %s в чате %s", cmd, who(user), chat_id)
            if private and cmd == "/start":
                send_message(chat_id, "🔒 Это закрытый бот, он работает только для разработчиков.")
            return
    else:
        return

    # --- Команды для всех в подписанных чатах ---
    if cmd == "/id":
        send_message(chat_id, f"🆔 id этого чата: <code>{chat_id}</code>")

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

    # --- Команды только для админов ---
    elif cmd == "/start":
        add_chat(chat_id, title)
        send_message(chat_id, greeting_text())

    elif cmd == "/stop":
        if remove_chat(chat_id):
            send_message(chat_id, "🔕 Уведомления отключены. Включить обратно: /start")
        else:
            send_message(chat_id, "Уведомления тут и так отключены. Включить: /start")

    elif cmd == "/status":
        send_message(chat_id, cmd_status())

    elif cmd == "/chats":
        send_message(chat_id, cmd_chats())

    elif cmd == "/test":
        cmd_test(chat_id)

    elif cmd == "/say":
        body = text.split(None, 1)
        if len(body) < 2:
            send_message(chat_id, "Напиши текст после команды: <code>/say Привет всем!</code>")
            return
        ok_count, fail_count = broadcast_text(esc(body[1]))
        log.info("/say от %s: отправлено %d, ошибок %d", who(user), ok_count, fail_count)
        send_message(chat_id, f"📣 Отправлено в {ok_count} чат(ов), ошибок: {fail_count}.")


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

    load_admins()
    load_chats()
    FIRST_RUN["baseline"] = not load_state()
    load_bot_username()
    log.info("Админы: usernames=%s, ids=%s", sorted(ADMIN_USERNAMES), admin_ids())
    threading.Thread(target=monitor_thread, name="monitor", daemon=True).start()
    threading.current_thread().name = "telegram"
    telegram_polling()
