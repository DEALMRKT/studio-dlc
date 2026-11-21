import os, hmac, hashlib, json, time, urllib.parse, base64
import requests, psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse

TOKEN=os.getenv("BOT_TOKEN","")
OWNER=os.getenv("OWNER_TELEGRAM_ID","")
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
  q.execute("SELECT count(*) n FROM products")
  if q.fetchone()["n"]==0:
   q.execute("INSERT INTO products(title,description,price,category) VALUES(%s,%s,%s,%s),(%s,%s,%s,%s),(%s,%s,%s,%s)",("CRMP-сайт","Современный сайт для CRMP-проекта","От 1500 ₽","Сайты","Форум проекта","Категории, темы и профили пользователей","От 1200 ₽","Форумы","Motion-дизайн","Анимации и премиальная визуальная подача","От 700 ₽","Дизайн"))
init()
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
 if not OWNER or str(u["id"])!=OWNER: raise HTTPException(403,"Доступ только владельцу")
 return u
def subscribed(uid):
 try:
  x=requests.get("https://api.telegram.org/bot"+TOKEN+"/getChatMember",params={"chat_id":CHANNEL,"user_id":uid},timeout=8).json()
  return x.get("ok") and x.get("result",{}).get("status") in ("creator","administrator","member","restricted")
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
 user(r)
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id,title,description,price,category,image FROM products WHERE active=TRUE ORDER BY id DESC"); return {"items":q.fetchall()}
@app.post("/api/orders")
async def order(r:Request):
 u=user(r)
 if not subscribed(u["id"]): raise HTTPException(403,"Сначала подпишитесь на @studiodlc")
 d=await r.json()
 with conn() as c,c.cursor() as q:
  q.execute("SELECT * FROM products WHERE id=%s AND active=TRUE",(int(d.get("product_id",0)),)); p=q.fetchone()
  if not p: raise HTTPException(404,"Услуга не найдена")
  q.execute("INSERT INTO orders(user_id,username,product_id,product_title,price,comment) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",(u["id"],u.get("username",""),p["id"],p["title"],p["price"],str(d.get("comment",""))[:1000])); oid=q.fetchone()["id"]
 notify("Новый заказ #"+str(oid)+"\n"+p["title"]+" · "+p["price"]+"\nTelegram ID: "+str(u["id"]))
 return {"id":oid,"payment_url":PAYMENT}
@app.post("/api/orders/{oid}/receipt")
async def receipt(oid:int,r:Request,file:UploadFile=File(...)):
 u=user(r)
 if not subscribed(u["id"]): raise HTTPException(403,"Подпишитесь на канал")
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
 u=user(r)
 with conn() as c,c.cursor() as q:
  q.execute("SELECT id,product_title,price,status,created_at FROM orders WHERE user_id=%s ORDER BY id DESC",(u["id"],)); rows=q.fetchall()
 return {"items":[dict(x,created_at=x["created_at"].isoformat()) for x in rows]}
