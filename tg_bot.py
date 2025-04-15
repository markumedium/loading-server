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
report_pending = False

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

# ========== PNG / PDF ОТЧЁТ ==========
def generate_png_report(date_str):
    path = f"report_{date_str}.png"
    with open(path, "wb") as f:
        f.write(b"Fake PNG content")
    return path

def generate_pdf_report(date_str):
    path = f"report_{date_str}.pdf"
    with open(path, "wb") as f:
        f.write(b"Fake PDF content")
    return path

# ========== УВЕДОМЛЕНИЕ >30 МИН ==========
def check_long_loading():
    today = datetime.now().strftime("%Y-%m-%d")
    trucks = load_trucks()
    history = load_json(os.path.join(DATA_PATH, f"{today}.json"))

    for truck in trucks:
        if truck['status'] != "Отгружается":
            continue

        tid = truck['id']
        cycle = truck.get('cycle', 1)
        entries = history.get(tid, [])
        start_time = None

        for entry in reversed(entries):
            if entry['status'] == "Отгружается" and entry['cycle'] == cycle:
                start_time = entry['timestamp']
                break

        if not start_time:
            continue

        duration = time.time() - start_time
        if duration >= 1800 and notified_trucks.get(tid) != cycle:
            model = truck.get("model", "")
            plate = truck.get("licensePlate", "")
            start_str = datetime.utcfromtimestamp(start_time).strftime("%H:%M")
            dur_str = str(timedelta(seconds=int(duration)))
            text = f"🚨 *ВНИМАНИЕ*: {model} / {plate}\nСтатус: Отгружается более 30 минут!\n⏱ С начала: ({start_str}), прошло: {dur_str}"
            send_to_telegram(text)
            notified_trucks[tid] = cycle

# ========== АВТОМАТИЧЕСКИЙ ОТЧЕТ ==========
def auto_daily_report():
    sent_today = None
    while True:
        now = datetime.utcnow()
        if now.hour == SEND_HOUR_UTC and (sent_today is None or sent_today != now.date()):
            date = (now + timedelta(hours=5)).strftime("%Y-%m-%d")
            path = generate_png_report(date)
            send_to_telegram(text=f"Автоотчёт за {date}", image_path=path)
            sent_today = now.date()
        time.sleep(60)

# ========== ОБРАБОТКА КОМАНД ==========
def handle_command(text):
    global report_pending
    if report_pending:
        return

    text = text.lower().strip()
    if "отчет" in text:
        report_pending = True
        send_to_telegram(
            text="Выберите формат отчёта:",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "📷 PNG", "callback_data": "report_png"}],
                    [{"text": "📄 PDF", "callback_data": "report_pdf"}]
                ]
            }
        )
    elif text.startswith("/статус"):
        trucks = load_trucks()
        summary = "📋 *Текущие машины:*\n"
        for t in trucks:
            summary += f"\n• {t['model']} ({t['licensePlate']}) — {t['status']}"
        send_to_telegram(summary)
    elif text.startswith("/машина"):
        query = text.replace("/машина", "").strip().lower()
        trucks = load_trucks()
        matches = [t for t in trucks if query in t['licensePlate'].lower() or query in t['model'].lower()]
        if matches:
            for t in matches:
                msg = f"🔍 Найдена машина:\nМодель: {t['model']}\nГосномер: {t['licensePlate']}\nСтатус: {t['status']}"
                send_to_telegram(msg)
        else:
            send_to_telegram("Машина не найдена.")

# ========== ОБРАБОТКА КНОПОК ==========
def handle_callback(callback_data):
    global report_pending
    today = datetime.now().strftime("%Y-%m-%d")
    if callback_data == "report_png":
        path = generate_png_report(today)
        send_to_telegram(image_path=path)
    elif callback_data == "report_pdf":
        path = generate_pdf_report(today)
        send_to_telegram(text=f"PDF-файл: {today}", image_path=path)
    report_pending = False

# ========== ПОЛУЧЕНИЕ СООБЩЕНИЙ ==========
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

# ========== ЗАПУСК ==========
def start_telegram_bot():
    print("[Telegram] Бот запущен и работает 24/7")
    threading.Thread(target=check_updates, daemon=True).start()
    threading.Thread(target=lambda: loop_interval(check_long_loading, 60), daemon=True).start()
    threading.Thread(target=auto_daily_report, daemon=True).start()

def loop_interval(fn, interval):
    while True:
        fn()
        time.sleep(interval)

start_telegram_bot()