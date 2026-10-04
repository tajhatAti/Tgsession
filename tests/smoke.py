"""
Full backend smoke test for the tgweb/ package using the fake Telethon client.

Covers: site auth, telegram login (session-string ONLY — phone/OTP removed),
auto-login from tgsess.txt, static file serving under a path prefix, dialogs,
messages, media Range streaming, avatars, sending, editing, deleting,
forwarding, reactions, pin, mute, archive, search, gallery, members, upload,
typing, flood wait, error shapes, path-prefix behavior.

Run: /home/user/.venv/bin/python tests/smoke.py
"""
import asyncio
import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'tests'))

TMP = tempfile.mkdtemp(prefix='tgw_test_')
os.chdir(TMP)                      # tgsess.txt is written relative to cwd

import fake_telethon as fake     # noqa: E402

# patch the fake client into every module that constructs TelegramClient
import tgweb.client as client_mod            # noqa: E402
import tgweb.routes.auth as routes_auth_mod  # noqa: E402
client_mod.TelegramClient = fake.FakeTelegramClient
routes_auth_mod.TelegramClient = fake.FakeTelegramClient

import tgweb.config as cfg_mod   # noqa: E402
import tgweb.tgstate as tgstate_mod  # noqa: E402
import tgweb.util as util_mod    # noqa: E402
from tgweb.app import app  # noqa: E402

import uvicorn  # noqa: E402

PASS, FAIL = [], []


def H(hdrs, name, default=None):
    """case-insensitive header lookup (urllib may lowercase keys)"""
    for k, v in (hdrs or {}).items():
        if k.lower() == name.lower():
            return v
    return default


def check(name, cond, info=''):
    if cond:
        PASS.append(name)
        print('  ok  ' + name)
    else:
        FAIL.append(name)
        print('FAIL  ' + name + (('  | ' + str(info)[:300]) if info else ''))


jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
BASE = None
PORT = 8899


def req(method, path, body=None, headers=None, op=None, full=False):
    url = (BASE if not full else 'http://127.0.0.1:%d' % PORT) + path
    data = None
    h = dict(headers or {})
    if body is not None and not isinstance(body, bytes):
        data = json.dumps(body).encode()
        h.setdefault('Content-Type', 'application/json')
    elif isinstance(body, bytes):
        data = body
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        resp = (op or opener).open(r, timeout=60)
        return resp.getcode(), dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def jreq(method, path, body=None, **kw):
    code, hdrs, raw = req(method, path, body, **kw)
    try:
        return code, hdrs, json.loads(raw)
    except Exception:
        return code, hdrs, None


def start_server(port):
    global BASE, PORT
    PORT = port
    BASE = 'http://127.0.0.1:%d' % port
    config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, 'server did not start'
    return server, t


def stop_server(server, t):
    server.should_exit = True
    t.join(timeout=10)


def reset_state():
    asyncio.get_event_loop_policy()
    try:
        asyncio.run(tgstate_mod.state.reset())
    except RuntimeError:
        pass
    # clear caches IN PLACE (modules hold their own references)
    for c in (util_mod.thumb_cache, util_mod.photo_cache, util_mod.avatar_cache):
        c._d.clear()
        c._bytes = 0


def multipart(fields, filefield, filename, content):
    b = '----tgw' + uuid.uuid4().hex
    body = b''
    for k, v in fields.items():
        body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                 % (b, k, v)).encode()
    body += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n' % (b, filefield, filename)).encode()
    body += content + ('\r\n--%s--\r\n' % b).encode()
    return body, {'Content-Type': 'multipart/form-data; boundary=' + b}


# ==========================================================================
print('--- phase A: site auth + telegram login flows (no saved session)')
# ==========================================================================
server, thread = start_server(8899)

code, hdrs, data = jreq('GET', '/api/status')
check('A: status without cookie is 401', code == 401 and data and 'detail' in data, (code, data))

