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

notified_trucks = {}  # {truck_id: cycle}

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

# ========== ГЕНЕРАЦИЯ PNG ОТЧЁТА ==========
def generate_png_report(date_str):
    trucks = load_trucks()
    history_path = os.path.join(DATA_PATH, f"{date_str}.json")
    history = load_json(history_path)
    statuses = ['Отгружается', 'Готов к выезду', 'Выехал']

    rows = []
    for truck in trucks:
        tid = truck['id']
        model = truck.get('model', '-')
        plate = truck.get('licensePlate', '-')
        status = truck.get('status', '-')
        cycle = truck.get('cycle', 1)

        history_data = history.get(tid, [])
        cycles = {}
        for ch in history_data:
            c = ch.get("cycle")
            s = ch.get("status")
            ts = ch.get("timestamp")
            if isinstance(c, int) and isinstance(ts, (int, float)):
                cycles.setdefault(c, []).append((s, ts))

        act = cycles.get(cycle, [])
        t = {s: ('', '') for s in statuses}
        for i in range(1, len(act)):
            s1, t1 = act[i - 1]
            s2, t2 = act[i]
            if s1 in t:
                t[s1] = (datetime.utcfromtimestamp(t1).strftime("%H:%M"), str(timedelta(seconds=int(t2 - t1))))
        if act:
            ls, lt = act[-1]
            if ls in t:
                start = datetime.utcfromtimestamp(lt).strftime("%H:%M")
                duration = str(timedelta(seconds=int(time.time() - lt)))
                t[ls] = (start, duration)

        rows.append({
            "Модель": model,
            "Госномер": plate,
            "Статус": status,
            "Отгр.": f"({t['Отгружается'][0]}) {t['Отгружается'][1]}" if t['Отгружается'][0] else '—',
            "Готов": f"({t['Готов к выезду'][0]}) {t['Готов к выезду'][1]}" if t['Готов к выезду'][0] else '—',
            "Выехал": f"({t['Выехал'][0]}) {t['Выехал'][1]}" if t['Выехал'][0] else '—',
            "Цикл": cycle
        })

    for truck_id, entries in history.items():
        by_cycle = {}
        for ch in entries:
            c = ch['cycle']
            by_cycle.setdefault(c, []).append((ch['status'], ch['timestamp']))

        for c, changes in sorted(by_cycle.items()):
            if any(x for x in changes if x[0] == "Выехал"):
                truck = next((t for t in trucks if t['id'] == truck_id), None)
                if not truck:
                    continue
                model = truck.get('model', '-')
                plate = truck.get('licensePlate', '-')

                tmap = {s: None for s in statuses}
                for s, ts in changes:
                    if s in tmap:
                        tmap[s] = ts

                times = {s: ('', '') for s in statuses}
                for i, s in enumerate(statuses):
                    if tmap[s]:
                        start_time = datetime.utcfromtimestamp(tmap[s]).strftime("%H:%M")
                        end_time = None
                        for j in range(i + 1, len(statuses)):
                            if tmap[statuses[j]]:
                                end_time = tmap[statuses[j]]
                                break
                        if end_time:
                            delta = str(timedelta(seconds=int(end_time - tmap[s])))
                        else:
                            delta = '—'
                        times[s] = (start_time, delta)

                rows.append({
                    "Модель": model,
                    "Госномер": plate,
                    "Статус": "Завершено",
                    "Отгр.": f"({times['Отгружается'][0]}) {times['Отгружается'][1]}" if times['Отгружается'][0] else '—',
                    "Готов": f"({times['Готов к выезду'][0]}) {times['Готов к выезду'][1]}" if times['Готов к выезду'][0] else '—',
                    "Выехал": f"({times['Выехал'][0]}) {times['Выехал'][1]}" if times['Выехал'][0] else '—',
                    "Цикл": c
                })

    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(13, len(df)*0.8 + 2))
    ax.axis('off')
    header_color = '#2C3E50'
    row_colors = ['#ECF0F1', '#FFFFFF']
    text_color = '#2C3E50'
    header_text_color = 'white'

    table = plt.table(cellText=df.values,
                      colLabels=df.columns,
                      cellLoc='center',
                      loc='center',
                      colColours=[header_color]*len(df.columns),
                      cellColours=[[row_colors[i % 2]] * len(df.columns) for i in range(len(df))])
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.2, 1.6)

    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_text_props(weight='bold', color=header_text_color)
        else:
            cell.set_text_props(color=text_color)

    plt.title('Отчёт по рейсам на текущий день', fontsize=16, weight='bold', color=header_color, pad=20)

    output_path = f"/mnt/data/report_{date_str}.png"
    plt.savefig(output_path, bbox_inches='tight', dpi=300)
    plt.close()
    return output_path

# ========== ОТПРАВКА ==========
def send_to_telegram(text=None, image_path=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    if text:
        requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        })
    if image_path:
        with open(image_path, 'rb') as f:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                          files={"photo": f},
                          data={"chat_id": CHAT_ID})

# ========== ПЛАНОВАЯ ЗАДАЧА ==========
def daily_bot_task():
    sent_today = None
    while True:
        now = datetime.utcnow()
        if now.hour == SEND_HOUR_UTC and (sent_today is None or sent_today != now.date()):
            date = (now + timedelta(hours=5)).strftime("%Y-%m-%d")
            path = generate_png_report(date)
            send_to_telegram(image_path=path)
            sent_today = now.date()
            print("[Telegram] PNG-отчёт отправлен")
        time.sleep(60)

# ========== ОБРАБОТКА КОМАНД ==========
def check_commands_loop():
    offset = None
    while True:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        if offset:
            url += f"?offset={offset}"
        try:
            resp = requests.get(url).json()
            for result in resp.get("result", []):
                offset = result["update_id"] + 1
                msg = result.get("message")
                if msg and msg.get("text", "").lower().strip() == "/отчет":
                    today = datetime.now().strftime("%Y-%m-%d")
                    path = generate_png_report(today)
                    send_to_telegram(image_path=path)
        except:
            pass
        time.sleep(5)

# ========== ФОН ==========
def start_telegram_bot():
    print("[Telegram] Бот запущен и работает 24/7")
    threading.Thread(target=daily_bot_task, daemon=True).start()
    threading.Thread(target=lambda: loop_with_interval(check_long_loading, 60), daemon=True).start()
    threading.Thread(target=check_commands_loop, daemon=True).start()

def loop_with_interval(fn, seconds):
    while True:
        fn()
        time.sleep(seconds)