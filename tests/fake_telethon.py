"""
A fake TelegramClient that mimics the exact subset of Telethon's API used by
main.py. Used by tests/smoke.py to exercise the whole backend without network.

Session auth_key bytes decide the account state:
  b'\\xAA'*256 -> valid + authorized
  b'\\xBB'*256 -> AuthKeyDuplicatedError on connect
  b'\\xCC'*256 -> connects but not authorized (expired/revoked)
  fresh/None   -> not authorized (phone login possible)
"""
import struct
import zlib
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from telethon import types
from telethon import errors as tg_errors
from telethon.crypto import AuthKey
from telethon.sessions import StringSession
from telethon.utils import get_peer_id

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)

FLOOD_CHAT = -999
UNKNOWN_CHAT = -424242


def _peer_of(chat_id):
    """marked int -> Peer object (telethon convention)."""
    if chat_id >= 0:
        return types.PeerUser(user_id=chat_id)
    if chat_id > -1000000000000:
        return types.PeerChat(chat_id=-chat_id)
    return types.PeerChannel(channel_id=-chat_id - 1000000000000)


def _chat_id_of(x):
    """anything (marked int, entity, input peer, peer) -> marked int."""
    if isinstance(x, int):
        return x
    for t, make in ((types.User, lambda e: types.PeerUser(user_id=e.id)),
                    (types.Chat, lambda e: types.PeerChat(chat_id=e.id)),
                    (types.Channel, lambda e: types.PeerChannel(channel_id=e.id)),
                    (types.InputPeerUser, lambda e: types.PeerUser(user_id=e.user_id)),
                    (types.InputPeerChat, lambda e: types.PeerChat(chat_id=e.chat_id)),
                    (types.InputPeerChannel, lambda e: types.PeerChannel(channel_id=e.channel_id)),
                    (types.PeerUser, lambda e: e),
                    (types.PeerChat, lambda e: e),
                    (types.PeerChannel, lambda e: e)):
        if isinstance(x, t):
            return get_peer_id(make(x))
    if isinstance(x, types.InputPeerSelf):
        return 777
    return None


# ---------------------------------------------------------------- media bytes
def _png(w, h, rgb):
    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    rows = b''
    for _ in range(h):
        rows += b'\x00' + bytes(rgb) * w
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(rows, 6))
    png += chunk(b'IEND', b'')
    return png


PNG_THUMB = _png(8, 8, (200, 60, 60))
PNG_PHOTO = _png(80, 60, (60, 120, 220))
PNG_AVATAR = _png(4, 4, (90, 200, 90))


def _pattern(size):
    return bytes(((i * 31 + 7) % 256) for i in range(size))


# ---------------------------------------------------------------- entities
ME = types.User(id=777, access_hash=1, first_name='Ahad', username='ahad', phone='+8801111111111',
                is_self=True, status=types.UserStatusOnline(expires=NOW))
ALICE = types.User(id=101, access_hash=1, first_name='Alice', last_name='Wonder', username='alice',
                   status=types.UserStatusRecently(),
                   photo=types.UserProfilePhoto(photo_id=101, dc_id=2, has_video=False,
                                                stripped_thumb=b'\x01\x02'))
BOB = types.User(id=501, access_hash=1, first_name='Bob', username='bob')
BOT = types.User(id=401, access_hash=1, first_name='StoreBot', username='storebot', bot=True)
OLD = types.User(id=601, access_hash=1, first_name='Old', last_name='Friend')
GROUP = types.Chat(id=201, title='Test Group', participants_count=5,
                   photo=types.ChatPhoto(photo_id=201, dc_id=2), date=NOW, version=1)
NEWS = types.Channel(id=301, access_hash=1, title='News Channel', username='news_ch', broadcast=True,
                     participants_count=1000, photo=types.ChatPhoto(photo_id=301, dc_id=2),
                     date=NOW)