code, hdrs, data = jreq('GET', '/healthz')
check('A: healthz ok', code == 200 and data == {'ok': True}, (code, data))

code, hdrs, data = jreq('POST', '/api/login', {'password': 'wrong'})
check('A: wrong site password 401', code == 401 and 'detail' in data, (code, data))

code, hdrs, data = jreq('POST', '/api/login', {'password': cfg_mod.PASSWORD})
setc = H(hdrs, 'Set-Cookie', '')
check('A: site login ok + httpOnly cookie', code == 200 and 'tgw_auth=' in setc
      and 'HttpOnly' in setc, (code, setc))

code, hdrs, data = jreq('GET', '/api/status')
check('A: status tg=false initially', code == 200 and data['tg'] is False, data)

code, hdrs, data = jreq('POST', '/api/tg/import_session', {'session': 'garbage!!!'})
check('A: garbage session 400', code == 400 and 'valid session' in data['detail'], (code, data))

code, hdrs, data = jreq('POST', '/api/tg/import_session', {'session': fake.make_session('dup')})
check('A: duplicated session 409 mentions IP', code == 409 and 'IP' in data['detail'], (code, data))

code, hdrs, data = jreq('POST', '/api/tg/import_session', {'session': fake.make_session('expired')})
check('A: expired session 400', code == 400, (code, data))

# phone + OTP + 2FA login flow (account sign-in)
code, hdrs, data = jreq('POST', '/api/tg/login_phone', {'phone': '+8801111111111'})
check('A: send code ok', code == 200 and data.get('ok'), (code, data))
code, hdrs, data = jreq('POST', '/api/tg/login_code', {'code': '00000'})
check('A: wrong code 400', code == 400, (code, data))
code, hdrs, data = jreq('POST', '/api/tg/login_code', {'code': '12 345'})
check('A: correct code -> need_password', code == 200 and data.get('need_password'), (code, data))
code, hdrs, data = jreq('POST', '/api/tg/login_password', {'password': 'wrong'})
check('A: wrong 2fa 400', code == 400, (code, data))
code, hdrs, data = jreq('POST', '/api/tg/login_password', {'password': 'secret'})
check('A: 2fa ok -> logged in as Ahad', code == 200 and data['me']['name'] == 'Ahad', (code, data))
check('A: tgsess.txt written', os.path.exists('tgsess.txt'))
code, hdrs, data = jreq('POST', '/api/tg/logout')
check('A: logout before session import', code == 200, (code, data))
check('A: tgsess.txt removed', not os.path.exists('tgsess.txt'))

# session string login (the second method)
code, hdrs, data = jreq('POST', '/api/tg/import_session', {'session': fake.make_session('good')})
check('A: import session -> logged in as Ahad', code == 200 and data['me']['name'] == 'Ahad', (code, data))
check('A: tgsess.txt written', os.path.exists('tgsess.txt'))

code, hdrs, data = jreq('GET', '/api/status')
check('A: status tg=true', code == 200 and data['tg'] is True, data)

code, hdrs, data = jreq('POST', '/api/tg/logout')
check('A: telegram logout ok', code == 200, (code, data))
check('A: tgsess.txt removed on logout', not os.path.exists('tgsess.txt'))
code, hdrs, data = jreq('GET', '/api/status')
check('A: status tg=false after logout', code == 200 and data['tg'] is False, data)

stop_server(server, thread)

# ==========================================================================
print('--- phase B: auto-login from saved tgsess.txt (restart simulation)')
# ==========================================================================
with open('tgsess.txt', 'w') as f:
    f.write(fake.make_session('good'))
reset_state()
server, thread = start_server(8898)

tg = False
for _ in range(100):
    code, hdrs, data = jreq('GET', '/api/status')
    if code == 200 and data.get('tg'):
        tg = True
        break
    time.sleep(0.1)
check('B: auto-login from session file', tg, data)

