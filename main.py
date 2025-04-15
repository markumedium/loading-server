from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import json
import os
from datetime import datetime, timedelta
from datetime import timezone
import time
import uuid
from tg_bot import start_telegram_bot
import threading

threading.Thread(target=start_telegram_bot, daemon=True).start()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_PATH = "status_history"
TRUCKS_PATH = "trucks.json"
USERS_PATH = "users.json"

allowed_transitions = {
    "На территории": ["Отгружается"],
    "Отгружается": ["Готов к выезду"],
    "Готов к выезду": ["Выехал"],
    "Выехал": ["На территории"]
}

os.makedirs(DATA_PATH, exist_ok=True)

class StatusUpdate(BaseModel):
    truck_id: str
    status: str
    timestamp: str

class UserAuth(BaseModel):
    login: str
    password: str

class AddUser(BaseModel):
    login: str
    password: str
    role: str

class AssignTruck(BaseModel):
    login: str
    truck_id: str

class Truck(BaseModel):
    id: str
    model: str
    licensePlate: str
    status: str

class AddTruckRequest(BaseModel):
    model: str
    licensePlate: str

# ========== ХЕЛПЕРЫ ==========
def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return []

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_trucks():
    return load_json(TRUCKS_PATH)

def save_trucks(trucks):
    save_json(TRUCKS_PATH, trucks)

def load_users():
    return load_json(USERS_PATH)

def save_users(users):
    save_json(USERS_PATH, users)

# ========== API ==========

