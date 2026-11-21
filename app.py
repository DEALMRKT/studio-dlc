import os, hmac, hashlib, json, time, urllib.parse, base64
import requests, psycopg2
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
def require_subscription(u):
 if not subscribed(u['id']): raise HTTPException(403,'Сначала подпишитесь на @studiodlc и нажмите «Проверить подписку».')
 return u
def subscribed(uid):
 try:
  x=requests.get("https://api.telegram.org/bot"+TOKEN+"/getChatMember",params={"chat_id":CHANNEL,"user_id":uid},timeout=8).json()
  return x.get("ok") and x.get("result",{}).get("status") in ("creator","administrator","member")
 except: return False
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
 u=user(r); return {"user":u,"owner":str(u["id"])==OWNER,"subscribed":subscribed(u["id"])}
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
 sent=tg_call("sendMessage",{"chat_id":"@logovotziv","text":"⭐ Отзыв STUDIO DLC\\nЗаказ #"+str(oid)+" · "+o["product_title"]+"\\nОценка: "+("⭐"*rating)+"\\nОт: "+who+"\\n\\n"+body})
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
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=Space+Grotesk:wght@500;600;700&display=swap');
:root{--bg:#090811;--panel:#12111d;--panel2:#191626;--line:rgba(255,255,255,.09);--muted:#9692a9;--white:#f7f4ff;--violet:#a78bfa;--cyan:#76e4df;--pink:#e6a5ff}
*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;background:var(--bg);color:var(--white);font-family:Manrope,system-ui,sans-serif;overflow-x:hidden;padding-bottom:105px}
body:before{content:"";position:fixed;inset:0;pointer-events:none;z-index:-2;background:radial-gradient(ellipse at 8% 4%,rgba(112,65,190,.25),transparent 34%),radial-gradient(ellipse at 92% 24%,rgba(34,151,155,.13),transparent 32%),linear-gradient(180deg,#0b0914,#090811 70%)}
.noise{position:fixed;inset:0;pointer-events:none;opacity:.12;z-index:-1;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.22'/%3E%3C/svg%3E")}
.aurora{position:fixed;width:340px;height:340px;border-radius:50%;filter:blur(90px);opacity:.18;pointer-events:none;z-index:-1;animation:float 13s ease-in-out infinite alternate}.a1{background:#8b5cf6;top:10%;left:-220px}.a2{background:#27c6bb;right:-250px;top:48%;animation-delay:-6s}
@keyframes float{from{transform:translate3d(0,-20px,0) scale(.9)}to{transform:translate3d(70px,90px,0) scale(1.2)}}
input[type=file]::file-selector-button{appearance:none;border:1px solid #8e7ac8;background:linear-gradient(110deg,#c8b4ff,#83e5df);color:#171222;border-radius:11px;padding:10px 14px;margin:0 12px 0 0;font:800 12px Manrope;cursor:pointer}input[type=file]{color:#aaa4bc;font-size:12px}@keyframes rise{from{opacity:0;transform:translateY(18px) scale(.985);filter:blur(5px)}to{opacity:1;transform:none;filter:blur(0)}}
@keyframes shimmer{0%{background-position:0% 50%}100%{background-position:200% 50%}}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(167,139,250,.25)}50%{box-shadow:0 0 0 9px rgba(167,139,250,0)}}
.shell{width:min(100%,760px);margin:auto;padding:18px 17px 30px}.topbar{height:48px;display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}
.brand{display:flex;align-items:center;gap:10px;font:700 17px 'Space Grotesk',sans-serif;letter-spacing:1.7px}.brandmark{width:31px;height:31px;border-radius:10px;display:grid;place-items:center;background:linear-gradient(135deg,#b69aff,#5e43bd 55%,#42c9c2);box-shadow:0 5px 25px #8b5cf655;color:#090811;font-size:17px}
.brand em{font-style:normal;color:#b9a4ff}.status{border:1px solid var(--line);border-radius:99px;padding:8px 11px;color:#aaa5bb;font-size:10px;letter-spacing:1.2px;background:#ffffff06}
.view{display:none}.view.active{display:block;animation:rise .48s cubic-bezier(.2,.75,.2,1) both}
.hero{position:relative;overflow:hidden;border:1px solid rgba(192,169,255,.2);border-radius:29px;padding:28px 24px 25px;min-height:320px;background:linear-gradient(120deg,rgba(42,30,72,.96),rgba(20,23,37,.92) 58%,rgba(13,48,54,.88));box-shadow:0 24px 90px #0008,inset 0 1px 0 #ffffff17}
.hero:before{content:"";position:absolute;width:250px;height:250px;right:-65px;top:-95px;border:1px solid #d6c5ff33;border-radius:50%;box-shadow:0 0 0 28px #ffffff05,0 0 0 58px #ffffff04,0 0 80px #a78bfa33;animation:orbit 22s linear infinite}
.hero:after{content:"";position:absolute;width:160px;height:160px;right:24px;bottom:-100px;border-radius:50%;background:#6ee7df22;filter:blur(20px)}
@keyframes orbit{to{transform:rotate(360deg)}}
.eyebrow{font-size:10px;letter-spacing:2.6px;color:#c9b7ff;font-weight:800;display:flex;align-items:center;gap:9px}.eyebrow:before{content:"";width:20px;height:1px;background:var(--cyan)}
.hero h1{position:relative;z-index:1;font:700 clamp(38px,9vw,58px)/.98 'Space Grotesk',sans-serif;letter-spacing:-2.5px;margin:23px 0 14px;max-width:470px}.gradient{background:linear-gradient(100deg,#fff 4%,#c9b5ff 40%,#83e8df 85%);background-size:200% auto;color:transparent;background-clip:text;animation:shimmer 7s linear infinite}
.hero p{position:relative;z-index:1;color:#b6b1c5;font-size:13px;line-height:1.75;max-width:370px;margin:0 0 23px}
.primary{border:0;color:#110d1b;background:linear-gradient(105deg,#c8b4ff,#a78bfa 48%,#83e5df);padding:14px 19px;border-radius:14px;font:800 12px Manrope;letter-spacing:.2px;box-shadow:0 9px 28px #9c7af633;transition:transform .22s,filter .22s,box-shadow .22s;cursor:pointer}
.primary:active,.secondary:active,.navbtn:active{transform:scale(.96)}.primary:hover{filter:brightness(1.08);box-shadow:0 12px 35px #9c7af655}
.secondary{border:1px solid var(--line);background:#ffffff08;color:#e8e2f8;border-radius:13px;padding:12px 15px;font:700 12px Manrope;cursor:pointer;transition:.2s}
.sectionhead{display:flex;align-items:end;justify-content:space-between;margin:29px 2px 14px}.sectionhead h2{font:700 21px 'Space Grotesk';margin:0;letter-spacing:-.5px}.sectionhead small{color:#79748d;font-size:10px;letter-spacing:1.3px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:14px}.stat{padding:13px 11px;border:1px solid var(--line);border-radius:16px;background:#ffffff05}.stat strong{display:block;font:700 19px 'Space Grotesk';color:#e5d9ff}.stat span{font-size:9px;color:#8e899f;letter-spacing:.8px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.service{position:relative;overflow:hidden;border:1px solid var(--line);border-radius:21px;padding:15px;background:linear-gradient(145deg,#1b1929,#11111a 75%);min-width:0;transition:transform .3s,border-color .3s,box-shadow .3s}.service:before{content:"";position:absolute;inset:0;background:radial-gradient(circle at 90% 0%,rgba(167,139,250,.14),transparent 44%);opacity:.8;pointer-events:none}.service:hover{transform:translateY(-4px);border-color:#b69aff55;box-shadow:0 14px 35px #0005}
.serviceart{height:112px;border-radius:15px;margin-bottom:14px;position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#33264e,#172d3a)}
.serviceart:before,.serviceart:after{content:"";position:absolute;border:1px solid #ffffff25;border-radius:50%;width:100px;height:100px;transform:rotate(-30deg);box-shadow:0 0 0 16px #ffffff06,0 0 35px #b59aff33}.serviceart:after{width:55px;height:55px;border-color:#9dece055;transform:translate(27px,-17px)}
.glyph{font-size:35px;z-index:1;filter:drop-shadow(0 5px 14px #b69aff88)}.category{color:#b9a6f5;font-size:9px;letter-spacing:1.4px;text-transform:uppercase;font-weight:800}.service h3{font:700 15px 'Space Grotesk';margin:8px 0 6px}.service p{color:#9994a8;font-size:11px;line-height:1.55;min-height:36px;margin:0}.price{font-weight:800;color:#e0d4ff;font-size:13px;margin:14px 0 11px}.service .secondary{width:100%;padding:11px}
.panel{border:1px solid var(--line);border-radius:20px;background:linear-gradient(145deg,#191725,#111019);padding:17px;margin:12px 0}
.muted{color:var(--muted);font-size:12px;line-height:1.6}.field{display:block;width:100%;background:#0c0b13;border:1px solid #ffffff17;border-radius:13px;padding:13px 14px;color:white;font:13px Manrope;margin:9px 0;outline:none;transition:border .2s,box-shadow .2s}.field:focus{border-color:#a78bfa88;box-shadow:0 0 0 3px #a78bfa16}.wide{width:100%;margin-top:8px}
.order{border:1px solid var(--line);border-radius:17px;background:#ffffff06;padding:15px;margin:10px 0}.orderhead{display:flex;justify-content:space-between;gap:8px}.order b{font-size:13px}.badge{font-size:9px;color:#d9cbff;background:#a78bfa1c;border:1px solid #a78bfa30;padding:5px 8px;border-radius:99px;white-space:nowrap}.empty{border:1px dashed #ffffff20;border-radius:17px;text-align:center;padding:27px 15px;color:#918ba1;font-size:12px;line-height:1.7}
.post{padding:14px 2px;border-bottom:1px solid var(--line)}.post:last-child{border:0}.post p{white-space:pre-wrap;color:#d8d3e2;font-size:13px;line-height:1.6}.post small{color:#8f899f}
.nav{position:fixed;z-index:10;bottom:0;left:0;right:0;padding:9px 8px calc(9px + env(safe-area-inset-bottom));display:flex;justify-content:center;gap:4px;background:rgba(12,10,19,.88);backdrop-filter:blur(24px);border-top:1px solid #ffffff12}.navinner{width:min(100%,620px);display:flex;justify-content:space-around}.navbtn{border:0;background:transparent;color:#817b91;min-width:54px;padding:5px 8px;display:flex;flex-direction:column;align-items:center;gap:4px;font:600 9px Manrope;cursor:pointer;transition:.2s}.navbtn .ico{font-size:19px;line-height:1.2}.navbtn.active{color:#d6c5ff}.navbtn.active .ico{filter:drop-shadow(0 0 9px #b69affaa);transform:translateY(-2px)}
.modalback{display:none;position:fixed;inset:0;z-index:20;background:#05040bd9;backdrop-filter:blur(13px);padding:18px;align-items:end;justify-content:center}.modalback.open{display:flex;animation:fade .2s}.modal{width:min(100%,500px);border:1px solid #c2aaff38;border-radius:25px 25px 19px 19px;background:linear-gradient(145deg,#211b31,#111019);padding:22px;box-shadow:0 20px 90px #000b;animation:sheet .3s cubic-bezier(.2,.8,.2,1)}@keyframes fade{from{opacity:0}to{opacity:1}}@keyframes sheet{from{transform:translateY(35px);opacity:.6}to{transform:none;opacity:1}}
.modal h2{font:700 23px 'Space Grotesk';margin:7px 0}.modalclose{float:right;border:0;background:#ffffff12;color:#fff;border-radius:10px;width:32px;height:32px;font-size:17px}
.notice{border:1px solid #f2c46c35;background:#f2c46c10;color:#f0d49b;border-radius:15px;padding:13px;font-size:12px;line-height:1.6;margin:12px 0}.notice a{color:#d4c2ff}
.toast{position:fixed;z-index:30;left:50%;bottom:100px;transform:translate(-50%,15px);opacity:0;pointer-events:none;background:#262137;border:1px solid #ffffff20;color:#fff;border-radius:13px;padding:12px 16px;font-size:12px;box-shadow:0 12px 35px #0008;transition:.25s;max-width:90%;text-align:center}.toast.show{opacity:1;transform:translate(-50%,0)}
@media(min-width:650px){.shell{padding-top:28px}.hero{padding:39px;min-height:350px}.grid{gap:15px}.service{padding:18px}.serviceart{height:135px}.modalback{align-items:center}.modal{border-radius:25px}}
@media(max-width:370px){.shell{padding-left:12px;padding-right:12px}.hero{padding:22px 18px}.grid{gap:8px}.service{padding:11px}.navbtn{min-width:48px;padding-inline:4px}}
/* cinematic motion refresh */
:root{--bg:#070710;--panel:#11111d;--violet:#b7a0ff;--cyan:#75f0df;--line:rgba(255,255,255,.11)}
html{background:#070710;scroll-padding-top:20px}body{background:#070710;overscroll-behavior-y:contain;-webkit-tap-highlight-color:transparent}
body:before{background:radial-gradient(ellipse at 15% 0%,rgba(123,79,230,.30),transparent 38%),radial-gradient(ellipse at 100% 35%,rgba(40,204,192,.19),transparent 34%),radial-gradient(ellipse at 45% 100%,rgba(83,61,145,.15),transparent 38%),linear-gradient(180deg,#090813 0%,#070710 65%,#0b0b16 100%)}
.aurora{width:440px;height:440px;filter:blur(105px);opacity:.24;mix-blend-mode:screen}.a1{animation:auroraDrift 16s ease-in-out infinite alternate}.a2{animation:auroraDrift 19s ease-in-out -7s infinite alternate-reverse}
@keyframes auroraDrift{0%{transform:translate3d(-12px,-20px,0) scale(.82)}50%{transform:translate3d(70px,35px,0) scale(1.14)}100%{transform:translate3d(15px,100px,0) scale(.96)}}
.shell{padding-top:max(18px,env(safe-area-inset-top));padding-bottom:calc(34px + env(safe-area-inset-bottom))}
.topbar{position:sticky;top:0;z-index:8;margin:0 -17px 20px;padding:9px 17px;background:linear-gradient(180deg,rgba(8,8,17,.94),rgba(8,8,17,.72));backdrop-filter:blur(20px);border-bottom:1px solid #ffffff0b}
.brandmark{position:relative;overflow:hidden;box-shadow:0 0 0 1px #ffffff20,0 7px 30px #9475ff55}.brandmark:after{content:"";position:absolute;inset:-60%;background:linear-gradient(110deg,transparent 35%,#ffffff99 50%,transparent 65%);transform:translateX(-70%) rotate(15deg);animation:markSweep 5s ease-in-out infinite}
@keyframes markSweep{0%,55%{transform:translateX(-80%) rotate(15deg)}85%,100%{transform:translateX(80%) rotate(15deg)}}
.hero{min-height:350px;padding:clamp(26px,6vw,42px);border-radius:30px;background:linear-gradient(125deg,rgba(37,28,67,.97),rgba(15,22,38,.96) 56%,rgba(10,43,48,.94));box-shadow:0 28px 100px #0009,inset 0 1px 0 #ffffff20;isolation:isolate}
.hero:before{width:290px;height:290px;right:-55px;top:-115px;border-color:#d8cbff55;box-shadow:0 0 0 1px #ffffff09,0 0 0 34px #ffffff07,0 0 0 70px #ffffff04,0 0 95px #a78bfa55}
.hero:after{width:230px;height:230px;right:0;bottom:-160px;background:#64f4df26;filter:blur(35px)}
.hero h1{font-size:clamp(42px,10vw,68px);line-height:.94;letter-spacing:-3px;text-shadow:0 8px 40px #0006;animation:heroTextIn .85s cubic-bezier(.16,1,.3,1) both}
@keyframes heroTextIn{from{opacity:0;transform:translateY(24px);filter:blur(9px)}to{opacity:1;transform:none;filter:blur(0)}}
.hero p{font-size:14px;max-width:400px;color:#c1bdd0}.eyebrow{letter-spacing:2.2px}
.primary{position:relative;overflow:hidden;isolation:isolate;min-height:46px;border-radius:15px;background:linear-gradient(105deg,#d6c9ff,#a78bfa 48%,#80eee0);box-shadow:0 9px 30px #9c7af644,inset 0 1px 0 #ffffffaa}
.primary:before{content:"";position:absolute;z-index:-1;inset:0;background:linear-gradient(110deg,transparent 20%,#ffffff88 48%,transparent 72%);transform:translateX(-120%);transition:transform .65s}.primary:active:before,.primary:hover:before{transform:translateX(120%)}
.secondary{min-height:43px;background:linear-gradient(135deg,#ffffff0d,#ffffff04);border-color:#ffffff18;box-shadow:inset 0 1px 0 #ffffff0c}
button{touch-action:manipulation;-webkit-user-select:none;user-select:none}button:focus-visible{outline:2px solid #bba8ff;outline-offset:3px}
.grid{gap:13px}.service{border-radius:23px;padding:16px;background:linear-gradient(145deg,rgba(30,27,47,.96),rgba(13,14,24,.98));box-shadow:0 12px 35px #0003,inset 0 1px 0 #ffffff0b;transform:translateZ(0)}
.service:after{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;background:linear-gradient(135deg,#ffffff0c,transparent 35%,transparent 70%,#a78bfa0b);opacity:.7}
.serviceart{height:124px;border:1px solid #ffffff10;background:radial-gradient(circle at 50% 45%,#58438b 0%,#292343 38%,#142a36 100%)}
.serviceart:before{width:116px;height:116px;animation:artOrbit 18s linear infinite}.serviceart:after{animation:artOrbit 12s linear infinite reverse}
@keyframes artOrbit{to{transform:rotate(360deg)}}.glyph{font-size:39px;filter:drop-shadow(0 0 18px #c7aaffaa)}.service h3{font-size:16px}.service .secondary{position:relative;z-index:2}
.sectionhead{margin-top:34px}.sectionhead h2{font-size:23px;letter-spacing:-.8px}
.stat{background:linear-gradient(145deg,#ffffff0b,#ffffff03);border-color:#ffffff12;box-shadow:inset 0 1px 0 #ffffff0a}
.panel,.order{background:linear-gradient(145deg,#19182aee,#10101bee);box-shadow:0 12px 35px #0002,inset 0 1px 0 #ffffff0a}
.field{font-size:16px;min-height:46px}
.nav{padding:10px 8px calc(10px + env(safe-area-inset-bottom));background:rgba(8,8,16,.91);border-top:1px solid #ffffff16;box-shadow:0 -12px 40px #0005}.navinner{gap:3px}
.navbtn{position:relative;min-width:57px;min-height:49px;border-radius:13px;font-size:10px;transition:color .2s,background .25s,transform .25s}.navbtn.active{background:linear-gradient(145deg,#b69aff20,#75e9df0b);color:#eee7ff}
.navbtn.active:before{content:"";position:absolute;top:-10px;left:35%;right:35%;height:2px;border-radius:9px;background:linear-gradient(90deg,#b69aff,#75e9df);box-shadow:0 0 12px #b69aff}.navbtn .ico{font-size:21px;transition:transform .25s}.navbtn.active .ico{transform:translateY(-2px) scale(1.08)}
.modalback{padding:12px 12px calc(12px + env(safe-area-inset-bottom))}.modal{border-radius:27px 27px 20px 20px;background:linear-gradient(145deg,#25203a,#10101b);border-color:#c2aaff45}
.toast{bottom:calc(100px + env(safe-area-inset-bottom));backdrop-filter:blur(18px)}
.view.active{animation:pageIn .42s cubic-bezier(.2,.75,.2,1) both}@keyframes pageIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
.reveal{opacity:0;transform:translateY(18px);transition:opacity .65s ease,transform .65s cubic-bezier(.2,.75,.2,1)}.reveal.visible{opacity:1;transform:none}
@media(hover:hover){.service:hover{transform:translateY(-6px) scale(1.012);border-color:#b69aff66;box-shadow:0 20px 50px #0007}.navbtn:hover{background:#ffffff0b;color:#fff}}
@media(max-width:390px){.navbtn{min-width:49px;font-size:9px}.navbtn .ico{font-size:19px}.hero{min-height:330px}.serviceart{height:105px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.01ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.01ms!important}}

/* STUDIO DLC liquid-blue identity */
:root{--bg:#030814;--violet:#2f8dff;--cyan:#83d5ff;--line:rgba(95,166,255,.16)}
body:before{background:radial-gradient(ellipse at 50% 0%,#063d8040,transparent 48%),radial-gradient(ellipse at 100% 42%,#0058a522,transparent 38%),linear-gradient(180deg,#030714,#020611 75%)}
.hero{background:linear-gradient(125deg,#061b38,#07152b 55%,#062a43);border-color:#267bcb55}
.hero:before{border-color:#62baff55;box-shadow:0 0 0 30px #1688ff0a,0 0 0 65px #1688ff08,0 0 95px #1688ff55}
.gradient{background-image:linear-gradient(100deg,#fff 4%,#a9d8ff 40%,#238fff 85%)}
.brandmark{background:radial-gradient(circle at 30% 20%,#dff7ff,#279cff 40%,#073c8b 78%,#021b45);color:#00152e;box-shadow:0 0 0 1px #80caff88,0 0 22px #178fff88;font-family:'Space Grotesk';font-weight:800;font-size:22px;text-shadow:0 1px 2px #fff9,0 -2px 4px #001a55}
.primary{background:linear-gradient(105deg,#d8f1ff,#5bb8ff 48%,#1268d9);color:#00142c;box-shadow:0 9px 30px #087cff44}
.service{background:linear-gradient(145deg,#0d1c32,#07111f 75%)}
.serviceart,.gallery{background:radial-gradient(circle at 50% 45%,#1262a7 0%,#092b52 45%,#061426 100%)}
.nav{background:rgba(2,8,19,.93)}
.gallery{position:relative;height:150px;border-radius:16px;overflow:hidden;margin-bottom:14px;touch-action:pan-y}
.gallery img{width:100%;height:100%;object-fit:cover;display:block}
.gallery-count{position:absolute;right:9px;bottom:9px;padding:5px 8px;border-radius:20px;background:#020914b8;color:#e9f5ff;font-size:10px}
.gallery-prev,.gallery-next{position:absolute;top:50%;transform:translateY(-50%);width:29px;height:29px;border:1px solid #ffffff44;border-radius:50%;background:#04152bbd;color:white}
.gallery-prev{left:7px}.gallery-next{right:7px}
.photo-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:9px}
.photo-thumb{width:54px;height:54px;object-fit:cover;border-radius:10px;border:1px solid #ffffff24}
.avatar{width:58px;height:58px;border-radius:19px;display:grid;place-items:center;background:linear-gradient(135deg,#d9f2ff,#238fff 55%,#062c65);color:#04142b;font:800 27px 'Space Grotesk';box-shadow:0 0 25px #168bff55}.profilehead{display:flex;align-items:center;gap:14px}.wallet{margin:24px 0 16px;padding:22px;border:1px solid #3b8edb55;border-radius:20px;background:radial-gradient(circle at 90% 0%,#1479c733,transparent 50%),linear-gradient(135deg,#0d2544,#081528)}.wallet small{color:#9acfff;letter-spacing:2px;font-size:10px}.wallet strong{display:block;font:700 34px 'Space Grotesk';margin-top:8px;color:#e5f5ff}.profilemeta{padding:12px 0;color:#8ea9c3;font-size:11px}.reviewbox{margin-top:12px;padding:14px;border:1px solid #3985c455;border-radius:16px;background:#0c1a2a}.reviewbox .field{margin-top:9px}#admin[hidden]{display:none!important}body.locked #home,body.locked #shop,body.locked #orders,body.locked #profile,body.locked .nav{display:none!important}body.locked #gate{display:block!important;margin:20px 0;padding:20px;border:1px solid #287dc855;border-radius:18px;background:#0b1b30;color:#e7f5ff}body.locked #gate a{color:#8fd1ff}.brandmark{background:radial-gradient(circle at 50% 50%,#07182d,#020711 78%)!important;color:transparent!important;background-image:linear-gradient(145deg,#fff 2%,#a8e4ff 24%,#238cff 46%,#e3f7ff 58%,#1766c5 78%,#fff)!important;-webkit-background-clip:text!important;background-clip:text!important;text-shadow:0 0 9px #238cffaa,0 0 20px #1268d988!important;border:1px solid #4caaff66;box-shadow:0 0 18px #167bdb55,inset 0 0 12px #167bdb22!important}/* Make decorative layers transparent to taps on iOS Telegram WebView */.hero:before,.hero:after,.service:before,.service:after,.serviceart:before,.serviceart:after{pointer-events:none!important}.hero .primary,.service button,.nav button{position:relative;z-index:3;pointer-events:auto!important}.nav{z-index:50;pointer-events:auto}.navinner,.navbtn{pointer-events:auto!important}/* STUDIO DLC membership gate */
#gate[hidden]{display:none!important}
#gate:not([hidden]){position:relative;isolation:isolate;overflow:hidden;margin:clamp(22px,7vh,72px) auto 34px;max-width:520px;min-height:360px;padding:34px 26px;border:1px solid rgba(91,174,255,.28);border-radius:30px;background:radial-gradient(ellipse at 50% -10%,rgba(20,119,255,.27),transparent 58%),linear-gradient(145deg,rgba(12,27,49,.98),rgba(4,10,22,.98));box-shadow:0 28px 90px rgba(0,0,0,.42),inset 0 1px 0 rgba(255,255,255,.07);text-align:center;animation:gateEnter .65s cubic-bezier(.2,.8,.2,1) both}
#gate:before{content:"";position:absolute;inset:-45%;z-index:-1;background:conic-gradient(from 0deg,transparent 0 68%,rgba(40,145,255,.13),transparent 78%);animation:gateSpin 18s linear infinite}
#gate h2{margin:13px 0 10px;color:#f3f8ff;font:700 clamp(25px,6vw,34px)/1.08 'Space Grotesk',sans-serif;letter-spacing:-1.2px}
#gate p{max-width:390px;margin:0 auto 24px;color:#a9bdd5;font-size:14px;line-height:1.7}
.gate-orb{width:82px;height:82px;margin:0 auto 22px;display:grid;place-items:center;border-radius:27px;background:linear-gradient(145deg,#f8fdff 0%,#7bcaff 24%,#126bd0 54%,#061b3c 100%);box-shadow:0 0 28px rgba(38,143,255,.45),inset 0 2px 4px rgba(255,255,255,.8);transform:rotate(-7deg)}
.gate-orb span{font:900 51px/1 'Space Grotesk';color:#eaf8ff;text-shadow:0 2px 2px #06427d,0 0 16px #fff}
.gate-kicker{color:#78bfff;font-size:10px;font-weight:800;letter-spacing:3px}
.gate-link{display:flex!important;align-items:center;justify-content:center;gap:12px;width:100%;max-width:350px;margin:0 auto 12px;text-decoration:none!important;min-height:54px;border-radius:16px!important;box-shadow:0 10px 30px rgba(24,126,255,.25);transition:transform .22s ease,filter .22s ease,box-shadow .22s ease}
.gate-link:active,.gate-check-btn:active{transform:scale(.975)}
.gate-check-btn{width:100%;max-width:350px;min-height:50px;border-radius:16px!important}
.gate-status{margin:18px auto 0;max-width:350px;color:#7894b2;font-size:11px;line-height:1.55}
#checkSub.loading{opacity:.7;pointer-events:none}
.gate-check{width:64px;height:64px;margin:0 auto 18px;display:grid;place-items:center;border-radius:50%;background:linear-gradient(145deg,#7ce7c1,#0c9d72);color:#042c24;font-size:32px;box-shadow:0 0 35px #16bd8a55}
.gate-success{animation:gateEnter .35s ease both}
body.locked #home,body.locked #shop,body.locked #orders,body.locked #profile,body.locked #admin,body.locked .nav{display:none!important}
@keyframes gateEnter{from{opacity:0;transform:translateY(18px) scale(.985);filter:blur(8px)}to{opacity:1;transform:none;filter:blur(0)}}
@keyframes gateSpin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){#gate,#gate:before{animation:none!important}.gate-link,.gate-check-btn{transition:none!important}}
/* Fast, responsive interaction tuning */
button,.navbtn,.service,.gallery,.modal{ -webkit-tap-highlight-color:transparent; }
button{touch-action:manipulation;transition:transform .12s ease,opacity .12s ease,background-color .12s ease!important}
button:active{transform:scale(.975)}
.navbtn:active{transform:translateY(1px)}
.reveal{transition:opacity .22s ease,transform .22s ease!important;transition-delay:0ms!important}
html{scroll-behavior:auto!important}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;transition-duration:.01ms!important;scroll-behavior:auto!important}}
.view.active .reveal{opacity:1!important;transform:none!important;transition:opacity .22s ease,transform .22s ease}
</style></head><body><div class="noise"></div><div class="aurora a1"></div><div class="aurora a2"></div>
<main class="shell">
<header class="topbar"><div class="brand"><span class="brandmark">S</span><span>STUDIO <em>DLC</em></span></div><div class="status" id="userStatus">DIGITAL ATELIER</div></header>
<div id="gate" class="notice" hidden>Чтобы пользоваться сервисом, подпишись на <a href="https://t.me/studiodlc">канал STUDIO DLC</a>, затем нажми «Проверить».</div>
<section id="home" class="view active">
<div class="hero"><div class="eyebrow">INDEPENDENT DIGITAL STUDIO</div><h1>Создаём<br><span class="gradient">цифровое.</span></h1><p>Сайты, форумы и motion-дизайн. Собираем визуальный опыт, который запоминается.</p><button class="primary" data-go="shop">Исследовать студию <span>↗</span></button></div>
<div class="stats"><div class="stat"><strong>01—03</strong><span>НАПРАВЛЕНИЯ</span></div><div class="stat"><strong>∞</strong><span>ИДЕИ</span></div><div class="stat"><strong>24/7</strong><span>НА СВЯЗИ</span></div></div>
<div class="sectionhead"><h2>Избранные услуги</h2><small>SELECTED WORK</small></div><div id="featured" class="grid"></div>
<div class="panel" style="margin-top:17px"><div class="eyebrow">LET'S BUILD</div><h2 style="font:700 22px 'Space Grotesk';margin:10px 0">Есть идея?</h2><p class="muted">Расскажи о проекте — обсудим формат и детали.</p><button class="secondary" data-go="shop">Посмотреть услуги ↗</button></div>
</section>
<section id="shop" class="view"><div class="sectionhead"><h2>Каталог</h2><small>OUR SERVICES / 01</small></div><p class="muted">Выбери направление, чтобы посмотреть детали и оформить заявку.</p><div id="catalog" class="grid"></div></section>
<section id="orders" class="view"><div class="sectionhead"><h2>Мои заказы</h2><small>YOUR SPACE / 02</small></div><p class="muted">История заявок, оплата и отправка чека на ручную проверку.</p><div id="myorders"></div></section>
<section id="profile" class="view"><div class="sectionhead"><h2>Мой профиль</h2><small>PERSONAL SPACE</small></div><div id="profileCard" class="panel"></div></section>
<section id="admin" class="view" hidden><div class="sectionhead"><h2>Панель владельца</h2><small>CONTROL ROOM</small></div>
<div class="panel"><div class="eyebrow">NEW SERVICE</div><input id="pt" class="field" placeholder="Название услуги"><input id="pp" class="field" placeholder="Цена"><input id="pc" class="field" placeholder="Категория"><textarea id="pd" class="field" placeholder="Описание"></textarea><label class="muted">Фото услуги — можно выбрать несколько</label><input id="newPhotos" class="field" type="file" accept="image/jpeg,image/png,image/webp" multiple><button class="primary wide" data-action="add-product">＋ Добавить услугу</button></div>
<div class="sectionhead"><h2>Заказы клиентов</h2></div><div id="adminorders"></div><div class="sectionhead"><h2>Управление каталогом</h2></div><div id="adminproducts"></div></section>
</main>
<nav class="nav"><div class="navinner">
<button class="navbtn active" data-go="home"><span class="ico">⌂</span>Главная</button><button class="navbtn" data-go="shop"><span class="ico">◇</span>Каталог</button><button class="navbtn" data-go="profile"><span class="ico">◉</span>Профиль</button><button class="navbtn" data-go="orders"><span class="ico">▤</span>Заказы</button><button class="navbtn" id="adminNav" data-go="admin" hidden><span class="ico">⌘</span>Владелец</button>
</div></nav>
<div id="orderModal" class="modalback"><div class="modal"><button class="modalclose" data-action="close-modal">×</button><div class="eyebrow">PROJECT REQUEST</div><h2 id="modalTitle">Услуга</h2><p id="modalDesc" class="muted"></p><textarea id="orderComment" class="field" rows="3" placeholder="Опиши задачу, пожелания и сроки..."></textarea><button class="primary wide" data-action="create-order">Создать заявку и перейти к оплате ↗</button><p class="muted">После оплаты прикрепи скриншот в разделе «Заказы». Платёж проверяется владельцем вручную.</p></div></div>
<div id="toast" class="toast"></div>
<script>
(function(){
const tg=window.Telegram&&window.Telegram.WebApp;
if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#090811');tg.setBackgroundColor('#090811');}catch(e){}}
const $=id=>document.getElementById(id), headers=()=>({'X-Telegram-Init-Data':tg&&tg.initData?tg.initData:''});
let products=[],selected=null,isOwner=false,subscribed=false;const imageCache={};
function toast(msg){const el=$('toast');el.textContent=msg;el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),3000);if(tg&&tg.HapticFeedback)tg.HapticFeedback.notificationOccurred('success')}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function api(path,opt){opt=opt||{};const h=Object.assign(headers(),opt.headers||{});const r=await fetch(path,Object.assign({},opt,{headers:h}));let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw Error(d.detail||('Ошибка '+r.status));return d}
function go(v){if(v==='admin'&&!isOwner){$('adminNav').hidden=true;toast('Нет доступа');return}if(v==='admin')$('admin').hidden=false;if(v==='profile')loadProfile();document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===v));document.querySelectorAll('.navbtn').forEach(x=>x.classList.toggle('active',x.dataset.go===v));const active=document.getElementById(v);if(active)active.querySelectorAll('.reveal').forEach(el=>el.classList.add('visible'));if(v==='orders')loadOrders();if(v==='admin'){active.querySelectorAll('.reveal').forEach(el=>{el.style.opacity='1';el.style.transform='none'});loadAdmin()}window.scrollTo({top:0,behavior:'instant'});if(tg&&tg.HapticFeedback)tg.HapticFeedback.selectionChanged()}
function iconFor(category){const s=(category||'').toLowerCase();if(s.includes('сайт'))return '⌘';if(s.includes('форум'))return '◈';if(s.includes('дизайн'))return '✳';return '✦'}
function card(p){return '<article class="service"><div class="gallery" data-gallery="'+p.id+'"><span class="glyph">'+iconFor(p.category)+'</span></div><div class="category">'+esc(p.category)+'</div><h3>'+esc(p.title)+'</h3><p>'+esc(p.description)+'</p><div class="price">'+esc(p.price)+'</div><button class="secondary" data-order="'+p.id+'">Подробнее <span>↗</span></button></article>'}
function renderProducts(){ $('catalog').innerHTML=products.map(card).join('')||'<div class="empty">Скоро здесь появятся услуги.</div>'; $('featured').innerHTML=products.slice(0,2).map(card).join('')||'<div class="empty">Каталог обновляется.</div>';products.forEach(p=>loadProductImages(p.id))}
async function loadProductImages(pid){try{const d=await api('/api/products/'+pid+'/images');imageCache[pid]=d.items||[];document.querySelectorAll('[data-gallery="'+pid+'"]').forEach(el=>paintGallery(el,pid,0));const thumbs=$('thumbs-'+pid);if(thumbs)thumbs.innerHTML=(imageCache[pid]||[]).map(x=>'<span><img class="photo-thumb" src="data:'+x.type+';base64,'+x.data+'"><button class="secondary" data-delphoto="'+pid+':'+x.id+'">×</button></span>').join('')}catch(e){console.warn('gallery load',e)}}function paintGallery(el,pid,index){const arr=imageCache[pid]||[];if(!arr.length){el.innerHTML='<span class="glyph">✦</span>';return}index=(index+arr.length)%arr.length;el.dataset.index=index;const x=arr[index];el.innerHTML='<img src="data:'+x.type+';base64,'+x.data+'">'+(arr.length>1?'<button class="gallery-prev" data-galleryprev="'+pid+'">‹</button><button class="gallery-next" data-gallerynext="'+pid+'">›</button><span class="gallery-count">'+(index+1)+' / '+arr.length+'</span>':'')}async function uploadProductImages(pid,files){if(!files||!files.length)return;const fd=new FormData();Array.from(files).slice(0,8).forEach(f=>fd.append('files',f));await api('/api/admin/products/'+pid+'/images',{method:'POST',body:fd});await loadProductImages(pid)}
async function loadProducts(){const d=await api('/api/products');products=d.items||[];renderProducts()}
function openOrder(id){selected=products.find(p=>Number(p.id)===Number(id));if(!selected)return; $('modalTitle').textContent=selected.title;$('modalDesc').textContent=(selected.description||'')+' · '+selected.price;$('orderComment').value='';$('orderModal').classList.add('open');if(tg&&tg.HapticFeedback)tg.HapticFeedback.impactOccurred('light')}
function closeModal(){$('orderModal').classList.remove('open')}
async function createOrder(){if(!selected)return;try{const d=await api('/api/orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:selected.id,comment:$('orderComment').value})});closeModal();toast('Заявка #'+d.id+' создана');if(tg&&tg.openLink)tg.openLink(d.payment_url);else window.open(d.payment_url,'_blank');go('orders')}catch(e){toast(e.message)}}
async function loadProfile(){try{const d=await api('/api/profile'),u=d.user||{},name=[u.first_name,u.last_name].filter(Boolean).join(' ')||'Пользователь';$('profileCard').innerHTML='<div class="profilehead"><div class="avatar">'+esc(name.slice(0,1).toUpperCase())+'</div><div><b>'+esc(name)+'</b><div class="muted">@'+esc(u.username||'telegram-user')+'</div></div></div><div class="wallet"><small>МОЙ БАЛАНС</small><strong>'+esc(d.balance)+' ₽</strong><p class="muted">Баланс аккаунта STUDIO DLC</p></div><div class="profilemeta">Telegram ID · '+esc(u.id)+'</div><button class="secondary wide" data-go="orders">Мои заказы ↗</button>'}catch(e){$('profileCard').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
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
async function setStatus(id,status){try{await api('/api/admin/orders/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});toast('Статус заказа обновлён');loadAdmin()}catch(e){toast(e.message)}}
async function receiptView(id){try{const x=await api('/api/admin/orders/'+id+'/receipt');const w=window.open();if(w)w.document.write('<title>Чек заказа #'+id+'</title><body style="margin:0;background:#111;display:grid;place-items:center"><img style="max-width:100%;max-height:100vh" src="data:'+x.type+';base64,'+x.data+'"></body>');else toast('Разреши всплывающие окна для просмотра чека')}catch(e){toast(e.message)}}
async function editProduct(id){const p=products.find(x=>Number(x.id)===Number(id));if(!p)return;const title=prompt('Название услуги',p.title);if(title===null)return;const price=prompt('Цена',p.price),description=prompt('Описание',p.description),category=prompt('Категория',p.category);try{await api('/api/admin/products/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,price,description,category,active:true})});await loadProducts();loadAdmin();toast('Услуга обновлена')}catch(e){toast(e.message)}}
async function deleteProduct(id){if(!confirm('Удалить услугу из каталога?'))return;try{await api('/api/admin/products/'+id,{method:'DELETE'});await loadProducts();loadAdmin();toast('Услуга удалена')}catch(e){toast(e.message)}}
function routeFromButton(b){if(!b||b.disabled)return;const g=b.dataset.go;if(g){go(g);return}if(b.dataset.order){openOrder(b.dataset.order);return}const a=b.dataset.action;if(a==='close-modal')closeModal();else if(a==='create-order')createOrder();else if(a==='send-post')sendPost();else if(a==='add-product')addProduct();else if(b.dataset.upload)upload(b.dataset.upload);else if(b.dataset.review)submitReview(b.dataset.review);else if(b.dataset.status)setStatus(b.dataset.status,b.dataset.value);else if(b.dataset.receipt)receiptView(b.dataset.receipt);else if(b.dataset.edit)editProduct(b.dataset.edit);else if(b.dataset.delete)deleteProduct(b.dataset.delete);else if(b.dataset.addphotos){const input=$('photos-'+b.dataset.addphotos);if(input&&input.files.length)uploadProductImages(Number(b.dataset.addphotos),input.files).then(()=>toast('Фотографии добавлены')).catch(e=>toast(e.message));else toast('Выбери фотографии')}else if(b.dataset.delphoto){const [pid,iid]=b.dataset.delphoto.split(':');api('/api/admin/products/'+pid+'/images/'+iid,{method:'DELETE'}).then(()=>loadProductImages(Number(pid))).then(()=>toast('Фото удалено')).catch(e=>toast(e.message))}else if(b.dataset.galleryprev||b.dataset.gallerynext){const pid=Number(b.dataset.galleryprev||b.dataset.gallerynext);const el=b.closest('.gallery');paintGallery(el,pid,(Number(el.dataset.index)||0)+(b.dataset.gallerynext?1:-1))}}
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
  subscribed=true;isOwner=!!s.owner;
  document.body.classList.remove('locked');
  $('gate').classList.add('gate-success');
  $('gate').innerHTML='<div class="gate-check">✓</div><div class="gate-kicker">ACCESS GRANTED</div><h2>Добро пожаловать в STUDIO DLC</h2><p>Подписка подтверждена. Открываем каталог услуг…</p>';
  if(isOwner){$('adminNav').hidden=false;$('admin').hidden=false;}
  await loadProducts();
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
  isOwner=!!s.owner;subscribed=!!s.subscribed;
  $('adminNav').hidden=!isOwner;$('admin').hidden=!isOwner;
  if(!subscribed){
   document.body.classList.add('locked');
   $('gate').hidden=false;
   $('gate').innerHTML='<div class="gate-orb"><span>S</span></div><div class="gate-kicker">STUDIO DLC · MEMBER ACCESS</div><h2>Сначала — подписка</h2><p>Подпишись на наш Telegram-канал, чтобы открыть каталог, оформить заказ и пользоваться личным кабинетом.</p><a class="primary gate-link" href="https://t.me/studiodlc" target="_blank" rel="noopener">Подписаться на канал <span>↗</span></a><button class="secondary gate-check-btn" id="checkSub">Я подписался — проверить</button><div class="gate-status" id="subStatus">После подписки вернись сюда — доступ откроется автоматически.</div>';
   return;
  }
  document.body.classList.remove('locked');
  if(isOwner){$('adminNav').hidden=false;$('admin').hidden=false;}
  await loadProducts();
 }catch(e){
  $('gate').hidden=false;
  $('gate').innerHTML='<div class="gate-kicker">CONNECTION</div><h2>Не удалось загрузить приложение</h2><p>'+esc(e.message)+'</p><button class="secondary" onclick="location.reload()">Повторить</button>';
  toast(e.message);
 }
}
boot().then(()=>bindMotion()).catch(e=>toast('Не удалось запустить приложение: '+e.message));
})();
</script></body></html>"""
