# TgWeb — নিজের Telegram Web Client (Telegram-এর কপি ভার্সন)

একজন ইউজারের জন্য, নিজের সার্ভারে হোস্ট করা, বিজ্ঞাপনমুক্ত Telegram ওয়েব ক্লায়েন্ট।
**লগইন দুই ভাবে:** ফোন নম্বর + OTP + 2FA পাসওয়ার্ড (ডিফল্ট), অথবা Telethon StringSession পেস্ট করে।

A self-hosted, single-user, ad-free Telegram web client.
**Login two ways:** phone number + OTP + 2FA password (default), or by pasting a Telethon StringSession.

---

## 🚀 চালানো (Run)

```bash
pip install -r requirements.txt
python main.py            # http://localhost:8000
```

Render-এ ডিপ্লয়: zip আনজিপ করুন → Build: `pip install -r requirements.txt` →
Start: `python main.py` — ব্যস। `PORT` environment variable সাপোর্ট করে।

## 🔑 প্রথম লগইন

**পদ্ধতি ১ — ফোন নম্বর (ডিফল্ট):**
1. ওয়েবসাইট খুলুন → সাইট পাসওয়ার্ড দিন (`tgweb/config.py`-এর `PASSWORD`)
2. **Phone number** ট্যাবে ফোন নম্বর দিন (দেশের কোড সহ, যেমন `+8801XXXXXXXXX`)
3. Telegram অ্যাপে যে কোড আসবে সেটি দিন; 2FA চালু থাকলে পাসওয়ার্ডও দিন — ব্যস!

**পদ্ধতি ২ — StringSession:** নিজের কম্পিউটারে একবার এই কোড চালিয়ে session string বানান:

```bash
pip install telethon
python -c "
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
c = TelegramClient(StringSession(), API_ID, API_HASH)
c.start()
print(c.session.save())
"
```

3. প্রিন্ট হওয়া লাইনটি ওয়েবসাইটে পেস্ট করুন → **Log in with session**

Session টি সার্ভারে `tgsess.txt` ফাইলে সেভ থাকে; রিস্টার্টে অটো-লগইন হয়।
Session খারাপ হলে ফাইল ডিলিট হয়ে আবার লগইন স্ক্রিন দেখায় — অ্যাপ কখনো crash করে না।

---

## 📁 প্রজেক্ট স্ট্রাকচার (কোথায় কী আছে)

```
main.py                  এন্ট্রি পয়েন্ট — শুধু সার্ভার চালু করে
requirements.txt         Python dependencies

tgweb/                   ⬅ Python ব্যাকএন্ড (FastAPI + Telethon)
├── config.py            ⭐ API_ID, API_HASH, PASSWORD — শুধু এই ফাইলে এডিট করবেন
├── app.py               FastAPI app, path-prefix middleware, static সার্ভিং, error handlers
├── security.py          সাইট পাসওয়ার্ড → HMAC-signed httpOnly cookie
├── tgstate.py           Telegram ক্লায়েন্ট state, session ফাইল সেভ/লোড
├── client.py            স্টার্টআপে auto-login (কখনো crash করে না)
├── updates.py           টাইপিং ইন্ডিকেটর আপডেট
├── serialize.py         Telegram data → JSON (dialog, message, member…)
├── formatting.py        মেসেজ entities → HTML (bold/italic/link/code/spoiler)
├── media.py             ছবি/ভিডিও/ফাইল স্ট্রিমিং — HTTP Range (seek কাজ করে), cache
├── util.py              LRU cache, semaphore, ছোট হেল্পার
├── schemas.py           API request models
└── routes/
    ├── auth.py          লগইন (session import), status, logout — শুধু session দিয়ে
    ├── chat.py          dialogs, messages, search, gallery, members, send/edit/
    │                    delete/forward, reactions, pin/mute/archive, upload
    └── media.py         avatar, thumbnail, মিডিয়া স্ট্রিম endpoint

static/                  ⬅ ফ্রন্টএন্ড — কোনো build step লাগে না, সরাসরি ব্রাউজারে চলে
├── index.html           একটাই পেজ (সব view এর মধ্যে লুকানো থাকে)
├── css/
│   ├── base.css         ⭐ রং/থিম (CSS variables), লগইন স্ক্রিন, বাটন
│   ├── dialogs.css      বাম পাশের চ্যাট লিস্ট, ট্যাব
│   ├── chat.css         চ্যাট এরিয়া, মেসেজ বাবল, রিঅ্যাকশন
│   ├── composer.css     মেসেজ লেখার বক্স, reply/edit বার
│   └── overlays.css     মেনু, মোডাল, গ্যালারি, ফুলস্ক্রিন ভিউয়ার, টোস্ট
├── js/
│   ├── util.js          হেল্পার ফাংশন, state (S), api() fetch wrapper
│   ├── login.js         ⭐ লগইন লজিক (সাইট পাসওয়ার্ড + session import)
│   ├── dialogs.js       চ্যাট লিস্ট লোড/রেন্ডার, ট্যাব ফিল্টার
│   ├── chat.js          চ্যাট খোলা, মেসেজ রেন্ডার, infinite scroll, polling
│   ├── composer.js      মেসেজ পাঠানো, reply, edit, ফাইল আপলোড, সিলেকশন
│   ├── menus.js         রাইট-ক্লিক মেনু, রিঅ্যাকশন, থিম, লগআউট
│   ├── modals.js        ফরওয়ার্ড পিকার, মিডিয়া গ্যালারি, মেম্বার লিস্ট, সার্চ
│   ├── viewer.js        ফুলস্ক্রিন ছবি/ভিডিও ভিউয়ার
│   └── app.js           কীবোর্ড শর্টকাট + অ্যাপ শুরু (boot)
└── icons/               PWA আইকন (PNG)

tests/                   ফেক Telethon ক্লায়েন্ট দিয়ে ৯৯টি automated টেস্ট
tools/                   check_js.py (JS syntax check), gen_icons.py
```

