import os, hmac, hashlib, json, time, urllib.parse, base64
import requests, psycopg2, threading
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse

TOKEN=os.getenv("BOT_TOKEN","")
OWNER=os.getenv("OWNER_TELEGRAM_ID","").strip()
CHANNEL=os.getenv("REQUIRED_CHANNEL","@studiodlc")
PAYMENT=os.getenv("PAYMENT_URL","https://yoomoney.ru/to/4100119440946091")
DB=os.getenv("DATABASE_URL")
if not DB: raise RuntimeError("DATABASE_URL is not configured")
app=FastAPI(title="STUDIO DLC")
def conn(): return psycopg2.connect(DB,sslmode="require",cursor_factory=RealDictCursor)
def init():
 with conn() as c, c.cursor() as q:
  q.execute("CREATE TABLE IF NOT EXISTS products(id SERIAL PRIMARY KEY,title TEXT NOT NULL,description TEXT DEFAULT '',price TEXT DEFAULT 'По запросу',category TEXT DEFAULT 'Услуги',image TEXT DEFAULT '',active BOOLEAN DEFAULT TRUE)")
  q.execute("CREATE TABLE IF NOT EXISTS orders(id SERIAL PRIMARY KEY,user_id BIGINT NOT NULL,username TEXT DEFAULT '',product_id INT REFERENCES products(id) ON DELETE SET NULL,product_title TEXT NOT NULL,price TEXT NOT NULL,comment TEXT DEFAULT '',receipt BYTEA,receipt_type TEXT,status TEXT DEFAULT 'awaiting_review',created_at TIMESTAMPTZ DEFAULT now())")
  q.execute("CREATE TABLE IF NOT EXISTS forum_posts(id SERIAL PRIMARY KEY,user_id BIGINT NOT NULL,username TEXT DEFAULT '',body TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT now())")
  q.execute("CREATE TABLE IF NOT EXISTS product_images(id SERIAL PRIMARY KEY,product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,data BYTEA NOT NULL,mime TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT now())")
  q.execute("CREATE TABLE IF NOT EXISTS user_profiles(user_id BIGINT PRIMARY KEY,username TEXT DEFAULT '',first_name TEXT DEFAULT '',balance NUMERIC(12,2) NOT NULL DEFAULT 0,created_at TIMESTAMPTZ DEFAULT now())")
  q.execute("CREATE TABLE IF NOT EXISTS order_reviews(id SERIAL PRIMARY KEY,order_id INT NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,user_id BIGINT NOT NULL,rating INT NOT NULL CHECK(rating BETWEEN 1 AND 5),body TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT now())")
  q.execute("CREATE TABLE IF NOT EXISTS staff_roles(user_id BIGINT PRIMARY KEY,username TEXT DEFAULT '',role TEXT NOT NULL CHECK(role IN ('deputy','support','moderator')),granted_by BIGINT NOT NULL,created_at TIMESTAMPTZ DEFAULT now())")
  q.execute("CREATE TABLE IF NOT EXISTS support_tickets(id SERIAL PRIMARY KEY,user_id BIGINT NOT NULL,username TEXT DEFAULT '',subject TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_at TIMESTAMPTZ DEFAULT now(),updated_at TIMESTAMPTZ DEFAULT now())")
  q.execute("CREATE TABLE IF NOT EXISTS support_messages(id SERIAL PRIMARY KEY,ticket_id INT NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,sender_id BIGINT NOT NULL,sender_role TEXT NOT NULL,body TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT now())")
  q.execute("SELECT count(*) n FROM products")
  if q.fetchone()["n"]==0:
   q.execute("INSERT INTO products(title,description,price,category) VALUES(%s,%s,%s,%s),(%s,%s,%s,%s),(%s,%s,%s,%s)",("CRMP-сайт","Современный сайт для CRMP-проекта","От 1500 ₽","Сайты","Форум проекта","Категории, темы и профили пользователей","От 1200 ₽","Форумы","Motion-дизайн","Анимации и премиальная визуальная подача","От 700 ₽","Дизайн"))
init()

WEBAPP_URL = "https://studio-dlc.onrender.com"
WEBHOOK_SECRET = hmac.new(b"studio-dlc-webhook", TOKEN.encode(), hashlib.sha256).hexdigest()[:32] if TOKEN else ""

def tg_call(method, payload):
 if not TOKEN: return None
 try:
  response = requests.post("https://api.telegram.org/bot"+TOKEN+"/"+method, json=payload, timeout=10)
  data = response.json()
  if not data.get("ok"): print("Telegram API", method, data.get("description", "request failed"))
  return data
 except Exception as e:
  print("Telegram API", method, type(e).__name__)
  return None

@app.on_event("startup")
def configure_telegram():
 if not TOKEN: 
  print("BOT_TOKEN is missing; Telegram bot setup skipped")
  return
 result = tg_call("setWebhook", {
  "url": WEBAPP_URL + "/telegram/webhook",
  "secret_token": WEBHOOK_SECRET,
  "allowed_updates": ["message"]
 })
 if result and result.get("ok"): print("Telegram webhook configured")
 tg_call("setChatMenuButton", {"menu_button": {
  "type": "web_app", "text": "Открыть STUDIO DLC",
  "web_app": {"url": WEBAPP_URL + "?v=2"}
 }})
 tg_call("setMyCommands", {"commands": [
  {"command": "start", "description": "Открыть STUDIO DLC"},
  {"command": "help", "description": "Помощь"}
 ]})

@app.post("/telegram/webhook")
async def telegram_webhook(r: Request):
 if not WEBHOOK_SECRET or not hmac.compare_digest(r.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), WEBHOOK_SECRET):
  raise HTTPException(403, "Forbidden")
 update = await r.json()
 message = update.get("message") or {}
 chat = message.get("chat") or {}
 text_in = (message.get("text") or "").strip()
 if chat.get("id") and text_in.split("@")[0] in ("/start", "/help"):
  tg_call("sendMessage", {
   "chat_id": chat["id"],
   "text": "Добро пожаловать в STUDIO DLC! Сначала подпишись на канал, затем открой приложение и проверь подписку.",
   "reply_markup": {"inline_keyboard": [
    [{"text":"✦ Подписаться на STUDIO DLC","url":"https://t.me/studiodlc"}],
    [{"text":"Открыть STUDIO DLC","web_app":{"url":WEBAPP_URL+"?v=3"}}]
   ]}
  })
 return {"ok": True}

def user(req):
 raw=req.headers.get("X-Telegram-Init-Data","")
 if not raw or not TOKEN: raise HTTPException(401,"Откройте приложение через Telegram. Владелец должен настроить BOT_TOKEN.")
 d=dict(urllib.parse.parse_qsl(raw,keep_blank_values=True)); sig=d.pop("hash",None)
 if not sig: raise HTTPException(401,"Некорректная Telegram-сессия")
 check="\n".join(k+"="+v for k,v in sorted(d.items()))
 secret=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
 if not hmac.compare_digest(hmac.new(secret,check.encode(),hashlib.sha256).hexdigest(),sig): raise HTTPException(401,"Проверка Telegram не пройдена")
 if abs(time.time()-int(d.get("auth_date","0")))>86400: raise HTTPException(401,"Сессия устарела")
 try: u=json.loads(d.get("user","{}"))
 except: raise HTTPException(401,"Профиль Telegram не найден")
 if not u.get("id"): raise HTTPException(401,"Нет Telegram ID")
 return u
def owner(req):
 u=user(req)
 if not OWNER or str(u["id"]).strip()!=OWNER: raise HTTPException(403,"Доступ только владельцу")
 return u
def role_of(uid):
 if OWNER and str(uid)==OWNER: return "owner"
 with conn() as c,c.cursor() as q:
  q.execute("SELECT role FROM staff_roles WHERE user_id=%s",(uid,)); x=q.fetchone()
 return x["role"] if x else "user"
def staff_user(req, allowed=("deputy","support")):
 u=user(req); role=role_of(u["id"])
 if role=="owner": return u,role
 if role not in allowed: raise HTTPException(403,"Нет доступа к разделу команды")
 return u,role
def require_subscription(u):
 if not subscribed(u['id']): raise HTTPException(403,'Сначала подпишитесь на @studiodlc и нажмите «Проверить подписку».')
 return u
_membership_cache={}
_membership_lock=threading.Lock()
def subscribed(uid):
 now=time.monotonic()
 with _membership_lock:
  cached=_membership_cache.get(int(uid))
  if cached and now-cached[0]<cached[1]: return cached[2]
 try:
  x=requests.get("https://api.telegram.org/bot"+TOKEN+"/getChatMember",params={"chat_id":CHANNEL,"user_id":uid},timeout=4).json()
  ok=bool(x.get("ok") and x.get("result",{}).get("status") in ("creator","administrator","member"))
  ttl=45 if ok else 8
 except:
  ok=False; ttl=4
 with _membership_lock:
  _membership_cache[int(uid)]=(time.monotonic(),ttl,ok)
 return ok
def notify(msg):
 if TOKEN and OWNER:
  try: requests.post("https://api.telegram.org/bot"+TOKEN+"/sendMessage",json={"chat_id":OWNER,"text":msg},timeout=8)
  except: pass
@app.get("/health")
def health(): return {"ok":True}
@app.get("/",response_class=HTMLResponse)
def index(): return PAGE
@app.get("/api/session")
def session(r:Request):
 u=user(r); role=role_of(u["id"]); member=subscribed(u["id"]); items=[]
 if member:
  with conn() as c,c.cursor() as q:
   q.execute("SELECT id,title,description,price,category,image FROM products WHERE active=TRUE ORDER BY id DESC")
   items=q.fetchall()
 return {"user":u,"owner":role=="owner","role":role,"staff":role in ("owner","deputy","support"),"subscribed":member,"products":items}
