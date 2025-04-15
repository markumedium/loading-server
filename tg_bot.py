import time
import requests
import threading
import json
import os
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, timezone

BOT_TOKEN = "7705882526:AAG0ZaDbFjNxGRe7-TGbAVSEIrwKuOmOW0k"
CHAT_ID = "-1002540325886"

DATA_PATH = "status_history"
TRUCKS_PATH = "trucks.json"
SEND_HOUR_UTC = 14  # 19:00 по UTC+5
notified_trucks = {}

# ========== УТИЛИТЫ ==========
def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}

def load_trucks():
    if os.path.exists(TRUCKS_PATH):
        with open(TRUCKS_PATH, "r") as f:
            return json.load(f)
    return []

# ========== ОТПРАВКА ==========
def send_to_telegram(text=None, image_path=None, reply_markup=None):
    if text:
        data = {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data)
    if image_path:
        with open(image_path, 'rb') as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                          files={"photo": f},
                          data={"chat_id": CHAT_ID})

# ========== PNG ОТЧЁТ ==========
def generate_png_report(date_str):
    path = f"report_{date_str}.png"
    # Здесь можно использовать существующую логику генерации PNG
    # Сейчас делаем заглушку
    with open(path, "wb") as f:
        f.write(b"Fake PNG content")
    return path

# ========== PDF ОТЧЁТ ==========
def generate_pdf_report(date_str):
    path = f"report_{date_str}.pdf"
    # Заглушка — реальную генерацию PDF добавим позже
    with open(path, "wb") as f:
        f.write(b"Fake PDF content")
    return path

# ========== ОБРАБОТКА КОМАНД ==========
def handle_command(text):
    text = text.lower().strip()
    if "отчет" in text:
        send_to_telegram(
            text="Выберите формат отчёта:",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "📷 PNG", "callback_data": "report_png"}],
                    [{"text": "📄 PDF", "callback_data": "report_pdf"}]
                ]
            }
        )

def handle_callback(callback_data):
    today = datetime.now().strftime("%Y-%m-%d")
    if callback_data == "report_png":
        path = generate_png_report(today)
        send_to_telegram(image_path=path)
    elif callback_data == "report_pdf":
        path = generate_pdf_report(today)
        send_to_telegram(text=f"PDF-файл: {today}", image_path=path)

# ========== ОБРАБОТКА ОБНОВЛЕНИЙ ==========
def check_updates():
    offset = None
    while True:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        if offset:
            url += f"?offset={offset}"
        try:
            resp = requests.get(url).json()
            for result in resp.get("result", []):
                offset = result["update_id"] + 1
                if "message" in result:
                    msg = result["message"]
                    text = msg.get("text", "")
                    handle_command(text)
                elif "callback_query" in result:
                    data = result["callback_query"]["data"]
                    handle_callback(data)
        except Exception as e:
            print("Ошибка в check_updates:", e)
        time.sleep(3)

# ========== СТАРТ ==========
def start_telegram_bot():
    print("[Telegram] Бот запущен и работает 24/7")
    threading.Thread(target=check_updates, daemon=True).start()

start_telegram_bot()
