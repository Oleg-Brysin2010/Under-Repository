import json
import threading
import time
import urllib.request

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8987351216:AAFuBiem5l3Ef5FKWJCJFBVZsV4aJX2UcbU"
CHAT_ID = "5399489280"

# Репозиторий для отслеживания
REPO_OWNER = "akanchik-id"
REPO_NAME = "akanchik-id.github.io"
CHECK_INTERVAL = 300  # Проверка каждые 5 минут (300 сек)


# ==================== ФУНКЦИИ TELEGRAM API ====================
def tg_api_request(method, payload=None):
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
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_api_request("sendMessage", payload)

def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    tg_api_request("answerCallbackQuery", payload)


# ==================== ФУНКЦИИ GITHUB API ====================
def get_latest_commit():
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits"
    req = urllib.request.Request(url, headers={'User-Agent': 'Render-Telegram-Bot'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                if data:
                    commit = data[0]
                    author = commit["commit"]["author"]["name"]
                    return commit["sha"], commit["commit"]["message"], commit["html_url"], author
    except Exception as e:
        print(f"Ошибка GitHub API: {e}")
    return None, None, None, None


# ==================== МОНИТОРИНГ ГИТХАБА (ПОТОК 1) ====================
def github_monitor_thread():
    print("🚀 [GitHub Monitor] Запущен мониторинг коммитов...")
    last_sha, _, _, _ = get_latest_commit()
    
    if last_sha:
        print(f"📌 [GitHub Monitor] Текущий хэш: {last_sha[:7]}")
        
    while True:
        time.sleep(CHECK_INTERVAL)
        sha, msg, url, author = get_latest_commit()
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
            send_message(CHAT_ID, text, reply_markup=keyboard)
            print(f"✅ Уведомление отправлено: {sha[:7]}")


# ==================== ОБРАБОТКА КОМАНД И КНОПОК (ПОТОК 2) ====================
def handle_update(update):
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")
        
        if text == "/start":
            welcome_text = (
                f"👋 **Привет! Я твой автономный GitHub-помощник!**\n\n"
                f"Я слежу за репозиторием `{REPO_OWNER}/{REPO_NAME}` и сразу присылаю "
                f"уведомление, когда появляется новый коммит.\n\n"
                f"Выбери действие на кнопках ниже 👇"
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "📊 Последний коммит", "callback_data": "check_commit"}],
                    [
                        {"text": "📁 О репозитории", "callback_data": "about_repo"},
                        {"text": "🟢 Статус бота", "callback_data": "bot_status"}
                    ],
                    [{"text": "🌐 Открыть GitHub", "url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}"}]
                ]
            }
            send_message(chat_id, welcome_text, reply_markup=keyboard)

    elif "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        chat_id = cb["message"]["chat"]["id"]
        data = cb.get("data")
        
        if data == "check_commit":
            answer_callback_query(cb_id, "Получаю данные с GitHub...")
            sha, msg, url, author = get_latest_commit()
            if sha:
                reply = (
                    f"📌 **Последний коммит:**\n\n"
                    f"🔑 `SHA:` `{sha[:7]}`\n"
                    f"👤 `Автор:` {author}\n"
                    f"📝 `Сообщение:` {msg}"
                )
                kb = {"inline_keyboard": [[{"text": "🔗 Перейти к коммиту", "url": url}]]}
                send_message(chat_id, reply, reply_markup=kb)
            else:
                send_message(chat_id, "⚠️ Не удалось получить коммит с GitHub.")
                
        elif data == "about_repo":
            answer_callback_query(cb_id)
            info = (
                f"📁 **Отслеживаемый репозиторий:**\n\n"
                f"• **Владелец:** `{REPO_OWNER}`\n"
                f"• **Название:** `{REPO_NAME}`\n"
                f"• **Интервал проверки:** каждые {CHECK_INTERVAL // 60} мин."
            )
            send_message(chat_id, info)
            
        elif data == "bot_status":
            answer_callback_query(cb_id)
            status = (
                "🟢 **Бот работает идеально!**\n\n"
                "• **Сервер:** Render.com (24/7)\n"
                "• **Режим:** Автономный (Мониторинг + Интерактив)\n"
                "• **Язык:** Python 3"
            )
            send_message(chat_id, status)


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