@app.get("/api/products")
def products(r:Request):
 require_subscription(user(r))
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id,title,description,price,category,image FROM products WHERE active=TRUE ORDER BY id DESC"); return {"items":q.fetchall()}
@app.post("/api/orders")
async def order(r:Request):
 u=require_subscription(user(r))
 d=await r.json()
 with conn() as c,c.cursor() as q:
  q.execute("SELECT * FROM products WHERE id=%s AND active=TRUE",(int(d.get("product_id",0)),)); p=q.fetchone()
  if not p: raise HTTPException(404,"Услуга не найдена")
  q.execute("INSERT INTO orders(user_id,username,product_id,product_title,price,comment) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",(u["id"],u.get("username",""),p["id"],p["title"],p["price"],str(d.get("comment",""))[:1000])); oid=q.fetchone()["id"]
 notify("Новый заказ #"+str(oid)+"\n"+p["title"]+" · "+p["price"]+"\nTelegram ID: "+str(u["id"]))
 return {"id":oid,"payment_url":PAYMENT}
@app.post("/api/orders/{oid}/receipt")
async def receipt(oid:int,r:Request,file:UploadFile=File(...)):
 u=require_subscription(user(r))
 if file.content_type not in ("image/jpeg","image/png","image/webp"): raise HTTPException(400,"Нужен JPG, PNG или WEBP")
 b=await file.read()
 if len(b)>5*1024*1024: raise HTTPException(413,"Максимум 5 МБ")
 with conn() as c,c.cursor() as q:
  q.execute("UPDATE orders SET receipt=%s,receipt_type=%s WHERE id=%s AND user_id=%s AND status='awaiting_review' RETURNING id",(psycopg2.Binary(b),file.content_type,oid,u["id"]))
  if not q.fetchone(): raise HTTPException(404,"Заказ не найден")
 notify("К заказу #"+str(oid)+" загружен скриншот. Проверьте оплату вручную.")
 return {"ok":True}
@app.get("/api/my-orders")
def myorders(r:Request):
 u=require_subscription(user(r))
 with conn() as c,c.cursor() as q:
  q.execute("SELECT o.id,o.product_title,o.price,o.status,o.created_at,EXISTS(SELECT 1 FROM order_reviews rv WHERE rv.order_id=o.id) reviewed FROM orders o WHERE o.user_id=%s ORDER BY o.id DESC",(u["id"],))
  rows=q.fetchall()
 return {"items":[dict(x,created_at=x["created_at"].isoformat()) for x in rows]}
@app.get("/api/profile")
def profile(r:Request):
 u=require_subscription(user(r))
 with conn() as c,c.cursor() as q:
  q.execute("INSERT INTO user_profiles(user_id,username,first_name) VALUES(%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name",(u["id"],u.get("username",""),u.get("first_name","")))
  q.execute("SELECT balance,created_at FROM user_profiles WHERE user_id=%s",(u["id"],)); p=q.fetchone()
 return {"user":{"id":u["id"],"first_name":u.get("first_name",""),"last_name":u.get("last_name",""),"username":u.get("username","")},"balance":str(p["balance"]),"created_at":p["created_at"].isoformat()}
@app.post("/api/orders/{oid}/review")
async def submit_review(oid:int,r:Request):
 u=require_subscription(user(r)); d=await r.json()
 rating=int(d.get("rating",0)); body=str(d.get("body","")).strip()
 if rating<1 or rating>5 or len(body)<3 or len(body)>1000: raise HTTPException(400,"Поставь оценку от 1 до 5 и напиши отзыв (3–1000 символов).")
 with conn() as c,c.cursor() as q:
  q.execute("SELECT product_title,status FROM orders WHERE id=%s AND user_id=%s",(oid,u["id"])); o=q.fetchone()
  if not o: raise HTTPException(404,"Заказ не найден")
  if o["status"]!="completed": raise HTTPException(400,"Отзыв можно оставить после выдачи заказа.")
  q.execute("INSERT INTO order_reviews(order_id,user_id,rating,body) VALUES(%s,%s,%s,%s) ON CONFLICT(order_id) DO NOTHING RETURNING id",(oid,u["id"],rating,body))
  if not q.fetchone(): raise HTTPException(409,"Отзыв по этому заказу уже отправлен.")
 who="@"+u.get("username","") if u.get("username") else (u.get("first_name") or "Клиент")
 import html
 review_text="\n".join([
  "⭐ <b>НОВЫЙ ОТЗЫВ STUDIO DLC</b>",
  "",
  "<b>Заказ:</b> #"+str(oid),
  "<b>Услуга:</b> "+html.escape(str(o["product_title"])),
  "<b>Оценка:</b> "+("⭐"*rating),
  "<b>Автор:</b> "+html.escape(who),
  "",
  "<blockquote>"+html.escape(body)+"</blockquote>"
 ])
 sent=tg_call("sendMessage",{"chat_id":"@logovotziv","text":review_text,"parse_mode":"HTML"})
 if not sent or not sent.get("ok"):
  with conn() as c,c.cursor() as q: q.execute("DELETE FROM order_reviews WHERE order_id=%s AND user_id=%s",(oid,u["id"]))
  raise HTTPException(503,"Не удалось отправить отзыв. Попробуй ещё раз позже.")
 return {"ok":True,"published":True}
@app.get("/api/forum")
def forum(r:Request):
 require_subscription(user(r))
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id,username,body,created_at FROM forum_posts ORDER BY id DESC LIMIT 100"); rows=q.fetchall()
 return {"items":[dict(x,created_at=x["created_at"].isoformat()) for x in rows]}
@app.post("/api/forum")
async def post(r:Request):
 u=user(r)
 if not subscribed(u["id"]): raise HTTPException(403,"Подпишитесь на канал")
 body=str((await r.json()).get("body","")).strip()
 if not body or len(body)>2000: raise HTTPException(400,"Текст: 1–2000 символов")
 with conn() as c,c.cursor() as q: q.execute("INSERT INTO forum_posts(user_id,username,body) VALUES(%s,%s,%s)",(u["id"],u.get("username",""),body))
 return {"ok":True}

@app.get("/api/support/tickets")
def support_tickets(r:Request):
 u=require_subscription(user(r))
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id,subject,status,created_at,updated_at FROM support_tickets WHERE user_id=%s ORDER BY updated_at DESC",(u["id"],))
  rows=q.fetchall()
 return {"items":[dict(x,created_at=x["created_at"].isoformat(),updated_at=x["updated_at"].isoformat()) for x in rows]}
@app.post("/api/support/tickets")
async def create_support_ticket(r:Request):
 u=require_subscription(user(r)); d=await r.json()
 subject=str(d.get("subject","")).strip()[:120]; body=str(d.get("body","")).strip()
 if len(subject)<3 or len(body)<3 or len(body)>2000: raise HTTPException(400,"Укажи тему и сообщение (3–2000 символов).")
 with conn() as c,c.cursor() as q:
  q.execute("INSERT INTO support_tickets(user_id,username,subject) VALUES(%s,%s,%s) RETURNING id",(u["id"],u.get("username",""),subject)); tid=q.fetchone()["id"]
  q.execute("INSERT INTO support_messages(ticket_id,sender_id,sender_role,body) VALUES(%s,%s,'user',%s)",(tid,u["id"],body))
 notify("Новое обращение в поддержку #"+str(tid)+" от "+str(u["id"])+": "+subject)
 return {"id":tid,"ok":True}
@app.get("/api/support/tickets/{tid}/messages")
def support_ticket_messages(tid:int,r:Request):
 u=require_subscription(user(r))
 with conn() as c,c.cursor() as q:
  q.execute("SELECT user_id FROM support_tickets WHERE id=%s",(tid,)); t=q.fetchone()
  if not t or t["user_id"]!=u["id"]: raise HTTPException(404,"Обращение не найдено")
  q.execute("SELECT sender_role,body,created_at FROM support_messages WHERE ticket_id=%s ORDER BY id",(tid,))
  rows=q.fetchall()
 return {"items":[dict(x,created_at=x["created_at"].isoformat()) for x in rows]}
@app.post("/api/support/tickets/{tid}/messages")
async def support_user_reply(tid:int,r:Request):
 u=require_subscription(user(r)); body=str((await r.json()).get("body","")).strip()
 if len(body)<1 or len(body)>2000: raise HTTPException(400,"Сообщение должно быть до 2000 символов.")
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id FROM support_tickets WHERE id=%s AND user_id=%s AND status='open'",(tid,u["id"]))
  if not q.fetchone(): raise HTTPException(404,"Открытое обращение не найдено")
  q.execute("INSERT INTO support_messages(ticket_id,sender_id,sender_role,body) VALUES(%s,%s,'user',%s)",(tid,u["id"],body))
  q.execute("UPDATE support_tickets SET updated_at=now() WHERE id=%s",(tid,))
 return {"ok":True}
@app.get("/api/staff/tickets")
def staff_ticket_list(r:Request):
 u,role=staff_user(r)
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id,user_id,username,subject,status,created_at,updated_at FROM support_tickets ORDER BY updated_at DESC LIMIT 200")
  rows=q.fetchall()
 return {"items":[dict(x,created_at=x["created_at"].isoformat(),updated_at=x["updated_at"].isoformat()) for x in rows],"role":role}
@app.get("/api/staff/tickets/{tid}/messages")
def staff_ticket_messages(tid:int,r:Request):
 staff_user(r)
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id FROM support_tickets WHERE id=%s",(tid,))
  if not q.fetchone(): raise HTTPException(404,"Обращение не найдено")
  q.execute("SELECT sender_role,body,created_at FROM support_messages WHERE ticket_id=%s ORDER BY id",(tid,))
  rows=q.fetchall()
 return {"items":[dict(x,created_at=x["created_at"].isoformat()) for x in rows]}
@app.post("/api/staff/tickets/{tid}/messages")
async def staff_ticket_reply(tid:int,r:Request):
 u,role=staff_user(r); body=str((await r.json()).get("body","")).strip()
 if len(body)<1 or len(body)>2000: raise HTTPException(400,"Сообщение должно быть до 2000 символов.")
 with conn() as c,c.cursor() as q:
  q.execute("SELECT user_id FROM support_tickets WHERE id=%s AND status='open'",(tid,)); t=q.fetchone()
  if not t: raise HTTPException(404,"Открытое обращение не найдено")
  q.execute("INSERT INTO support_messages(ticket_id,sender_id,sender_role,body) VALUES(%s,%s,%s,%s)",(tid,u["id"],role,body))
  q.execute("UPDATE support_tickets SET updated_at=now() WHERE id=%s",(tid,))
 if TOKEN: tg_call("sendMessage",{"chat_id":t["user_id"],"text":"Ответ поддержки по обращению #"+str(tid)+":\n"+body})
 return {"ok":True}
