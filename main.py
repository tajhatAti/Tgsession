# Ad-free Telegram web client - single file, run on CodeNest (binds $PORT)
# Open the public URL WITH trailing slash. Never share this file (contains your session).
import os, hmac, hashlib, mimetypes
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, FloodWaitError

API_ID = 37109385
API_HASH = "b50a9ccaf4a0352b895a9fb2998c7f0d"
PASSWORD = "Ahad@2026tg"   # website login password - change it

TOKEN = hmac.new(PASSWORD.encode(), b"tgweb", hashlib.sha256).hexdigest()

SESS_FILE = "tgsess.txt"
client = None
avatars = {}
login_state = {}

def load_sess():
    try: return open(SESS_FILE).read().strip()
    except Exception: return ""

async def make_client(string):
    global client
    c = TelegramClient(StringSession(string), API_ID, API_HASH)
    c.parse_mode = "html"
    await c.connect()
    client = c

@asynccontextmanager
async def lifespan(app):
    try: await make_client(load_sess())
    except Exception as e:
        print("saved session unusable, login again from website:", e)
        try: os.remove(SESS_FILE)
        except Exception: pass
        await make_client("")
    yield
    await client.disconnect()

app = FastAPI(lifespan=lifespan)

@app.exception_handler(Exception)
async def on_error(request, exc):
    if isinstance(exc, FloodWaitError):
        return JSONResponse({"detail": f"Telegram limit: {exc.seconds}s por abar try koro"}, status_code=429)
    print("ERR", request.url.path, type(exc).__name__, exc)
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


@app.middleware("http")
async def guard(request: Request, call_next):
    p = request.url.path
    if "/api/" in p and not p.endswith("/api/login"):
        if not hmac.compare_digest(request.cookies.get("tgw_auth", ""), TOKEN):
            return JSONResponse({"error": "auth"}, status_code=401)
    return await call_next(request)

@app.post("/api/login")
async def login(body: dict, response: Response):
    if not hmac.compare_digest(body.get("password", ""), PASSWORD):
        raise HTTPException(401, "wrong password")
    response.set_cookie("tgw_auth", TOKEN, httponly=True, secure=True, samesite="strict", max_age=60*60*24*30)
    return {"ok": True}

def save_sess():
    open(SESS_FILE, "w").write(client.session.save())

@app.get("/api/tg/status")
async def tg_status():
    return {"authorized": await client.is_user_authorized()}

@app.post("/api/tg/phone")
async def tg_phone(body: dict):
    phone = body["phone"].strip()
    try: r = await client.send_code_request(phone)
    except Exception as e: raise HTTPException(400, str(e))
    login_state.update(phone=phone, hash=r.phone_code_hash)
    return {"sent": True}

@app.post("/api/tg/code")
async def tg_code(body: dict):
    try:
        await client.sign_in(login_state["phone"], body["code"].strip(), phone_code_hash=login_state["hash"])
    except SessionPasswordNeededError:
        return {"need_password": True}
    except Exception as e: raise HTTPException(400, str(e))
    save_sess(); return {"ok": True}

@app.post("/api/tg/password")
async def tg_password(body: dict):
    try: await client.sign_in(password=body["password"])
    except Exception as e: raise HTTPException(400, str(e))
    save_sess(); return {"ok": True}


@app.post("/api/tg/session")
async def tg_session(body: dict):
    global client
    try:
        c = TelegramClient(StringSession(body["session"].strip()), API_ID, API_HASH)
        await c.connect()
        ok = await c.is_user_authorized()
    except Exception as e: raise HTTPException(400, str(e))
    if not ok:
        await c.disconnect()
        raise HTTPException(400, "Session valid na (logout/expired). Notun string baniye dao.")
    old, client = client, c
    try: await old.disconnect()
    except Exception: pass
    avatars.clear(); save_sess()
    return {"ok": True}

