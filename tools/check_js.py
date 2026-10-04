"""Extract <script> blocks from main.py's INDEX_HTML and write them to /tmp/tgw_app.js for node --check."""
import re, sys

src = open('/home/user/Tgsession/main.py', encoding='utf-8').read()
m = re.search(r'INDEX_HTML = r"""(.*?)\n"""\n', src, re.S)
assert m, 'INDEX_HTML not found'
html = m.group(1)
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
assert scripts, 'no scripts found'
js = '\n;\n'.join(scripts)
open('/tmp/tgw_app.js', 'w', encoding='utf-8').write(js)
print(f'extracted {len(scripts)} script block(s), {len(js)} chars -> /tmp/tgw_app.js')