@app.post("/api/staff/tickets/{tid}/close")
def staff_close_ticket(tid:int,r:Request):
 staff_user(r)
 with conn() as c,c.cursor() as q:
  q.execute("UPDATE support_tickets SET status='closed',updated_at=now() WHERE id=%s RETURNING id",(tid,))
  if not q.fetchone(): raise HTTPException(404,"Обращение не найдено")
 return {"ok":True}
@app.get("/api/owner/roles")
def owner_roles(r:Request):
 owner(r)
 with conn() as c,c.cursor() as q:
  q.execute("SELECT user_id,username,role,created_at FROM staff_roles ORDER BY created_at DESC")
  rows=q.fetchall()
 return {"items":[dict(x,created_at=x["created_at"].isoformat()) for x in rows]}
@app.post("/api/owner/roles")
async def grant_role(r:Request):
 u=owner(r); d=await r.json()
 try: uid=int(d.get("user_id",0))
 except: raise HTTPException(400,"Нужен числовой Telegram ID")
 role=str(d.get("role",""))
 if uid<=0 or (OWNER and str(uid)==OWNER): raise HTTPException(400,"Нельзя назначить владельца через список ролей")
 if role not in ("deputy","support","moderator"): raise HTTPException(400,"Неизвестная роль")
 username=str(d.get("username",""))[:64]
 with conn() as c,c.cursor() as q:
  q.execute("INSERT INTO staff_roles(user_id,username,role,granted_by) VALUES(%s,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username,role=EXCLUDED.role,granted_by=EXCLUDED.granted_by,created_at=now()",(uid,username,role,u["id"]))
 return {"ok":True}
@app.delete("/api/owner/roles/{uid}")
def revoke_role(uid:int,r:Request):
 owner(r)
 with conn() as c,c.cursor() as q: q.execute("DELETE FROM staff_roles WHERE user_id=%s",(uid,))
 return {"ok":True}

@app.get("/api/admin/orders")
def adminorders(r:Request):
 owner(r)
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id,user_id,username,product_title,price,comment,status,created_at,receipt IS NOT NULL has_receipt FROM orders ORDER BY id DESC LIMIT 200"); rows=q.fetchall()
 return {"items":[dict(x,created_at=x["created_at"].isoformat()) for x in rows]}
@app.get("/api/admin/orders/{oid}/receipt")
def adminreceipt(oid:int,r:Request):
 owner(r)
 with conn() as c,c.cursor() as q: q.execute("SELECT receipt,receipt_type FROM orders WHERE id=%s",(oid,)); x=q.fetchone()
 if not x or not x["receipt"]: raise HTTPException(404,"Скриншот не найден")
 return {"type":x["receipt_type"],"data":base64.b64encode(bytes(x["receipt"])).decode()}
@app.post("/api/admin/orders/{oid}/status")
async def status(oid:int,r:Request):
 owner(r); s=(await r.json()).get("status")
 if s not in ("paid","rejected","completed"): raise HTTPException(400,"Недопустимый статус")
 with conn() as c,c.cursor() as q:
  q.execute("UPDATE orders SET status=%s WHERE id=%s RETURNING user_id",(s,oid)); x=q.fetchone()
  if not x: raise HTTPException(404,"Заказ не найден")
 if TOKEN:
  try: requests.post("https://api.telegram.org/bot"+TOKEN+"/sendMessage",json={"chat_id":x["user_id"],"text":"Статус заказа #"+str(oid)+": "+s},timeout=8)
  except: pass
 return {"ok":True}

@app.get("/api/products/{pid}/images")
def product_images(pid:int,r:Request):
 require_subscription(user(r))
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id,mime,data FROM product_images WHERE product_id=%s ORDER BY id",(pid,))
  return {"items":[{"id":x["id"],"type":x["mime"],"data":base64.b64encode(bytes(x["data"])).decode()} for x in q.fetchall()]}
@app.post("/api/admin/products/{pid}/images")
async def add_product_images(pid:int,r:Request,files:list[UploadFile]=File(...)):
 owner(r)
 if not files or len(files)>8: raise HTTPException(400,"Можно загрузить от 1 до 8 фото за раз")
 saved=[]
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id FROM products WHERE id=%s",(pid,))
  if not q.fetchone(): raise HTTPException(404,"Услуга не найдена")
  q.execute("SELECT count(*) n FROM product_images WHERE product_id=%s",(pid,))
  if q.fetchone()["n"]+len(files)>12: raise HTTPException(400,"Для услуги максимум 12 фотографий")
  for f in files:
   if f.content_type not in ("image/jpeg","image/png","image/webp"): raise HTTPException(400,"Поддерживаются JPG, PNG и WEBP")
   b=await f.read()
   if not b or len(b)>5*1024*1024: raise HTTPException(413,"Каждое фото — максимум 5 МБ")
   q.execute("INSERT INTO product_images(product_id,data,mime) VALUES(%s,%s,%s) RETURNING id",(pid,psycopg2.Binary(b),f.content_type))
   saved.append(q.fetchone()["id"])
 return {"ok":True,"ids":saved}
@app.delete("/api/admin/products/{pid}/images/{image_id}")
def delete_product_image(pid:int,image_id:int,r:Request):
 owner(r)
 with conn() as c,c.cursor() as q: q.execute("DELETE FROM product_images WHERE id=%s AND product_id=%s",(image_id,pid))
 return {"ok":True}

@app.post("/api/admin/products")
async def addproduct(r:Request):
 owner(r); d=await r.json()
 with conn() as c,c.cursor() as q:
  q.execute("INSERT INTO products(title,description,price,category,image) VALUES(%s,%s,%s,%s,%s) RETURNING id",(str(d.get("title","Новая услуга"))[:120],str(d.get("description",""))[:2000],str(d.get("price","По запросу"))[:80],str(d.get("category","Услуги"))[:80],str(d.get("image",""))[:1000])); return {"id":q.fetchone()["id"]}
@app.put("/api/admin/products/{pid}")
async def updateproduct(pid:int,r:Request):
 owner(r); d=await r.json()
 with conn() as c,c.cursor() as q:
  q.execute("UPDATE products SET title=%s,description=%s,price=%s,category=%s,image=%s,active=%s WHERE id=%s",(str(d.get("title",""))[:120],str(d.get("description",""))[:2000],str(d.get("price",""))[:80],str(d.get("category","Услуги"))[:80],str(d.get("image",""))[:1000],bool(d.get("active",True)),pid))
 return {"ok":True}
@app.delete("/api/admin/products/{pid}")
def delproduct(pid:int,r:Request):
 owner(r)
 with conn() as c,c.cursor() as q: q.execute("DELETE FROM products WHERE id=%s",(pid,))
 return {"ok":True}