SUPER = types.Channel(id=302, access_hash=1, title='Super Group', username='super_gr', megagroup=True,
                      participants_count=42, photo=types.ChatPhoto(photo_id=302, dc_id=2),
                      date=NOW)

ENTITIES = {777: ME, 101: ALICE, 501: BOB, 401: BOT, 601: OLD,
            get_peer_id(types.PeerChat(chat_id=201)): GROUP,
            get_peer_id(types.PeerChannel(channel_id=301)): NEWS,
            get_peer_id(types.PeerChannel(channel_id=302)): SUPER}

ID_ME, ID_ALICE, ID_BOB, ID_BOT, ID_OLD = 777, 101, 501, 401, 601
ID_GROUP = get_peer_id(types.PeerChat(chat_id=201))                      # -201
ID_NEWS = get_peer_id(types.PeerChannel(channel_id=301))                 # -1000000000301
ID_SUPER = get_peer_id(types.PeerChannel(channel_id=302))

# ---------------------------------------------------------------- documents
PAYLOADS = {}


def _doc(doc_id, mime, size, attrs):
    return types.Document(id=doc_id, access_hash=1, file_reference=b'x', date=NOW,
                          mime_type=mime, size=size, dc_id=2, attributes=attrs)


VIDEO_DOC = _doc(9001, 'video/mp4', 3_000_000, [
    types.DocumentAttributeVideo(duration=60, w=640, h=360, supports_streaming=True),
    types.DocumentAttributeFilename(file_name='clip.mp4')])
PAYLOADS[9001] = _pattern(3_000_000)

VOICE_DOC = _doc(9002, 'audio/ogg', 30_000, [
    types.DocumentAttributeAudio(duration=12, voice=True, title=None, performer=None)])
PAYLOADS[9002] = _pattern(30_000)

FILE_DOC = _doc(9003, 'application/pdf', 123_456, [
    types.DocumentAttributeFilename(file_name='report.pdf')])
PAYLOADS[9003] = _pattern(123_456)

STICKER_DOC = _doc(9004, 'image/webp', 50_000, [
    types.DocumentAttributeSticker(alt='🙂', stickerset=types.InputStickerSetEmpty()),
    types.DocumentAttributeImageSize(w=512, h=512)])
PAYLOADS[9004] = _pattern(50_000)

PHOTO = types.Photo(id=7001, access_hash=1, file_reference=b'x', date=NOW, dc_id=2,
                    sizes=[types.PhotoSize(type='m', w=800, h=600, size=50_000),
                           types.PhotoSize(type='x', w=1600, h=1200, size=180_000)])


# ---------------------------------------------------------------- messages
GP = types.PeerChat(chat_id=201)
MESSAGES = {}   # (chat_id, msg_id) -> Message


def _msg(mid, chat_peer, text='', sender=None, out=False, media=None, entities=None,
         reply_to=None, reactions=None, date=None, views=None, action=None,
         fwd_from=None, no_date=False):
    d = None if no_date else (date if date is not None else NOW - timedelta(minutes=(200 - mid)))
    kwargs = dict(
        id=mid, peer_id=chat_peer, date=d,
        message=text, out=out, entities=entities, media=media,
        reply_to=types.MessageReplyHeader(reply_to_msg_id=reply_to) if reply_to else None,
        reactions=reactions, views=views,
        from_id=types.PeerUser(user_id=sender.id) if sender else None,
        fwd_from=fwd_from)
    if action is not None:
        kwargs['action'] = action
        return types.MessageService(**kwargs)
    return types.Message(**kwargs)


def _add(chat_id, m):
    MESSAGES[(chat_id, m.id)] = m


_add(ID_GROUP, _msg(120, GP, 'latest message with bold part', sender=ALICE,
                    entities=[types.MessageEntityBold(offset=20, length=4)]))
_add(ID_GROUP, _msg(119, GP, 'a reply from bob', sender=BOB, reply_to=118))
_add(ID_GROUP, _msg(118, GP, '', sender=ALICE, media=types.MessageMediaPhoto(photo=PHOTO)))
_add(ID_GROUP, _msg(117, GP, '', sender=BOB, action=types.MessageActionPinMessage()))
_add(ID_GROUP, _msg(116, GP, 'watch this', sender=ALICE,
                    media=types.MessageMediaDocument(document=VIDEO_DOC)))
