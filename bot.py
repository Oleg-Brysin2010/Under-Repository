import base64
import html
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler

# ==================== НАСТРОЙКИ ====================
# Все значения берутся из окружения сервера (файл /root/bot/.env)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "5399489280")  # куда слать уведомления (и единственный, кому бот отвечает)

REPO_OWNER = os.environ.get("REPO_OWNER", "akanchik-id")
REPO_NAME = os.environ.get("REPO_NAME", "akanchik-id.github.io")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))  # интервал проверки (секунд)
WATCH_FILE = os.environ.get("WATCH_FILE", "index.html")  # файл, который присылаем при изменении
LOG_FILE = os.environ.get("LOG_FILE", "bot.log")  # файл лога

START_TIME = time.time()
STATE = {"last_check": None, "last_error": None}

# Что бот "помнит" между проверками
MONITOR = {"commit_sha": None, "commit_etag": None, "file_sha": None}
CHECK_LOCK = threading.Lock()  # чтобы ручная и автоматическая проверки не пересекались


# ==================== ЛОГИРОВАНИЕ ====================
def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # В файл: до 1 МБ, хранится 3 старых копии
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # И в консоль (видно в journalctl / screen)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)


log = logging.getLogger("bot")


# ==================== БЕЗОПАСНОСТЬ ====================
def is_allowed(chat_id):
    """Бот отвечает только владельцу. Если CHAT_ID не задан — не отвечает никому."""
    return bool(CHAT_ID) and str(chat_id) == str(CHAT_ID)


# ==================== TELEGRAM API ====================
def tg_api_request(method, payload=None, timeout=15):
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
        if "not modified" not in body:
            log.error("Ошибка TG API (%s): %s %s", method, e.code, body[:200])
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
    return tg_api_request("sendMessage", payload)


