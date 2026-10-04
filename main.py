#!/usr/bin/env python3
"""
TgWeb — a single-user, self-hosted, ad-free Telegram web client.
Telegram-এর কপি ভার্সন: শুধুমাত্র StringSession দিয়ে লগইন।

প্রজেক্ট স্ট্রাকচার (এই ফাইলটা শুধু চালানোর জন্য):

    main.py              <- এই ফাইল: python main.py দিয়ে সার্ভার চালু হয়
    requirements.txt     <- pip install -r requirements.txt
    tgweb/               <- Python ব্যাকএন্ড (FastAPI + Telethon)
        config.py           API_ID / API_HASH / PASSWORD — শুধু এখানে এডিট করবেন
        app.py              FastAPI অ্যাপ, middleware, static ফাইল সার্ভিং
        security.py         সাইট পাসওয়ার্ড (HMAC cookie)
        tgstate.py          Telegram ক্লায়েন্ট state + session ফাইল
        client.py           রিস্টার্টে auto-login (কখনো crash করে না)
        serialize.py        Telegram data -> JSON
        formatting.py       মেসেজ entities -> HTML (bold/italic/spoiler...)
        media.py            ছবি/ভিডিও/ফাইল স্ট্রিমিং (HTTP Range, seek)
        util.py             cache ইত্যাদি
        schemas.py          request models
        routes/             API endpoint গুলো (auth / chat / media)
    static/              <- ফ্রন্টএন্ড (কোনো build step লাগে না)
        index.html          একটাই পেজ
        css/                স্টাইল (থিম, চ্যাট লিস্ট, বাবল, ভিউয়ার...)
        js/                 JavaScript (প্রতিটা ফিচার আলাদা ফাইলে)
        icons/              অ্যাপ আইকন (PWA)

চালানো:
    pip install -r requirements.txt
    python main.py            # http://localhost:8000

কাস্টমাইজ করতে চাইলে: tgweb/config.py (credentials), static/css/*.css (রং/থিম),
static/js/*.js (আচরণ) — বিস্তারিত README.md ফাইলে।
"""
import os

import uvicorn

from tgweb.app import app  # noqa: F401  (imported so uvicorn can serve it)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