_add(ID_GROUP, _msg(115, GP, '', sender=BOB, out=True,
                    media=types.MessageMediaDocument(document=VOICE_DOC)))
_add(ID_GROUP, _msg(114, GP, 'the report', sender=ALICE,
                    media=types.MessageMediaDocument(document=FILE_DOC)))
_add(ID_GROUP, _msg(113, GP, '', sender=ALICE,
                    media=types.MessageMediaDocument(document=STICKER_DOC)))
_add(ID_GROUP, _msg(112, GP, 'react to me', sender=BOB, reactions=types.MessageReactions(
    results=[types.ReactionCount(reaction=types.ReactionEmoji(emoticon='👍'), count=3),
             types.ReactionCount(reaction=types.ReactionEmoji(emoticon='❤️'), count=1,
                                 chosen_order=0)])))
_add(ID_GROUP, _msg(111, GP, 'look at https://example.com page', sender=ALICE,
                    media=types.MessageMediaWebPage(webpage=types.WebPage(
                        id=1, url='https://example.com', display_url='example.com', hash=1,
                        title='Example Domain',
                        description='Example test page', site_name='example.com'))))
_add(ID_GROUP, _msg(110, GP, 'poll below', sender=ALICE,
                    media=types.MessageMediaPoll(
                        poll=types.Poll(id=1, question=types.TextWithEntities(
                                            text='Best color?', entities=[]),
                                        answers=[types.PollAnswer(text=types.TextWithEntities(
                                            text='Blue', entities=[]), option=b'0'),
                                                 types.PollAnswer(text=types.TextWithEntities(
                                            text='Red', entities=[]), option=b'1')],
                                        hash=1),
                        results=types.PollResults(total_voters=10, results=[
                            types.PollAnswerVoters(option=b'0', voters=8, chosen=True),
                            types.PollAnswerVoters(option=b'1', voters=2)]))))
_add(ID_GROUP, _msg(109, GP, 'secret spoiler text', sender=ALICE,
                    entities=[types.MessageEntitySpoiler(offset=7, length=7)]))
_add(ID_GROUP, _msg(108, GP, 'no date message', sender=BOB, no_date=True))
_add(ID_GROUP, _msg(107, GP, 'reply to deleted', sender=ALICE, reply_to=999))
_add(ID_GROUP, _msg(106, GP, 'forwarded thing', sender=BOB,
                    fwd_from=types.MessageFwdHeader(from_id=types.PeerUser(user_id=101),
                                                    date=NOW)))
for i in range(20, 106):
    _add(ID_GROUP, _msg(i, GP, 'older message number %d' % i,
                        sender=(ALICE if i % 2 else BOB), out=(i % 5 == 0)))

UP = types.PeerUser(user_id=101)
_add(ID_ALICE, _msg(25, UP, 'hello from alice', sender=ALICE))
_add(ID_ALICE, _msg(24, UP, 'hi alice, my reply', out=True, reply_to=25, sender=ME))
_add(ID_ALICE, _msg(23, UP, 'x < y comparison', sender=ALICE))
_add(ID_ALICE, _msg(22, UP, '', sender=ALICE, media=types.MessageMediaPhoto(photo=PHOTO)))
for i in range(5, 22):
    _add(ID_ALICE, _msg(i, UP, 'alice history %d' % i, sender=ALICE))

NP = types.PeerChannel(channel_id=301)
_add(ID_NEWS, _msg(305, NP, 'big news today', views=1500))
_add(ID_NEWS, _msg(304, NP, 'video post', views=900,
                   media=types.MessageMediaDocument(document=VIDEO_DOC)))
_add(ID_NEWS, _msg(303, NP, 'file post', views=10,
                   media=types.MessageMediaDocument(document=FILE_DOC)))