@app.get("/api/forum")
def forum(r:Request):
 user(r)
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
PAGE=r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><meta name="theme-color" content="#08090d"><script src="https://telegram.org/js/telegram-web-app.js"></script><title>STUDIO DLC</title><style>
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% 0,#39245d66,transparent 42%),#08090d;color:#f5f5fb;font:15px system-ui;padding-bottom:85px}.wrap{max-width:720px;margin:auto;padding:18px 16px}.top{display:flex;justify-content:space-between;margin:8px 0 24px}.logo{font-weight:900;letter-spacing:2px}.logo span{color:#a78bfa}.pill,.tag{border:1px solid #ffffff18;border-radius:30px;padding:7px 11px;color:#aaaec0;font-size:11px}.hero{padding:25px;border:1px solid #a78bfa55;border-radius:26px;background:linear-gradient(130deg,#30204e,#111722 70%,#11252c);box-shadow:0 20px 60px #0008}.hero small{color:#c4b5fd;letter-spacing:2px}.hero h1{font-size:34px;line-height:1.05;margin:13px 0}.hero p,.muted{color:#a3a7b7;line-height:1.5}.btn{background:linear-gradient(100deg,#8b5cf6,#6366f1);padding:13px 17px;border-radius:14px;font-weight:750;color:white;border:0}.head{display:flex;justify-content:space-between;align-items:center;margin:26px 0 14px}.head h2{margin:0;font-size:19px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.card,.order{background:linear-gradient(145deg,#171a24,#10121a);border:1px solid #ffffff13;border-radius:19px;padding:15px;min-width:0}.art{height:90px;border-radius:13px;background:linear-gradient(135deg,#39245f,#163342);display:grid;place-items:center;font-size:34px;margin-bottom:12px}.card h3{font-size:15px;margin:9px 0}.card p{font-size:12px;color:#9da2b4;min-height:36px}.price{color:#d8ccff;font-weight:800;margin:12px 0}.subbtn{width:100%;padding:11px;border-radius:12px;background:#ffffff12;color:white;font-weight:700;border:0}.view{display:none}.view.active{display:block;animation:fade .25s}.nav{position:fixed;bottom:0;left:0;right:0;display:flex;justify-content:space-around;background:#0d0f17ed;backdrop-filter:blur(18px);border-top:1px solid #ffffff16;padding:10px 2px calc(10px + env(safe-area-inset-bottom));z-index:4}.nav button{background:none;color:#898da0;font-size:10px;display:flex;flex-direction:column;align-items:center;gap:4px}.nav b{font-size:19px}.nav .active{color:#c4b5fd}.field{width:100%;background:#090b11;color:white;border:1px solid #ffffff20;border-radius:12px;padding:12px;margin:7px 0}.wide{width:100%;margin:8px 0}.order{margin:10px 0}.notice{padding:13px;background:#f59e0b18;color:#fcd34d;border-radius:13px;margin:12px 0}.post{padding:14px;border-bottom:1px solid #ffffff15}.post p{white-space:pre-wrap}.empty{text-align:center;padding:32px;color:#969bad}@keyframes fade{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}@media(max-width:390px){.grid{grid-template-columns:1fr}.hero h1{font-size:29px}}
</style></head><body><main class="wrap"><header class="top"><div class="logo">STUDIO <span>DLC</span></div><div class="pill" id="who">TELEGRAM MINI APP</div></header><div id="gate" class="notice" style="display:none">Подпишитесь на <a href="https://t.me/studiodlc" style="color:#c4b5fd">@studiodlc</a>, затем перезапустите приложение.</div>
<section id="home" class="view active"><div class="hero"><small>DIGITAL STUDIO · 2026</small><h1>Создаём<br>цифровой стиль.</h1><p>Сайты CRMP, форумы и motion-дизайн с вниманием к каждой детали.</p><button class="btn" onclick="go('shop')">Открыть каталог ↗</button></div><div class="head"><h2>Популярное</h2></div><div id="featured" class="grid"></div></section>
<section id="shop" class="view"><div class="head"><h2>Каталог услуг</h2><span class="pill">01 / SHOP</span></div><div id="catalog" class="grid"></div></section>
<section id="orders" class="view"><div class="head"><h2>Мои заказы</h2></div><div id="myorders"></div></section>
<section id="forum" class="view"><div class="head"><h2>Сообщество</h2></div><textarea id="body" class="field" rows="3" placeholder="Сообщение в форум..."></textarea><button class="btn wide" onclick="sendPost()">Опубликовать</button><div id="posts"></div></section>
<section id="admin" class="view"><div class="head"><h2>Панель владельца</h2></div><h3>Новая услуга</h3><input id="pt" class="field" placeholder="Название"><input id="pp" class="field" placeholder="Цена"><input id="pc" class="field" placeholder="Категория"><textarea id="pd" class="field" placeholder="Описание"></textarea><button class="btn wide" onclick="addProduct()">Добавить услугу</button><div class="head"><h2>Заказы</h2></div><div id="adminorders"></div><div class="head"><h2>Каталог</h2></div><div id="adminproducts"></div></section></main>
<nav class="nav"><button class="active" data-v="home" onclick="go('home')"><b>⌂</b>Главная</button><button data-v="shop" onclick="go('shop')"><b>▦</b>Каталог</button><button data-v="forum" onclick="go('forum')"><b>◎</b>Форум</button><button data-v="orders" onclick="go('orders')"><b>▤</b>Заказы</button><button id="anav" data-v="admin" onclick="go('admin')" style="display:none"><b>⚙</b>Владелец</button></nav>
<div id="modal" style="display:none;position:fixed;inset:0;background:#000b;z-index:8;padding:20px;align-items:center;justify-content:center"><div class="card" style="width:100%;max-width:440px"><button onclick="closeModal()" style="float:right">✕</button><h2 id="mt"></h2><p id="md" class="muted"></p><textarea id="comment" class="field" placeholder="Комментарий"></textarea><button class="btn wide" onclick="createOrder()">Создать заказ и оплатить</button><p class="muted">Оплата проверяется владельцем вручную. Скриншот не подтверждает платёж автоматически.</p></div></div>
<script>
const tg=window.Telegram&&Telegram.WebApp; if(tg){tg.ready();tg.expand()} const H={'X-Telegram-Init-Data':tg?tg.initData:''};let products=[],selected=null;
async function api(path,opt={}){let r=await fetch(path,Object.assign({},opt,{headers:Object.assign({},H,opt.headers||{})}));let d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.detail||'Ошибка');return d}
function esc(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function go(v){document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===v));document.querySelectorAll('.nav button').forEach(x=>x.classList.toggle('active',x.dataset.v===v));if(v==='orders')loadOrders();if(v==='forum')loadForum();if(v==='admin')loadAdmin()}
function card(p){return '<article class="card"><div class="art">✦</div><span class="tag">'+esc(p.category)+'</span><h3>'+esc(p.title)+'</h3><p>'+esc(p.description)+'</p><div class="price">'+esc(p.price)+'</div><button class="subbtn" onclick="openOrder('+p.id+')">Подробнее →</button></article>'}
async function loadProducts(){let d=await api('/api/products');products=d.items;catalog.innerHTML=products.map(card).join('')||'<div class="empty">Каталог пока пуст</div>';featured.innerHTML=products.slice(0,2).map(card).join('')}
function openOrder(id){selected=products.find(x=>x.id===id);mt.textContent=selected.title;md.textContent=selected.description;modal.style.display='flex'}function closeModal(){modal.style.display='none'}
async function createOrder(){try{let d=await api('/api/orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:selected.id,comment:comment.value})});closeModal();if(tg)tg.openLink(d.payment_url);alert('Заказ #'+d.id+' создан. После оплаты загрузите скриншот в разделе «Заказы».');go('orders')}catch(e){alert(e.message)}}
async function loadOrders(){try{let d=await api('/api/my-orders');myorders.innerHTML=d.items.map(o=>'<div class="order"><b>#'+o.id+' · '+esc(o.product_title)+'</b><p class="muted">'+esc(o.price)+' · '+esc(o.status)+'</p><input class="field" type="file" id="f'+o.id+'" accept="image/png,image/jpeg,image/webp"><button class="subbtn" onclick="upload('+o.id+')">Отправить скриншот</button></div>').join('')||'<div class="empty">Заказов пока нет</div>'}catch(e){myorders.innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function upload(id){let f=document.getElementById('f'+id).files[0];if(!f)return alert('Выберите скриншот');let fd=new FormData();fd.append('file',f);try{await api('/api/orders/'+id+'/receipt',{method:'POST',body:fd});alert('Отправлено владельцу на проверку')}catch(e){alert(e.message)}}
async function loadForum(){let d=await api('/api/forum');posts.innerHTML=d.items.map(p=>'<div class="post"><b>@'+esc(p.username||'user')+'</b><span class="muted"> · '+new Date(p.created_at).toLocaleString('ru')+'</span><p>'+esc(p.body)+'</p></div>').join('')||'<div class="empty">Пока нет публикаций</div>'}
async function sendPost(){try{await api('/api/forum',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body:document.getElementById('body').value})});document.getElementById('body').value='';loadForum()}catch(e){alert(e.message)}}
async function addProduct(){try{await api('/api/admin/products',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:pt.value,price:pp.value,category:pc.value,description:pd.value})});pt.value=pp.value=pc.value=pd.value='';await loadProducts();alert('Услуга добавлена')}catch(e){alert(e.message)}}
async function loadAdmin(){try{let a=await api('/api/admin/orders');adminorders.innerHTML=a.items.map(o=>'<div class="order"><b>#'+o.id+' · '+esc(o.product_title)+'</b><p class="muted">'+esc(o.price)+' · @'+esc(o.username)+' · '+esc(o.status)+' · ID '+o.user_id+'</p>'+(o.has_receipt?'<button class="subbtn" onclick="receiptView('+o.id+')">Посмотреть скриншот</button>':'')+'<div style="display:flex;gap:6px;margin-top:8px"><button class="subbtn" onclick="setStatus('+o.id+',\\'paid\\')">Оплата ОК</button><button class="subbtn" onclick="setStatus('+o.id+',\\'rejected\\')">Отклонить</button></div><button class="subbtn" style="margin-top:6px" onclick="setStatus('+o.id+',\\'completed\\')">Выполнен</button></div>').join('')||'<div class="empty">Заказов нет</div>';adminproducts.innerHTML=products.map(p=>'<div class="order"><b>'+esc(p.title)+'</b><p>'+esc(p.price)+'</p><button class="subbtn" onclick="editProduct('+p.id+')">Изменить</button></div>').join('')}catch(e){adminorders.innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function setStatus(id,status){try{await api('/api/admin/orders/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});loadAdmin()}catch(e){alert(e.message)}}
async function receiptView(id){try{let x=await api('/api/admin/orders/'+id+'/receipt');let w=window.open();w.document.write('<img style="max-width:100%" src="data:'+x.type+';base64,'+x.data+'">')}catch(e){alert(e.message)}}
async function editProduct(id){let p=products.find(x=>x.id===id);let title=prompt('Название',p.title);if(title===null)return;let price=prompt('Цена',p.price),description=prompt('Описание',p.description),category=prompt('Категория',p.category);try{await api('/api/admin/products/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,price,description,category})});await loadProducts();loadAdmin()}catch(e){alert(e.message)}}
(async()=>{try{let s=await api('/api/session');who.textContent=s.user.first_name||'TELEGRAM';if(!s.subscribed){gate.style.display='block';return}if(s.owner)anav.style.display='flex';await loadProducts()}catch(e){gate.style.display='block';gate.textContent=e.message}})();
</script></body></html>"""
