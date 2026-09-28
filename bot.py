import json
import time
import urllib.request

BOT_TOKEN = "8987351216:AAEzBIjHcT0IjLY_xldTlSJ23e9ExPuBD0A"
CHAT_ID = "5399489280"
REPO_OWNER = "akanchik-id"
REPO_NAME = "akanchik-id.github.io"
CHECK_INTERVAL = 300  # Проверка каждые 5 минут

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

def get_latest_commit():
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits"
    req = urllib.request.Request(url, headers={'User-Agent': 'Termux-Bot'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                if data:
                    commit = data[0]
                    return commit["sha"], commit["commit"]["message"], commit["html_url"]
    except Exception as e:
        print(f"Ошибка GitHub API: {e}")
    return None, None, None

print("🚀 Запуск мониторинга GitHub...")
last_commit_sha, _, _ = get_latest_commit()

if last_commit_sha:
    print(f"📌 Текущий хэш коммита: {last_commit_sha[:7]}")

while True:
    time.sleep(CHECK_INTERVAL)
    sha, msg, url = get_latest_commit()
    if sha and sha != last_commit_sha:
        last_commit_sha = sha
        text = (
            f"🚀 **Новый коммит в репозитории!**\n\n"
            f"📝 **Сообщение:** {msg}\n"
            f"🔗 [Посмотреть изменения]({url})"
        )
        send_telegram_message(text)
        print(f"✅ Уведомление отправлено: {sha[:7]}")