def edit_message_text(chat_id, message_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_api_request("editMessageText", payload)


def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    tg_api_request("answerCallbackQuery", payload)


def setup_bot_commands():
    tg_api_request(
        "setMyCommands",
        {
            "commands": [
                {"command": "start", "description": "Главное меню"},
                {"command": "last", "description": "Последний коммит"},
                {"command": "commits", "description": "Последние 5 коммитов"},
                {"command": "check", "description": "Принудительная проверка GitHub"},
                {"command": "file", "description": f"Прислать {WATCH_FILE}"},
            ]
        },
    )


# ==================== GITHUB API ====================
def gh_get(path, etag=None):
    """Возвращает (status, data, etag, error). 304 = ничего не изменилось."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}{path}"
    headers = {
        "User-Agent": "github-telegram-monitor",
        "Accept": "application/vnd.github+json",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    if etag:
        headers["If-None-Match"] = etag

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return resp.status, data, resp.headers.get("ETag"), None
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, None, etag, None
        msg = f"HTTP {e.code}: {e.reason}"
        if e.code in (403, 429):
            msg += " (лимит запросов GitHub API)"
        elif e.code == 404:
            msg += " (репозиторий не найден)"
        return e.code, None, etag, msg
    except Exception as e:
        return 0, None, etag, str(e)


def get_commits(limit=5):
    status, data, _, err = gh_get(f"/commits?per_page={limit}")
    if status == 200 and isinstance(data, list) and data:
        return data, None
    return None, err or "Пустой ответ от GitHub"


def get_repo_info():
    status, data, _, err = gh_get("")
    if status == 200 and isinstance(data, dict):
        return data, None
    return None, err or "Пустой ответ от GitHub"


# ==================== ФОРМАТИРОВАНИЕ ====================
def esc(s):
    return html.escape(str(s), quote=False)


def fmt_date(iso):
    return iso[:16].replace("T", " ") + " UTC" if iso else "?"


def fmt_commit(c, full=True):
    info = c["commit"]
    lines = info["message"].strip().split("\n")
    title = lines[0][:200]
    body = "\n".join(lines[1:]).strip()[:300]
    text = (
        f"🔑 <code>{c['sha'][:7]}</code>\n"
        f"👤 <b>{esc(info['author']['name'])}</b> · "
        f"{fmt_date(info['author']['date'])}\n"
        f"📝 {esc(title)}"
    )
    if full and body:
        text += f"\n<i>{esc(body)}</i>"
    return text


def fmt_uptime(seconds):
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    return f"{d}д {h}ч {m}м" if d else f"{h}ч {m}м"


# ==================== КЛАВИАТУРЫ ====================
BACK_KB = {
    "inline_keyboard": [
        [{"text": "🔙 Назад в меню", "callback_data": "main_menu"}]
    ]
}

CHECK_KB = {
    "inline_keyboard": [
        [{"text": "🔄 Проверить ещё раз", "callback_data": "force_check"}],
        [{"text": "🔙 Назад в меню", "callback_data": "main_menu"}],
    ]
}


def get_main_menu():
    text = (
        "👋 <b>Привет! Я твой автономный GitHub-помощник!</b>\n\n"
        f"Слежу за репозиторием <code>{esc(REPO_OWNER)}/{esc(REPO_NAME)}</code> "
        "и сразу присылаю уведомление о новых коммитах.\n\n"
        f"📄 Если изменится <code>{esc(WATCH_FILE)}</code>, пришлю его файлом. "
        "Получить прямо сейчас: /file\n"
        "🔄 Проверить GitHub немедленно: /check\n\n"
        "Выбери действие 👇"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📊 Последний коммит", "callback_data": "check_commit"},
                {"text": "📜 Последние 5", "callback_data": "list_commits"},
            ],
            [
                {"text": "🔄 Принудительная проверка", "callback_data": "force_check"},
            ],
            [
                {"text": "📁 О репозитории", "callback_data": "about_repo"},
                {"text": "🟢 Статус бота", "callback_data": "bot_status"},
            ],
            [
                {
                    "text": "🌐 Открыть GitHub",
                    "url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}",
                }
            ],
        ]
    }
    return text, keyboard


# ==================== ПРОВЕРКА GITHUB (общая логика) ====================
def notify_commit(c):
    text = f"🚀 <b>Новый коммит в {esc(REPO_OWNER)}/{esc(REPO_NAME)}!</b>\n\n{fmt_commit(c)}"
    kb = {
        "inline_keyboard": [
            [{"text": "🔗 Посмотреть на GitHub", "url": c["html_url"]}]
        ]
    }
    send_message(CHAT_ID, text, reply_markup=kb)


def check_commits(force=False):
    """Возвращает (кол-во новых коммитов, ошибка). При force ETag игнорируется."""
    etag = None if force else MONITOR["commit_etag"]
    status, data, new_etag, err = gh_get("/commits?per_page=10", etag)

    if status == 304:
        return 0, None
    if status == 200 and data:
        MONITOR["commit_etag"] = new_etag
        shas = [c["sha"] for c in data]
        last_sha = MONITOR["commit_sha"]

        if last_sha is None:
            MONITOR["commit_sha"] = shas[0]
            log.info("Текущий хэш: %s", shas[0][:7])
            return 0, None
        if shas[0] == last_sha:
            return 0, None

        new = data[: shas.index(last_sha)] if last_sha in shas else data
        MONITOR["commit_sha"] = shas[0]
        to_send = new[:5]
        if CHAT_ID:
            for c in reversed(to_send):
                notify_commit(c)
        log.info("Новых коммитов: %d, отправлено уведомлений: %d", len(new), len(to_send))
        return len(new), None

    return 0, err or "Пустой ответ от GitHub"


def check_file():
    """Возвращает (изменился ли файл, ошибка)."""
    sha, raw, err = get_file()
    if sha and raw is not None:
        last = MONITOR["file_sha"]
        if last is None:
            MONITOR["file_sha"] = sha
            log.info("Текущий sha файла: %s", sha[:7])
            return False, None
        if sha != last:
            MONITOR["file_sha"] = sha
            if CHAT_ID:
                send_document(
                    CHAT_ID,
                    WATCH_FILE.split("/")[-1],
                    raw,
                    f"📄 <b>{esc(WATCH_FILE)}</b> обновлён",
                )
            log.info("Файл %s изменился, отправлен", WATCH_FILE)
            return True, None
        return False, None
    return False, err or "Пустой ответ от GitHub"


def run_check(force=False):
    """Одна полная проверка (коммиты + файл). Используется и таймером, и кнопкой."""
    with CHECK_LOCK:
        result = {"new_commits": 0, "file_changed": False, "errors": []}
        try:
            n, err = check_commits(force)
            result["new_commits"] = n
            if err:
                result["errors"].append(f"коммиты: {err}")
        except Exception as e:
            log.exception("Ошибка проверки коммитов")
            result["errors"].append(f"коммиты: {e}")

        try:
            changed, err = check_file()
            result["file_changed"] = changed
            if err:
                result["errors"].append(f"файл: {err}")
        except Exception as e:
            log.exception("Ошибка проверки файла")
            result["errors"].append(f"файл: {e}")

        STATE["last_check"] = time.time()
        STATE["last_error"] = "; ".join(result["errors"]) or None
        for e in result["errors"]:
            log.warning("Ошибка проверки: %s", e)
        return result


# ==================== ЭКРАНЫ ====================
def screen_last_commit():
    commits, err = get_commits(1)
    if not commits:
        return (
            f"⚠️ <b>Не удалось получить данные с GitHub.</b>\n\n"
            f"🔍 <b>Причина:</b> <code>{esc(err)}</code>",
            BACK_KB,
        )
    c = commits[0]
    kb = {
        "inline_keyboard": [
            [{"text": "🔗 Перейти к коммиту", "url": c["html_url"]}],
            [{"text": "🔙 Назад в меню", "callback_data": "main_menu"}],
        ]
    }
    return f"📌 <b>Последний коммит в {esc(REPO_NAME)}:</b>\n\n{fmt_commit(c)}", kb


def screen_commit_list():
    commits, err = get_commits(5)
    if not commits:
        return f"⚠️ Не удалось получить коммиты: <code>{esc(err)}</code>", BACK_KB
    blocks = [fmt_commit(c, full=False) for c in commits]
    text = "📜 <b>Последние коммиты:</b>\n\n" + "\n\n".join(blocks)
    return text, BACK_KB


def screen_repo_info():
    repo, err = get_repo_info()
    if not repo:
        return f"⚠️ Не удалось получить данные: <code>{esc(err)}</code>", BACK_KB
    desc = repo.get("description") or "—"
    text = (
        "📁 <b>Отслеживаемый репозиторий</b>\n\n"
        f"• <b>Название:</b> <code>{esc(repo['full_name'])}</code>\n"
        f"• <b>Описание:</b> {esc(desc)}\n"
        f"• <b>Язык:</b> {esc(repo.get('language') or '—')}\n"
        f"• ⭐ {repo['stargazers_count']} · 🍴 {repo['forks_count']} · "
        f"🐞 {repo['open_issues_count']}\n"
        f"• <b>Последний push:</b> {fmt_date(repo.get('pushed_at'))}\n"
        f"• <b>Проверка:</b> каждые {CHECK_INTERVAL // 60} мин."
    )
    return text, BACK_KB


def screen_status():
    last = STATE["last_check"]
    last_txt = fmt_uptime(time.time() - last) + " назад" if last else "ещё не было"
    err = STATE["last_error"]
    icon = "🟢" if not err else "🟡"
    text = (
        f"{icon} <b>Бот работает</b>\n\n"
        f"• <b>Аптайм:</b> {fmt_uptime(time.time() - START_TIME)}\n"
        f"• <b>Последняя проверка GitHub:</b> {last_txt}\n"
        f"• <b>Уведомления в чат:</b> {'настроены ✅' if CHAT_ID else 'CHAT_ID не задан ❌'}\n"
        f"• <b>GitHub токен:</b> {'есть ✅' if GITHUB_TOKEN else 'нет (лимит 60 запросов/час)'}"
    )
    if err:
        text += f"\n• <b>Последняя ошибка:</b> <code>{esc(err)}</code>"
    return text, BACK_KB


def screen_force_check():
    r = run_check(force=True)
    commits_txt = (
        f"🚀 новых коммитов: <b>{r['new_commits']}</b> (уведомления отправлены)"
        if r["new_commits"]
        else "✅ новых коммитов нет"
    )
    file_txt = (
        f"📄 <code>{esc(WATCH_FILE)}</code> изменился — файл отправлен"
        if r["file_changed"]
        else f"✅ <code>{esc(WATCH_FILE)}</code> без изменений"
    )
    text = f"🔄 <b>Принудительная проверка выполнена</b>\n\n• {commits_txt}\n• {file_txt}"
    if r["errors"]:
        text += "\n\n⚠️ <b>Ошибки:</b>\n" + "\n".join(
            f"<code>{esc(e)}</code>" for e in r["errors"]
        )
    return text, CHECK_KB


# ==================== ОТПРАВКА ФАЙЛА ====================
def send_document(chat_id, filename, content, caption=None):
    """Отправляет файл в Telegram (multipart/form-data, без сторонних библиотек)."""
    boundary = "----tgbot" + str(int(time.time() * 1000))

    def field(name, value):
        return (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()

    body = field("chat_id", chat_id)
    if caption:
        body += field("caption", caption) + field("parse_mode", "HTML")
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
        f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
    ).encode()
    body += content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log.error("Ошибка sendDocument: %s", e)
        return None


def get_file():
    """Возвращает (sha, байты файла, ошибка)."""
    status, data, _, err = gh_get(f"/contents/{WATCH_FILE}")
    if status == 200 and isinstance(data, dict) and "sha" in data:
        try:
            if data.get("content"):
                raw = base64.b64decode(data["content"])
            else:
                with urllib.request.urlopen(data["download_url"], timeout=30) as r:
                    raw = r.read()
            return data["sha"], raw, None
        except Exception as e:
            return None, None, str(e)
    if status == 404:
        err = f"файл {WATCH_FILE} не найден в репозитории"
    return None, None, err or "Пустой ответ от GitHub"


# ==================== АВТОМАТИЧЕСКИЙ МОНИТОРИНГ (ПОТОК 1) ====================
def monitor_thread():
    log.info("Запущен мониторинг коммитов и файла %s (каждые %d с)", WATCH_FILE, CHECK_INTERVAL)
    if not CHAT_ID:
        log.warning("CHAT_ID не задан — уведомления отправляться не будут!")
    while True:
        run_check(force=False)
        time.sleep(CHECK_INTERVAL)


# ==================== КОМАНДЫ И КНОПКИ (ПОТОК 2) ====================
def handle_update(update):
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]

        # --- Whitelist ---
        if not is_allowed(chat_id):
            log.warning(
                "Игнорирую сообщение от чужого чата: chat_id=%s, user=%s, text=%r",
                chat_id,
                (msg.get("from") or {}).get("username"),
                (msg.get("text") or "")[:50],
            )
            return

        text = (msg.get("text") or "").split("@")[0].strip()
        log.info("Команда: %s", text)

        if text == "/start":
            t, kb = get_main_menu()
            send_message(chat_id, t, reply_markup=kb)
        elif text == "/last":
            t, kb = screen_last_commit()
            send_message(chat_id, t, reply_markup=kb)
        elif text == "/commits":
            t, kb = screen_commit_list()
            send_message(chat_id, t, reply_markup=kb)
        elif text == "/check":
            send_message(chat_id, "🔄 Проверяю GitHub...")
            t, kb = screen_force_check()
            send_message(chat_id, t, reply_markup=kb)
        elif text == "/file":
            sha, raw, err = get_file()
            if raw is not None:
                send_document(chat_id, WATCH_FILE.split("/")[-1], raw)
            else:
                send_message(chat_id, f"⚠️ Не удалось получить файл: <code>{esc(err)}</code>")

    elif "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        data = cb.get("data")

        # --- Whitelist ---
        if not is_allowed(chat_id):
            log.warning(
                "Игнорирую нажатие кнопки от чужого чата: chat_id=%s, data=%r",
                chat_id,
                data,
            )
            return

        log.info("Кнопка: %s", data)
        screens = {
            "main_menu": get_main_menu,
            "check_commit": screen_last_commit,
            "list_commits": screen_commit_list,
            "about_repo": screen_repo_info,
            "bot_status": screen_status,
            "force_check": screen_force_check,
        }
        loading = ("check_commit", "list_commits", "about_repo", "force_check")
        answer_callback_query(cb_id, "Загружаю..." if data in loading else None)
        if data in screens:
            t, kb = screens[data]()
            edit_message_text(chat_id, msg_id, t, reply_markup=kb)


def telegram_polling_thread():
    log.info("Запущена обработка команд и кнопок")
    setup_bot_commands()
    offset = 0
    while True:
        res = tg_api_request(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            },
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
    log.info("Бот запускается. Репозиторий: %s/%s", REPO_OWNER, REPO_NAME)
    # Фоновый поток: коммиты и файл; основной цикл — Telegram
    threading.Thread(target=monitor_thread, name="monitor", daemon=True).start()
    threading.current_thread().name = "telegram"
    telegram_polling_thread()