# ==========================================================================
print('--- phase C: dialogs / messages / media / actions')
# ==========================================================================
code, hdrs, data = jreq('GET', '/api/dialogs')
dlgs = data['dialogs'] if data else []
check('C: dialogs 200', code == 200 and len(dlgs) >= 7, (code, len(dlgs) if data else data))
by_id = {d['id']: d for d in dlgs}
check('C: pinned dialogs first', dlgs[0].get('pinned') is True and dlgs[1].get('pinned') is True,
      [d['name'] for d in dlgs[:3]])
check('C: group dialog unread=12', by_id[fake.ID_GROUP]['unread'] == 12, by_id.get(fake.ID_GROUP))
check('C: muted dialog detected', by_id[fake.ID_BOB]['muted'] is True, by_id.get(fake.ID_BOB))
check('C: archived dialog flagged', by_id[fake.ID_OLD]['archived'] is True, by_id.get(fake.ID_OLD))
check('C: bot type', by_id[fake.ID_BOT]['type'] == 'bot', by_id.get(fake.ID_BOT))
check('C: saved messages named', by_id[fake.ID_ME]['name'] == 'Saved Messages'
      and by_id[fake.ID_ME]['is_self'] is True, by_id.get(fake.ID_ME))
check('C: channel members count', by_id[fake.ID_NEWS]['members'] == 1000, by_id.get(fake.ID_NEWS))
check('C: me passed', data.get('me', {}).get('name') == 'Ahad', data.get('me'))

G = fake.ID_GROUP
code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d&limit=50' % G)
msgs = data['messages'] if data else []
check('C: messages 200 newest-first', code == 200 and msgs[0]['id'] == 124, (code, msgs[:1]))
m120 = next(m for m in msgs if m['id'] == 120)
check('C: bold entity -> strong', '<strong>bold</strong>' in m120['html'], m120['html'])
check('C: sender serialized', m120['sender']['name'] == 'Alice Wonder', m120['sender'])
m119 = next(m for m in msgs if m['id'] == 119)
check('C: reply snippet batched', m119['reply_to']['id'] == 118
      and m119['reply_to']['name'] == 'Alice Wonder'
      and 'Photo' in (m119['reply_to']['snippet'] or ''), m119['reply_to'])
m117 = next(m for m in msgs if m['id'] == 117)
check('C: service message humanized', m117['service'] is True and 'pinned' in m117['html'], m117)
m116 = next(m for m in msgs if m['id'] == 116)
check('C: video media meta', m116['media']['kind'] == 'video' and m116['media']['size'] == 3000000
      and m116['media']['duration'] == 60 and m116['media']['w'] == 640, m116['media'])
m112 = next(m for m in msgs if m['id'] == 112)
check('C: reactions serialized with me-flag',
      {'emoji': '❤️', 'count': 1, 'me': True} in m112['reactions'], m112.get('reactions'))
m109 = next(m for m in msgs if m['id'] == 109)
check('C: spoiler rendered', '<span class="spoiler">spoiler</span>' in m109['html'], m109['html'])
m108 = next(m for m in msgs if m['id'] == 108)
check('C: null date survives', m108['date'] is None and m108['html'] == 'no date message', m108)
m107 = next(m for m in msgs if m['id'] == 107)
check('C: reply-to-deleted is null-safe', m107['reply_to']['id'] == 999
      and m107['reply_to']['name'] is None, m107['reply_to'])
m111 = next(m for m in msgs if m['id'] == 111)
check('C: webpage preview', m111['webpage']['url'] == 'https://example.com'
      and m111['webpage']['title'] == 'Example Domain', m111['webpage'])
m115 = next(m for m in msgs if m['id'] == 115)
check('C: voice meta', m115['media']['kind'] == 'voice' and m115['out'] is True, m115['media'])
m114 = next(m for m in msgs if m['id'] == 114)
check('C: file meta name+size', m114['media']['name'] == 'report.pdf'
      and m114['media']['size'] == 123456, m114['media'])