@app.post("/update_status")
def update_status(data: StatusUpdate):
    trucks = load_trucks()
    updated = False
    current_cycle = 1

    for truck in trucks:
        if truck['id'] == data.truck_id:
            current_status = truck['status']
            current_cycle = truck.get("cycle", 1)

            # 🔒 Проверка допустимого перехода
            if data.status not in allowed_transitions.get(current_status, []):
                raise HTTPException(status_code=400, detail=f"Недопустимый переход: {current_status} → {data.status}")

            # ⬆️ Увеличение цикла при завершении
            if current_status == "Выехал" and data.status == "На территории":
                current_cycle += 1

            truck['status'] = data.status
            truck['cycle'] = current_cycle
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="Truck not found")

    save_trucks(trucks)

    # 🗂️ Работа с историей
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = os.path.join(DATA_PATH, f"{today}.json")
    history = load_json(file_path) if os.path.exists(file_path) else {}

    if data.truck_id not in history:
        history[data.truck_id] = []

    try:
        from datetime import timezone

        timestamp_unix = int(
            datetime.strptime(data.timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except:
        timestamp_unix = int(time.time())

    # ✅ Запись нового перехода
    history[data.truck_id].append({
        "timestamp": timestamp_unix,
        "status": data.status,
        "cycle": current_cycle
    })

    # 🕓 Сортировка истории по времени (на всякий случай)
    history[data.truck_id].sort(key=lambda x: x["timestamp"])

    save_json(file_path, history)
    return {"message": "Статус, история и цикл обновлены"}

@app.get("/status_history")
def get_status_history():
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = os.path.join(DATA_PATH, f"{today}.json")
    return {today: load_json(file_path) if os.path.exists(file_path) else {}}

@app.get("/trucks")
def get_trucks():
    return load_trucks()

@app.get("/user/{login}")
def get_user(login: str):
    users = load_users()
    for u in users:
        if u["login"] == login:
            return u
    raise HTTPException(status_code=404, detail="User not found")

@app.post("/login")
def login(auth: UserAuth):
    users = load_users()
    for u in users:
        if u["login"] == auth.login and u["password"] == auth.password:
            return {"role": u["role"], "login": u["login"], "truck": u.get("truck")}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.post("/add_user")
def add_user(user: AddUser):
    users = load_users()
    if any(u["login"] == user.login for u in users):
        raise HTTPException(status_code=400, detail="User already exists")
    users.append({
        "login": user.login,
        "password": user.password,
        "role": user.role,
        "truck": None
    })
    save_users(users)
    return {"message": "User added"}

@app.get("/drivers")
def get_drivers():
    return [u for u in load_users() if u["role"] == "driver"]

@app.post("/assign_truck")
def assign_truck(data: AssignTruck):
    users = load_users()
    for u in users:
        if u["login"] == data.login:
            u["truck"] = data.truck_id
    save_users(users)
    return {"message": "Truck assigned"}

@app.post("/add_truck")
def add_truck(data: AddTruckRequest):
    model = data.model
    license_plate = data.licensePlate

    if not model or not license_plate:
        raise HTTPException(status_code=400, detail="Модель и госномер обязательны")

    trucks = load_trucks()
    new_id = str(uuid.uuid4())
    new_truck = {
        "id": new_id,
        "model": model,
        "licensePlate": license_plate,
        "status": "На территории",
        "cycle": 1
    }
    trucks.append(new_truck)
    save_trucks(trucks)

    # 💾 Сохраняем в статус-историю момент входа в "На территории"
    timestamp = int(datetime.utcnow().timestamp())
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = os.path.join(DATA_PATH, f"{today}.json")
    history = load_json(file_path) if os.path.exists(file_path) else {}

    if new_id not in history:
        history[new_id] = []

    history[new_id].append({
        "timestamp": timestamp,
        "status": "На территории",
        "cycle": 1
    })

    save_json(file_path, history)

    return {"message": "Машина добавлена"}



@app.post("/update_truck")
def update_truck(truck: Truck):
    trucks = load_trucks()
    for t in trucks:
        if t["id"] == truck.id:
            t["model"] = truck.model
            t["licensePlate"] = truck.licensePlate
            break
    save_trucks(trucks)
    return {"message": "Truck updated"}

@app.post("/delete_truck")
def delete_truck(truck_id: str):
    trucks = load_trucks()
    trucks = [t for t in trucks if t["id"] != truck_id]
    save_trucks(trucks)
    return {"message": "Truck deleted"}

# ========== ЕЖЕДНЕВНЫЙ СБРОС ==========
def reset_task():
    while True:
        now_utc = datetime.utcnow()
        target_utc = now_utc.replace(hour=22, minute=0, second=0, microsecond=0)
        if now_utc > target_utc:
            target_utc += timedelta(days=1)
        time.sleep((target_utc - now_utc).total_seconds())

        print("[RESET] Выполняется сброс в 03:00 по UTC+5")
        trucks = load_trucks()
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        today = datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(DATA_PATH, f"{today}.json")
        history = load_json(file_path) if os.path.exists(file_path) else {}

        for truck in trucks:
            # Обязательно записываем начальный статус каждого нового цикла!
            truck['status'] = "На территории"
            truck['cycle'] += 1  # Увеличиваем цикл

            if truck['id'] not in history:
                history[truck['id']] = []

            timestamp_unix = int(datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
            history[truck['id']].append({
                "timestamp": timestamp_unix,
                "status": "На территории",
                "cycle": truck['cycle']
            })

        save_json(file_path, history)  # Сохраняем один раз всю историю
        save_trucks(trucks)  # Сохраняем изменения всех машин



@app.get("/")
def root():
    return {"message": "Сервер работает"}

@app.get("/trucks_by_status")
def get_trucks_by_status(status: str):
    trucks = load_trucks()
    filtered = [truck for truck in trucks if truck.get("status", "").strip() == status.strip()]
    return filtered
@app.get("/truck/{truck_id}")
def get_truck_by_id(truck_id: str):
    trucks = load_trucks()
    for t in trucks:
        if t["id"] == truck_id:
            return t
    raise HTTPException(status_code=404, detail="Truck not found")

@app.get("/status_history_range")
def get_status_history_range(start: str, end: str):
    result = {}
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d")
        end_date = datetime.strptime(end, "%Y-%m-%d")
        delta = timedelta(days=1)

        while start_date <= end_date:
            date_str = start_date.strftime("%Y-%m-%d")
            file_path = os.path.join(DATA_PATH, f"{date_str}.json")
            if os.path.exists(file_path):
                result[date_str] = load_json(file_path)
            start_date += delta
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result
