import time
import requests
import threading
import json
import os
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BOT_TOKEN = "7705882526:AAG0ZaDbFjNxGRe7-TGbAVSEIrwKuOmOW0k"
CHAT_ID = "-1002540325886"

DATA_PATH = "status_history"
TRUCKS_PATH = "trucks.json"
SEND_HOUR_UTC = 14  # 19:00 по UTC+5
notified_trucks = {}
report_pending = False  # блокировка выбора формата
processed_callbacks = set()  # защита от повторов

pdfmetrics.registerFont(TTFont('DejaVuSans', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))

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
    doc = SimpleDocTemplate(path, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = []
    title = Paragraph(f"<b>Отчет за {date_str}</b><br/><i>(в скобках указано время начала статуса)</i>", styles['Title'])
    elements.append(title)
    elements.append(Paragraph("<br/><br/>", styles['Normal']))
    data = [df.columns.tolist()] + df.values.tolist()
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('FONTNAME', (0, 0), (-1, -1), 'DejaVuSans'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    elements.append(table)
    doc.build(elements)
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
    if "/отчет" in text or "/otchet" in text:
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
    elif "/status" in text:
        trucks = load_trucks()
        statuses = "\n".join(f"{t['model']} / {t['licensePlate']}: {t['status']}" for t in trucks)
        send_to_telegram("*Текущие статусы:*" + statuses)
    elif "/mashina" in text:
        trucks = load_trucks()
        buttons = [[{"text": f"{t['model']} ({t['licensePlate']})", "callback_data": f"info_{t['id']}"}] for t in trucks]
        send_to_telegram("Выберите машину:", reply_markup={"inline_keyboard": buttons})

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
        report_pending = False
    elif callback_data == "report_pdf":
        path = generate_pdf_report(today)
        send_to_telegram(text=f"PDF-файл: {today}", image_path=path)
        report_pending = False
    elif callback_data.startswith("info_"):
        tid = callback_data.replace("info_", "")
        truck = next((t for t in load_trucks() if t["id"] == tid), None)
        if truck:
            info = f"Модель: {truck['model']}\nГосномер: {truck['licensePlate']}\nСтатус: {truck['status']}\nЦикл: {truck.get('cycle', '-') }"
            send_to_telegram(f"*Информация о машине:*\n{info}")

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