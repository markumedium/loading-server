import time
import requests
import threading
import json
import os
from datetime import datetime, timedelta, timezone
from telegram import start_telegram_bot  # импорт из telegram.py

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

# ========== ФОРМИРОВАНИЕ ОТЧЁТА ==========
def generate_daily_report(date_str):
    trucks = load_trucks()
    history_path = os.path.join(DATA_PATH, f"{date_str}.json")
    history = load_json(history_path)

    statuses = ['Отгружается', 'Готов к выезду', 'Выехал']
    column_widths = [10, 10, 18] + [22]*len(statuses) + [6]

    def format_row(*cols):
        return " ".join(str(col).ljust(width) for col, width in zip(cols, column_widths))

    report = ["СТАТУСЫ МАШИН", "(в скобках указано время начала статуса)",
              format_row("Модель", "Госномер", "Статус", *statuses, "Цикл"), ""]
    sort_order = {s: i for i, s in enumerate(statuses)}

    # Активные
    for truck in trucks:
        tid = truck['id']
        model = truck.get('model', '-')
        plate = truck.get('licensePlate', '-')
        status = truck.get('status', '-')
        cycle = truck.get('cycle', 1)

        file_path = os.path.join(DATA_PATH, f"{date_str}.json")
        history_data = load_json(file_path)
        changes = history_data.get(tid, [])

        cycles = {}
        for ch in changes:
            c = ch.get("cycle")
            s = ch.get("status")
            ts = ch.get("timestamp")
            if isinstance(c, int) and isinstance(ts, (int, float)):
                cycles.setdefault(c, []).append((s, ts))

        act = cycles.get(cycle, [])
        t = {s: ('', '') for s in statuses}  # (время начала, длительность)
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

        report.append(format_row(model, plate, status, *(f"({t[s][0]}) {t[s][1]}" if t[s][0] else '—' for s in statuses), cycle))

    # Завершённые
    report.append("\nЗАВЕРШЕННЫЕ РЕЙСЫ")
    report.append(format_row("Модель", "Госномер", "Статус", *statuses, "Цикл"))

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

                report.append(format_row(model, plate, "Завершено", *(f"({times[s][0]}) {times[s][1]}" if times[s][0] else '—' for s in statuses), c))

    return "```\n" + "\n".join(report) + "\n```"

# ========== ОТПРАВКА ==========
def send_to_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    })

# ========== ОПОВЕЩЕНИЯ ==========
def check_long_loading():
    global notified_trucks
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

# ========== ПЛАНОВАЯ ЗАДАЧА ==========
def daily_bot_task():
    sent_today = None
    while True:
        now = datetime.utcnow()
        if now.hour == SEND_HOUR_UTC and (sent_today is None or sent_today != now.date()):
            date = (now + timedelta(hours=5)).strftime("%Y-%m-%d")
            report = generate_daily_report(date)
            send_to_telegram(report)
            sent_today = now.date()
            print("[Telegram] Отчёт отправлен")
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
                    send_to_telegram(generate_daily_report(today))
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
threading.Thread(target=start_telegram_bot, daemon=True).start()