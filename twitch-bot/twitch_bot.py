"""Telegram-бот, который следит за стримом на Twitch и сам пишет в группы, когда стрим начался.

Что умеет (версия 2):
- сам присылает карточку в подписанные чаты в момент начала стрима (с превью и кнопкой);
- закрепляет её в группе (если у бота есть право закреплять), чтобы было заметно;
- если сообщение не ушло, повторяет попытку;
- при смене названия или игры обновляет карточку;
- когда стрим закончился, снимает закреп и превращает карточку в итог (сколько длился, пик зрителей);
- пишет админу в личку, если нет подписанных чатов, Twitch долго не отвечает и т.п.

Версия 2.1 (всё работает и без ключей Twitch):
- кнопка «Уведомлять меня» под карточкой: нажавших бот отмечает по имени в следующем уведомлении;
- /last и /stats: когда был последний стрим, сколько длился, статистика за неделю и месяц;
- настройки каждого чата: закреп, «без звука», ночной тихий режим (/settings);
- при лимите запросов decapi.me (ошибка 429) бот сам реже спрашивает и меньше запросов делает.

Версия 2.1.1:
- если decapi.me не ответил на запрос названия, карточка не превращается в «Без названия»;
- при смене названия или игры в карточке остаётся строка с отмеченными людьми.
"""
import calendar
import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from logging.handlers import RotatingFileHandler

VERSION = "2.1.1"

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

# Кому бот пишет в личку (ошибки, «Twitch не отвечает», сообщение о запуске). Только этим людям,
# остальные админы получают лишь право управлять ботом. Бот узнаёт id, когда человек напишет ему /start в личке.
NOTIFY_USERNAMES = {
    x.strip().lstrip("@").lower()
    for x in os.environ.get("NOTIFY_USERNAMES", "ghchjfjtfh").split(",")
    if x.strip()
}
NOTIFY_IDS = {x.strip() for x in os.environ.get("NOTIFY_IDS", "").split(",") if x.strip()}
NOTIFY_FILE = os.environ.get("NOTIFY_FILE", "notify.json")
MENTIONS_FILE = os.environ.get("MENTIONS_FILE", "mentions.json")  # кто нажал «Уведомлять меня»
HISTORY_FILE = os.environ.get("HISTORY_FILE", "history.json")  # прошлые стримы
CHAT_SETTINGS_FILE = os.environ.get("CHAT_SETTINGS_FILE", "chat_settings.json")  # настройки по чатам
TZ_OFFSET_HOURS = int(os.environ.get("TZ_OFFSET_HOURS", "3"))  # часовой пояс для ночного режима и статистики (3 = Москва)
DETAILS_TTL = int(os.environ.get("DETAILS_TTL", "180"))  # как часто в режиме без ключей перечитывать название, игру и зрителей
MAX_MENTION_CHARS = 600  # сколько символов имён влезет в уведомление

# Чаты, которые подписаны всегда (id через запятую). Необязательно.
CHAT_IDS = [x.strip() for x in os.environ.get("CHAT_IDS", "").split(",") if x.strip()]


def keys_configured():
    return bool(
        TWITCH_CLIENT_ID
        and TWITCH_CLIENT_SECRET
        and TWITCH_CLIENT_ID != "temp"
        and TWITCH_CLIENT_SECRET != "temp"
    )


# как часто опрашивать Twitch (сек): с официальным API можно чаще, через decapi.me — аккуратнее
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL") or (30 if keys_configured() else 60))
OFFLINE_GRACE_SEC = int(os.environ.get("OFFLINE_GRACE_SEC", "300"))  # сколько секунд "оффлайн" подряд, чтобы считать стрим завершённым
ALERT_AFTER = int(os.environ.get("ALERT_AFTER", "10"))  # после скольких неудачных проверок подряд писать админам
SEND_PREVIEW = os.environ.get("SEND_PREVIEW", "1") == "1"  # присылать картинку-превью стрима
PREVIEW_WAIT = int(os.environ.get("PREVIEW_WAIT", "20"))  # сколько секунд ждать превью в начале стрима
PIN_ANNOUNCE = os.environ.get("PIN_ANNOUNCE", "1") == "1"  # закреплять уведомление в группе
EDIT_ON_END = os.environ.get("EDIT_ON_END", "1") == "1"  # после стрима превращать карточку в итог
STARTUP_NOTICE = os.environ.get("STARTUP_NOTICE", "1") == "1"  # писать админам при запуске бота
RETRY_LIMIT = int(os.environ.get("RETRY_LIMIT", "5"))  # сколько раз повторять отправку в чат, если не вышло
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
LEARNED_NOTIFY = set()  # id людей из NOTIFY_USERNAMES, которых бот узнал
MENTIONS = {}  # id чата -> {id пользователя: имя}: кого отмечать в уведомлении
CHAT_SETTINGS = {}  # id чата -> {"pin": bool, "silent": bool, "night": [с, до] | None}
HISTORY = []  # прошлые стримы
DATA_LOCK = threading.Lock()
STATE = {
    "is_live": False,
    "offline_since": None,
    "stream_id": None,
    "started_at": None,  # когда начался стрим (unix)
    "peak": 0,  # пик зрителей
    "title": None,
    "game": None,
    "messages": {},  # id чата -> {"id": id сообщения, "photo": bool}: карточки, которые отправили
    "retry": {},  # id чата -> сколько раз не получилось отправить
    "games": [],  # во что играл за этот стрим
    "rate_hits": 0,  # сколько проверок подряд упёрлись в лимит запросов
    "stream": None,
    "last_check": None,
    "last_error": None,
    "fail_count": 0,
    "alerted": False,
}
PERSIST_KEYS = ("is_live", "stream_id", "started_at", "peak", "title", "game", "messages", "retry", "games")

