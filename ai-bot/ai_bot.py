import os
import time
import requests

# Ключи берутся автоматически из секретного файла .env на сервере
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL = "llama-3.3-70b-versatile"

TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Память диалогов для каждого пользователя
chat_histories = {}

def ask_groq(chat_id, user_text):
    if chat_id not in chat_histories:
        chat_histories[chat_id] = [
            {"role": "system", "content": "Ты умный, дружелюбный и полезный ИИ-ассистент. Отвечай на русском языке."}
        ]
    
    chat_histories[chat_id].append({"role": "user", "content": user_text})
    
    # Храним последние 15 сообщений, чтобы не перегружать память
    if len(chat_histories[chat_id]) > 16:
        chat_histories[chat_id] = [chat_histories[chat_id][0]] + chat_histories[chat_id][-15:]

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": MODEL,
        "messages": chat_histories[chat_id],
        "temperature": 0.7
    }
    
    try:
        resp = requests.post(GROQ_URL, headers=headers, json=data, timeout=30)
        if resp.status_code == 200:
            answer = resp.json()["choices"][0]["message"]["content"]
            chat_histories[chat_id].append({"role": "assistant", "content": answer})
            return answer
        else:
            return f"Ошибка ИИ ({resp.status_code}): {resp.text}"
    except Exception as e:
        return f"Ошибка соединения с ИИ: {e}"

def send_message(chat_id, text):
    requests.post(f"{TG_URL}/sendMessage", json={"chat_id": chat_id, "text": text})

def main():
    print("ИИ-бот успешно запущен!")
    offset = None
    while True:
        try:
            params = {"timeout": 30, "offset": offset}
            resp = requests.get(f"{TG_URL}/getUpdates", params=params, timeout=35).json()
            
            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg or "text" not in msg:
                    continue
                
                chat_id = msg["chat"]["id"]
                text = msg["text"]
                
                if text == "/start":
                    send_message(chat_id, "Привет! Я ИИ-бот 🤖 Напиши мне любой вопрос, и я отвечу!\nЧтобы очистить историю диалога, напиши /clear")
                elif text == "/clear":
                    chat_histories.pop(chat_id, None)
                    send_message(chat_id, "Память диалога очищена! 🧹")
                else:
                    # Показываем статус "печатает..."
                    requests.post(f"{TG_URL}/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
                    reply = ask_groq(chat_id, text)
                    send_message(chat_id, reply)
                    
        except Exception as e:
            print(f"Ошибка цикла: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