def kind(d):
    if d.is_channel and not d.is_group: return "channel"
    if d.is_group: return "group"
    e = d.entity
    return "bot" if getattr(e, "bot", False) else "chat"

@app.get("/api/dialogs")
async def dialogs():
    out = []
    async for d in client.iter_dialogs(limit=300):
        try:
            m = d.message
            txt = (getattr(m, "message", "") or ("📎 media" if getattr(m, "media", None) else "")) if m else ""
            dt = getattr(m, "date", None)
            out.append({"id": d.id, "name": d.name or "Deleted", "type": kind(d),
                        "unread": d.unread_count, "pinned": d.pinned,
                        "last": txt[:80], "date": dt.isoformat() if dt else ""})
        except Exception as e:
            print("dialog skipped:", d.id, e)
    return out

def media_kind(m):
    if m.photo: return "photo"
    if m.video or m.video_note: return "video"
    if m.voice or m.audio: return "audio"
    if m.media: return "file"
    return None

@app.get("/api/messages/{chat}")
async def messages(chat: int, before: int = 0, after: int = 0, q: str = "", limit: int = 40):
    kw = {"min_id": after, "limit": 100} if after else {"offset_id": before, "limit": limit}
    if q: kw["search"] = q
    out = []
    async for m in client.iter_messages(chat, **kw):
        try:
            s = None if m.out else m.sender
            out.append({"id": m.id, "text": m.message or "", "html": m.text or "",
                        "date": m.date.isoformat() if m.date else "", "out": bool(m.out), "views": m.views,
                        "sender": (getattr(s, "first_name", None) or getattr(s, "title", "") or "") if s else "",
                        "media": media_kind(m), "file": (m.file.name or "") if m.file else "",
                        "size": (m.file.size or 0) if m.file else 0})
        except Exception as e:
            print("msg skipped", m.id, e)
    return sorted(out, key=lambda x: x["id"])

@app.post("/api/read/{chat}")
async def read(chat: int):
    await client.send_read_acknowledge(chat); return {"ok": True}

@app.post("/api/tg/logout")
async def logout():
    try: await client.log_out()
    except Exception: pass
    try: os.remove(SESS_FILE)
    except Exception: pass
    await make_client(""); avatars.clear(); return {"ok": True}

@app.post("/api/send/{chat}")
async def send(chat: int, body: dict):
    m = await client.send_message(chat, body["text"])
    return {"id": m.id}