---

## 🎨 কাস্টমাইজ করবেন যেভাবে

| কী বদলাতে চান | কোথায় |
|---|---|
| রং / থিম / dark mode | `static/css/base.css` — উপরের `:root` আর `[data-theme="dark"]` variables |
| সাইট পাসওয়ার্ড / API keys | `tgweb/config.py` |
| লগইন স্ক্রিনের লেখা | `static/index.html` (`scr-tg` section) + `static/css/base.css` |
| চ্যাট বাবলের স্টাইল | `static/css/chat.css` |
| মেসেজ পাঠানোর লজিক | `static/js/composer.js` + `tgweb/routes/chat.py` |
| নতুন API endpoint | `tgweb/routes/chat.py`-এ যোগ করুন (`@router.post("/...")`) |
| নতুন ফিচার (JS) | `static/js/`-এ নতুন ফাইল + `index.html`-এ `<script src="js/...">` |

CSS variables উদাহরণ (`base.css`):

```css
:root{
  --bg:#f3f4f6;        /* পেজের ব্যাকগ্রাউন্ড */
  --panel:#ffffff;     /* প্যানেল */
  --accent:#3390ec;    /* ⭐ Telegram নীল — নিজের রং দিন */
  --bub-out:#effdde;   /* নিজের মেসেজের বাবল */
}
```

⚠️ সব URL relative (`api/...`, `css/...`) — কখনো `/api/...` লিখবেন না, তাহলে
path-prefix (`/live/slug/`) এর নিচে ভেঙে যাবে।

---

## ✨ ফিচার

- **লগইন**: ফোন নম্বর + OTP + 2FA পাসওয়ার্ড (ডিফল্ট ট্যাব), অথবা StringSession
- ডায়ালগ লিস্ট: ট্যাব (All/Chats/Groups/Channels/Bots/Saved), সার্চ + **গ্লোবাল সার্চ
  (ড্রপডাউনে চ্যাট+মেসেজ)**, অ্যাভাটার, unread badge, pinned আগে, last-message preview,
  relative time, **Draft: … ইন্ডিকেটর**
- চ্যাট: উপরে infinite scroll, ~8s পোলিং, reply (quote সহ), forward (chat picker +
  **"Hide sender names" অপশন**), edit/delete, mark-as-read বাটন, in-chat search,
  Telegram formatting (bold/italic/link/code/spoiler), emoji reactions, pin, mute, archive
- **প্রোফাইল**: চ্যাটের নামে ক্লিক করলে ইউজার/গ্রুপ/চ্যানেল info মোডাল (bio, username,
  ফোন, কমন গ্রুপ, মেম্বার, mute) — মেনু থেকে নিজের প্রোফাইল + **Settings (নাম/বায়ো এডিট)**
- **বট**: ইনলাইন বাটন (callback + URL + copy + switch), বট রিপ্লাই কীবোর্ড
- মিডিয়া: ছবি (thumb → ফুল), **অ্যালবাম (grouped ছবি এক বাবলে গ্রিডে, পুরো অ্যালবাম
  একসাথে সিলেক্ট/ফরওয়ার্ড/ডিলিট)**, ভিডিও **HTTP Range সাপোর্ট (seek কাজ করে)**,
  অডিও/ভয়েস প্লেয়ার, ফাইল ডাউনলোড (নাম+সাইজ), মিডিয়া গ্যালারি (media/files/voice/links),
  ফাইল আপলোড (**2GB পর্যন্ত**), **ভয়েস মেসেজ রেকর্ড (মাইক বাটন)**, ওয়েবপেজ preview, পোল