ADMIN_COMMANDS = ("/start", "/stop", "/status", "/chats", "/test", "/say", "/pin", "/silent", "/night", "/settings")
# русские варианты команд (в меню Telegram они не показываются, но работают, если их написать)
ALIASES = {
    "/стрим": "/stream", "/статус": "/status", "/чаты": "/chats", "/тест": "/test",
    "/последний": "/last", "/статистика": "/stats", "/уведомлять": "/notify", "/настройки": "/settings",
}

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
    try:
        with open(NOTIFY_FILE, encoding="utf-8") as f:
            LEARNED_NOTIFY.update(str(x) for x in json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Не удалось прочитать %s: %s", NOTIFY_FILE, e)


def learn_notify(user_id):
    """Запоминаем id человека, которому бот должен писать в личку."""
    with ADMINS_LOCK:
        uid = str(user_id)
        if uid in LEARNED_NOTIFY or uid in NOTIFY_IDS:
            return
        LEARNED_NOTIFY.add(uid)
        try:
            with open(NOTIFY_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(LEARNED_NOTIFY), f)
        except Exception as e:
            log.warning("Не удалось сохранить %s: %s", NOTIFY_FILE, e)
    log.info("Буду писать в личку: id=%s", uid)


def notify_ids():
    with ADMINS_LOCK:
        return sorted(NOTIFY_IDS | LEARNED_NOTIFY)


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
    uname = (user.get("username") or "").lower()
    if uname and uname in NOTIFY_USERNAMES:
        learn_notify(uid)
    if uid in ADMIN_IDS or uid in LEARNED_IDS:
        return True
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
    """Личное сообщение только тем, кто указан в NOTIFY_USERNAMES / NOTIFY_IDS
    (человек должен хотя бы раз нажать /start у бота в личке, иначе Telegram не даст боту написать первым)."""
    ids = notify_ids()
    if not ids:
        log.warning("Некому писать в личку: напиши боту /start с аккаунта @%s", ", @".join(sorted(NOTIFY_USERNAMES)))
        return
    for uid in ids:
        res = send_message(uid, text)
        if not (res and res.get("ok")):
            log.info("Не удалось написать %s (он ещё не нажимал /start у бота?)", uid)


# ==================== ДАННЫЕ: «УВЕДОМЛЯТЬ МЕНЯ», НАСТРОЙКИ ЧАТОВ, ИСТОРИЯ ====================
def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        log.warning("Не удалось прочитать %s: %s", path, e)
        return default


def _save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log.warning("Не удалось сохранить %s: %s", path, e)


def load_data():
    mentions = _load_json(MENTIONS_FILE, {})
    if isinstance(mentions, dict):
        MENTIONS.update(
            {str(c): {str(u): n for u, n in v.items()} for c, v in mentions.items() if isinstance(v, dict)}
        )
    settings = _load_json(CHAT_SETTINGS_FILE, {})
    if isinstance(settings, dict):
        CHAT_SETTINGS.update({str(c): v for c, v in settings.items() if isinstance(v, dict)})
    history = _load_json(HISTORY_FILE, [])
    if isinstance(history, list):
        HISTORY.extend(x for x in history if isinstance(x, dict) and "ended" in x and "duration" in x)


def toggle_mention(chat_id, user):
    """Возвращает True, если человека добавили в список «уведомлять», и False, если убрали."""
    cid, uid = str(chat_id), str(user["id"])
    name = user.get("first_name") or user.get("username") or "друг"
    with DATA_LOCK:
        users = MENTIONS.setdefault(cid, {})
        if uid in users:
            users.pop(uid)
            added = False
        else:
            users[uid] = name
            added = True
        _save_json(MENTIONS_FILE, MENTIONS)
    return added


def mention_line(chat_id):
    """Строка с именами тех, кто нажал «Уведомлять меня» (в группе они получат отдельное уведомление)."""
    with DATA_LOCK:
        users = dict(MENTIONS.get(str(chat_id), {}))
    parts, used = [], 0
    for uid, name in users.items():
        if used + len(name) > MAX_MENTION_CHARS:
            break
        parts.append(f'<a href="tg://user?id={uid}">{esc(name)}</a>')
        used += len(name) + 2
    return ("🔔 " + ", ".join(parts)) if parts else ""


def chat_setting(chat_id, key):
    defaults = {"pin": PIN_ANNOUNCE, "silent": False, "night": None}
    with DATA_LOCK:
        return CHAT_SETTINGS.get(str(chat_id), {}).get(key, defaults[key])


def set_chat_setting(chat_id, key, value):
    with DATA_LOCK:
        CHAT_SETTINGS.setdefault(str(chat_id), {})[key] = value
        _save_json(CHAT_SETTINGS_FILE, CHAT_SETTINGS)


def local_hour():
    return int((int(time.time()) // 3600 + TZ_OFFSET_HOURS) % 24)


def is_silent(chat_id):
    """Уведомление без звука: если так включено в чате или сейчас ночной тихий режим."""
    if chat_setting(chat_id, "silent"):
        return True
    night = chat_setting(chat_id, "night")
    if not night:
        return False
    start, end = night
    hour = local_hour()
    return (start <= hour < end) if start < end else (hour >= start or hour < end)


def settings_text(chat_id):
    night = chat_setting(chat_id, "night")
    night_text = f"{night[0]:02d}:00–{night[1]:02d}:00" if night else "выкл"
    with DATA_LOCK:
        people = len(MENTIONS.get(str(chat_id), {}))
    return (
        "⚙️ <b>Настройки этого чата</b>\n\n"
        f"• Закреп уведомления: {'вкл' if chat_setting(chat_id, 'pin') else 'выкл'} (<code>/pin on</code> или <code>/pin off</code>)\n"
        f"• Всегда без звука: {'да' if chat_setting(chat_id, 'silent') else 'нет'} (<code>/silent on</code> или <code>/silent off</code>)\n"
        f"• Ночной тихий режим: {night_text} (<code>/night 23-9</code> или <code>/night off</code>)\n"
        f"• Нажали «Уведомлять меня»: {people}\n\n"
        f"Время для ночного режима: UTC{TZ_OFFSET_HOURS:+d}."
    )


def fmt_ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60} мин назад"
    if seconds < 86400:
        return f"{seconds // 3600} ч назад"
    return f"{seconds // 86400} дн назад"


def record_stream(started, ended, peak, title, games):
    """Запоминаем завершённый стрим для /last и /stats."""
    entry = {
        "started": started, "ended": ended, "duration": max(0, ended - started),
        "peak": peak, "title": title, "games": games,
    }
    with DATA_LOCK:
        HISTORY.append(entry)
        del HISTORY[:-200]
        _save_json(HISTORY_FILE, HISTORY)


def last_text():
    with DATA_LOCK:
        last = HISTORY[-1] if HISTORY else None
    if not last:
        return "Пока нет данных о прошлых стримах: бот начал вести историю недавно."
    text = f"🕓 <b>Последний стрим</b> закончился {fmt_ago(time.time() - last['ended'])}\n\n"
    text += f"📺 {esc(last.get('title') or 'Без названия')}\n"
    if last.get("games"):
        text += f"🎮 {esc(', '.join(last['games']))}\n"
    text += f"⏱ Длился: {fmt_uptime(last['duration'])}\n"
    if last.get("peak"):
        text += f"👀 Пик зрителей: {last['peak']}\n"
    return text.rstrip()


def stats_text():
    with DATA_LOCK:
        items = list(HISTORY)
    if not items:
        return "Пока нет данных: статистика копится с момента запуска версии 2.1."
    now = time.time()
    week = [x for x in items if now - x["ended"] <= 7 * 86400]
    month = [x for x in items if now - x["ended"] <= 30 * 86400]
    week_total = sum(x["duration"] for x in week)
    month_total = sum(x["duration"] for x in month)
    text = "📊 <b>Статистика стримов</b>\n\n"
    text += f"• За 7 дней: {len(week)} шт., всего {fmt_uptime(week_total)}\n"
    text += f"• За 30 дней: {len(month)} шт., всего {fmt_uptime(month_total)}"
    if month:
        text += f", в среднем {fmt_uptime(month_total / len(month))}"
    hours = [int((int(x["started"]) // 3600 + TZ_OFFSET_HOURS) % 24) for x in items if x.get("started")]
    if hours:
        common = max(set(hours), key=hours.count)
        text += f"\n• Чаще всего начинает около {common:02d}:00 (UTC{TZ_OFFSET_HOURS:+d})"
    longest = max(items, key=lambda x: x["duration"])
    text += f"\n• Самый длинный: {fmt_uptime(longest['duration'])}"
    best = max(items, key=lambda x: x.get("peak") or 0)
    if best.get("peak"):
        text += f"\n• Рекорд зрителей: {best['peak']}"
    return text


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
    """Официальный API Twitch, если заданы ключи; иначе публичный прокси decapi.me (без ключей)."""
    if keys_configured():
        return get_stream_official()
    return get_stream_public()


def get_stream_official():
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


# ==================== РЕЖИМ БЕЗ КЛЮЧЕЙ (публичный прокси decapi.me) ====================
DECAPI = "https://decapi.me/twitch/"
_TIME_WORDS = re.compile(r"\b(and|seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b", re.I)
RATE_MARK = "слишком много запросов"  # по этой фразе в ошибке понимаем, что упёрлись в лимит
_DETAILS = {"ts": 0.0, "data": None}  # кэш названия, игры и зрителей (чтобы делать меньше запросов)
_UPTIME_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000, "year": 31536000}


def uptime_seconds(body):
    """'1 hour, 5 minutes' -> число секунд."""
    total = 0
    for num, unit in re.findall(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?", body, re.I):
        total += int(num) * _UPTIME_UNITS[unit.lower()]
    return total


def http_text(url, timeout=15):
    """Возвращает (status, текст, ошибка)."""
    req = urllib.request.Request(url, headers={"User-Agent": "twitch-telegram-bot"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="ignore").strip(), None
    except urllib.error.HTTPError as e:
        return e.code, None, f"HTTP {e.code}"
    except Exception as e:
        return 0, None, str(e)


def get_stream_public():
    """Проверка стрима без ключей через бесплатный публичный сервис decapi.me.
    Возвращает (stream | None если оффлайн, ошибка). Сервис неофициальный, без гарантий."""
    login = urllib.parse.quote(TWITCH_LOGIN)
    status, body, err = http_text(f"{DECAPI}uptime/{login}")
    if "429" in (err or ""):
        return None, f"decapi.me: {RATE_MARK} (429)"
    if err or not body:
        return None, f"decapi.me недоступен: {err or 'пустой ответ'}"
    low = body.lower()
    if "429" in low or "too many" in low:
        return None, f"decapi.me: {RATE_MARK} (429)"
    if "offline" in low:
        _DETAILS["data"] = None  # в следующий раз название и игру прочитаем заново
        return None, None
    # Если стрим идёт, сервис отвечает только временем: "1 hour, 5 minutes"
    leftover = re.sub(r"[\d\s,]", "", _TIME_WORDS.sub("", body))
    if leftover:
        return None, f"decapi.me вернул неожиданный ответ: {body[:100]}"

    def fetch(endpoint):
        st, text, e = http_text(f"{DECAPI}{endpoint}/{login}")
        if e or not text or text.startswith("[Error from"):  # decapi кладёт текст ошибки прямо в ответ
            return None
        return text

    now = time.time()
    if _DETAILS["data"] and now - _DETAILS["ts"] < DETAILS_TTL:
        title, game, viewer_count = _DETAILS["data"]
    else:
        title = fetch("title")
        if title is None:
            # decapi не ответил (429 и т.п.): оставляем прежнее и не кэшируем, повторим на следующей проверке
            title = STATE["title"] or "Без названия"
            game, viewer_count = STATE["game"], None
        else:
            game = fetch("game")
            viewers = fetch("viewercount")
            try:
                viewer_count = int(viewers) if viewers is not None else None
            except ValueError:
                viewer_count = None
            _DETAILS["ts"] = now
            _DETAILS["data"] = (title, game, viewer_count)
    uptime = uptime_seconds(body)
    return {
        "id": "public",
        "title": title,
        "game_name": game,
        "viewer_count": viewer_count,
        "started_ts": (now - uptime) if uptime else None,
        "thumbnail_url": f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{TWITCH_LOGIN}-{{width}}x{{height}}.jpg",
    }, None


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


def send_message(chat_id, text, reply_markup=None, silent=False):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if silent:
        payload["disable_notification"] = True
    return tg_api("sendMessage", payload)


def send_photo(chat_id, photo_url, caption, reply_markup=None, silent=False):
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if silent:
        payload["disable_notification"] = True
    return tg_api("sendPhoto", payload, timeout=30)


def edit_card(chat_id, info, text, kb=None):
    """Меняет текст ранее отправленной карточки (с картинкой или без)."""
    payload = {"chat_id": chat_id, "message_id": info["id"], "parse_mode": "HTML"}
    if kb:
        payload["reply_markup"] = kb
    if info.get("photo"):
        payload["caption"] = text
        return tg_api("editMessageCaption", payload)
    payload["text"] = text
    payload["disable_web_page_preview"] = True
    return tg_api("editMessageText", payload)


def pin_message(chat_id, message_id):
    """Закрепляет сообщение без лишнего уведомления (оно и так заметное). Нужно право «Закреплять сообщения»."""
    res = tg_api("pinChatMessage", {"chat_id": chat_id, "message_id": message_id, "disable_notification": True})
    if not (res and res.get("ok")):
        log.info("Не удалось закрепить сообщение в %s (у бота нет права закреплять?)", chat_id)


def unpin_message(chat_id, message_id):
    tg_api("unpinChatMessage", {"chat_id": chat_id, "message_id": message_id})


def answer_callback(callback_id, text=""):
    tg_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def setup_bot_commands():
    # В общем меню — только безобидные команды; админские не светим
    tg_api(
        "setMyCommands",
        {
            "commands": [
                {"command": "stream", "description": "Идёт ли стрим сейчас"},
                {"command": "last", "description": "Когда был последний стрим"},
                {"command": "stats", "description": "Статистика стримов"},
                {"command": "notify", "description": "Отмечать меня, когда начнётся стрим"},
            ]
        },
    )


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


def stream_card(stream, header, viewers=True):
    title = stream.get("title") or "Без названия"
    game = stream.get("game_name")
    text = f"{header}\n\n📺 <b>{esc(title)}</b>\n"
    if game:
        text += f"🎮 {esc(game)}\n"
    if viewers and stream.get("viewer_count") is not None:
        text += f"👀 Зрителей: {stream['viewer_count']}\n"
    text += f"\n👉 {STREAM_URL}"
    kb = {"inline_keyboard": [[{"text": "▶️ Смотреть стрим", "url": STREAM_URL}]]}
    return text, kb


def live_header():
    return f"🔴 <b>Стрим у {esc(STREAMER_NAME)}!</b>"


def with_bell(kb, chat_id):
    """В группах под карточкой добавляем кнопку «Уведомлять меня»."""
    if str(chat_id).startswith("-"):
        return {"inline_keyboard": kb["inline_keyboard"] + [[{"text": "🔔 Уведомлять меня", "callback_data": "notify"}]]}
    return kb


def ended_card(duration, peak):
    title = STATE.get("title") or "Без названия"
    game = STATE.get("game")
    text = f"⚫ <b>Стрим у {esc(STREAMER_NAME)} закончился</b>\n\n📺 {esc(title)}\n"
    if game:
        text += f"🎮 {esc(game)}\n"
    if duration and duration > 0:
        text += f"⏱ Длился: {fmt_uptime(duration)}\n"
    if peak:
        text += f"👀 Пик зрителей: {peak}\n"
    text += "\nДо следующего стрима 👋"
    kb = {"inline_keyboard": [[{"text": "🌐 Открыть канал", "url": STREAM_URL}]]}
    return text, kb


def preview_url(stream):
    thumb = stream.get("thumbnail_url")
    if not thumb:
        return None
    thumb = thumb.replace("{width}", "1280").replace("{height}", "720")
    return f"{thumb}?t={int(time.time())}"  # cache-buster, чтобы Telegram не взял старую картинку


def preview_ready(url):
    """В первые секунды стрима Twitch отдаёт заглушку «нет превью». Проверяем, что картинка настоящая."""
    req = urllib.request.Request(url, headers={"User-Agent": "twitch-telegram-bot"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ctype = resp.headers.get("Content-Type", "")
            return "404_preview" not in resp.geturl() and ctype.startswith("image")
    except urllib.error.HTTPError:
        return False
    except Exception:
        return True  # проверить не вышло — пусть попробует сам Telegram, в крайнем случае уйдёт текст


def wait_preview(stream):
    """Возвращает ссылку на готовое превью или None, если за PREVIEW_WAIT секунд оно не появилось."""
    url = preview_url(stream)
    if not url:
        return None
    deadline = time.time() + PREVIEW_WAIT
    while True:
        if preview_ready(url):
            return url
        if time.time() >= deadline:
            log.info("Превью стрима пока нет, отправляю без картинки")
            return None
        time.sleep(5)


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


def announce(stream, only_chats=None):
    """Рассылает карточку «Стрим начался». only_chats — повторная отправка только в эти чаты."""
    first_try = only_chats is None
    chats = subscribers() if first_try else list(only_chats)
    if not chats:
        log.warning("Стрим начался, но нет подписанных чатов")
        if first_try:
            notify_admins(
                f"⚠️ Стрим у {esc(STREAMER_NAME)} начался, но ни один чат не подписан на уведомления.\n"
                "Зайди в нужную группу и отправь там /start (от админа бота)."
            )
        return

    base_text, base_kb = stream_card(stream, live_header(), viewers=False)
    photo = wait_preview(stream) if SEND_PREVIEW else None
    log.info("Рассылка уведомления в %d чат(ов)", len(chats))
    ok_count = 0
    for chat_id in chats:
        cid = str(chat_id)
        mentions = mention_line(chat_id)  # те, кто нажал «Уведомлять меня»
        text = base_text + (f"\n\n{mentions}" if mentions else "")
        kb = with_bell(base_kb, chat_id)
        silent = is_silent(chat_id)
        res = None
        is_photo = False
        if photo:
            res = send_photo(chat_id, photo, text, kb, silent)
            is_photo = bool(res and res.get("ok"))
        if not (res and res.get("ok")):  # нет превью или не получилось — шлём обычным текстом
            res = send_message(chat_id, text, kb, silent)
        ok = bool(res and res.get("ok"))
        log.info("Уведомление в %s: %s%s", cid, "ок" if ok else "ошибка", " (без звука)" if silent else "")
        if ok:
            ok_count += 1
            msg_id = res["result"]["message_id"]
            STATE["messages"][cid] = {"id": msg_id, "photo": is_photo}
            STATE["retry"].pop(cid, None)
            if chat_setting(chat_id, "pin"):
                pin_message(chat_id, msg_id)
        else:
            STATE["retry"][cid] = STATE["retry"].get(cid, 0) + 1
            if res:
                handle_send_error(chat_id, res)
        time.sleep(0.1)  # не упираемся в лимиты Telegram
    save_state()
    if first_try and ok_count == 0:
        notify_admins(
            f"⚠️ Стрим начался, но уведомление не ушло ни в один чат ({len(chats)}). "
            "Проверь, что бот всё ещё в группах и может в них писать. Буду пробовать ещё."
        )


def retry_failed(stream):
    """Повторяем отправку в чаты, куда с первого раза не дошло."""
    pending = []
    for cid, attempts in list(STATE["retry"].items()):
        if not is_subscribed(cid) or attempts >= RETRY_LIMIT:
            STATE["retry"].pop(cid, None)
        else:
            pending.append(cid)
    if pending:
        log.info("Повторная отправка уведомления в %d чат(ов)", len(pending))
        announce(stream, only_chats=pending)


def refresh_cards(stream):
    """Название или игра сменились — обновляем уже отправленные карточки (без нового сообщения)."""
    title = stream.get("title") or "Без названия"
    game = stream.get("game_name")
    changed = title != STATE["title"] or game != STATE["game"]
    STATE["title"], STATE["game"] = title, game
    if game and game not in STATE["games"]:
        STATE["games"].append(game)
    if not changed:
        return
    if STATE["messages"]:
        text, kb = stream_card(stream, live_header(), viewers=False)
        for cid, info in STATE["messages"].items():
            m = mention_line(cid)
            edit_card(cid, info, text + (f"\n\n{m}" if m else ""), with_bell(kb, cid))
            time.sleep(0.1)
    save_state()


def finish_stream():
    """Стрим закончился: снимаем закреп и превращаем карточки в итог."""
    end_ts = STATE["offline_since"] or time.time()
    duration = (end_ts - STATE["started_at"]) if STATE["started_at"] else None
    if STATE["started_at"]:
        record_stream(STATE["started_at"], end_ts, STATE["peak"], STATE["title"], list(STATE["games"]))
    text, kb = ended_card(duration, STATE["peak"])
    for cid, info in STATE["messages"].items():
        if chat_setting(cid, "pin"):
            unpin_message(cid, info["id"])
        if EDIT_ON_END:
            edit_card(cid, info, text, kb)
        time.sleep(0.1)
    log.info("Итог стрима: длился %s, пик зрителей %s", fmt_uptime(duration) if duration else "?", STATE["peak"])


# ==================== СОСТОЯНИЕ СТРИМА ====================
def load_state():
    """Возвращает True, если состояние было сохранено раньше."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        for key in PERSIST_KEYS:
            if key in saved:
                STATE[key] = saved[key]
        STATE["is_live"] = bool(STATE["is_live"])
        if not isinstance(STATE["messages"], dict):
            STATE["messages"] = {}
        if not isinstance(STATE["retry"], dict):
            STATE["retry"] = {}
        STATE["peak"] = STATE["peak"] or 0
        if not isinstance(STATE["games"], list):
            STATE["games"] = []
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        log.warning("Не удалось прочитать %s: %s", STATE_FILE, e)
        return False


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({k: STATE[k] for k in PERSIST_KEYS}, f, ensure_ascii=False)
    except Exception as e:
        log.warning("Не удалось сохранить состояние: %s", e)


def reset_stream_state():
    STATE.update(
        is_live=False, offline_since=None, stream_id=None, started_at=None,
        peak=0, title=None, game=None, messages={}, retry={}, games=[],
    )


def parse_started(stream):
    """Время начала стрима из ответа Twitch (в режиме без ключей его нет)."""
    if stream.get("started_ts"):  # режим без ключей: считаем по аптайму
        return stream["started_ts"]
    s = stream.get("started_at")
    if not s:
        return None
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return None


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
            # при лимите запросов бот притормаживает (см. next_delay)
            STATE["rate_hits"] = STATE["rate_hits"] + 1 if RATE_MARK in err else 0
            # Если Twitch (или decapi.me) долго недоступен — сообщаем админам один раз
            if STATE["fail_count"] >= ALERT_AFTER and not STATE["alerted"]:
                STATE["alerted"] = True
                notify_admins(
                    f"⚠️ Twitch не отвечает уже {STATE['fail_count']} проверок подряд.\n"
                    f"<code>{esc(err)}</code>"
                )
            return None, err

        if STATE["alerted"]:
            notify_admins("✅ Связь с Twitch восстановлена.")
        STATE["fail_count"] = 0
        STATE["rate_hits"] = 0
        STATE["alerted"] = False
        STATE["stream"] = stream

        if stream:
            STATE["offline_since"] = None
            if not STATE["is_live"]:
                reset_stream_state()
                STATE["is_live"] = True
                STATE["stream_id"] = stream.get("id")
                STATE["started_at"] = parse_started(stream) or time.time()
                STATE["peak"] = stream.get("viewer_count") or 0
                STATE["title"] = stream.get("title") or "Без названия"
                STATE["game"] = stream.get("game_name")
                STATE["games"] = [STATE["game"]] if STATE["game"] else []
                save_state()
                if FIRST_RUN["baseline"]:
                    # первый запуск без файла состояния: стрим уже идёт — не спамим, просто запоминаем
                    log.info("Первый запуск: стрим уже идёт, уведомление пропущено")
                else:
                    log.info("Стрим начался: %s", stream.get("title"))
                    announce(stream)
            else:
                viewers = stream.get("viewer_count") or 0
                if viewers > STATE["peak"]:
                    STATE["peak"] = viewers
                if STATE["retry"]:
                    retry_failed(stream)
                refresh_cards(stream)
            FIRST_RUN["baseline"] = False
        else:
            FIRST_RUN["baseline"] = False
            if STATE["is_live"]:
                if STATE["offline_since"] is None:
                    STATE["offline_since"] = time.time()
                # ждём несколько минут, чтобы короткий обрыв связи не считался новым стримом
                if time.time() - STATE["offline_since"] >= OFFLINE_GRACE_SEC:
                    log.info("Стрим завершён")
                    finish_stream()
                    reset_stream_state()
                    save_state()
        return stream, None


def next_delay():
    """Пауза до следующей проверки. Если сервис проверки ограничивает запросы (429), ждём всё дольше."""
    hits = STATE["rate_hits"]
    if not hits:
        return CHECK_INTERVAL
    return min(CHECK_INTERVAL * 2 ** min(hits, 3), 300)


def monitor_thread():
    log.info("Слежу за %s (каждые %d с), подписанных чатов: %d", STREAM_URL, CHECK_INTERVAL, len(subscribers()))
    while True:
        try:
            run_check()
        except Exception:
            log.exception("Ошибка в мониторинге")
        time.sleep(next_delay())


# ==================== КОМАНДЫ АДМИНОВ ====================
def cmd_status():
    if STATE["last_check"]:
        last = fmt_uptime(time.time() - STATE["last_check"]) + " назад"
    else:
        last = "ещё не было"
    if keys_configured():
        keys = "официальный API Twitch ✅"
    else:
        keys = "без ключей, через decapi.me ⚠️"
    if STATE["last_error"]:
        live = "❓ неизвестно (ошибка Twitch)"
    else:
        live = "🔴 идёт" if STATE["is_live"] else "⚫ оффлайн"
    text = (
        f"{'🟡' if STATE['last_error'] else '🟢'} <b>Бот работает</b> (версия {VERSION})\n\n"
        f"• <b>Аптайм:</b> {fmt_uptime(time.time() - START_TIME)}\n"
        f"• <b>Канал:</b> {esc(TWITCH_LOGIN)} — {live}\n"
        f"• <b>Режим Twitch:</b> {keys}\n"
        f"• <b>Проверка каждые:</b> {CHECK_INTERVAL} с\n"
        f"• <b>Последняя проверка:</b> {last}\n"
        f"• <b>Подписанных чатов:</b> {len(subscribers())}\n"
        f"• <b>Закреп уведомления:</b> {'вкл' if PIN_ANNOUNCE else 'выкл'}\n"
        f"• <b>Админов известно:</b> {len(admin_ids())}"
    )
    if STATE["last_error"]:
        text += f"\n• <b>Последняя ошибка:</b> <code>{esc(STATE['last_error'])}</code>"
    if STATE["rate_hits"]:
        text += f"\n• <b>Замедление:</b> сервис проверки ограничивает запросы, следующая проверка через {next_delay()} с"
    return text


def cmd_chats():
    items = subscribed_with_titles()
    if not items:
        return "Подписанных чатов нет. Отправь /start в нужной группе."
    lines = [f"• {esc(t or '(без названия)')} — <code>{cid}</code>" for cid, t in items.items()]
    return f"📋 <b>Подписанные чаты ({len(items)}):</b>\n\n" + "\n".join(lines)


def cmd_test(chat_id):
    fake = {
        "title": "Тестовый стрим — так будет выглядеть уведомление",
        "game_name": "Just Chatting",
    }
    text, kb = stream_card(fake, live_header(), viewers=False)
    mentions = mention_line(chat_id)
    if mentions:
        text += f"\n\n{mentions}"
    send_message(chat_id, "🧪 <i>Это тест, реального стрима нет:</i>")
    send_message(chat_id, text, with_bell(kb, chat_id), is_silent(chat_id))


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
    cmd = ALIASES.get(cmd.lower(), cmd.lower())
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
    if cmd in ("/stream", "/id", "/last", "/stats", "/notify"):
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
        # при лимите запросов не долбим сервис ещё и командами
        cooling = STATE["rate_hits"] and STATE["last_check"] and time.time() - STATE["last_check"] < 60
        if fresh:
            stream, err = STATE["stream"], None
        elif cooling:
            stream, err = None, STATE["last_error"]
        else:
            stream, err = run_check()
        if err:
            send_message(chat_id, f"⚠️ Не удалось проверить Twitch: <code>{esc(err)}</code>")
        elif stream:
            t, kb = stream_card(stream, f"🔴 <b>Сейчас стрим у {esc(STREAMER_NAME)}!</b>")
            send_message(chat_id, t, with_bell(kb, chat_id))
        else:
            kb = {"inline_keyboard": [[{"text": "🌐 Открыть канал", "url": STREAM_URL}]]}
            msg_text = f"⚫ У {esc(STREAMER_NAME)} сейчас оффлайн."
            if HISTORY:
                msg_text += "\n\n" + last_text()
            send_message(chat_id, msg_text, with_bell(kb, chat_id))
        if not subscribed:
            send_message(
                chat_id,
                "⚠️ Этот чат <b>не подписан</b> на автоуведомления, поэтому о начале стрима бот тут не напишет. "
                "Чтобы включить, отправь /start",
            )

    elif cmd == "/last":
        send_message(chat_id, last_text())

    elif cmd == "/stats":
        send_message(chat_id, stats_text())

    elif cmd == "/notify":
        if not subscribed:
            send_message(chat_id, "Этот чат не подписан на уведомления. Админ может включить: /start")
        elif not user or is_anonymous_user(user) or anon_sender:
            send_message(chat_id, "Не вижу, кто ты: отключи анонимность админа и повтори команду.")
        elif toggle_mention(chat_id, user):
            send_message(chat_id, "🔔 Буду отмечать тебя, когда начнётся стрим. Отключить: /notify")
        else:
            send_message(chat_id, "🔕 Больше не буду отмечать тебя. Включить обратно: /notify")

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

    elif cmd == "/settings":
        send_message(chat_id, settings_text(chat_id))

    elif cmd in ("/pin", "/silent"):
        arg = parts[1].lower() if len(parts) > 1 else ""
        if arg not in ("on", "off", "вкл", "выкл"):
            send_message(chat_id, f"Напиши <code>{cmd} on</code> или <code>{cmd} off</code>")
            return
        set_chat_setting(chat_id, cmd[1:], arg in ("on", "вкл"))
        send_message(chat_id, settings_text(chat_id))

    elif cmd == "/night":
        arg = parts[1].lower() if len(parts) > 1 else ""
        if arg in ("off", "выкл"):
            set_chat_setting(chat_id, "night", None)
            send_message(chat_id, settings_text(chat_id))
            return
        match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", arg)
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 23 or match.group(1) == match.group(2):
            send_message(chat_id, "Напиши часы, например <code>/night 23-9</code> (с 23:00 до 9:00), или <code>/night off</code>")
            return
        set_chat_setting(chat_id, "night", [int(match.group(1)), int(match.group(2))])
        send_message(chat_id, settings_text(chat_id))

    elif cmd == "/say":
        body = text.split(None, 1)
        if len(body) < 2:
            send_message(chat_id, "Напиши текст после команды: <code>/say Привет всем!</code>")
            return
        ok_count, fail_count = broadcast_text(esc(body[1]))
        log.info("/say от %s: отправлено %d, ошибок %d", who(user), ok_count, fail_count)
        send_message(chat_id, f"📣 Отправлено в {ok_count} чат(ов), ошибок: {fail_count}.")


def handle_callback(cb):
    """Нажатие на кнопку «Уведомлять меня» под карточкой."""
    user = cb.get("from")
    chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
    if cb.get("data") != "notify" or chat_id is None or not user:
        answer_callback(cb["id"])
        return
    if not is_subscribed(chat_id):
        answer_callback(cb["id"], "Этот чат не подписан на уведомления")
        return
    if toggle_mention(chat_id, user):
        answer_callback(cb["id"], "🔔 Буду отмечать тебя, когда начнётся стрим")
    else:
        answer_callback(cb["id"], "🔕 Больше не буду отмечать")


def handle_update(update):
    if "my_chat_member" in update:
        handle_membership(update["my_chat_member"])
    elif "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])


def telegram_polling():
    log.info("Запущена обработка команд")
    setup_bot_commands()
    offset = 0
    while True:
        res = tg_api(
            "getUpdates",
            {"offset": offset, "timeout": 30, "allowed_updates": ["message", "my_chat_member", "callback_query"]},
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
    if not BOT_TOKEN:
        log.error("Не задана переменная окружения BOT_TOKEN")
        raise SystemExit(1)
    log.info("Версия бота: %s", VERSION)
    if keys_configured():
        log.info("Режим Twitch: официальный API")
    else:
        log.info("Режим Twitch: без ключей (публичный прокси decapi.me)")

    load_admins()
    load_chats()
    load_data()
    FIRST_RUN["baseline"] = not load_state()
    load_bot_username()
    log.info("Админы: usernames=%s, ids=%s", sorted(ADMIN_USERNAMES), admin_ids())
    if STARTUP_NOTICE:
        n = len(subscribers())
        note = f"🤖 Бот запущен (версия {VERSION}). Подписанных чатов: {n}."
        if n == 0:
            note += "\n⚠️ Ни один чат не подписан: отправь /start в нужной группе, иначе уведомлений о стриме не будет."
        notify_admins(note)
    threading.Thread(target=monitor_thread, name="monitor", daemon=True).start()
    threading.current_thread().name = "telegram"
    telegram_polling()
