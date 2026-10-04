"""Syntax-check every frontend JS file with node --check.

The frontend now lives in static/js/*.js (no longer embedded in main.py).
Run:  python3 tools/check_js.py && node --check /tmp/tgw_check/<file>.js
"""
import glob
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
files = sorted(glob.glob(os.path.join(REPO, 'static', 'js', '*.js')))
assert files, 'no JS files found in static/js/'

fail = 0
for f in files:
    r = subprocess.run(['node', '--check', f], capture_output=True, text=True)
    status = 'OK  ' if r.returncode == 0 else 'FAIL'
    print('%s %s' % (status, os.path.basename(f)))
    if r.returncode != 0:
        print(r.stderr)
        fail += 1

total = len(files)
print('%d/%d JS files pass node --check' % (total - fail, total))
sys.exit(1 if fail else 0)