- **স্টিকার**: বড় (168px) ট্রান্সপারেন্ট বাবলে রেন্ডার + **অ্যানিমেটেড স্টিকার (.tgs
  Lottie — lottie-web দিয়ে, ব্রাউজারে gunzip হয়) এবং .webm ভিডিও স্টিকার**
- **স্টোরিজ**: চ্যাট লিস্টের উপরে স্টোরিজ বার (unseen গ্রেডিয়েন্ট রিং), ফুলস্ক্রিন ভিউয়ার
  (progress bar, ট্যাপ/অ্যারো নেভিগেশন, ক্যাপশন, view count), নিজের স্টোরি পোস্ট (ছবি/ভিডিও
  + ক্যাপশন) ও ডিলিট, দেখা মার্ক অটো
- **Inline @bot**: চ্যাটে `@botname query` টাইপ করলে কম্পোজারের উপরে রেজাল্ট ড্রপডাউন
  (arrow key + Enter সাপোর্ট), ক্লিকে সেন্ট — মেসেজে "via @bot" দেখায়
- **Scheduled messages**: সেন্ড বাটনে long-press (বা রাইট-ক্লিক) → শিডিউল; চ্যাট মেনু →
  Scheduled messages লিস্ট (সময়+টেক্সট+ডিলিট)
- **Pinned message বার**: হেডারের নিচে 📌 বার, ক্লিকে jump, ✕ দিয়ে unpin
- **Copy link**: মেসেজ মেনু → t.me লিংক কপি (পাবলিক গ্রুপ/চ্যানেল)
- **নতুন চ্যাট/জয়েন**: ✏️ বাটন → @username বা t.me লিংক → ওপেন/জয়েন
- **নিজের ছবি বদল**: Settings → অ্যাভাটারে ক্লিক → আপলোড
- **ডাবল-ট্যাপ ❤️ রিঅ্যাকশন** (হার্ট অ্যানিমেশন সহ), **সেন্ড/রিসিভ সাউন্ড** (Settings-এ টগল),
  **চ্যাট ওয়ালপেপার** ৫টা প্রিসেট (Settings-ে চয়েস)
- **ইমোজি পিকার**: ১০ ক্যাটাগরি + সাম্প্রতিক (localStorage), কার্সরের জায়গায় বসে
- **ড্রাফট**: লেখা অসমাপ্ত রাখলে সেভ থাকে, লিস্টে "Draft:" দেখায়, পাঠালে মুছে যায়
- অন্যান্য: Saved Messages, মেম্বার লিস্ট, light/dark থিম (localStorage), টাইপিং
  ইন্ডিকেটর, unread divider, jump-to-reply, scroll-to-bottom বাটন, কীবোর্ড শর্টকাট
  (Esc, Ctrl+K, Ctrl+F), মোবাইল responsive (**scroll hardening — iOS
  -webkit-overflow-scrolling:touch, overscroll-behavior:contain**), PWA manifest, লগআউট

## 🛠️ ডেভেলপার টেস্ট

```bash
python -m pyflakes tgweb/*.py tgweb/routes/*.py   # Python lint
python3 tools/check_js.py                          # JS syntax (node --check)
python tests/smoke.py                              # ১৫৩টি API টেস্ট (fake Telegram)
node tests/boot_test.js                            # ফ্রন্টএন্ড jsdom বুট টেস্ট (jsdom লাগবে:
                                                   #   npm install jsdom)
```

## ⚠️ যা নেই (সৎ স্বীকারোক্তি)

- **কল/ভিডিও কল** — Telegram-এর WebRTC প্রোটোকল, Telethon দিয়ে সম্ভব নয়
- **সিক্রেট চ্যাট** — MTProto secret-chat ক্রিপ্টো (DH key exchange + AES-IGE) Telethon-এ
  নেই; নিজে লিখতে হয়, আর দুটো আসল অ্যাকাউন্ট ছাড়া টেস্টও করা যায় না — ভুল ইমপ্লিমেন্টেশনে
  মেসেজ হারাতে পারে, তাই রাখা হয়নি (একমাত্র লাইব্রেরি যেটা পারে সেটা TDLib — সেটা দিয়ে
  পুরো অ্যাপ আবার লিখতে হতো, Render free tier-এও চলে না)

## ⚠️ নোট

- `tgsess.txt` কখনো কারও সাথে শেয়ার করবেন না / git-এ দেবেন না (.gitignore-এ আছে)
- Render **free tier**-এ disk ephemeral — রিস্টার্টে `tgsess.txt` মুছে যায়, আবার
  session পেস্ট করতে হবে (কয়েক সেকেন্ডের কাজ)
- Session string অন্য IP-তে চললে `AuthKeyDuplicated` — নতুন session বানিয়ে দিন