check('C: read state included', data.get('read_inbox_max_id') == 105
      and data.get('unread_count') == 12, {k: data.get(k) for k in
      ('read_inbox_max_id', 'unread_count')})

code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d&offset_id=110&limit=20' % G)
ids = [m['id'] for m in data['messages']]
check('C: offset_id pagination (older)', all(i < 110 for i in ids) and len(ids) == 20, ids)

code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d&min_id=118' % G)
ids = [m['id'] for m in data['messages']]
check('C: min_id poll (newer)', sorted(ids) == [119, 120, 123, 124], ids)

code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d&anchor_id=112&limit=10' % G)
ids = [m['id'] for m in data['messages']]
check('C: anchor jump includes anchor', ids and ids[0] == 112 and len(ids) == 10, ids)

# ---- media
code, hdrs, raw = req('GET', '/api/media/%d/118?kind=thumb' % G)
check('C: photo thumb 200 png + cache', code == 200 and raw[:8] == b'\x89PNG\r\n\x1a\n'
      and 'max-age' in H(hdrs, 'Cache-Control', ''), (code, H(hdrs, 'Cache-Control')))

code, hdrs, raw = req('GET', '/api/media/%d/118?kind=photo' % G)
check('C: photo full 200', code == 200 and raw == fake.PNG_PHOTO, (code, len(raw)))

code, hdrs, raw = req('GET', '/api/media/%d/116?kind=file' % G)
payload = fake.PAYLOADS[9001]
check('C: full file 200 + headers', code == 200 and raw == payload
      and H(hdrs, 'Content-Length') == '3000000'
      and H(hdrs, 'Accept-Ranges') == 'bytes'
      and H(hdrs, 'Content-Type') == 'video/mp4',
      (code, H(hdrs, 'Content-Length'), H(hdrs, 'Accept-Ranges'), H(hdrs, 'Content-Type')))

code, hdrs, raw = req('GET', '/api/media/%d/116?kind=file' % G, headers={'Range': 'bytes=100-199'})
check('C: range 100-199 -> 206 exact', code == 206 and raw == payload[100:200]
      and H(hdrs, 'Content-Length') == '100'
      and H(hdrs, 'Content-Range') == 'bytes 100-199/3000000',
      (code, H(hdrs, 'Content-Range'), len(raw)))

code, hdrs, raw = req('GET', '/api/media/%d/116?kind=file' % G, headers={'Range': 'bytes=2999990-'})
check('C: open-ended range', code == 206 and raw == payload[2999990:]
      and H(hdrs, 'Content-Length') == '10', (code, len(raw)))

code, hdrs, raw = req('GET', '/api/media/%d/116?kind=file' % G, headers={'Range': 'bytes=-500'})
check('C: suffix range -500', code == 206 and raw == payload[-500:], (code, len(raw)))

code, hdrs, raw = req('GET', '/api/media/%d/116?kind=file' % G,
                      headers={'Range': 'bytes=4000000-5000000'})
check('C: unsatisfiable range -> 416', code == 416 and H(hdrs, 'Content-Range') == 'bytes */3000000',
      (code, H(hdrs, 'Content-Range')))

code, hdrs, raw = req('GET', '/api/media/%d/116?kind=file&dl=1' % G)
check('C: download disposition', code == 200 and 'attachment' in H(hdrs, 'Content-Disposition', '')
      and 'clip.mp4' in H(hdrs, 'Content-Disposition', ''), H(hdrs, 'Content-Disposition'))

code, hdrs, raw = req('GET', '/api/media/%d/114?kind=thumb' % G)
check('C: doc without thumb -> 404', code == 404, code)

code, hdrs, raw = req('GET', '/api/media/%d/114?kind=file' % G)
check('C: pdf streams', code == 200 and raw == fake.PAYLOADS[9003]
      and H(hdrs, 'Content-Type') == 'application/pdf', (code, len(raw)))

