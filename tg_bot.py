import time
import requests
import threading
import json
import os
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, timezone
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

BOT_TOKEN = "7705882526:AAG0ZaDbFjNxGRe7-TGbAVSEIrwKuOmOW0k"
CHAT_ID = "-1002540325886"

DATA_PATH = "status_history"
TRUCKS_PATH = "trucks.json"
SEND_HOUR_UTC = 14  # 19:00 по UTC+5
notified_trucks = {}
report_pending = False  # блокировка выбора формата
processed_callbacks = set()  # защита от повторов

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
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                          files={"document": f},
                          data={"chat_id": CHAT_ID})
        os.remove(image_path)

# ========== PDF/PNG ОТЧЁТЫ ==========
def generate_report_dataframe(date_str):
    trucks = load_trucks()
    history = load_json(os.path.join(DATA_PATH, f"{date_str}.json"))
    statuses = ['Отгружается', 'Готов к выезду', 'Выехал']
    rows = []
    for truck in trucks:
        tid = truck['id']
        model = truck.get('model', '-')
        plate = truck.get('licensePlate', '-')
        cycle = truck.get('cycle', 1)
        entries = history.get(tid, [])
        by_status = {s: ('', '') for s in statuses}
        for i in range(1, len(entries)):
            s1, t1 = entries[i-1]['status'], entries[i-1]['timestamp']
            s2, t2 = entries[i]['status'], entries[i]['timestamp']
            if s1 in by_status:
                start = datetime.utcfromtimestamp(t1).strftime("%H:%M")
                delta = str(timedelta(seconds=t2 - t1))
                by_status[s1] = (start, delta)
        if entries:
            ls, lt = entries[-1]['status'], entries[-1]['timestamp']
            if ls in by_status:
                start = datetime.utcfromtimestamp(lt).strftime("%H:%M")
                delta = str(timedelta(seconds=int(time.time() - lt)))
                by_status[ls] = (start, delta)
        rows.append({
            "Модель": model,
            "Госномер": plate,
            "Статус": truck.get('status', '-')
        } | {s: f"({by_status[s][0]}) {by_status[s][1]}" if by_status[s][0] else '' for s in statuses} | {"Цикл": cycle})
    return pd.DataFrame(rows)

def generate_pdf_report(date_str):
    df = generate_report_dataframe(date_str)
    path = f"report_{date_str}.pdf"
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter
    textobject = c.beginText(40, height - 40)
    textobject.setFont("Helvetica", 10)
    lines = df.to_string(index=False).split("\n")
    for line in lines:
        textobject.textLine(line)
    c.drawText(textobject)
    c.save()
    return path

def generate_png_report(date_str):
    df = generate_report_dataframe(date_str)
    fig, ax = plt.subplots(figsize=(12, len(df) * 0.5 + 2))
    ax.axis('off')
    tbl = ax.table(cellText=df.values, colLabels=df.columns, loc='center', cellLoc='center')
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    path = f"report_{date_str}.png"
    plt.savefig(path, bbox_inches='tight')
    plt.close()
    return path

# ========== ОБРАБОТКА КОМАНД ==========
def handle_command(text):
    global report_pending
    if report_pending:
        return

    text = text.lower().strip()
    if text in ["/отчет", "/отчет@marmari_loadcontrol_bot", "/otchet", "/otchet@marmari_loadcontrol_bot"]:
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
    elif text in ["/status", "/status@marmari_loadcontrol_bot"]:
        trucks = load_trucks()
        statuses = "\n".join(f"{t['model']} / {t['licensePlate']}: {t['status']}" for t in trucks)
        send_to_telegram("*Текущие статусы:*\n\n" + statuses)
    elif text.startswith("/mashina"):
        keyword = text.split(" ", 1)[-1].strip().lower()
        matches = []
        for t in load_trucks():
            if keyword in t['model'].lower() or keyword in t['licensePlate'].lower():
                matches.append(f"{t['model']} / {t['licensePlate']}: {t['status']}")
        send_to_telegram("*Результаты поиска:*\n\n" + ("\n".join(matches) if matches else "Ничего не найдено"))

# ========== CALLBACK ==========
def handle_callback(callback_data, callback_id):
    global report_pending
    if callback_id in processed_callbacks:
        return
    processed_callbacks.add(callback_id)

    today = datetime.now().strftime("%Y-%m-%d")
    if callback_data == "report_png":
        path = generate_png_report(today)
        send_to_telegram(image_path=path)
    elif callback_data == "report_pdf":
        path = generate_pdf_report(today)
        send_to_telegram(text=f"PDF-файл: {today}", image_path=path)

    report_pending = False

# ========== ОБНОВЛЕНИЯ ==========
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
                    cid = result["callback_query"]["id"]
                    handle_callback(data, cid)
        except Exception as e:
            print("Ошибка в check_updates:", e)
        time.sleep(3)

# ========== СТАРТ ==========
def start_telegram_bot():
    print("[Telegram] Бот запущен и работает 24/7")
    threading.Thread(target=check_updates, daemon=True).start()

start_telegram_bot()
