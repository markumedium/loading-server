import time
import requests
import threading
import json
import os
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

BOT_TOKEN = "7705882526:AAG0ZaDbFjNxGRe7-TGbAVSEIrwKuOmOW0k"
CHAT_ID = "-1002540325886"

DATA_PATH = "status_history"
TRUCKS_PATH = "trucks.json"
SEND_HOUR_UTC = 14  # 19:00 по UTC+5
notified_trucks = {}
report_pending = False
processed_callbacks = set()

# === УТИЛИТЫ ===
def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}

def load_trucks():
    return load_json(TRUCKS_PATH)

def send_to_telegram(text=None, image_path=None, reply_markup=None, delete_menu_id=None):
    if delete_menu_id:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage", data={
            "chat_id": CHAT_ID,
            "message_id": delete_menu_id
        })
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

# === ОТЧЕТЫ ===
def build_report_rows(trucks, history, current_only=True):
    statuses = ['Отгружается', 'Готов к выезду', 'Выехал']
    result = []
    for t in trucks:
        tid = t['id']
        model = t['model']
        plate = t['licensePlate']
        cycle = t.get('cycle', 1)
        status = t.get('status', '-')
        changes = history.get(tid, [])
        per_status = {s: ('', '') for s in statuses}
        blocks = {}
        for ch in changes:
            blocks.setdefault(ch['cycle'], []).append((ch['status'], ch['timestamp']))

        if current_only:
            block = blocks.get(cycle, [])
            for i in range(1, len(block)):
                s1, t1 = block[i-1]
                s2, t2 = block[i]
                if s1 in per_status:
                    per_status[s1] = (
                        datetime.utcfromtimestamp(t1).strftime("%H:%M"),
                        str(timedelta(seconds=t2 - t1))
                    )
            if block:
                ls, lt = block[-1]
                if ls in per_status:
                    per_status[ls] = (
                        datetime.utcfromtimestamp(lt).strftime("%H:%M"),
                        str(timedelta(seconds=int(time.time() - lt)))
                    )
            result.append([
                model, plate, status,
                *(f"({per_status[s][0]}) {per_status[s][1]}" if per_status[s][0] else '' for s in statuses),
                cycle
            ])
        else:
            for c in sorted(blocks):
                if c >= cycle:
                    continue
                blk = blocks[c]
                tmap = {s: None for s in statuses}
                for s, ts in blk:
                    if s in tmap:
                        tmap[s] = ts
                times = {}
                for i, s in enumerate(statuses):
                    if tmap[s]:
                        st = datetime.utcfromtimestamp(tmap[s]).strftime("%H:%M")
                        end = None
                        for j in range(i + 1, len(statuses)):
                            if tmap[statuses[j]]:
                                end = tmap[statuses[j]]
                                break
                        delta = str(timedelta(seconds=int(end - tmap[s]))) if end else '—'
                        times[s] = (st, delta)
                    else:
                        times[s] = ('', '')
                result.append([
                    model, plate, "Завершено",
                    *(f"({times[s][0]}) {times[s][1]}" if times[s][0] else '' for s in statuses),
                    c
                ])
    return result

def generate_png_report(date_str):
    trucks = load_trucks()
    history = load_json(os.path.join(DATA_PATH, f"{date_str}.json"))
    headers = ["Модель", "Госномер", "Статус", "Отгружается", "Готов к выезду", "Выехал", "Цикл"]
    data = build_report_rows(trucks, history, True)
    data += [[""] * len(headers)] + [["ЗАВЕРШЕННЫЕ РЕЙСЫ"] + [""] * (len(headers) - 1)]
    data += build_report_rows(trucks, history, False)

    df = pd.DataFrame(data, columns=headers)
    fig, ax = plt.subplots(figsize=(14, len(df) * 0.4 + 2))
    ax.axis('off')
    tbl = ax.table(cellText=df.values, colLabels=df.columns, loc='center', cellLoc='center')
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    path = f"report_{date_str}.png"
    plt.savefig(path, bbox_inches='tight')
    plt.close()
    return path