code, hdrs, raw = req('GET', '/api/media/%d/113?kind=thumb' % G)
check('C: sticker thumb 200', code == 200, code)

code, hdrs, raw = req('GET', '/api/avatar/101')
check('C: avatar 200 png', code == 200 and raw == fake.PNG_AVATAR, (code, len(raw)))
code, hdrs, raw = req('GET', '/api/avatar/501')
check('C: avatar without photo 404', code == 404, code)

# ---- send / edit / react / pin / forward / read / delete
code, hdrs, data = jreq('POST', '/api/send', {'chat_id': G, 'text': 'hello <b>world</b>', 'reply_to': 112})
m = data.get('message') if data else None
check('C: send html ok', code == 200 and m and m['id'] == 125 and m['out'] is True, (code, data))
check('C: sent reply_to populated', m and m['reply_to']['id'] == 112
      and 'react' in (m['reply_to']['snippet'] or ''), m and m['reply_to'])

code, hdrs, data = jreq('POST', '/api/send', {'chat_id': G, 'text': 'math: 5 < 10 but 3 > 1'})
m2 = data.get('message') if data else None
check('C: send invalid-html falls back to plain', code == 200 and m2
      and m2['raw'] == 'math: 5 < 10 but 3 > 1', (code, data))

code, hdrs, data = jreq('POST', '/api/edit', {'chat_id': G, 'msg_id': 125, 'text': 'hello edited'})
check('C: edit ok', code == 200 and data['message']['raw'] == 'hello edited', (code, data))

code, hdrs, data = jreq('POST', '/api/react', {'chat_id': G, 'msg_id': 112, 'emoji': '🔥'})
reacts = data.get('message', {}).get('reactions') if data else None
check('C: react adds emoji', code == 200 and any(r['emoji'] == '🔥' and r['me'] for r in reacts or []),
      (code, reacts))

code, hdrs, data = jreq('POST', '/api/react', {'chat_id': G, 'msg_id': 112, 'emoji': ''})
reacts = data.get('message', {}).get('reactions') if data else None
check('C: react toggle removes mine', code == 200 and reacts is not None
      and all(not r['me'] for r in reacts), (code, reacts))

code, hdrs, data = jreq('POST', '/api/pin', {'chat_id': G, 'msg_id': 125, 'pinned': True})
check('C: pin ok', code == 200, (code, data))

code, hdrs, data = jreq('POST', '/api/forward',
                        {'from_chat_id': G, 'msg_ids': [120], 'to_chat_id': fake.ID_BOB})
check('C: forward ok', code == 200, (code, data))
code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d' % fake.ID_BOB)
fmsg = data['messages'][0] if data and data['messages'] else None
check('C: forwarded message exists', fmsg and fmsg['forward_from'] == 'Alice Wonder'
      and 'bold part' in fmsg['raw'], fmsg)

code, hdrs, data = jreq('POST', '/api/read', {'chat_id': G, 'max_id': 122})
check('C: mark read ok', code == 200, (code, data))
code, hdrs, data = jreq('GET', '/api/dialogs')
by_id2 = {d['id']: d for d in data['dialogs']}
check('C: unread cleared after read', by_id2[G]['unread'] == 0, by_id2[G]['unread'])

code, hdrs, data = jreq('POST', '/api/typing', {'chat_id': G})
check('C: typing ok', code == 200, (code, data))

code, hdrs, data = jreq('POST', '/api/mute', {'chat_id': G, 'muted': True})
check('C: mute ok', code == 200, (code, data))
code, hdrs, data = jreq('GET', '/api/dialogs')
by_id3 = {d['id']: d for d in data['dialogs']}
check('C: dialog now muted', by_id3[G]['muted'] is True, by_id3[G]['muted'])

