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

DATA_PATH   = "status_history"
TRUCKS_PATH = "trucks.json"
USERS_PATH  = "users.json"

allowed = {
    "На территории": ["Отгружается"],
    "Отгружается"  : ["Готов к выезду"],
    "Готов к выезду": ["Выехал"],
    "Выехал"       : ["На территории"]
}

os.makedirs(DATA_PATH, exist_ok=True)

# ---------- модели ----------
class StatusUpdate(BaseModel):
    truck_id : str
    status   : str
    timestamp: str
    weight   : float | None = None   # ← новинка!

class UserAuth(BaseModel):
    login: str;  password: str

class AddUser(BaseModel):
    login:str; password:str; role:str

class AssignTruck(BaseModel):
    login:str; truck_id:str

class Truck(BaseModel):
    id:str; model:str; licensePlate:str; status:str

class AddTruckRequest(BaseModel):
    model:str; licensePlate:str
# ---------- helpers ----------
load_json = lambda p: json.load(open(p)) if os.path.exists(p) else []
def save_json(path, data): json.dump(data, open(path,"w"), indent=2, ensure_ascii=False)
load_trucks = lambda : load_json(TRUCKS_PATH)
save_trucks = lambda t: save_json(TRUCKS_PATH, t)
load_users  = lambda : load_json(USERS_PATH)
save_users  = lambda u: save_json(USERS_PATH, u)
# ---------- API ----------
@app.post("/update_status")
def update_status(data: StatusUpdate):
    trucks = load_trucks()
    for tr in trucks:
        if tr["id"] == data.truck_id:
            cur_status = tr["status"]
            cur_cycle  = tr.get("cycle",1)
            if data.status not in allowed.get(cur_status,[]):
                raise HTTPException(400,f"Недопустимый переход: {cur_status}→{data.status}")
            # если цикл завершается («Выехал»→«На территории»)
            if cur_status=="Выехал" and data.status=="На территории":
                cur_cycle += 1
            tr["status"] = data.status
            tr["cycle"]  = cur_cycle
            break
    else:
        raise HTTPException(404,"Truck not found")
    save_trucks(trucks)

    today = datetime.now().strftime("%Y-%m-%d")
    fpath = os.path.join(DATA_PATH,f"{today}.json")
    hist  = load_json(fpath)
    hist.setdefault(data.truck_id,[])
    try:
        ts_unix = int(datetime.strptime(data.timestamp,"%Y-%m-%d %H:%M:%S")
                      .replace(tzinfo=timezone.utc).timestamp())
    except: ts_unix = int(time.time())

    # вес фиксируем ТОЛЬКО на переходе Отгружается→Готов к выезду
    w = 0.0
    if cur_status=="Отгружается" and data.status=="Готов к выезду":
        w = float(data.weight or 0)

    hist[data.truck_id].append({
        "timestamp": ts_unix,
        "status"   : data.status,
        "cycle"    : tr["cycle"],
        "weight"   : w
    })
    hist[data.truck_id].sort(key=lambda x:x["timestamp"])
    save_json(fpath,hist)
    return {"message":"OK"}

# -------- read end-points (без изменений) --------
@app.get("/status_history")
def status_today():
    today=datetime.now().strftime("%Y-%m-%d")
    fp=os.path.join(DATA_PATH,f"{today}.json")
    return {today: load_json(fp)}

@app.get("/trucks")                
def trucks(): return load_trucks()
@app.get("/user/{login}")          
def user(login):
    for u in load_users():
        if u["login"]==login: return u
    raise HTTPException(404,"User not found")

@app.post("/login")
def login(auth:UserAuth):
    for u in load_users():
        if u["login"]==auth.login and u["password"]==auth.password:
            return {"role":u["role"],"login":u["login"],"truck":u.get("truck")}
    raise HTTPException(401,"Invalid creds")

@app.post("/add_user") ; def add_user(u:AddUser):
    users=load_users()
    if any(x["login"]==u.login for x in users): raise HTTPException(400,"Exists")
    users.append({"login":u.login,"password":u.password,"role":u.role,"truck":None})
    save_users(users); return {"message":"User added"}

@app.get("/drivers") ; def drivers(): return [u for u in load_users() if u["role"]=="driver"]

@app.post("/assign_truck")
def assign(at:AssignTruck):
    users=load_users()
    for u in users:
        if u["login"]==at.login: u["truck"]=at.truck_id
    save_users(users); return {"message":"OK"}

@app.post("/add_truck")
def add_truck(t:AddTruckRequest):
    trucks=load_trucks()
    tid=str(uuid.uuid4())
    trucks.append({"id":tid,"model":t.model,"licensePlate":t.licensePlate,
                   "status":"На территории","cycle":1})
    save_trucks(trucks)
    # сразу пишем историю
    ts=int(time.time())
    fp=os.path.join(DATA_PATH,datetime.now().strftime("%Y-%m-%d")+".json")
    hist=load_json(fp); hist.setdefault(tid,[]).append(
        {"timestamp":ts,"status":"На территории","cycle":1,"weight":0})
    save_json(fp,hist)
    return {"message":"Truck added"}

@app.post("/update_truck") ; def upd_truck(t:Truck):
    trucks=load_trucks()
    for tr in trucks:
        if tr["id"]==t.id: tr.update({"model":t.model,"licensePlate":t.licensePlate})
    save_trucks(trucks); return {"message":"OK"}

@app.post("/delete_truck") ; def del_truck(truck_id:str):
    save_trucks([t for t in load_trucks() if t["id"]!=truck_id]); return {"message":"OK"}

# -------- ночной каскадный сброс --------
RESET_TOKEN=os.getenv("RESET_TOKEN","supersecret")
def check(x_internal_token:str=Header(...)):
    if x_internal_token!=RESET_TOKEN: raise HTTPException(403,"forbidden")

def nightly_reset():
    trucks=load_trucks()
    today=datetime.now().strftime("%Y-%m-%d")
    fp=os.path.join(DATA_PATH,f"{today}.json")
    hist=load_json(fp)
    ts=int(datetime.utcnow().replace(tzinfo=timezone.utc).timestamp())

    seq={
        "Отгружается": ["Готов к выезду","Выехал","На территории"],
        "Готов к выезду":["Выехал","На территории"],
        "Выехал":["На территории"],
        "На территории":[]
    }
    for tr in trucks:
        cur=tr["status"]
        for nxt in seq[cur]:
            w=0.0
            if nxt=="Готов к выезду": w=0.0   # ночной вес всегда 0
            hist.setdefault(tr["id"],[]).append({
                "timestamp":ts,
                "status":nxt,
                "cycle":tr["cycle"],
                "weight":w
            })
            tr["status"]=nxt
            if nxt=="На территории":
                tr["cycle"]+=1
                break
    save_trucks(trucks); save_json(fp,hist)
    return {"message":"night reset done"}

@app.post("/internal/reset")
def internal(_:None=Depends(check)): return nightly_reset()

# root / misc
@app.get("/") ; def root(): return {"status":"alive"}