_add(ID_NEWS, _msg(302, NP, 'voice post', views=5,
                   media=types.MessageMediaDocument(document=VOICE_DOC)))
_add(ID_NEWS, _msg(301, NP, 'link post https://example.com', views=200,
                   media=types.MessageMediaWebPage(webpage=types.WebPage(
                       id=2, url='https://example.com', display_url='example.com', hash=1,
                       title='Example', site_name='ex'))))
_add(ID_NEWS, _msg(300, NP, 'first post', views=42))

_add(ID_ME, _msg(3, types.PeerUser(user_id=777), 'note to self', out=True, sender=ME))
_add(ID_ME, _msg(2, types.PeerUser(user_id=777), 'saved link https://example.com',
                  out=True, sender=ME))


def chat_msgs(chat_id):
    return sorted((m for (c, _), m in MESSAGES.items() if c == chat_id),
                  key=lambda m: -m.id)


# ---------------------------------------------------------------- dialogs
def _raw_dialog(peer, top, unread, read_in, pinned=False, folder=None, mute_until=None):
    return types.Dialog(
        peer=peer, top_message=top, read_inbox_max_id=read_in, read_outbox_max_id=read_in,
        unread_count=unread, unread_mentions_count=0, unread_reactions_count=0,
        unread_poll_votes_count=0,
        notify_settings=types.PeerNotifySettings(mute_until=mute_until,
                                                 show_previews=True, silent=False),
        pinned=pinned, folder_id=folder,
        draft=types.DraftMessageEmpty(date=NOW))


RAW_DIALOGS = [
    _raw_dialog(types.PeerUser(user_id=777), 3, 0, 3, pinned=True),
    _raw_dialog(types.PeerUser(user_id=101), 25, 2, 23),
    _raw_dialog(types.PeerChat(chat_id=201), 120, 12, 105, pinned=True),
    _raw_dialog(types.PeerChannel(channel_id=301), 305, 5, 300),
    _raw_dialog(types.PeerUser(user_id=401), 0, 0, 0),
    _raw_dialog(types.PeerUser(user_id=501), 0, 0, 0, mute_until=2147483647),
    _raw_dialog(types.PeerUser(user_id=601), 0, 0, 0, folder=1),
]

def _finish_msg(m, client, extra_entities=None):
    try:
        entities = dict(extra_entities or {})
        for uid in (getattr(getattr(m, 'from_id', None), 'user_id', None),
                    getattr(getattr(getattr(m, 'fwd_from', None),
                                    'from_id', None), 'user_id', None)):
            if uid and uid in ENTITIES:
                entities.setdefault(uid, ENTITIES[uid])
        m._finish_init(client, entities, None)
    except Exception:
        pass


class _NullEntityCache:
    def get(self, *args, **kwargs):
        return None