code, hdrs, data = jreq('POST', '/api/archive', {'chat_id': fake.ID_ALICE, 'archived': True})
check('C: archive ok', code == 200, (code, data))
code, hdrs, data = jreq('GET', '/api/dialogs')
by_id4 = {d['id']: d for d in data['dialogs']}
check('C: dialog archived', by_id4[fake.ID_ALICE]['archived'] is True, by_id4[fake.ID_ALICE])

code, hdrs, data = jreq('GET', '/api/search?chat_id=%d&q=bold' % G)
check('C: in-chat search', code == 200 and any(m['id'] == 120 for m in data['messages']),
      (code, data))

code, hdrs, data = jreq('GET', '/api/gallery?chat_id=%d&tab=media' % G)
gids = [m['id'] for m in data['messages']]
check('C: gallery media tab', code == 200 and 118 in gids and 116 in gids and 114 not in gids, gids)
code, hdrs, data = jreq('GET', '/api/gallery?chat_id=%d&tab=files' % G)
gids = [m['id'] for m in data['messages']]
check('C: gallery files tab', 114 in gids and 116 not in gids, gids)
code, hdrs, data = jreq('GET', '/api/gallery?chat_id=%d&tab=voice' % G)
gids = [m['id'] for m in data['messages']]
check('C: gallery voice tab', gids == [115], gids)
code, hdrs, data = jreq('GET', '/api/gallery?chat_id=%d&tab=links' % G)
gids = [m['id'] for m in data['messages']]
check('C: gallery links tab', 111 in gids, gids)

code, hdrs, data = jreq('GET', '/api/members?chat_id=%d' % G)
mem = data.get('members') if data else []
check('C: members list + roles', code == 200 and len(mem) == 4
      and any(u['name'] == 'Alice Wonder' and u['role'] == 'admin' for u in mem)
      and any(u['role'] == 'owner' for u in mem), (code, mem))

code, hdrs, data = jreq('GET', '/api/members?chat_id=%d' % fake.ID_NEWS)
check('C: members unavailable -> note, no crash', code == 200
      and 'not available' in (data.get('note') or '')
      and data['members'] == [], (code, data))

# upload
body, hdrs_mp = multipart({'chat_id': str(fake.ID_ALICE), 'caption': 'my upload'},
                          'file', 'test_upload.txt', b'hello upload bytes 123')
code, hdrs, raw = req('POST', '/api/upload', body, headers=hdrs_mp)
up = json.loads(raw) if raw else {}
um = up.get('message') or {}
check('C: upload ok', code == 200 and um.get('media', {}).get('name') == 'test_upload.txt'
      and um.get('media', {}).get('size') == 22, (code, um))
code, hdrs, raw = req('GET', '/api/media/%d/%d?kind=file' % (fake.ID_ALICE, um.get('id', 0)))
check('C: uploaded file downloads identical', code == 200 and raw == b'hello upload bytes 123',
      (code, len(raw)))

# delete
code, hdrs, data = jreq('POST', '/api/delete', {'chat_id': G, 'msg_ids': [125, 122]})
check('C: delete ok', code == 200, (code, data))
code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d&min_id=119' % G)
check('C: deleted gone', all(m['id'] not in (121, 122) for m in data['messages']),
      [m['id'] for m in data['messages']])

# ---- albums (grouped media) ----
m124 = next(m for m in msgs if m['id'] == 124)
m123 = next(m for m in msgs if m['id'] == 123)
check('C: album grouped_id set on both', m124['grouped_id'] is not None
      and m124['grouped_id'] == m123['grouped_id'], (m124['grouped_id'], m123['grouped_id']))

# ---- bot inline buttons + reply keyboard ----
code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d' % fake.ID_BOT)
bmsgs = data['messages'] if data else []
b3 = next(m for m in bmsgs if m['id'] == 3)
b2 = next(m for m in bmsgs if m['id'] == 2)
check('C: inline buttons serialized', b3['buttons'] and b3['buttons'][0][0]['text'] == 'Ping me'
      and b3['buttons'][0][0].get('data') and b3['buttons'][0][1].get('url') == 'https://example.com'
      and b3['buttons'][1][0].get('copy') == 'tgw-1234', b3['buttons'])