PAGE=r"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#090811"><script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>STUDIO DLC — Digital Atelier</title>
<style>
:root{color-scheme:dark;--bg:#070912;--panel:#101426;--panel2:#151b31;--line:rgba(120,157,255,.19);--text:#f4f6ff;--muted:#929bb8;--blue:#43a8ff;--violet:#9b70ff;--cyan:#74e5ee;--shadow:0 18px 60px #0008}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{min-height:100%;background:var(--bg);-webkit-text-size-adjust:100%;scroll-behavior:auto}
body{margin:0;min-height:100vh;min-height:100svh;padding:0 0 calc(96px + env(safe-area-inset-bottom));overflow-x:hidden;color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif;background:radial-gradient(ellipse at 8% 0%,#164c9a2c,transparent 38%),radial-gradient(ellipse at 92% 22%,#6536a52a,transparent 38%),linear-gradient(180deg,#080a15,#070912 60%,#090b18);-webkit-tap-highlight-color:transparent}
body:before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:linear-gradient(120deg,transparent 25%,#5e91ff08 50%,transparent 75%);background-size:240% 240%;animation:ambient 20s ease-in-out infinite alternate}
@keyframes ambient{to{background-position:100% 100%}}
.noise{display:none}.aurora{position:fixed;z-index:-1;width:260px;height:260px;border-radius:50%;filter:blur(100px);opacity:.15;pointer-events:none;animation:drift 22s ease-in-out infinite alternate}.a1{left:-180px;top:8%;background:#287dff}.a2{right:-190px;top:48%;background:#8c52ff;animation-delay:-9s}@keyframes drift{to{transform:translate3d(45px,55px,0) scale(1.1)}}
.shell{width:min(100%,760px);margin:0 auto;padding:max(10px,env(safe-area-inset-top)) 18px 34px}
.topbar{height:66px;display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 -18px 24px;padding:0 20px;position:sticky;top:0;z-index:12;background:#080a15eF;border-bottom:1px solid #899bff1c;box-shadow:0 8px 30px #02030a55;backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px)}
.brand{display:flex;align-items:baseline;gap:10px;font-size:19px;font-weight:950;letter-spacing:3px;white-space:nowrap}.word-studio{color:#f7f8ff;text-shadow:0 0 22px #6d8fff22}.word-dlc{background:linear-gradient(100deg,#a78aff,#58c7ff);background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.status{max-width:45%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:8px 13px;border:1px solid #8c85ff33;border-radius:99px;background:linear-gradient(110deg,#16182d,#101b31);color:#c7c8ff;font-size:10px;font-weight:800;letter-spacing:1px}
.view{display:none;min-width:0}.view.active{display:block;animation:pageEnter .34s cubic-bezier(.2,.8,.2,1) both}.view.active.page-left{animation:slideLeft .32s cubic-bezier(.2,.8,.2,1) both}.view.active.page-right{animation:slideRight .32s cubic-bezier(.2,.8,.2,1) both}
@keyframes pageEnter{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}@keyframes slideLeft{from{opacity:.35;transform:translateX(18px)}to{opacity:1;transform:none}}@keyframes slideRight{from{opacity:.35;transform:translateX(-18px)}to{opacity:1;transform:none}}
.hero{position:relative;isolation:isolate;overflow:hidden;min-height:315px;padding:clamp(26px,7vw,44px);border:1px solid #8d9dff38;border-radius:28px;background:radial-gradient(ellipse at 88% 10%,#9c64ff36,transparent 42%),radial-gradient(ellipse at 8% 100%,#248fff35,transparent 46%),linear-gradient(135deg,#151a35,#0b1125 56%,#17102c);box-shadow:0 24px 65px #0007,inset 0 1px 0 #ffffff18}
.hero:before{content:"";position:absolute;z-index:-1;width:210px;height:210px;right:-80px;top:-100px;border:1px solid #aabaff55;border-radius:50%;box-shadow:0 0 0 22px #fff1,0 0 0 48px #fff1,0 0 70px #8e6bff3a;animation:orbit 34s linear infinite}@keyframes orbit{to{transform:rotate(360deg)}}
.hero:after{content:"";position:absolute;z-index:-1;width:180px;height:180px;right:8%;bottom:-155px;border-radius:50%;background:#3f9cff;filter:blur(65px);opacity:.22}
.eyebrow{display:flex;align-items:center;gap:9px;color:#b2a2ff;font-size:10px;font-weight:900;letter-spacing:2.2px}.eyebrow:before{content:"";width:19px;height:1px;background:linear-gradient(90deg,var(--blue),var(--violet));box-shadow:0 0 10px var(--blue)}
.hero h1{position:relative;margin:25px 0 15px;max-width:480px;font-size:clamp(42px,10vw,68px);line-height:.96;letter-spacing:-2.8px;font-weight:900}.gradient{background:linear-gradient(100deg,#fff 0%,#9edbff 35%,#b59aff 76%,#fff 100%);background-size:180% auto;background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:shimmer 8s ease-in-out infinite alternate}@keyframes shimmer{to{background-position:100% center}}
.hero p{position:relative;max-width:400px;margin:0 0 25px;color:#b2bad4;font-size:14px;line-height:1.75}
button{font:inherit;touch-action:manipulation;cursor:pointer;-webkit-user-select:none;user-select:none}button:focus-visible{outline:2px solid #91cfff;outline-offset:3px}
.primary{position:relative;overflow:hidden;min-height:48px;padding:0 20px;border:0;border-radius:15px;color:#080b18;background:linear-gradient(105deg,#dff7ff,#78baff 48%,#ad8cff);font-size:13px;font-weight:900;box-shadow:0 9px 28px #648cff36,inset 0 1px 0 #fff;transition:transform .2s,filter .2s}.primary:active,.secondary:active{transform:scale(.975)}
.secondary{min-height:44px;padding:11px 16px;border:1px solid #9286ff38;border-radius:14px;color:#ecebff;background:linear-gradient(145deg,#1c1d38,#111426);font-size:12px;font-weight:800;box-shadow:inset 0 1px 0 #ffffff0c;transition:transform .18s,border-color .18s}.secondary:hover{border-color:#6ccaff88}
.sectionhead{display:flex;align-items:end;justify-content:space-between;gap:12px;margin:32px 2px 15px}.sectionhead h2{margin:0;font-size:23px;line-height:1.15;font-weight:900;letter-spacing:-.8px}.sectionhead small{color:#8985b4;font-size:9px;font-weight:900;letter-spacing:1.6px;text-align:right}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:14px 0 4px}.stat{min-width:0;padding:14px 12px;border:1px solid #8995ff26;border-radius:17px;background:linear-gradient(145deg,#171a32,#0d1224);box-shadow:inset 0 1px 0 #ffffff0c}.stat strong{display:block;background:linear-gradient(90deg,#fff,#9edbff 60%,#bba0ff);background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-size:19px;font-weight:900}.stat span{display:block;margin-top:3px;color:#898eae;font-size:9px;font-weight:900;letter-spacing:1px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.service{position:relative;min-width:0;overflow:hidden;padding:14px;border:1px solid #8a93ff2c;border-radius:21px;background:linear-gradient(145deg,#171a32,#0d1122 78%);box-shadow:0 12px 34px #0005,inset 0 1px 0 #ffffff0b;transition:transform .25s,border-color .25s,box-shadow .25s}.service:before{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(circle at 90% 0%,#9870ff22,transparent 48%)}.service:after{content:"";position:absolute;inset:0;pointer-events:none;border-radius:inherit;background:linear-gradient(135deg,#ffffff08,transparent 38%,transparent 70%,#65caff08)}@media(hover:hover){.service:hover{transform:translateY(-4px);border-color:#69baff77;box-shadow:0 18px 42px #0007}}
.serviceart{position:relative;display:flex;align-items:center;justify-content:center;height:112px;margin-bottom:13px;overflow:hidden;border:1px solid #a49aff30;border-radius:15px;background:radial-gradient(circle at 50% 40%,#42448c,#1c2349 58%,#101426)}.serviceart:before,.serviceart:after{content:"";position:absolute;width:100px;height:100px;border:1px solid #b3c4ff55;border-radius:50%;box-shadow:0 0 0 16px #ffffff06}.serviceart:after{width:56px;height:56px;border-color:#79dff055;transform:translate(24px,-14px)}.glyph{z-index:1;color:#f0eaff;font-size:37px;filter:drop-shadow(0 0 15px #8e7cffaa)}
.category{color:#a89cff;font-size:9px;font-weight:900;letter-spacing:1.3px;text-transform:uppercase}.service h3{margin:7px 0 6px;color:#f0f1ff;font-size:15px;font-weight:850;line-height:1.3}.service p{min-height:36px;margin:0;color:#a0a5c0;font-size:11px;line-height:1.55}.price{margin:13px 0 10px;color:#d7d1ff;font-size:14px;font-weight:900}.service .secondary{position:relative;z-index:2;width:100%}
.panel{margin:12px 0;padding:18px;border:1px solid #8995ff2b;border-radius:21px;background:linear-gradient(145deg,#171a31f5,#0d1122f7);box-shadow:0 14px 38px #0005,inset 0 1px 0 #ffffff0b}.muted{color:#a0a5c0;font-size:12px;line-height:1.7}
.field{display:block;width:100%;min-height:46px;margin:9px 0;padding:13px 14px;border:1px solid #8a96ff2e;border-radius:13px;outline:0;color:#f4f4ff;background:#090d1b;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;transition:border-color .2s,box-shadow .2s}.field::placeholder{color:#777f9f}.field:focus{border-color:#9a86ff;box-shadow:0 0 0 3px #9078ff20}textarea.field{min-height:92px;resize:vertical;line-height:1.5}.wide{width:100%;margin-top:8px}
.order{min-width:0;margin:11px 0;padding:16px;border:1px solid #8995ff2b;border-radius:19px;background:linear-gradient(145deg,#171a31,#0d1122);box-shadow:0 12px 30px #0005,inset 0 1px 0 #ffffff08;overflow:visible}.orderhead{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;flex-wrap:wrap}.order b{color:#f1f1ff;font-size:14px;line-height:1.45}.badge{display:inline-flex;align-items:center;max-width:100%;padding:6px 10px;border:1px solid #a18bff55;border-radius:99px;color:#d4c6ff;background:#9875ff19;font-size:10px;font-weight:800;overflow-wrap:anywhere}.order .muted{overflow-wrap:anywhere}.order button{max-width:100%}.order input[type=file]{max-width:100%;font-size:11px;color:#b1b5d0}
.empty{padding:26px 18px;border:1px dashed #9c8cff4d;border-radius:18px;color:#a0a5c0;background:#111426;font-size:12px;line-height:1.7;text-align:center}.post{padding:14px 2px;border-bottom:1px solid var(--line)}.post:last-child{border:0}.post p{margin:8px 0;white-space:pre-wrap;color:#e1e2f6;font-size:13px;line-height:1.65}.post small{color:#9298b6}
.nav{position:fixed;z-index:50;left:0;right:0;bottom:0;padding:8px 9px calc(8px + env(safe-area-inset-bottom));border-top:1px solid #8b8dff26;background:#080a16f2;box-shadow:0 -14px 42px #0009;backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px)}.navinner{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));align-items:center;gap:4px;width:min(100%,650px);margin:0 auto;overflow:visible}.navbtn{position:relative;display:flex;min-width:0;min-height:60px;flex-direction:column;align-items:center;justify-content:center;gap:5px;padding:5px 1px;border:1px solid transparent;border-radius:15px;color:#878ba9;background:transparent;font-size:9px;font-weight:850;white-space:nowrap;transition:color .2s,background .2s,border-color .2s}.navbtn .ico{font-size:20px;line-height:1.1;transition:transform .22s}.navbtn.active{color:#f1edff;border-color:#a08aff35;background:linear-gradient(145deg,#7771ff25,#318eff12);box-shadow:inset 0 1px 0 #ffffff12}.navbtn.active:before{content:"";position:absolute;top:-8px;left:32%;right:32%;height:2px;border-radius:4px;background:linear-gradient(90deg,#62cfff,#a57bff);box-shadow:0 0 12px #8d8cff}.navbtn.active .ico{color:#c5b7ff;transform:translateY(-1px) scale(1.08)}
.modalback{display:none;position:fixed;inset:0;z-index:70;align-items:flex-end;justify-content:center;padding:14px 12px calc(14px + env(safe-area-inset-bottom));background:#02030bd9;backdrop-filter:blur(12px)}.modalback.open{display:flex;animation:fadeIn .2s ease both}.modal{width:min(100%,520px);max-height:86svh;overflow:auto;padding:22px;border:1px solid #9c8bff55;border-radius:26px 26px 19px 19px;background:linear-gradient(145deg,#1a1b36,#0b0e1d);box-shadow:0 24px 80px #000b;animation:sheetIn .3s cubic-bezier(.2,.8,.2,1) both}@keyframes fadeIn{from{opacity:0}to{opacity:1}}@keyframes sheetIn{from{opacity:.5;transform:translateY(28px)}to{opacity:1;transform:none}}.modal h2{margin:8px 0;font-size:23px;letter-spacing:-.6px}.modalclose{float:right;width:34px;height:34px;border:1px solid var(--line);border-radius:11px;color:#e8e5ff;background:#ffffff0a;font-size:20px}
.notice{margin:12px 0;padding:13px;border:1px solid #f2c46c35;border-radius:15px;color:#f0d49b;background:#f2c46c10;font-size:12px;line-height:1.6}.notice a{color:#a9d9ff}
.toast{position:fixed;z-index:90;left:50%;bottom:calc(100px + env(safe-area-inset-bottom));max-width:90%;padding:12px 16px;border:1px solid #9b8cff55;border-radius:14px;color:#f1efff;background:#14162b;box-shadow:0 14px 38px #0009;opacity:0;pointer-events:none;transform:translate(-50%,12px);transition:opacity .22s,transform .22s;font-size:12px;text-align:center}.toast.show{opacity:1;transform:translate(-50%,0)}
#gate[hidden]{display:none!important}#gate:not([hidden]){position:relative;isolation:isolate;overflow:hidden;width:min(100%,520px);min-height:320px;margin:clamp(22px,7vh,72px) auto 34px;padding:32px 24px;border:1px solid #928cff55;border-radius:28px;background:radial-gradient(ellipse at 50% -10%,#5f7bff35,transparent 58%),linear-gradient(145deg,#171a34,#070a16);box-shadow:0 28px 90px #0009;text-align:center;animation:pageEnter .5s ease both}#gate h2{margin:13px 0 10px;color:#f5f4ff;font-size:29px;line-height:1.1;letter-spacing:-1px}#gate p{max-width:390px;margin:0 auto 24px;color:#b2b4d0;font-size:14px;line-height:1.7}.gate-orb{width:78px;height:78px;margin:0 auto 22px;display:grid;place-items:center;border-radius:24px;color:white;background:linear-gradient(145deg,#dff6ff,#438eff 55%,#6844c9);box-shadow:0 0 30px #6a8cff77}.gate-orb span{font-size:48px;font-weight:900}.gate-kicker{color:#a99bff;font-size:10px;font-weight:900;letter-spacing:2.6px}.gate-link{display:flex!important;align-items:center;justify-content:center;gap:12px;width:100%;max-width:350px;margin:0 auto 12px;text-decoration:none!important;min-height:52px}.gate-check-btn{width:100%;max-width:350px;min-height:50px}.gate-status{margin:18px auto 0;max-width:350px;color:#9299bc;font-size:11px;line-height:1.55}.gate-check{width:62px;height:62px;margin:0 auto 18px;display:grid;place-items:center;border-radius:50%;background:linear-gradient(145deg,#7ce7c1,#0c9d72);color:#042c24;font-size:32px}.gate-success{animation:pageEnter .35s ease both}body.locked #home,body.locked #shop,body.locked #orders,body.locked #profile,body.locked #admin,body.locked #staff,body.locked #support,body.locked .nav{display:none!important}
#admin[hidden],#staff[hidden]{display:none!important}
.gallery{position:relative;height:160px;margin-bottom:14px;overflow:hidden;border-radius:16px;background:#11162b;touch-action:pan-y}.gallery img{display:block;width:100%;height:100%;object-fit:cover}.gallery-count{position:absolute;right:9px;bottom:9px;padding:5px 8px;border-radius:20px;color:#f0efff;background:#02030dcc;font-size:10px}.gallery-prev,.gallery-next{position:absolute;top:50%;width:32px;height:32px;transform:translateY(-50%);border:1px solid #ffffff44;border-radius:50%;color:white;background:#11152ddd}.gallery-prev{left:7px}.gallery-next{right:7px}.photo-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:9px}.photo-thumb{width:54px;height:54px;object-fit:cover;border-radius:10px;border:1px solid #ffffff24}
.avatar{width:62px;height:62px;border-radius:19px;display:grid;place-items:center;color:#101326;background:linear-gradient(135deg,#e9e8ff,#a8a0ff 55%,#6b91ff);font-size:27px;font-weight:950;box-shadow:0 0 25px #7770ff55}.profilehead{display:flex;align-items:center;gap:14px}.profilehead b{font-size:20px}.wallet{margin:24px 0 16px;padding:23px;border:1px solid #3b8edb55;border-radius:20px;background:radial-gradient(circle at 90% 0%,#755aff35,transparent 50%),linear-gradient(135deg,#10254a,#101329)}.wallet small{color:#a6caff;letter-spacing:2px;font-size:10px}.wallet strong{display:block;margin-top:8px;color:#f1f2ff;font-size:36px;font-weight:950}.profilemeta{padding:12px 0;color:#9da4c4;font-size:11px}.reviewbox{margin-top:12px;padding:14px;border:1px solid #9285ff55;border-radius:16px;background:#11152a}.reviewbox .field{margin-top:9px}
.access-card{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:12px 0;padding:16px;border:1px solid #8c8fff38;border-radius:19px;background:linear-gradient(110deg,#171a32,#0d1122)}.access-card>div{display:grid;gap:5px}.access-card b{font-size:13px}.access-card small{color:#999fbd;font-size:10px;line-height:1.5}.access-card .primary,.access-card .secondary{flex:none}.owner-access{border-color:#a27bff66;background:linear-gradient(110deg,#281d49,#10162d)}
@media(min-width:650px){.shell{padding-top:22px}.hero{min-height:350px;padding:42px}.grid{gap:16px}.service{padding:17px}.serviceart{height:132px}.modalback{align-items:center}.modal{border-radius:25px}}
@media(max-width:390px){.shell{padding-left:14px;padding-right:14px}.topbar{margin-left:-14px;margin-right:-14px;padding-left:15px;padding-right:15px}.brand{font-size:17px;letter-spacing:2px}.hero{min-height:295px;padding:25px 21px}.hero h1{letter-spacing:-2px}.stats{gap:7px}.stat{padding:12px 9px}.stat strong{font-size:17px}.grid{gap:9px}.service{padding:11px;border-radius:18px}.serviceart{height:100px}.service p{font-size:10px}.navbtn{font-size:8px}.navbtn .ico{font-size:19px}.access-card{align-items:flex-start;flex-direction:column}.access-card button{width:100%}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important;scroll-behavior:auto!important}}
</style></head><body><div class="noise"></div><div class="aurora a1"></div><div class="aurora a2"></div>
<main class="shell">
<header class="topbar"><div class="brand" aria-label="STUDIO DLC"><span class="word-studio">STUDIO</span><span class="word-dlc">DLC</span></div><div class="status" id="userStatus">DIGITAL ATELIER</div></header>
<div id="gate" class="notice" hidden>Чтобы пользоваться сервисом, подпишись на <a href="https://t.me/studiodlc">канал STUDIO DLC</a>, затем нажми «Проверить».</div>
<section id="home" class="view active">
<div class="hero"><div class="eyebrow">INDEPENDENT DIGITAL STUDIO</div><h1>Создаём<br><span class="gradient">цифровое.</span></h1><p>Сайты, форумы и motion-дизайн. Собираем визуальный опыт, который запоминается.</p><button class="primary" data-go="shop">Исследовать студию <span>↗</span></button></div>
<div class="stats"><div class="stat"><strong>01—03</strong><span>НАПРАВЛЕНИЯ</span></div><div class="stat"><strong>∞</strong><span>ИДЕИ</span></div><div class="stat"><strong>24/7</strong><span>НА СВЯЗИ</span></div></div>
<div class="sectionhead"><h2>Избранные услуги</h2><small>SELECTED WORK</small></div><div id="featured" class="grid"></div>
<div class="panel" style="margin-top:17px"><div class="eyebrow">LET'S BUILD</div><h2 style="font:700 22px 'Space Grotesk';margin:10px 0">Есть идея?</h2><p class="muted">Расскажи о проекте — обсудим формат и детали.</p><button class="secondary" data-go="shop">Посмотреть услуги ↗</button></div>
</section>
<section id="shop" class="view"><div class="sectionhead"><h2>Каталог</h2><small>OUR SERVICES / 01</small></div><p class="muted">Выбери направление, чтобы посмотреть детали и оформить заявку.</p><div id="catalog" class="grid"></div></section>
<section id="orders" class="view"><div class="sectionhead"><h2>Мои заказы</h2><small>YOUR SPACE / 02</small></div><p class="muted">История заявок, оплата и отправка чека на ручную проверку.</p><div id="myorders"></div></section>
<section id="profile" class="view"><div class="sectionhead"><h2>Профиль</h2><small>YOUR ACCOUNT</small></div><div id="profileCard" class="panel"></div><div id="profileAccess"></div></section>
<section id="support" class="view"><div class="sectionhead"><h2>Поддержка</h2><small>WE ARE HERE</small></div><button id="staffShortcut" class="secondary wide" data-go="staff" hidden>Рабочее пространство команды ↗</button><p class="muted">Создай обращение — команда ответит прямо здесь.</p><div class="panel"><input id="ticketSubject" class="field" placeholder="Тема обращения"><textarea id="ticketBody" class="field" rows="4" placeholder="Опиши вопрос или проблему..."></textarea><button class="primary wide" data-action="create-ticket">Отправить обращение</button></div><div class="sectionhead"><h2>Мои обращения</h2></div><div id="myTickets"></div><div id="ticketChat"></div></section>
<section id="staff" class="view" hidden><div class="sectionhead"><h2>Команда и поддержка</h2><small>STAFF DESK</small></div><p id="staffRoleLabel" class="muted"></p><div id="staffTickets"></div><div id="staffChat"></div></section>
<section id="admin" class="view" hidden><div class="sectionhead"><h2>Панель владельца</h2><small>CONTROL ROOM</small></div>
<div class="panel"><div class="eyebrow">NEW SERVICE</div><input id="pt" class="field" placeholder="Название услуги"><input id="pp" class="field" placeholder="Цена"><input id="pc" class="field" placeholder="Категория"><textarea id="pd" class="field" placeholder="Описание"></textarea><label class="muted">Фото услуги — можно выбрать несколько</label><input id="newPhotos" class="field" type="file" accept="image/jpeg,image/png,image/webp" multiple><button class="primary wide" data-action="add-product">＋ Добавить услугу</button></div>
<div class="sectionhead"><h2>Заказы клиентов</h2></div><div id="adminorders"></div><div class="sectionhead"><h2>Управление каталогом</h2></div><div id="adminproducts"></div><div class="sectionhead"><h2>Управление ролями</h2></div><div class="panel"><p class="muted">Назначай сотрудников по их Telegram ID. Владелец не может быть заменён.</p><input id="roleUserId" class="field" inputmode="numeric" placeholder="Telegram ID сотрудника"><select id="roleSelect" class="field"><option value="deputy">Заместитель — заказы и поддержка</option><option value="support">Поддержка — обращения клиентов</option><option value="moderator">Модератор — модерация</option></select><button class="primary wide" data-action="grant-role">Назначить роль</button></div><div id="roleList"></div></section>
</main>
<nav class="nav" aria-label="Основная навигация"><div class="navinner">
<button class="navbtn active" data-go="home"><span class="ico">⌂</span><span>Главная</span></button>
<button class="navbtn" data-go="shop"><span class="ico">◇</span><span>Каталог</span></button>
<button class="navbtn" data-go="profile"><span class="ico">◉</span><span>Профиль</span></button>
<button class="navbtn" data-go="orders"><span class="ico">▤</span><span>Заказы</span></button>
<button class="navbtn" data-go="support"><span class="ico">✧</span><span>Поддержка</span></button>
</div></nav>
<div id="orderModal" class="modalback"><div class="modal"><button class="modalclose" data-action="close-modal">×</button><div class="eyebrow">PROJECT REQUEST</div><h2 id="modalTitle">Услуга</h2><p id="modalDesc" class="muted"></p><textarea id="orderComment" class="field" rows="3" placeholder="Опиши задачу, пожелания и сроки..."></textarea><button class="primary wide" data-action="create-order">Создать заявку и перейти к оплате ↗</button><p class="muted">После оплаты прикрепи скриншот в разделе «Заказы». Платёж проверяется владельцем вручную.</p></div></div>
<div id="toast" class="toast"></div>
<script>
(function(){
const tg=window.Telegram&&window.Telegram.WebApp;
if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#090811');tg.setBackgroundColor('#090811');}catch(e){}}
const $=id=>document.getElementById(id), headers=()=>({'X-Telegram-Init-Data':tg&&tg.initData?tg.initData:''});
let products=[],selected=null,isOwner=false,isStaff=false,staffRole="user",subscribed=false,activeTicket=0,activeStaffTicket=0;const imageCache={},imageLoading=new Set();
function toast(msg){const el=$('toast');el.textContent=msg;el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),3000);if(tg&&tg.HapticFeedback)tg.HapticFeedback.notificationOccurred('success')}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function api(path,opt){opt=opt||{};const h=Object.assign(headers(),opt.headers||{});const r=await fetch(path,Object.assign({},opt,{headers:h}));let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw Error(d.detail||('Ошибка '+r.status));return d}
function go(v){
 if(v==='admin'&&!isOwner){toast('Нет доступа');return}
 if(v==='staff'&&!isStaff){toast('Нет доступа');return}
 const active=document.getElementById(v);if(!active)return;
 const previous=document.querySelector('.view.active');
 const order=['home','shop','profile','orders','support','staff','admin'];
 const from=previous?order.indexOf(previous.id):-1,to=order.indexOf(v);
 const direction=(from<0||to>=from)?'page-left':'page-right';
 document.querySelectorAll('.view').forEach(x=>x.classList.remove('active','page-left','page-right'));
 active.hidden=false;active.classList.add('active',direction);
 document.querySelectorAll('.navbtn[data-go]').forEach(x=>x.classList.toggle('active',x.dataset.go===v));
 if(v==='profile')loadProfile();
 if(v==='orders')loadOrders();
 if(v==='support')loadMyTickets();
 if(v==='staff')loadStaffTickets();
 if(v==='admin'){loadAdmin();loadRoles()}
 window.scrollTo({top:0,behavior:'instant'});
 if(tg&&tg.HapticFeedback)tg.HapticFeedback.selectionChanged();
}
function iconFor(category){const s=(category||'').toLowerCase();if(s.includes('сайт'))return '⌘';if(s.includes('форум'))return '◈';if(s.includes('дизайн'))return '✳';return '✦'}
function card(p){return '<article class="service"><div class="gallery" data-gallery="'+p.id+'"><span class="glyph">'+iconFor(p.category)+'</span></div><div class="category">'+esc(p.category)+'</div><h3>'+esc(p.title)+'</h3><p>'+esc(p.description)+'</p><div class="price">'+esc(p.price)+'</div><button class="secondary" data-order="'+p.id+'">Подробнее <span>↗</span></button></article>'}
function renderProducts(){ $('catalog').innerHTML=products.map(card).join('')||'<div class="empty">Скоро здесь появятся услуги.</div>'; $('featured').innerHTML=products.slice(0,2).map(card).join('')||'<div class="empty">Каталог обновляется.</div>';const nodes=document.querySelectorAll('[data-gallery]');if('IntersectionObserver'in window){const io=new IntersectionObserver(entries=>entries.forEach(e=>{if(e.isIntersecting){loadProductImages(Number(e.target.dataset.gallery));io.unobserve(e.target)}}),{rootMargin:'180px'});nodes.forEach(n=>io.observe(n))}else{products.forEach(p=>loadProductImages(p.id))}}
async function loadProductImages(pid){if(imageCache[pid]||imageLoading.has(pid))return;imageLoading.add(pid);try{const d=await api('/api/products/'+pid+'/images');imageCache[pid]=d.items||[];document.querySelectorAll('[data-gallery="'+pid+'"]').forEach(el=>paintGallery(el,pid,0));const thumbs=$('thumbs-'+pid);if(thumbs)thumbs.innerHTML=(imageCache[pid]||[]).map(x=>'<span><img class="photo-thumb" src="data:'+x.type+';base64,'+x.data+'"><button class="secondary" data-delphoto="'+pid+':'+x.id+'">×</button></span>').join('')}catch(e){console.warn('gallery load',e)}finally{imageLoading.delete(pid)}}function paintGallery(el,pid,index){const arr=imageCache[pid]||[];if(!arr.length){el.innerHTML='<span class="glyph">✦</span>';return}index=(index+arr.length)%arr.length;el.dataset.index=index;const x=arr[index];el.innerHTML='<img src="data:'+x.type+';base64,'+x.data+'">'+(arr.length>1?'<button class="gallery-prev" data-galleryprev="'+pid+'">‹</button><button class="gallery-next" data-gallerynext="'+pid+'">›</button><span class="gallery-count">'+(index+1)+' / '+arr.length+'</span>':'')}async function uploadProductImages(pid,files){if(!files||!files.length)return;const fd=new FormData();Array.from(files).slice(0,8).forEach(f=>fd.append('files',f));await api('/api/admin/products/'+pid+'/images',{method:'POST',body:fd});await loadProductImages(pid)}
async function loadProducts(){const d=await api('/api/products');products=d.items||[];renderProducts()}
function openOrder(id){selected=products.find(p=>Number(p.id)===Number(id));if(!selected)return; $('modalTitle').textContent=selected.title;$('modalDesc').textContent=(selected.description||'')+' · '+selected.price;$('orderComment').value='';$('orderModal').classList.add('open');if(tg&&tg.HapticFeedback)tg.HapticFeedback.impactOccurred('light')}
function closeModal(){$('orderModal').classList.remove('open')}
async function createOrder(){if(!selected)return;try{const d=await api('/api/orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:selected.id,comment:$('orderComment').value})});closeModal();toast('Заявка #'+d.id+' создана');if(tg&&tg.openLink)tg.openLink(d.payment_url);else window.open(d.payment_url,'_blank');go('orders')}catch(e){toast(e.message)}}
async function loadProfile(){try{const d=await api('/api/profile'),u=d.user||{},name=[u.first_name,u.last_name].filter(Boolean).join(' ')||'Пользователь';$('profileCard').innerHTML='<div class="profilehead"><div class="avatar">'+esc(name.slice(0,1).toUpperCase())+'</div><div><b>'+esc(name)+'</b><div class="muted">@'+esc(u.username||'telegram-user')+'</div></div></div><div class="wallet"><small>МОЙ БАЛАНС</small><strong>'+esc(d.balance)+' ₽</strong><p class="muted">Баланс аккаунта STUDIO DLC</p></div><div class="profilemeta">Telegram ID · '+esc(u.id)+'</div><button class="secondary wide" data-go="orders">Мои заказы ↗</button>';$('profileAccess').innerHTML=(isOwner?'<div class="access-card owner-access"><div><b>Панель владельца</b><small>Управление студией, заказами и ролями</small></div><button class="primary" data-go="admin">Открыть ↗</button></div>':'')+(isStaff?'<div class="access-card"><div><b>Рабочее пространство</b><small>Обращения и задачи команды</small></div><button class="secondary" data-go="staff">Открыть ↗</button></div>':'');$('staffShortcut').hidden=!isStaff}catch(e){$('profileCard').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function loadOrders(){
 try{
  const d=await api('/api/my-orders');
  const rows=d.items||[];
  $('myorders').innerHTML=rows.map(o=>{
   const status=({awaiting_review:'Ожидает проверки',paid:'Оплачен',rejected:'Отклонён',completed:'Заказ выдан'})[o.status]||'В обработке';
   let extra='';
   if(o.status==='completed'){
    extra=o.reviewed
     ? '<div class="notice">Отзыв уже отправлен. Спасибо!</div>'
     : '<div class="reviewbox"><b>Заказ выдан! Оставь отзыв</b><select class="field" id="rating-'+o.id+'"><option value="5">★★★★★ — Отлично</option><option value="4">★★★★ — Хорошо</option><option value="3">★★★ — Нормально</option><option value="2">★★ — Плохо</option><option value="1">★ — Очень плохо</option></select><textarea class="field" id="review-'+o.id+'" placeholder="Расскажи о результате..."></textarea><button class="primary wide" data-review="'+o.id+'">Отправить отзыв ↗</button></div>';
   } else {
    extra='<div class="muted">Заказ в обработке</div>';
   }
   if(o.status==='awaiting_review'){
    extra+='<input class="field" type="file" id="receipt-'+o.id+'" accept="image/png,image/jpeg,image/webp"><button class="secondary wide" data-upload="'+o.id+'">Прикрепить скриншот оплаты</button>';
   }
   return '<div class="order"><div class="orderhead"><b>#'+o.id+' · '+esc(o.product_title)+'</b><span class="badge">'+status+'</span></div><p class="muted">'+esc(o.price)+' · '+new Date(o.created_at).toLocaleString('ru-RU')+'</p>'+extra+'</div>';
  }).join('')||'<div class="empty">Пока нет заказов.<br>Выбери услугу в каталоге.</div>';
 }catch(e){
  $('myorders').innerHTML='<div class="notice">'+esc(e.message)+'</div>';
 }
}
async function submitReview(id){const body=$('review-'+id).value.trim(),rating=Number($('rating-'+id).value);try{const d=await api('/api/orders/'+id+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rating,body})});toast(d.published?'Отзыв отправлен в @logovotziv':'Отзыв сохранён, публикация в Telegram не подтверждена');loadOrders()}catch(e){toast(e.message)}}
async function upload(id){const input=$('receipt-'+id),file=input&&input.files[0];if(!file){toast('Сначала выбери изображение');return}const fd=new FormData();fd.append('file',file);try{await api('/api/orders/'+id+'/receipt',{method:'POST',body:fd});toast('Скриншот отправлен владельцу');loadOrders()}catch(e){toast(e.message)}}
async function loadForum(){try{const d=await api('/api/forum');$('posts').innerHTML=(d.items||[]).map(p=>'<article class="post"><b>@'+esc(p.username||'участник')+'</b><small> · '+new Date(p.created_at).toLocaleString('ru-RU')+'</small><p>'+esc(p.body)+'</p></article>').join('')||'<div class="empty">Здесь пока тихо. Будь первым, кто начнёт разговор.</div>'}catch(e){$('posts').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function sendPost(){const body=$('postBody').value.trim();if(!body){toast('Напиши сообщение');return}try{await api('/api/forum',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body})});$('postBody').value='';toast('Публикация добавлена');loadForum()}catch(e){toast(e.message)}}
async function addProduct(){const data={title:$('pt').value.trim(),price:$('pp').value.trim(),category:$('pc').value.trim(),description:$('pd').value.trim()};if(!data.title){toast('Укажи название услуги');return}try{const made=await api('/api/admin/products',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const pics=$('newPhotos').files;if(pics.length)await uploadProductImages(made.id,pics);['pt','pp','pc','pd','newPhotos'].forEach(id=>$(id).value='');await loadProducts();toast('Услуга добавлена');loadAdmin()}catch(e){toast(e.message)}}
async function loadAdmin(){try{const a=await api('/api/admin/orders');$('adminorders').innerHTML=(a.items||[]).map(o=>'<div class="order"><div class="orderhead"><b>#'+o.id+' · '+esc(o.product_title)+'</b><span class="badge">'+esc(o.status)+'</span></div><p class="muted">'+esc(o.price)+' · @'+esc(o.username)+' · ID '+o.user_id+'</p>'+(o.has_receipt?'<button class="secondary wide" data-receipt="'+o.id+'">Посмотреть чек</button>':'<p class="muted">Чек ещё не загружен</p>')+'<div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:8px"><button class="secondary" data-status="'+o.id+'" data-value="paid">Оплата подтверждена</button><button class="secondary" data-status="'+o.id+'" data-value="rejected">Отклонить</button><button class="secondary" data-status="'+o.id+'" data-value="completed">Заказ выдан</button></div></div>').join('')||'<div class="empty">Новых заказов пока нет.</div>';$('adminproducts').innerHTML=products.map(p=>'<div class="order"><div class="orderhead"><b>'+esc(p.title)+'</b><span class="badge">'+esc(p.price)+'</span></div><div class="photo-tools"><input class="field" style="margin:0" type="file" id="photos-'+p.id+'" accept="image/jpeg,image/png,image/webp" multiple><button class="secondary" data-addphotos="'+p.id+'">＋ Фото</button></div><div id="thumbs-'+p.id+'" class="photo-tools"></div><div style="display:flex;gap:8px;margin-top:10px"><button class="secondary" data-edit="'+p.id+'">Изменить</button><button class="secondary" data-delete="'+p.id+'">Удалить</button></div></div>').join('')}catch(e){$('adminorders').innerHTML='<div class="notice">Не удалось загрузить заказы: '+esc(e.message)+'</div>';$('adminproducts').innerHTML='<div class="notice">Проверь соединение и открой панель ещё раз.</div>'}}

async function createTicket(){const subject=$('ticketSubject').value.trim(),body=$('ticketBody').value.trim();try{await api('/api/support/tickets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subject,body})});$('ticketSubject').value='';$('ticketBody').value='';toast('Обращение отправлено');loadMyTickets()}catch(e){toast(e.message)}}
async function loadMyTickets(){try{const d=await api('/api/support/tickets');$('myTickets').innerHTML=(d.items||[]).map(t=>'<div class="order"><div class="orderhead"><b>#'+t.id+' · '+esc(t.subject)+'</b><span class="badge">'+(t.status==='open'?'Открыто':'Закрыто')+'</span></div><button class="secondary wide" data-action="ticket-open" data-ticket="'+t.id+'">Открыть переписку</button></div>').join('')||'<div class="empty">Обращений пока нет.</div>'}catch(e){$('myTickets').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function openTicket(id){activeTicket=Number(id);try{const d=await api('/api/support/tickets/'+activeTicket+'/messages');$('ticketChat').innerHTML='<div class="panel">'+(d.items||[]).map(m=>'<div class="post"><b>'+(m.sender_role==='user'?'Вы':'Поддержка')+'</b><p>'+esc(m.body)+'</p></div>').join('')+'<textarea id="ticketReply" class="field" placeholder="Ваш ответ..."></textarea><button class="primary wide" data-action="ticket-send">Отправить</button></div>'}catch(e){toast(e.message)}}
async function sendTicketReply(){const body=$('ticketReply').value.trim();try{await api('/api/support/tickets/'+activeTicket+'/messages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body})});openTicket(activeTicket);loadMyTickets()}catch(e){toast(e.message)}}
async function loadStaffTickets(){try{const d=await api('/api/staff/tickets');$('staffRoleLabel').textContent='Твоя роль: '+({owner:'Владелец',deputy:'Заместитель',support:'Поддержка'}[d.role]||d.role);$('staffTickets').innerHTML=(d.items||[]).map(t=>'<div class="order"><div class="orderhead"><b>#'+t.id+' · '+esc(t.subject)+'</b><span class="badge">'+(t.status==='open'?'Открыто':'Закрыто')+'</span></div><p class="muted">@'+esc(t.username||'без username')+' · ID '+t.user_id+'</p><button class="secondary wide" data-action="staff-ticket-open" data-ticket="'+t.id+'">Открыть обращение</button></div>').join('')||'<div class="empty">Новых обращений нет.</div>'}catch(e){$('staffTickets').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function openStaffTicket(id){activeStaffTicket=Number(id);try{const d=await api('/api/staff/tickets/'+activeStaffTicket+'/messages');$('staffChat').innerHTML='<div class="panel">'+(d.items||[]).map(m=>'<div class="post"><b>'+esc(m.sender_role)+'</b><p>'+esc(m.body)+'</p></div>').join('')+'<textarea id="staffReplyBody" class="field" placeholder="Ответ клиенту..."></textarea><button class="primary wide" data-action="staff-reply">Отправить ответ</button><button class="secondary wide" data-action="ticket-close">Закрыть обращение</button></div>'}catch(e){toast(e.message)}}
async function sendStaffReply(){const body=$('staffReplyBody').value.trim();try{await api('/api/staff/tickets/'+activeStaffTicket+'/messages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body})});openStaffTicket(activeStaffTicket);loadStaffTickets();toast('Ответ отправлен')}catch(e){toast(e.message)}}
async function closeStaffTicket(){try{await api('/api/staff/tickets/'+activeStaffTicket+'/close',{method:'POST'});$('staffChat').innerHTML='';loadStaffTickets();toast('Обращение закрыто')}catch(e){toast(e.message)}}
async function loadRoles(){if(!isOwner)return;try{const d=await api('/api/owner/roles');$('roleList').innerHTML=(d.items||[]).map(x=>'<div class="order"><div class="orderhead"><b>'+esc(x.username?'@'+x.username:'Telegram ID '+x.user_id)+'</b><span class="badge">'+({deputy:'Заместитель',support:'Поддержка',moderator:'Модератор'}[x.role]||x.role)+'</span></div><p class="muted">ID: '+x.user_id+'</p><button class="secondary" data-revoke-role="'+x.user_id+'">Снять роль</button></div>').join('')||'<div class="empty">Сотрудники ещё не назначены.</div>'}catch(e){$('roleList').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function grantRole(){const user_id=$('roleUserId').value.trim(),role=$('roleSelect').value;if(!/^[0-9]{5,20}$/.test(user_id)){toast('Проверь Telegram ID');return}try{await api('/api/owner/roles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id,role})});$('roleUserId').value='';toast('Роль назначена');loadRoles()}catch(e){toast(e.message)}}
async function revokeRole(id){try{await api('/api/owner/roles/'+id,{method:'DELETE'});toast('Роль снята');loadRoles()}catch(e){toast(e.message)}}

async function setStatus(id,status){try{await api('/api/admin/orders/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});toast('Статус заказа обновлён');loadAdmin()}catch(e){toast(e.message)}}
async function receiptView(id){try{const x=await api('/api/admin/orders/'+id+'/receipt');const w=window.open();if(w)w.document.write('<title>Чек заказа #'+id+'</title><body style="margin:0;background:#111;display:grid;place-items:center"><img style="max-width:100%;max-height:100vh" src="data:'+x.type+';base64,'+x.data+'"></body>');else toast('Разреши всплывающие окна для просмотра чека')}catch(e){toast(e.message)}}
async function editProduct(id){const p=products.find(x=>Number(x.id)===Number(id));if(!p)return;const title=prompt('Название услуги',p.title);if(title===null)return;const price=prompt('Цена',p.price),description=prompt('Описание',p.description),category=prompt('Категория',p.category);try{await api('/api/admin/products/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,price,description,category,active:true})});await loadProducts();loadAdmin();toast('Услуга обновлена')}catch(e){toast(e.message)}}
async function deleteProduct(id){if(!confirm('Удалить услугу из каталога?'))return;try{await api('/api/admin/products/'+id,{method:'DELETE'});await loadProducts();loadAdmin();toast('Услуга удалена')}catch(e){toast(e.message)}}
function routeFromButton(b){if(!b||b.disabled)return;const g=b.dataset.go;if(g){go(g);return}if(b.dataset.order){openOrder(b.dataset.order);return}const a=b.dataset.action;if(a==='close-modal')closeModal();else if(a==='create-order')createOrder();else if(a==='send-post')sendPost();else if(a==='add-product')addProduct();else if(a==='create-ticket')createTicket();else if(a==='ticket-open')openTicket(b.dataset.ticket);else if(a==='ticket-send')sendTicketReply();else if(a==='staff-ticket-open')openStaffTicket(b.dataset.ticket);else if(a==='staff-reply')sendStaffReply();else if(a==='ticket-close')closeStaffTicket();else if(a==='toggle-more')toggleMore();else if(a==='close-more')closeMore();else if(a==='grant-role')grantRole();else if(b.dataset.revokeRole)revokeRole(b.dataset.revokeRole);else if(b.dataset.upload)upload(b.dataset.upload);else if(b.dataset.review)submitReview(b.dataset.review);else if(b.dataset.status)setStatus(b.dataset.status,b.dataset.value);else if(b.dataset.receipt)receiptView(b.dataset.receipt);else if(b.dataset.edit)editProduct(b.dataset.edit);else if(b.dataset.delete)deleteProduct(b.dataset.delete);else if(b.dataset.addphotos){const input=$('photos-'+b.dataset.addphotos);if(input&&input.files.length)uploadProductImages(Number(b.dataset.addphotos),input.files).then(()=>toast('Фотографии добавлены')).catch(e=>toast(e.message));else toast('Выбери фотографии')}else if(b.dataset.delphoto){const [pid,iid]=b.dataset.delphoto.split(':');api('/api/admin/products/'+pid+'/images/'+iid,{method:'DELETE'}).then(()=>loadProductImages(Number(pid))).then(()=>toast('Фото удалено')).catch(e=>toast(e.message))}else if(b.dataset.galleryprev||b.dataset.gallerynext){const pid=Number(b.dataset.galleryprev||b.dataset.gallerynext);const el=b.closest('.gallery');paintGallery(el,pid,(Number(el.dataset.index)||0)+(b.dataset.gallerynext?1:-1))}}
document.addEventListener('click',e=>{const t=e.target instanceof Element?e.target:null;if(!t)return;const b=t.closest('button');if(!b)return;routeFromButton(b)});
document.addEventListener('touchstart',e=>{const g=e.target.closest('.gallery');if(g)g.dataset.touchx=e.touches[0].clientX},{passive:true});document.addEventListener('touchend',e=>{const g=e.target.closest('.gallery');if(!g||g.dataset.touchx===undefined)return;const dx=e.changedTouches[0].clientX-Number(g.dataset.touchx);delete g.dataset.touchx;if(Math.abs(dx)>35){const pid=Number(g.dataset.gallery);paintGallery(g,pid,(Number(g.dataset.index)||0)+(dx<0?1:-1))}},{passive:true});
function bindMotion(){const obs=('IntersectionObserver'in window)?new IntersectionObserver(es=>es.forEach(x=>{if(x.isIntersecting){x.target.classList.add('visible');obs.unobserve(x.target)}}),{threshold:.08}):null;document.querySelectorAll('.hero,.stats,.sectionhead,.service,.panel,.order').forEach((el,i)=>{el.classList.add('reveal');el.style.transitionDelay=(Math.min(i%5,4)*55)+'ms';if(obs)obs.observe(el);else el.classList.add('visible')})}
async function checkSubscription(showMessage){
 const b=$('checkSub'),status=$('subStatus');
 if(b){b.disabled=true;b.classList.add('loading');b.textContent='Проверяем подписку…'}
 if(status)status.textContent='Проверяем участие в канале';
 try{
  const s=await api('/api/session');
  if(!s.subscribed){
   if(status)status.textContent='Подписка пока не найдена. Подпишись на канал и попробуй ещё раз.';
   if(showMessage)toast('Подписка пока не найдена');
   return false;
  }
  subscribed=true;isOwner=!!s.owner;isStaff=!!s.staff;staffRole=s.role||'user';
  $('admin').hidden=!isOwner;$('staff').hidden=!isStaff;$('staffShortcut').hidden=!isStaff;
  document.body.classList.remove('locked');
  $('gate').classList.add('gate-success');
  $('gate').innerHTML='<div class="gate-check">✓</div><div class="gate-kicker">ACCESS GRANTED</div><h2>Добро пожаловать в STUDIO DLC</h2><p>Подписка подтверждена. Открываем каталог услуг…</p>';
  if(isOwner){$('admin').hidden=false;}if(isStaff){$('staffNav').hidden=false;$('staff').hidden=false;}
  products=s.products||[];renderProducts();
  $('gate').hidden=true;
  if(showMessage)toast('Доступ открыт');
  return true;
 }catch(err){
  if(status)status.textContent=err.message||'Не удалось проверить подписку. Попробуй ещё раз.';
  if(showMessage)toast(err.message||'Ошибка проверки');
  return false;
 }finally{
  if(b){b.disabled=false;b.classList.remove('loading');b.textContent='Я подписался — проверить'}
 }
}
document.addEventListener('click',e=>{const t=e.target instanceof Element?e.target:null;if(t&&t.closest('#checkSub'))checkSubscription(true)});
window.addEventListener('focus',()=>{if(!subscribed&&tg&&tg.initData)checkSubscription(false)});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&!subscribed&&tg&&tg.initData)checkSubscription(false)});
window.addEventListener('error',e=>{if(e.message)toast('Ошибка интерфейса: '+e.message.slice(0,100))});

$('orderModal').addEventListener('click',e=>{if(e.target===$('orderModal'))closeModal()});

let swipeStart=null;
document.querySelector('.shell').addEventListener('touchstart',e=>{if(e.touches.length!==1||e.target.closest('input,textarea,select,button,.gallery,.modal,.nav')){swipeStart=null;return}swipeStart={x:e.touches[0].clientX,y:e.touches[0].clientY}},{passive:true});
document.querySelector('.shell').addEventListener('touchend',e=>{if(!swipeStart||!e.changedTouches.length)return;const dx=e.changedTouches[0].clientX-swipeStart.x,dy=e.changedTouches[0].clientY-swipeStart.y;swipeStart=null;if(Math.abs(dx)<65||Math.abs(dy)>55||Math.abs(dx)<Math.abs(dy)*1.25)return;const tabs=['home','shop','profile','orders','support'];if(isStaff)tabs.push('staff');if(isOwner)tabs.push('admin');const active=document.querySelector('.view.active');const i=active?tabs.indexOf(active.id):-1;const next=tabs[i+(dx<0?1:-1)];if(next)go(next)},{passive:true});

async function boot(){
 if(!tg||!tg.initData){
  document.body.classList.add('locked');
  $('userStatus').textContent='ОТКРОЙ В TELEGRAM';
  $('gate').hidden=false;
  $('gate').innerHTML='<div class="gate-kicker">STUDIO DLC · PRIVATE ACCESS</div><h2>Открой приложение в Telegram</h2><p>Авторизация и проверка подписки доступны внутри Telegram Mini App.</p><a class="primary gate-link" href="https://t.me/studiodlc">Открыть Telegram ↗</a>';
  return;
 }
 try{
  const s=await api('/api/session');
  $('userStatus').textContent=s.user.first_name||'WELCOME';
  isOwner=!!s.owner;isStaff=!!s.staff;staffRole=s.role||'user';subscribed=!!s.subscribed;
  $('adminNav').hidden=!isOwner;$('admin').hidden=!isOwner;$('staffNav').hidden=!isStaff;$('staff').hidden=!isStaff;
  if(!subscribed){
   document.body.classList.add('locked');
   $('gate').hidden=false;
   $('gate').innerHTML='<div class="gate-orb"><span>S</span></div><div class="gate-kicker">STUDIO DLC · MEMBER ACCESS</div><h2>Сначала — подписка</h2><p>Подпишись на наш Telegram-канал, чтобы открыть каталог, оформить заказ и пользоваться личным кабинетом.</p><a class="primary gate-link" href="https://t.me/studiodlc" target="_blank" rel="noopener">Подписаться на канал <span>↗</span></a><button class="secondary gate-check-btn" id="checkSub">Я подписался — проверить</button><div class="gate-status" id="subStatus">После подписки вернись сюда — доступ откроется автоматически.</div>';
   return;
  }
  document.body.classList.remove('locked');
  if(isOwner){$('adminNav').hidden=false;$('admin').hidden=false;}
  products=s.products||[];renderProducts();
 }catch(e){
  $('gate').hidden=false;
  $('gate').innerHTML='<div class="gate-kicker">CONNECTION</div><h2>Не удалось загрузить приложение</h2><p>'+esc(e.message)+'</p><button class="secondary" onclick="location.reload()">Повторить</button>';
  toast(e.message);
 }
}
boot().then(()=>bindMotion()).catch(e=>toast('Не удалось запустить приложение: '+e.message));
})();
</script></body></html>"""