class FakeTelegramClient:
    # login state shared across instances for the phone-login flow
    _pending_code = '12345'
    _pending_hash = 'FAKEHASH'
    _2fa_password = 'secret'

    def __init__(self, session=None, api_id=None, api_hash=None, **kw):
        self.session = session
        key = getattr(getattr(session, 'auth_key', None), 'key', None)
        if key == b'\xAA' * 256:
            self._kind = 'good'
        elif key == b'\xBB' * 256:
            self._kind = 'dup'
        elif key == b'\xCC' * 256:
            self._kind = 'expired'
        else:
            self._kind = 'fresh'
        self._connected = False
        self._authorized = False
        self._self_id = 777
        self._mb_entity_cache = _NullEntityCache()
        self._handlers = []
        self._phone = None
        self._phone_code_hash = None
        self._sent_typing = []
        self._pinned = []

    # ---------------- lifecycle
    async def connect(self):
        if self._kind == 'dup':
            raise tg_errors.AuthKeyDuplicatedError(None)
        self._connected = True

    async def disconnect(self):
        self._connected = False

    async def is_user_authorized(self):
        return self._connected and self._kind == 'good'

    async def get_me(self):
        _finish_msg(ME, self)
        return ME

    async def log_out(self):
        self._kind = 'expired'
        self._connected = False
        return True

    # ---------------- auth
    async def send_code_request(self, phone):
        if not self._connected or self._kind != 'fresh':
            raise tg_errors.RPCError(None, 'You are already logged in', 400)
        self._phone = phone
        self._phone_code_hash = self._pending_hash
        return types.auth.SentCode(
            type=types.auth.SentCodeTypeApp(length=5),
            phone_code_hash=self._pending_hash, timeout=60)

    async def sign_in(self, phone=None, code=None, password=None, phone_code_hash=None):
        if password is not None:
            if password == self._2fa_password:
                self._kind = 'good'
                self._authorized = True
                # simulate telegram issuing a fresh auth key
                self.session.set_dc(2, '149.154.167.51', 443)
                self.session._auth_key = AuthKey(b'\xAA' * 256)
                _finish_msg(ME, self)
                return ME
            raise tg_errors.PasswordHashInvalidError(None)
        if phone and code:
            if phone_code_hash != self._pending_hash:
                raise tg_errors.PhoneCodeInvalidError(None)
            if code == self._pending_code:
                raise tg_errors.SessionPasswordNeededError(None)
            raise tg_errors.PhoneCodeInvalidError(None)
        raise tg_errors.PhoneCodeInvalidError(None)

    # ---------------- entities / dialogs
    async def get_input_entity(self, peer):
        pid = _chat_id_of(peer)
        if pid == FLOOD_CHAT:
            raise tg_errors.FloodWaitError(None, capture=42)
        if pid in ENTITIES:
            e = ENTITIES[pid]
            if isinstance(e, types.User):
                return types.InputPeerUser(user_id=e.id, access_hash=1)
            if isinstance(e, types.Chat):
                return types.InputPeerChat(chat_id=e.id)
            return types.InputPeerChannel(channel_id=e.id, access_hash=1)
        if pid == ID_ME and isinstance(peer, int):
            return types.InputPeerSelf()
        raise ValueError('Could not find any entity corresponding to "{}"'.format(peer))

    async def iter_dialogs(self, limit=None, folder=None, archived=None, **kw):
        if self._kind != 'good':
            raise tg_errors.AuthKeyUnregisteredError(None)
        want_folder = 0 if folder is None else folder
        from telethon.tl.custom import Dialog as D
        out = []
        for raw in RAW_DIALOGS:
            f = 0 if raw.folder_id is None else raw.folder_id
            if f != want_folder:
                continue
            if limit is not None and len(out) >= limit:
                break
            marked = get_peer_id(raw.peer)
            entity = ENTITIES[marked]
            entities = {marked: entity}
            if isinstance(entity, types.User):
                entities[entity.id] = entity
            top = MESSAGES.get((marked, raw.top_message))
            if top is not None:
                _finish_msg(top, self)
            out.append(D(self, raw, entities, top))
        for d in out:
            yield d

    # ---------------- messages
    async def get_messages(self, entity, ids=None, limit=None, offset_id=0, min_id=0,
                           search=None, filter=None, **kw):
        chat = _chat_id_of(entity)
        if chat == FLOOD_CHAT:
            raise tg_errors.FloodWaitError(None, capture=42)
        if chat not in ENTITIES and chat != ID_ME:
            raise ValueError('Could not find any entity corresponding to "{}"'.format(chat))
        if ids is not None:
            res = []
            single = isinstance(ids, int)
            id_list = [ids] if single else list(ids)
            for i in id_list:
                m = MESSAGES.get((chat, i))
                if m is not None:
                    _finish_msg(m, self)
                res.append(m)
            return res[0] if single else res
        msgs = chat_msgs(chat)
        if min_id:
            msgs = [m for m in msgs if m.id > min_id]
        if offset_id:
            msgs = [m for m in msgs if m.id < offset_id]
        if search:
            q = search.lower()
            msgs = [m for m in msgs if q in (m.message or '').lower()]
        if filter is not None:
            kinds = {
                types.InputMessagesFilterPhotoVideo: ('photo', 'video', 'gif', 'videonote'),
                types.InputMessagesFilterPhotos: ('photo',),
                types.InputMessagesFilterDocument: ('file', 'picfile', 'sticker'),
                types.InputMessagesFilterVoice: ('voice',),
                types.InputMessagesFilterMusic: ('audio',),
                types.InputMessagesFilterUrl: ('__webpage__', '__url__'),
            }
            fkey = filter if isinstance(filter, type) else type(filter)
            wanted = kinds.get(fkey, ())
            def _kind_of(m):
                from telethon.extensions import html as _h
                if isinstance(getattr(m, 'media', None), types.MessageMediaWebPage) or \
                        (m.message or '').startswith('link post') or \
                        (m.message or '').startswith('look at') or \
                        (m.message or '').startswith('saved link'):
                    return '__webpage__'
                d = getattr(m, 'document', None)
                if d is None:
                    return 'photo' if getattr(m, 'photo', None) else None
                attrs = d.attributes or []
                if any(isinstance(a, types.DocumentAttributeSticker) for a in attrs):
                    return 'sticker'
                aud = next((a for a in attrs if isinstance(a, types.DocumentAttributeAudio)), None)
                vid = next((a for a in attrs if isinstance(a, types.DocumentAttributeVideo)), None)
                if aud is not None and aud.voice:
                    return 'voice'
                if aud is not None:
                    return 'audio'
                if vid is not None:
                    return 'gif' if any(isinstance(a, types.DocumentAttributeAnimated)
                                         for a in attrs) else 'video'
                return 'file'
            msgs = [m for m in msgs if _kind_of(m) in wanted]
        if limit is not None:
            msgs = msgs[:limit]
        for m in msgs:
            _finish_msg(m, self)
        return msgs

    async def iter_messages(self, entity, **kw):
        return self.get_messages(entity, **kw)

    # ---------------- sending
    @staticmethod
    def _html_ok(text):
        # simulate telethon's html parser rejecting malformed markup
        import re as _re
        return not _re.search(r'<[^a-zA-Z/]', text)

    async def send_message(self, entity, text, reply_to=None, parse_mode=None,
                           link_preview=None, **kw):
        chat = _chat_id_of(entity)
        if parse_mode == 'html' and not self._html_ok(text):
            raise ValueError('Could not parse the given HTML text')
        existing = chat_msgs(chat)
        nid = (existing[0].id + 1) if existing else 1
        m = _msg(nid, _peer_of(chat), text=text, sender=ME, out=True, reply_to=reply_to,
                 date=datetime.now(timezone.utc))
        _add(chat, m)
        _finish_msg(m, self)
        return m

    async def send_file(self, entity, file, file_name=None, caption=None,
                        force_document=False, **kw):
        chat = _chat_id_of(entity)
        existing = chat_msgs(chat)
        nid = (existing[0].id + 1) if existing else 1
        doc_id = 50000 + nid
        mime = 'application/octet-stream'
        if file_name and '.' in file_name:
            ext = file_name.rsplit('.', 1)[1].lower()
            mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                    'pdf': 'application/pdf', 'mp4': 'video/mp4', 'txt': 'text/plain',
                    'webp': 'image/webp'}.get(ext, mime)
        doc = _doc(doc_id, mime, len(file),
                   [types.DocumentAttributeFilename(file_name=file_name or 'file')])
        PAYLOADS[doc_id] = bytes(file)
        m = _msg(nid, _peer_of(chat), text=caption or '', sender=ME, out=True,
                 media=types.MessageMediaDocument(document=doc),
                 date=datetime.now(timezone.utc))
        _add(chat, m)
        _finish_msg(m, self)
        return m

    async def forward_messages(self, dest, messages, from_peer=None, **kw):
        src = _chat_id_of(from_peer)
        dst = _chat_id_of(dest)
        ids = [messages] if isinstance(messages, int) else list(messages)
        out = []
        existing = chat_msgs(dst)
        nid = (existing[0].id + 1) if existing else 1
        for i, mid in enumerate(ids):
            m = MESSAGES.get((src, mid))
            if m is None:
                continue
            nm = _msg(nid + i, _peer_of(dst), text=m.message or '', sender=None,
                      out=False, media=m.media,
                      fwd_from=types.MessageFwdHeader(
                          from_id=types.PeerUser(user_id=101), date=NOW))
            _add(dst, nm)
            _finish_msg(nm, self, {101: ALICE})
            out.append(nm)
        return out

    async def edit_message(self, entity, message=None, text=None, parse_mode=None, **kw):
        chat = _chat_id_of(entity)
        m = MESSAGES.get((chat, message))
        if m is None:
            raise tg_errors.MessageNotModifiedError(None)
        if parse_mode == 'html' and not self._html_ok(text):
            raise ValueError('Could not parse the given HTML text')
        if text == m.message:
            raise tg_errors.MessageNotModifiedError(None)
        m.message = text
        m.entities = None
        m.edit_date = datetime.now(timezone.utc)
        _finish_msg(m, self)
        return m

    async def delete_messages(self, entity, message_ids, revoke=True, **kw):
        chat = _chat_id_of(entity)
        for i in message_ids:
            MESSAGES.pop((chat, i), None)

    async def pin_message(self, entity, message=None, notify=False, **kw):
        self._pinned.append((_chat_id_of(entity), message, True))
        return True

    async def unpin_message(self, entity, message=None, **kw):
        self._pinned.append((_chat_id_of(entity), message, False))
        return True

    async def send_read_acknowledge(self, entity, message=None, max_id=None,
                                    clear_mentions=False, **kw):
        chat = _chat_id_of(entity)
        for raw in RAW_DIALOGS:
            marked = _peer_marked(raw.peer)
            if marked == chat and max_id:
                raw.read_inbox_max_id = max(raw.read_inbox_max_id, max_id)
                raw.unread_count = 0

    # ---------------- downloads
    async def download_media(self, message, file=None, thumb=None, **kw):
        if thumb is None:
            # full-size
            if getattr(message, 'photo', None) is not None:
                return PNG_PHOTO
            doc = getattr(message, 'document', None)
            if isinstance(doc, types.Document):
                return PAYLOADS.get(doc.id, b'')
            return None
        # thumbnail
        if getattr(message, 'photo', None) is not None:
            return PNG_THUMB
        doc = getattr(message, 'document', None)
        if isinstance(doc, types.Document):
            mime = doc.mime_type or ''
            if mime.startswith('video/') or mime == 'image/webp':
                return PNG_THUMB
            return None
        return None

    async def download_profile_photo(self, entity, file=None, download_big=False, **kw):
        e = ENTITIES.get(_chat_id_of(entity)) if not isinstance(entity, types.User) else entity
        if e is None and isinstance(entity, int):
            e = ENTITIES.get(entity)
        if e is not None and getattr(e, 'photo', None) is not None:
            return PNG_AVATAR
        return None

    async def iter_download(self, handle, offset=0, stride=None, limit=None,
                            chunk_size=None, request_size=None, file_size=None, dc_id=None):
        doc = handle
        payload = PAYLOADS.get(doc.id)
        if payload is None:
            return
        total = len(payload)
        end = total
        if file_size is not None:
            end = min(total, offset + file_size)
        pos = offset
        while pos < end:
            chunk = payload[pos:pos + 131072]
            if not chunk:
                break
            yield chunk
            pos += len(chunk)

    # ---------------- participants
    async def iter_participants(self, entity, limit=None, search=None, filter=None, **kw):
        chat = _chat_id_of(entity)
        if chat == ID_NEWS:
            # broadcast channels: participant list hidden
            raise tg_errors.ChatAdminRequiredError(None)
        if filter is not None and 'Admins' in type(filter).__name__:
            return
        members = [ME, ALICE, BOB, BOT]
        if search:
            q = search.lower()
            members = [u for u in members if q in display(u).lower()]
        ALICE.participant = types.ChannelParticipantAdmin(
            user_id=101, promoted_by=777, date=NOW, admin_rights=types.ChatAdminRights())
        BOB.participant = types.ChatParticipant(user_id=501, inviter_id=777, date=NOW)
        ME.participant = types.ChannelParticipantCreator(
            user_id=777, admin_rights=types.ChatAdminRights())
        for u in members[:limit or len(members)]:
            yield u

    # ---------------- raw calls
    async def __call__(self, request):
        t = type(request).__name__
        if t == 'SetTypingRequest':
            self._sent_typing.append(request)
            return True
        if t == 'SendReactionRequest':
            chat = _chat_id_of(request.peer)
            m = MESSAGES.get((chat, request.msg_id))
            if m is None:
                raise tg_errors.MessageIdInvalidError(None)
            r = m.reactions or types.MessageReactions(results=[])
            results = list(r.results or [])
            wanted = [x.emoticon for x in (request.reaction or [])]
            new = []
            if not wanted:
                # empty list = remove ALL of my reactions
                for rc in results:
                    if rc.chosen_order is not None:
                        new.append(types.ReactionCount(reaction=rc.reaction,
                                                       count=max(0, rc.count - 1),
                                                       chosen_order=None))
                    else:
                        new.append(rc)
            else:
                for em in wanted:
                    found = None
                    for rc in results:
                        if rc.reaction.emoticon == em:
                            found = rc
                            break
                    if found is None:
                        new.append(types.ReactionCount(
                            reaction=types.ReactionEmoji(emoticon=em), count=1, chosen_order=0))
                    elif found.chosen_order is None:
                        new.append(types.ReactionCount(reaction=found.reaction,
                                                       count=found.count + 1, chosen_order=0))
                    else:  # toggle off
                        new.append(types.ReactionCount(reaction=found.reaction,
                                                       count=max(0, found.count - 1),
                                                       chosen_order=None))
                for rc in results:
                    if all(rc.reaction.emoticon != x.emoticon for x in (request.reaction or [])):
                        new.append(rc)
            new = [rc for rc in new if rc.count > 0]
            m.reactions = types.MessageReactions(results=new) if new else None
            return True
        if t == 'UpdateNotifySettingsRequest':
            chat = _chat_id_of(request.peer.peer)
            for raw in RAW_DIALOGS:
                if _peer_marked(raw.peer) == chat:
                    raw.notify_settings = types.PeerNotifySettings(
                        mute_until=request.settings.mute_until,
                        show_previews=True, silent=False)
            return True
        if t == 'EditPeerFoldersRequest':
            chat = _chat_id_of(request.folder_peers[0].peer)
            for raw in RAW_DIALOGS:
                if _peer_marked(raw.peer) == chat:
                    raw.folder_id = request.folder_peers[0].folder_id or None
            return True
        if t == 'GetPeerDialogsRequest':
            peer = request.peers[0].peer
            chat = _chat_id_of(peer)
            for raw in RAW_DIALOGS:
                if _peer_marked(raw.peer) == chat:
                    return SimpleNamespace(dialogs=[raw])
            raise ValueError('unknown peer')
        raise ValueError('fake client: unhandled request ' + t)

    def add_event_handler(self, callback, event=None):
        self._handlers.append((callback, event))


def _peer_marked(peer):
    return get_peer_id(peer)


def display(u):
    return ' '.join(x for x in (getattr(u, 'first_name', None), getattr(u, 'last_name', None))
                    if x) or getattr(u, 'title', '') or str(u.id)


def make_session(kind):
    s = StringSession()
    s.set_dc(2, '149.154.167.51', 443)
    key = {'good': b'\xAA', 'dup': b'\xBB', 'expired': b'\xCC'}[kind]
    s._auth_key = AuthKey(key * 256)
    return s.save()