check('C: bot reply keyboard serialized', b2['keyboard'] == [['Help', 'About']], b2['keyboard'])

code, hdrs, data = jreq('POST', '/api/callback',
                        {'chat_id': fake.ID_BOT, 'msg_id': 3, 'data': b3['buttons'][0][0]['data']})
check('C: button callback -> answer + alert', code == 200 and 'Pong' in (data or {}).get('answer', '')
      and (data or {}).get('alert') is True, (code, data))

# ---- profile (user / channel / group) ----
code, hdrs, data = jreq('GET', '/api/profile?chat_id=%d' % fake.ID_ALICE)
check('C: user profile (bio+username+status)', code == 200 and data['bio'] == 'Hello, I am Alice \U0001f44b'
      and data['username'] == 'alice' and data['status'] and data['type'] == 'user'
      and data['common'] == 3, (code, data))
code, hdrs, data = jreq('GET', '/api/profile?chat_id=%d' % fake.ID_NEWS)
check('C: channel profile (members+bio)', code == 200 and data['members'] == 12500
      and data['type'] == 'channel' and 'breaking news' in (data['bio'] or ''), (code, data))
code, hdrs, data = jreq('GET', '/api/profile?chat_id=%d' % G)
check('C: group profile (members)', code == 200 and data['members'] == 3
      and data['type'] == 'group', (code, data))

# ---- forward with hidden sender (drop_author) ----
code, hdrs, data = jreq('POST', '/api/forward',
                        {'from_chat_id': G, 'msg_ids': [120], 'to_chat_id': fake.ID_BOB,
                         'hide_sender': True})
check('C: forward hide_sender ok', code == 200, (code, data))
code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d' % fake.ID_BOB)
hn = data['messages'][0] if data and data['messages'] else None
check('C: hidden forward has no fwd header', hn and hn['forward_from'] is None
      and 'bold part' in hn['raw'], hn)

# ---- global search ----
code, hdrs, data = jreq('GET', '/api/search_global?q=bold')
check('C: global search finds message + chat name', code == 200
      and any(m['id'] == 120 and m['chat']['name'] for m in (data or {}).get('messages', []))
      and len((data or {}).get('chats', [])) >= 0, (code, data))
code, hdrs, data = jreq('GET', '/api/search_global?q=alice')
check('C: global search finds chat', code == 200
      and any(c.get('id') == fake.ID_ALICE for c in (data or {}).get('chats', [])), (code, data))

# ---- voice upload (recorded in browser) ----
vbody, vhdrs = multipart({'chat_id': str(fake.ID_ALICE), 'caption': '', 'voice': '1',
                          'duration': '7'}, 'file', 'voice.webm', b'\x00' * 64)
code, hdrs, data = req('POST', '/api/upload', vbody, vhdrs)
try:
    vdata = json.loads(data)
except Exception:
    vdata = {}
vm = (vdata or {}).get('message') or {}
check('C: voice upload -> voice kind + duration', code == 200
      and vm.get('media', {}).get('kind') == 'voice'
      and vm.get('media', {}).get('duration') == 7, (code, vm.get('media')))

# ---- edit own profile (Settings) ----
code, hdrs, data = jreq('POST', '/api/tg/me/edit',
                        {'first_name': 'Ahad', 'about': 'night build bio'})
check('C: me edit ok', code == 200 and (data or {}).get('me', {}).get('name') == 'Ahad', (code, data))
code, hdrs, data = jreq('GET', '/api/profile?chat_id=%d' % fake.ID_ME)
check('C: own profile shows edited bio', code == 200 and data['bio'] == 'night build bio'
      and data['is_self'] is True, (code, data))

# ---- error handling
code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d' % fake.FLOOD_CHAT)
check('C: flood wait -> 429 + Retry-After', code == 429 and H(hdrs, 'Retry-After') == '42'
      and '42' in (data or {}).get('detail', ''), (code, H(hdrs, 'Retry-After'), data))

