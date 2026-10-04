"""Demo server: tgweb with the fake Telethon client — for trying the UI in a browser.

Phone login: +8801700000000 → code: 12 345 → 2FA password: secret
(Site password comes from tgweb/config.py)

Run:  /home/user/.venv/bin/python tools/demo_server.py
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'tests'))
os.chdir(tempfile.mkdtemp(prefix='tgw_demo_'))   # tgsess.txt is written relative to cwd

import fake_telethon as fake                    # noqa: E402

import tgweb.client as client_mod               # noqa: E402
import tgweb.routes.auth as routes_auth_mod     # noqa: E402
client_mod.TelegramClient = fake.FakeTelegramClient
routes_auth_mod.TelegramClient = fake.FakeTelegramClient

from tgweb.app import app                       # noqa: E402

import uvicorn                                  # noqa: E402

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), log_level='warning')
