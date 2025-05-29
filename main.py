from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import json, os, time, uuid
from datetime import datetime, timedelta, timezone

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

# ---------- модели ----------
class StatusUpdate(BaseModel):
    truck_id: str
    status: str
    timestamp: str
    weight: float | None = None   # сохраняем вес при переходе

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

# ---------- helpers ----------
def load_json(path: str, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_trucks():
    return load_json(TRUCKS_PATH, [])


def save_trucks(trucks):
    save_json(TRUCKS_PATH, trucks)


def load_users():
    return load_json(USERS_PATH, [])


def save_users(users):
    save_json(USERS_PATH, users)

# ---------- API ----------
@app.post("/update_status")
def update_status(data: StatusUpdate):
    trucks = load_trucks()
    found = False
    current_cycle = 1

    for tr in trucks:
        if tr["id"] == data.truck_id:
            cur_status = tr["status"]
            current_cycle = tr.get("cycle", 1)

            if data.status not in allowed_transitions.get(cur_status, []):
                raise HTTPException(status_code=400, detail=f"Недопустимый переход: {cur_status} → {data.status}")

            # фиксируем вес при переходе Отгружается→Готов к выезду
            w = 0.0
            if cur_status == "Отгружается" and data.status == "Готов к выезду":
                w = float(data.weight or 0)
                tr["weight"] = w

            # обновляем статус и цикл
            if cur_status == "Выехал" and data.status == "На территории":
                current_cycle += 1
            tr["status"] = data.status
            tr["cycle"] = current_cycle
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="Truck not found")

    save_trucks(trucks)

    # Работа с историей за сегодня
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = os.path.join(DATA_PATH, f"{today}.json")
    history = load_json(file_path, {})

    if data.truck_id not in history:
        history[data.truck_id] = []

    try:
        timestamp_unix = int(
            datetime.strptime(data.timestamp, "%Y-%m-%d %H:%M:%S").
            replace(tzinfo=timezone.utc).timestamp()
        )
    except:
        timestamp_unix = int(time.time())

    history[data.truck_id].append({
        "timestamp": timestamp_unix,
        "status": data.status,
        "cycle": current_cycle,
        "weight": w
    })
    history[data.truck_id].sort(key=lambda x: x["timestamp"])
    save_json(file_path, history)

    return {"message": "OK"}

@app.get("/status_history")
def get_status_history():
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = os.path.join(DATA_PATH, f"{today}.json")
    history = load_json(file_path, {})
    return {today: history}

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
    if not data.model or not data.licensePlate:
        raise HTTPException(status_code=400, detail="Модель и госномер обязательны")

    trucks = load_trucks()
    new_id = str(uuid.uuid4())
    trucks.append({
        "id": new_id,
        "model": data.model,
        "licensePlate": data.licensePlate,
        "status": "На территории",
        "cycle": 1,
        "weight": 0.0
    })
    save_trucks(trucks)

    # Запись в историю
    timestamp = int(datetime.utcnow().timestamp())
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = os.path.join(DATA_PATH, f"{today}.json")
    history = load_json(file_path, {})

    if new_id not in history:
        history[new_id] = []
    history[new_id].append({
        "timestamp": timestamp,
        "status": "На территории",
        "cycle": 1,
        "weight": 0.0
    })
    save_json(file_path, history)

    return {"message": "Машина добавлена"}

@app.post("/update_truck")
def update_truck(truck: Truck):
    trucks = load_trucks()
    for t in trucks:
        if t["id"] == truck.id:
            t.update({"model": truck.model, "licensePlate": truck.licensePlate})
            break
    save_trucks(trucks)
    return {"message": "Truck updated"}

@app.post("/delete_truck")
def delete_truck(truck_id: str):
    save_trucks([t for t in load_trucks() if t["id"] != truck_id])
    return {"message": "Truck deleted"}

# ========== НОЧНОЙ СБРОС ==========
RESET_TOKEN = os.getenv("RESET_TOKEN", "supersecret")

def check_token(x_internal_token: str = Header(...)):
    if x_internal_token != RESET_TOKEN:
        raise HTTPException(status_code=403, detail="Недопустимо")

@app.post("/internal/reset")
def internal_reset(_: None = Depends(check_token)):
    trucks = load_trucks()
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = os.path.join(DATA_PATH, f"{today}.json")
    history = load_json(file_path, {})

    timestamp_unix = int(datetime.utcnow().replace(tzinfo=timezone.utc).timestamp())
    for tr in trucks:
        tr["status"] = "На территории"
        tr["cycle"] = tr.get("cycle", 1) + 1
        tr.setdefault("weight", 0.0)
        history.setdefault(tr["id"], []).append({
            "timestamp": timestamp_unix,
            "status": "На территории",
            "cycle": tr["cycle"],
            "weight": tr["weight"]
        })

    save_trucks(trucks)
    save_json(file_path, history)
    return {"message": "Сброс успешно выполнен"}

@app.get("/")
def root():
    return {"message": "Сервер работает"}

@app.get("/trucks_by_status")
def get_trucks_by_status(status: str):
    return [t for t in load_trucks() if t.get("status") == status]

@app.get("/truck/{truck_id}")
def get_truck_by_id(truck_id: str):
    for t in load_trucks():
        if t["id"] == truck_id:
            return t
    raise HTTPException(status_code=404, detail="Truck not found")

@app.get("/status_history_range")
def get_status_history_range(start: str, end: str):
    result = {}
    try:
        sd = datetime.strptime(start, "%Y-%m-%d")
        ed = datetime.strptime(end, "%Y-%m-%d")
        while sd <= ed:
            key = sd.strftime("%Y-%m-%d")
            fp = os.path.join(DATA_PATH, f"{key}.json")
            data = load_json(fp, {})
            if data:
                result[key] = data
            sd += timedelta(days=1)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result