def generate_pdf_report(date_str):
    trucks = load_trucks()
    history = load_json(os.path.join(DATA_PATH, f"{date_str}.json"))
    headers = ["Модель", "Госномер", "Статус", "Отгружается", "Готов к выезду", "Выехал", "Цикл"]
    data = [headers]
    data += build_report_rows(trucks, history, True)
    data += [[""] * len(headers), ["ЗАВЕРШЕННЫЕ РЕЙСЫ"] + [""] * (len(headers) - 1)]
    data += build_report_rows(trucks, history, False)

    path = f"report_{date_str}.pdf"
    doc = SimpleDocTemplate(path, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = [Paragraph(f"<b>Отчет за {date_str}</b><br/>(в скобках — время начала статуса)", styles['Title'])]
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    elements.append(table)
    doc.build(elements)
    return path

# === ОПОВЕЩЕНИЕ ===
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
        if start_time and time.time() - start_time >= 300 and notified_trucks.get(tid) != cycle:
            start_str = datetime.utcfromtimestamp(start_time).strftime("%H:%M")
            dur = str(timedelta(seconds=int(time.time() - start_time)))
            msg = f"🚨 *ВНИМАНИЕ*\n{truck['model']} / {truck['licensePlate']}\nОтгружается более 5 мин\n({start_str}), прошло: {dur}"
            send_to_telegram(msg)
            notified_trucks[tid] = cycle

# === ОБРАБОТКА ===
def handle_command(text):
    global report_pending
    if report_pending:
        return
    text = text.lower()
    if "/отчет" in text or "/otchet" in text:
        report_pending = True
        resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={
            "chat_id": CHAT_ID,
            "text": "Выберите формат отчёта:",
            "reply_markup": json.dumps({
                "inline_keyboard": [
                    [{"text": "📷 PNG", "callback_data": "report_png"}],
                    [{"text": "📄 PDF", "callback_data": "report_pdf"}]
                ]
            })
        }).json()
        global last_menu_msg_id
        last_menu_msg_id = resp.get("result", {}).get("message_id")
    elif "/status" in text:
        txt = "*Текущие статусы:*\n" + "\n".join(f"{t['model']} / {t['licensePlate']}: {t['status']}" for t in load_trucks())
        send_to_telegram(txt)
    elif "/mashina" in text:
        btns = [[{"text": f"{t['model']} ({t['licensePlate']})", "callback_data": f"info_{t['id']}"}] for t in load_trucks()]
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={
            "chat_id": CHAT_ID,
            "text": "Выберите машину:",
            "reply_markup": json.dumps({"inline_keyboard": btns})
        })

def handle_callback(data, callback_id):
    global report_pending
    if callback_id in processed_callbacks:
        return
    processed_callbacks.add(callback_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if data == "report_png":
        path = generate_png_report(today)
        send_to_telegram(image_path=path, delete_menu_id=last_menu_msg_id)
        report_pending = False
    elif data == "report_pdf":
        path = generate_pdf_report(today)
        send_to_telegram(text=f"PDF: {today}", image_path=path, delete_menu_id=last_menu_msg_id)
        report_pending = False
    elif data.startswith("info_"):
        tid = data.replace("info_", "")
        t = next((x for x in load_trucks() if x['id'] == tid), None)
        if t:
            send_to_telegram(f"*Инфо:*\n{t['model']} / {t['licensePlate']}\nСтатус: {t['status']}\nЦикл: {t.get('cycle', '-')}")


def check_updates():
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            if offset:
                url += f"?offset={offset}"
            resp = requests.get(url).json()
            for r in resp.get("result", []):
                offset = r["update_id"] + 1
                if "message" in r:
                    text = r["message"].get("text", "")
                    handle_command(text)
                elif "callback_query" in r:
                    handle_callback(r["callback_query"]["data"], r["callback_query"]["id"])
        except Exception as e:
            print("Ошибка в check_updates:", e)
        time.sleep(3)

def loop_interval(fn, sec):
    while True:
        fn()
        time.sleep(sec)

def start_telegram_bot():
    print("[Telegram] Бот запущен и работает 24/7")
    threading.Thread(target=check_updates, daemon=True).start()
    threading.Thread(target=lambda: loop_interval(check_long_loading, 60), daemon=True).start()

start_telegram_bot()