code, hdrs, data = jreq('GET', '/api/messages?chat_id=%d' % fake.UNKNOWN_CHAT)
check('C: unknown chat -> 400 detail, no trace', code == 400
      and 'detail' in (data or {}) and 'Traceback' not in str(data), (code, data))

code, hdrs, data = jreq('GET', '/api/dialogs', op=urllib.request.build_opener())
check('C: no cookie -> 401 everywhere', code == 401 and 'detail' in (data or {}), code)

# ---- path prefix behavior
code, hdrs, raw = req('GET', '/live/myslug/')
check('P: /live/slug/ serves app html', code == 200 and b'createElement' in raw
      and b'<div id="toasts">' in raw, (code, len(raw)))
code, hdrs, raw = req('GET', '/live/myslug')
check('P: /live/slug (no slash) serves app html', code == 200 and b'createElement' in raw, code)
code, hdrs, data = jreq('GET', '/live/myslug/api/dialogs')
check('P: /live/slug/api/dialogs works', code == 200 and 'dialogs' in (data or {}), (code, data))
code, hdrs, data = jreq('GET', '/live/myslug/api/status')
check('P: /live/slug/api/status works', code == 200, (code, data))
code, hdrs, raw = req('GET', '/live/myslug/manifest.webmanifest')
check('P: manifest under prefix', code == 200 and b'TgWeb' in raw, (code, raw[:80]))
code, hdrs, raw = req('GET', '/live/myslug/icons/icon-192.png')
check('P: icon under prefix', code == 200 and raw[:8] == b'\x89PNG\r\n\x1a\n', code)
code, hdrs, raw = req('GET', '/manifest.webmanifest')
check('P: manifest at root', code == 200, code)
code, hdrs, raw = req('GET', '/icons/icon-512.png')
check('P: icon at root', code == 200 and raw[:4] == b'\x89PNG', code)
code, hdrs, raw = req('GET', '/live/myslug/healthz')
check('P: healthz under prefix', code == 200 and b'ok' in raw, (code, raw[:40]))

stop_server(server, thread)

# ==========================================================================
print('--- phase D: bad session files at startup never crash the app')
# ==========================================================================
# D1: corrupt file
with open('tgsess.txt', 'w') as f:
    f.write('n0t-a-session')
reset_state()
server, thread = start_server(8897)
code, hdrs, data = jreq('GET', '/api/status')
check('D: corrupt session -> login screen, file removed',
      code == 200 and data['tg'] is False and not os.path.exists('tgsess.txt')
      and 'corrupt' in (data.get('error') or ''), (code, data))
stop_server(server, thread)

# D2: duplicated key at startup
with open('tgsess.txt', 'w') as f:
    f.write(fake.make_session('dup'))
reset_state()
server, thread = start_server(8896)
code, hdrs, data = jreq('GET', '/api/status')
check('D: duplicated session -> clear error, file removed',
      code == 200 and data['tg'] is False and not os.path.exists('tgsess.txt')
      and 'IP' in (data.get('error') or ''), (code, data))
stop_server(server, thread)

# D3: unauthorized (expired) session file
with open('tgsess.txt', 'w') as f:
    f.write(fake.make_session('expired'))
reset_state()
server, thread = start_server(8895)
code, hdrs, data = jreq('GET', '/api/status')
check('D: expired session -> login screen, file removed',
      code == 200 and data['tg'] is False and not os.path.exists('tgsess.txt'), (code, data))
stop_server(server, thread)

# ==========================================================================
print()
print('=' * 60)
print('PASSED: %d   FAILED: %d' % (len(PASS), len(FAIL)))
if FAIL:
    print('Failed checks:')
    for f in FAIL:
        print('  - ' + f)
print('test workspace:', TMP)
sys.exit(1 if FAIL else 0)
