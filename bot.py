import html
import json
import os
import threading
import time
import urllib.error
import urllib.request

# ==================== НАСТРОЙКИ ====================
# Переменные берутся из окружения системы (или задаются в кавычках ниже по умолчанию)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8987351216:AAFuBiem5l3Ef5FKWJCJFBVZsV4aJX2UcbU")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "5399489280")  # куда слать уведомления

REPO_OWNER = os.environ.get("REPO_OWNER", "akanchik-id")
REPO_NAME = os.environ.get("REPO_NAME", "akanchik-id.github.io")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))  # интервал проверки (секунд)

START_TIME = time.time()
STATE = {"last_check": None, "last_error": None}


# ==================== TELEGRAM API ====================
def tg_api_request(method, payload=None, timeout=15):
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не установлен!")
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
            print(f"Ошибка TG API ({method}): {e.code} {body[:200]}")
    except Exception as e:
        print(f"Ошибка TG API ({method}): {e}")
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


def get_main_menu():
    text = (
        "👋 <b>Привет! Я твой автономный GitHub-помощник!</b>\n\n"
        f"Слежу за репозиторием <code>{esc(REPO_OWNER)}/{esc(REPO_NAME)}</code> "
        "и сразу присылаю уведомление о новых коммитах.\n\n"
        "Выбери действие 👇"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📊 Последний коммит", "callback_data": "check_commit"},
                {"text": "📜 Последние 5", "callback_data": "list_commits"},
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


# ==================== МОНИТОРИНГ ГИТХАБА (ПОТОК 1) ====================
def notify_commit(c):
    text = f"🚀 <b>Новый коммит в {esc(REPO_OWNER)}/{esc(REPO_NAME)}!</b>\n\n{fmt_commit(c)}"
    kb = {
        "inline_keyboard": [
            [{"text": "🔗 Посмотреть на GitHub", "url": c["html_url"]}]
        ]
    }
    send_message(CHAT_ID, text, reply_markup=kb)


def github_monitor_thread():
    print("🚀 [GitHub Monitor] Запущен мониторинг коммитов...")
    if not CHAT_ID:
        print("⚠️ CHAT_ID не задан — уведомления отправляться не будут!")

    last_sha, etag = None, None
    while True:
        try:
            status, data, new_etag, err = gh_get("/commits?per_page=10", etag)
            STATE["last_check"] = time.time()
            STATE["last_error"] = err

            if status == 200 and data:
                etag = new_etag
                shas = [c["sha"] for c in data]
                if last_sha is None:
                    last_sha = shas[0]
                    print(f"📌 Текущий хэш: {last_sha[:7]}")
                elif shas[0] != last_sha:
                    new = data[: shas.index(last_sha)] if last_sha in shas else data
                    last_sha = shas[0]
                    if CHAT_ID:
                        for c in reversed(new[:5]):
                            notify_commit(c)
                        print(f"✅ Отправлено уведомлений: {len(new[:5])}")
            elif err:
                print(f"Ошибка GitHub API: {err}")
        except Exception as e:
            STATE["last_error"] = str(e)
            print(f"Ошибка мониторинга: {e}")
        time.sleep(CHECK_INTERVAL)


# ==================== КОМАНДЫ И КНОПКИ (ПОТОК 2) ====================
def handle_update(update):
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").split("@")[0].strip()

        if text == "/start":
            t, kb = get_main_menu()
            send_message(chat_id, t, reply_markup=kb)
        elif text == "/last":
            t, kb = screen_last_commit()
            send_message(chat_id, t, reply_markup=kb)
        elif text == "/commits":
            t, kb = screen_commit_list()
            send_message(chat_id, t, reply_markup=kb)

    elif "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        data = cb.get("data")

        screens = {
            "main_menu": get_main_menu,
            "check_commit": screen_last_commit,
            "list_commits": screen_commit_list,
            "about_repo": screen_repo_info,
            "bot_status": screen_status,
        }
        answer_callback_query(cb_id, "Загружаю..." if data in ("check_commit", "list_commits", "about_repo") else None)
        if data in screens:
            t, kb = screens[data]()
            edit_message_text(chat_id, msg_id, t, reply_markup=kb)


def telegram_polling_thread():
    print("💬 [Telegram Polling] Запущена обработка команд и кнопок...")
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
                except Exception as e:
                    print(f"Ошибка обработки обновления: {e}")
        else:
            time.sleep(5)


# ==================== ТОЧКА ВХОДА ====================
if __name__ == "__main__":
    # Запускаем фоновый мониторинг GitHub и основной цикл Telegram
    threading.Thread(target=github_monitor_thread, daemon=True).start()
    telegram_polling_thread()