@app.get("/api/media/{chat}/{msg_id}")
async def media(chat: int, msg_id: int, request: Request, thumb: int = 0):
    m = await client.get_messages(chat, ids=msg_id)
    if not m or not m.media: raise HTTPException(404)
    if thumb or m.photo:
        try: data = await client.download_media(m, bytes, thumb=-1 if thumb else None)
        except Exception: data = None
        if not data: raise HTTPException(404)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
    mime = m.file.mime_type or mimetypes.guess_type(m.file.name or "")[0] or "application/octet-stream"
    name = (m.file.name or f"{msg_id}{m.file.ext or ''}").replace('"', "")
    size = m.file.size or 0
    start, end, status = 0, max(size - 1, 0), 200
    rng = request.headers.get("range")
    if rng and size:
        a_, _, b_ = rng.replace("bytes=", "").partition("-")
        start = int(a_ or 0); end = min(int(b_) if b_ else size - 1, size - 1); status = 206
    length = end - start + 1
    async def gen():
        sent = 0
        async for ch in client.iter_download(m.media, offset=start, request_size=512 * 1024):
            ch = ch[:length - sent] if size else ch
            sent += len(ch); yield ch
            if size and sent >= length: break
    h = {"Accept-Ranges": "bytes", "Content-Disposition": f'inline; filename="{name}"'}
    if size: h["Content-Length"] = str(length)
    if status == 206: h["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(gen(), status_code=status, media_type=mime, headers=h)

@app.get("/api/avatar/{chat}")
async def avatar(chat: int):
    if chat not in avatars:
        try: avatars[chat] = await client.download_profile_photo(chat, bytes)
        except Exception: avatars[chat] = None
    if not avatars[chat]: raise HTTPException(404)
    return Response(avatars[chat], media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

@app.get("/")
async def index(): return HTMLResponse(PAGE)


PAGE = r"""<!doctype html><html lang="bn"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Ahad TG</title>
<script>document.write('<base href="'+(location.pathname.endsWith("/")?location.pathname:location.pathname+"/")+'">')</script>
<style>
:root{--bg:#0f1a24;--panel:#17212b;--line:#232f3b;--txt:#e8eef4;--mut:#7f91a4;--acc:#4aa3df;--out:#2b5278;--in:#1f2c38}
[hidden]{display:none!important}*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--txt);font:15px/1.4 system-ui,sans-serif;display:flex}
button,input{font:inherit;color:inherit}
#side{width:340px;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column}
#tabs{display:flex;gap:4px;padding:8px;overflow-x:auto;border-bottom:1px solid var(--line)}
#tabs button{background:none;border:0;padding:6px 12px;border-radius:14px;color:var(--mut);white-space:nowrap;cursor:pointer}
#tabs button.on{background:var(--acc);color:#fff}
#q{margin:8px;padding:8px 12px;border-radius:18px;border:0;background:var(--bg)}
#list{overflow-y:auto;flex:1}
.d{display:flex;gap:10px;padding:9px 12px;cursor:pointer;align-items:center}
.d:hover,.d.on{background:var(--line)}
.av{width:46px;height:46px;border-radius:50%;background:var(--acc);flex:none;object-fit:cover;display:grid;place-items:center;font-weight:600}
.d .t{min-width:0;flex:1}.d b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.d small{color:var(--mut);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.u{background:var(--acc);border-radius:10px;padding:0 7px;font-size:12px}
#main{flex:1;display:flex;flex-direction:column;min-width:0}
#head{padding:10px 14px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center}
#back{display:none;background:none;border:0;font-size:22px}
#msgs{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:6px}
.m{max-width:min(560px,85%);background:var(--in);padding:7px 11px;border-radius:12px;align-self:flex-start;word-wrap:break-word;white-space:pre-wrap}
.m.out{background:var(--out);align-self:flex-end}
.m .s{color:var(--acc);font-size:13px;font-weight:600}
.m .i{color:var(--mut);font-size:11px;text-align:right}
.m img,.m video{max-width:100%;border-radius:8px;display:block;margin-bottom:4px}
.m a{color:var(--acc)}
#bar{display:flex;gap:8px;padding:8px;background:var(--panel)}
#txt{flex:1;padding:10px 14px;border-radius:20px;border:0;background:var(--bg)}
#bar button{background:var(--acc);border:0;border-radius:20px;padding:0 18px}
#empty{margin:auto;color:var(--mut)}
#lg{position:fixed;inset:0;background:var(--bg);display:grid;place-items:center}
#lg form{display:flex;gap:8px}#lg input{padding:10px;border-radius:8px;border:1px solid var(--line);background:var(--panel)}
#lg button{background:var(--acc);border:0;border-radius:8px;padding:0 16px}
@media(max-width:760px){#side{width:100%}#main{display:none;width:100%}
body.chat #side{display:none}body.chat #main{display:flex}#back{display:block}}
</style></head><body>
<div id="tl" hidden style="position:fixed;inset:0;background:var(--bg);display:grid;place-items:center;z-index:5"><form onsubmit="tgstep(event)" style="display:flex;flex-direction:column;gap:8px;width:280px"><b>Telegram login</b><input id="tv" placeholder="Phone with country code, e.g. +8801..." style="padding:10px;border-radius:8px;border:1px solid var(--line);background:var(--panel)"><button style="background:var(--acc);border:0;border-radius:8px;padding:10px">Next</button><small id="tm" style="color:#e57"></small><hr style="width:100%;border:0;border-top:1px solid var(--line)"><textarea id="ss" rows="3" placeholder="Or paste session string here" style="padding:10px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--txt);resize:none"></textarea><button type="button" onclick="pasteSess()" style="background:var(--out);border:0;border-radius:8px;padding:10px;color:var(--txt)">Login with session string</button></form></div>
<div id="lg" hidden><form onsubmit="login(event)"><input id="pw" type="password" placeholder="Password" autofocus><button>Login</button></form></div>
<aside id="side"><input id="q" placeholder="Search" oninput="render()"><div id="tabs"></div><div id="list"></div><button onclick="logout()" style="border:0;background:none;color:var(--mut);padding:10px">Logout</button></aside>
<section id="main"><div id="head"><button id="back" onclick="document.body.classList.remove('chat')">←</button><b id="title">Ahad TG</b><form id="tools" hidden onsubmit="searchChat(event)" style="margin-left:auto;display:flex;gap:6px"><input id="cs" placeholder="Search in chat" style="width:130px;padding:6px 10px;border-radius:14px;border:0;background:var(--bg)"><button type="button" title="Mark read" onclick="markRead()" style="background:none;border:0">✓</button><button type="button" title="Reload" onclick="openChat(cur)" style="background:none;border:0">⟳</button></form></div>
<div id="msgs"><div id="empty">Select a chat</div></div>
<form id="bar" onsubmit="send(event)" hidden><input id="txt" placeholder="Message" autocomplete="off"><button>Send</button></form></section>
<script>
const $=s=>document.querySelector(s);let D=[],tab='all',cur=null,oldest=0,busy=false;
const tabs=[['all','All'],['chat','Chats'],['group','Groups'],['channel','Channels'],['bot','Bots']];
async function api(u,o){const r=await fetch(u,o);if(r.status==401){$('#lg').hidden=false;throw 0}if(!r.ok)throw new Error(r.status);return r.json()}
async function login(e){e.preventDefault();const r=await fetch('api/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({password:$('#pw').value})});if(r.ok){$('#lg').hidden=true;load()}else{$('#pw').value='';$('#pw').placeholder='Wrong password ('+r.status+')'}}
$('#tabs').innerHTML=tabs.map(([k,l])=>`<button data-k="${k}" onclick="tab='${k}';render()">${l}</button>`).join('');
async function load(){const st=await api('api/tg/status');if(!st.authorized){$('#tl').hidden=false;return}$('#tl').hidden=true;D=await api('api/dialogs');render()}
async function pasteSess(){const v=$('#ss').value.trim();if(!v)return;$('#tm').textContent='Checking...';
const r=await fetch('api/tg/session',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({session:v})});
const j=await r.json().catch(()=>({}));if(!r.ok){$('#tm').textContent=j.detail||'Error '+r.status;return}
$('#tm').textContent='';$('#ss').value='';$('#tl').hidden=true;load().catch(e=>{if(e)$('#list').textContent='Error: '+e.message})}
let step='phone';
async function tgstep(e){e.preventDefault();const v=$('#tv').value.trim();if(!v)return;
const r=await fetch('api/tg/'+step,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({[step]:v})});
const j=await r.json().catch(()=>({}));if(!r.ok){$('#tm').textContent=j.detail||'Error '+r.status;return}
$('#tm').textContent='';$('#tv').value='';
if(j.need_password){step='password';$('#tv').type='password';$('#tv').placeholder='2FA password'}
else if(j.sent){step='code';$('#tv').placeholder='Code from Telegram app'}
else if(j.ok){$('#tl').hidden=true;load()}}
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function lk(s){return esc(s).replace(/https?:\/\/[^\s<]+/g,u=>`<a href="${u}" target="_blank" rel="noopener">${u}</a>`)}
function render(){const q=$('#q').value.toLowerCase();
document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.k==tab));
$('#list').innerHTML=D.filter(d=>(tab=='all'||d.type==tab)&&d.name.toLowerCase().includes(q)).map(d=>`<div class="d${cur==d.id?' on':''}" onclick="openChat(${d.id})"><img class="av" src="api/avatar/${d.id}" onerror="this.outerHTML='<div class=av>${esc(d.name[0]||'?')}</div>'"><div class="t"><b>${d.pinned?'📌 ':''}${esc(d.name)}</b><small>${esc(d.last)}</small></div>${d.unread?`<span class="u">${d.unread}</span>`:''}</div>`).join('')}
function bubble(m){let med='';const u=`api/media/${cur}/${m.id}`;
if(m.media=='photo')med=`<a href="${u}" target="_blank"><img loading="lazy" src="${u}"></a>`;
else if(m.media=='video')med=`<video controls preload="none" poster="${u}?thumb=1" src="${u}"></video>`;
else if(m.media=='audio')med=`<audio controls preload="none" src="${u}"></audio>`;
else if(m.media)med=`<a href="${u}" download>📎 ${esc(m.file||'file')} (${(m.size/1048576).toFixed(1)} MB)</a><br>`;
return `<div class="m${m.out?' out':''}">${m.sender&&!m.out?`<div class="s">${esc(m.sender)}</div>`:''}${med}${m.html||esc(m.text)}<div class="i">${m.views?'👁 '+m.views+' · ':''}${new Date(m.date).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</div></div>`}
let newest=0,searching=false;
async function openChat(id){cur=id;searching=false;$('#cs').value='';document.body.classList.add('chat');const d=D.find(x=>x.id==id);$('#title').textContent=d.name;$('#bar').hidden=d.type=='channel';$('#tools').hidden=false;render();
try{fill(await api(`api/messages/${id}`))}catch(e){if(e)$('#msgs').textContent='Error: '+e.message}}
function fill(ms){oldest=searching?0:(ms.length?ms[0].id:0);newest=ms.length?ms[ms.length-1].id:0;$('#msgs').innerHTML=ms.length?ms.map(bubble).join(''):'<div id="empty">No messages</div>';$('#msgs').scrollTop=1e9}
async function searchChat(e){e.preventDefault();const q=$('#cs').value.trim();if(!cur)return;if(!q)return openChat(cur);searching=true;fill(await api(`api/messages/${cur}?q=${encodeURIComponent(q)}`))}
async function markRead(){await api('api/read/'+cur,{method:'POST'});const d=D.find(x=>x.id==cur);if(d)d.unread=0;render()}
async function logout(){if(!confirm('Telegram logout?'))return;await api('api/tg/logout',{method:'POST'});location.reload()}
$('#msgs').addEventListener('scroll',async e=>{const el=e.target;if(el.scrollTop>80||busy||!cur||!oldest)return;busy=true;
try{const ms=await api(`api/messages/${cur}?before=${oldest}`);if(ms.length){oldest=ms[0].id;const h=el.scrollHeight;el.insertAdjacentHTML('afterbegin',ms.map(bubble).join(''));el.scrollTop=el.scrollHeight-h}else oldest=0}catch(e){}busy=false});
setInterval(async()=>{if(document.hidden||!cur||searching||!newest)return;try{const ms=await api(`api/messages/${cur}?after=${newest}`);if(ms.length){const el=$('#msgs');const nb=el.scrollHeight-el.scrollTop-el.clientHeight<150;newest=ms[ms.length-1].id;el.insertAdjacentHTML('beforeend',ms.map(bubble).join(''));if(nb)el.scrollTop=1e9}}catch(e){}},8000);
setInterval(async()=>{if(document.hidden)return;try{D=await api('api/dialogs');render()}catch(e){}},30000);
document.addEventListener('keydown',e=>{if(e.key=='Escape')document.body.classList.remove('chat')});
async function send(e){e.preventDefault();const t=$('#txt').value.trim();if(!t)return;$('#txt').value='';
await api('api/send/'+cur,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:t})});openChat(cur)}
load().catch(e=>{if(e)document.querySelector('#list').textContent='Error: '+e.message});
</script></body></html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
