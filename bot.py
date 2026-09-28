import json
import os
import threading
import time
import urllib.error
import urllib.request

# ==================== НАСТРОЙКИ (ИЗ ОКРУЖЕНИЯ RENDER) ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# Репозиторий для отслеживания
REPO_OWNER = "akanchik-id"
REPO_NAME = "akanchik-id.github.io"
CHECK_INTERVAL = 300  # Проверка каждые 5 минут


# ==================== ФУНКЦИИ TELEGRAM API ====================
def tg_api_request(method, payload=None):
    if not BOT_TOKEN:
        print("❌ Ошибка: BOT_TOKEN не установлен в Environment Variables!")
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = None
    headers = {}

    if payload:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"Ошибка TG API ({method}): {e}")
    return None


def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_api_request("sendMessage", payload)


def edit_message_text(
    chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"
):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_api_request("editMessageText", payload)


def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    tg_api_request("answerCallbackQuery", payload)


# ==================== ФУНКЦИИ GITHUB API ====================
def get_latest_commit():
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits"
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        ),
        'Accept': 'application/vnd.github.v3+json',
    }
    if GITHUB_TOKEN:
        headers['Authorization'] = f'token {GITHUB_TOKEN}'

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                if data and isinstance(data, list) and len(data) > 0:
                    commit = data[0]
                    author = commit["commit"]["author"]["name"]
                    return (
                        commit["sha"],
                        commit["commit"]["message"],
                        commit["html_url"],
                        author,
                        None,
                    )
    except urllib.error.HTTPError as e:
        err_msg = f"HTTP {e.code}: {e.reason}"
        if e.code == 403:
            err_msg += " (Лимит запросов GitHub API)"
        elif e.code == 404:
            err_msg += " (Репозиторий не найден)"
        print(f"Ошибка GitHub API: {err_msg}")
        return None, None, None, None, err_msg
    except Exception as e:
        print(f"Ошибка подключения к GitHub: {e}")
        return None, None, None, None, str(e)

    return None, None, None, None, "Пустой ответ от GitHub"


# ==================== ВЕРСТКА ГЛАВНОГО МЕНЮ ====================
def get_main_menu():
    text = (
        f"👋 **Привет! Я твой автономный GitHub-помощник!**\n\n"
        f"Я слежу за репозиторием `{REPO_OWNER}/{REPO_NAME}` и сразу присылаю "
        f"уведомление, когда появляется новый коммит.\n\n"
        f"Выбери действие на кнопках ниже 👇"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "📊 Последний коммит",
                    "callback_data": "check_commit",
                }
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


# ==================== МОНИТОРИНГ ГИТХАБА (ПОТОК 1) ====================
def github_monitor_thread():
    print("🚀 [GitHub Monitor] Запущен мониторинг коммитов...")
    last_sha, _, _, _, _ = get_latest_commit()

    if last_sha:
        print(f"📌 [GitHub Monitor] Текущий хэш: {last_sha[:7]}")

    while True:
        time.sleep(CHECK_INTERVAL)
        sha, msg, url, author, err = get_latest_commit()
        if sha and sha != last_sha:
            last_sha = sha
            text = (
                f"🚀 **Новый коммит в {REPO_OWNER}/{REPO_NAME}!**\n\n"
                f"👤 **Автор:** {author}\n"
                f"📝 **Сообщение:** {msg}"
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔗 Посмотреть на GitHub", "url": url}]
                ]
            }
            target_chat = CHAT_ID if CHAT_ID else "5399489280"
            send_message(target_chat, text, reply_markup=keyboard)
            print(f"✅ Уведомление отправлено: {sha[:7]}")


# ==================== ОБРАБОТКА КОМАНД И КНОПОК (ПОТОК 2) ====================
def handle_update(update):
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        if text == "/start":
            welcome_text, keyboard = get_main_menu()
            send_message(chat_id, welcome_text, reply_markup=keyboard)

    elif "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        data = cb.get("data")

        back_keyboard = {
            "inline_keyboard": [
                [{"text": "🔙 Назад в меню", "callback_data": "main_menu"}]
            ]
        }

        if data == "main_menu":
            answer_callback_query(cb_id)
            welcome_text, keyboard = get_main_menu()
            edit_message_text(
                chat_id, msg_id, welcome_text, reply_markup=keyboard
            )

        elif data == "check_commit":
            answer_callback_query(cb_id, "Загружаю данные...")
            sha, msg, url, author, err = get_latest_commit()
            if sha:
                reply = (
                    f"📌 **Последний коммит в {REPO_NAME}:**\n\n"
                    f"🔑 `SHA:` `{sha[:7]}`\n"
                    f"👤 `Автор:` {author}\n"
                    f"📝 `Сообщение:` {msg}"
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "🔗 Перейти к коммиту", "url": url}],
                        [
                            {
                                "text": "🔙 Назад в меню",
                                "callback_data": "main_menu",
                            }
                        ],
                    ]
                }
                edit_message_text(chat_id, msg_id, reply, reply_markup=kb)
            else:
                reply = (
                    f"⚠️ **Не удалось получить данные с GitHub.**\n\n"
                    f"🔍 **Причина:** `{err}`"
                )
                edit_message_text(
                    chat_id, msg_id, reply, reply_markup=back_keyboard
                )

        elif data == "about_repo":
            answer_callback_query(cb_id)
            info = (
                f"📁 **Отслеживаемый репозиторий:**\n\n"
                f"• **Владелец:** `{REPO_OWNER}`\n"
                f"• **Название:** `{REPO_NAME}`\n"
                f"• **Интервал проверки:** каждые {CHECK_INTERVAL // 60} мин."
            )
            edit_message_text(
                chat_id, msg_id, info, reply_markup=back_keyboard
            )

        elif data == "bot_status":
            answer_callback_query(cb_id)
            status = (
                "🟢 **Бот работает идеально!**\n\n"
                "• **Хостинг:** Render.com (24/7)\n"
                "• **Режим:** Интерактивное меню + автомониторинг\n"
                "• **Язык:** Python 3"
            )
            edit_message_text(
                chat_id, msg_id, status, reply_markup=back_keyboard
            )


def telegram_polling_thread():
    print("💬 [Telegram Polling] Запущена обработка /start и кнопок...")
    offset = 0
    while True:
        res = tg_api_request("getUpdates", {"offset": offset, "timeout": 20})
        if res and res.get("ok"):
            for update in res.get("result", []):
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception as e:
                    print(f"Ошибка обработки обновления: {e}")
        time.sleep(1)


# ==================== ТОЧКА ВХОДА ====================
if __name__ == "__main__":
    gh_thread = threading.Thread(target=github_monitor_thread, daemon=True)
    gh_thread.start()
    telegram_polling_thread()
