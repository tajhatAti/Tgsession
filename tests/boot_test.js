/* TgWeb frontend boot test — runs the whole frontend under jsdom against a stub API.
   Catches missing DOM ids, undefined functions and runtime errors in all flows.
   Run:  node tests/boot_test.js   (needs: npm install jsdom in this dir or /tmp) */
const { JSDOM } = require(process.env.JSDOM_PATH || '/tmp/node_modules/jsdom');
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..', 'static');

let html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
html = html.replace(/<link[^>]*>/g, '');
const scripts = [...html.matchAll(/<script src="js\/([^"]+)"><\/script>/g)].map(m => m[1]);
html = html.replace(/<script src="js\/[^"]*"><\/script>/g, '');
const dom = new JSDOM(html, { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
window.matchMedia = window.matchMedia || (q => ({ matches: false, addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){} }));
window.scrollTo = () => {};
window.HTMLCanvasElement.prototype.getContext = () => null;
window.fetch = (url, init) => {
  if (process.env.DEBUG_FETCH) console.log('FETCH:', url);
  const body = init && init.body ? JSON.parse(init.body) : {};
  const route = (u) => {
    if (u.startsWith('api/status')) return { tg: true, me: { id: 777, name: 'Ahad', username: 'ahad' } };
    if (u.startsWith('api/dialogs')) return { dialogs: [
      { id: 101, type: 'user', name: 'Alice', username: 'alice', has_photo: true, unread: 0,
        mentions: 0, pinned: false, muted: false, archived: false, last: {} }
    ] };
    if (u.startsWith('api/messages')) return { messages: [], pinned: { id: 5, raw: 'pinned hello' } };
    if (u.startsWith('api/search_global')) return { chats: [], messages: [] };
    if (u.startsWith('api/profile')) return { id: 777, type: 'user', name: 'Ahad', bio: '', status: 'online' };
    if (u.startsWith('api/stories')) return {
      me: { chat_id: 777, name: 'Ahad', has_photo: false, max_read_id: 1, unseen: 0,
            stories: [{ id: 1, chat_id: 777, date: 1700000000, caption: 'my story', views: 5, out: true,
                        media: { kind: 'photo', mime: 'image/jpeg' },
                        thumb_url: 'api/story_media/777/1?kind=thumb', media_url: 'api/story_media/777/1?kind=full' }] },
      peers: [{ chat_id: 101, name: 'Alice', has_photo: true, max_read_id: 0, unseen: 2,
                stories: [
                  { id: 1, chat_id: 101, date: 1700000000, caption: 'hi', views: null, out: false, media: { kind: 'photo', mime: 'image/jpeg' }, thumb_url: 'x', media_url: 'x' },
                  { id: 2, chat_id: 101, date: 1700000001, caption: '', views: null, out: false, media: { kind: 'video', mime: 'video/mp4' }, thumb_url: 'x', media_url: 'x' }] }]
    };
    if (u.startsWith('api/inline_query')) return {
      token: 'tok123', query_id: 555,
      results: [{ i: 0, id: 'r1', type: 'article', title: 'Hello', description: 'desc', has_thumb: false }]
    };
    if (u.startsWith('api/scheduled')) return {
      messages: [{ id: 90001, date: new Date(Date.now() + 3600000).toISOString(), out: true, raw: 'scheduled msg', html: 'scheduled msg', scheduled: true, sender: { id: 777, name: 'Ahad' } }]
    };
    if (u === 'api/tg/import_session') return { ok: true, me: { id: 777, name: 'Ahad' } };
    if (u === 'api/callback') return { ok: true, message: 'Pong!' };
    if (u === 'api/stories/read') return { ok: true };
    if (u === 'api/link') return { ok: true, link: 'https://t.me/testgroup/5' };
    if (u === 'api/resolve') return { ok: true, can_join: false, chat: { id: 101, type: 'user', name: 'Alice', username: 'alice', has_photo: true, verified: false, participants_count: null } };
    return {};
  };
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(route(url)) });
};

const errors = [];
window.addEventListener('error', e => errors.push('window.onerror: ' + e.message));
let ran = 0;
for (const s of scripts){
  if (s.startsWith('vendor/')) { ran++; continue; }   // lottie is minified 3rd-party
  const code = fs.readFileSync(path.join(ROOT, 'js', s), 'utf8');
  try { dom.window.eval(code.replace(/'use strict';?/, '')); ran++; }
  catch (e){ errors.push(s + ' THREW: ' + e.message); }
}
try { dom.window.eval(`
  showScreen('tg');
  document.getElementById('tabSession').click();
  document.getElementById('tabPhone').click();
  document.getElementById('tgSendCode').click();
  document.getElementById('tgPhone').value = '+8801700000000';
  showScreen('app');

  // message rendering: albums, buttons, keyboard, stickers (incl. lottie + webm), via_bot
  S.msgs = [
    { id: 1, date: Date.now()-60000, out: true, raw: 'hi', html: 'hi' },
    { id: 2, date: Date.now(), out: false, raw: 'yo', html: 'yo', via_bot: '@storebot',
      sender: { id: 401, name: 'Bot' },
      buttons: [[{text:'Ping', data:'AAAA'},{text:'Open', url:'https://example.com'}],[{text:'Copy me', copy:'tgw-1234'}]],
      keyboard: [['Help','About']] },
    { id: 3, date: Date.now(), grouped_id: 555, out: false, sender: {id: 101, name:'Alice'}, media: { kind: 'photo' } },
    { id: 4, date: Date.now(), grouped_id: 555, out: false, sender: {id: 101, name:'Alice'}, media: { kind: 'photo' } },
    { id: 5, date: Date.now(), out: false, sender: {id: 101, name:'Alice'},
      media: { kind: 'sticker', mime: 'application/x-tgsticker', alt: '🎉' } },
    { id: 6, date: Date.now(), out: false, sender: {id: 101, name:'Alice'},
      media: { kind: 'sticker', mime: 'video/webm', alt: '💥' } },
  ];
  S.msgById = {}; S.msgs.forEach(m => S.msgById[m.id] = m);
  S.dlg = { type: 'group' };
  S.current = -201;
  renderAllMessages();
  updateBotKb();
  updatePinBar({ id: 5, raw: 'pinned hello' });
  if (document.getElementById('pinBar').hidden) throw new Error('pin bar not shown');
  if (!document.querySelector('.viabot')) throw new Error('via_bot line missing');
  if (!document.querySelector('.sticker.lottie[data-src]')) throw new Error('lottie sticker markup missing');
  if (!document.querySelector('video.stk-vid')) throw new Error('webm sticker markup missing');
  var albumNode = document.querySelector('.msg[data-id="3"]');
  if (!albumNode || albumNode.dataset.gids !== '3,4') throw new Error('album gids wrong');
  var btn = document.querySelector('.ibtn[data-cb]');
  if (!btn) throw new Error('inline cb button missing');
  if (document.getElementById('botKb').hidden || document.querySelectorAll('#botKb .kbtn').length !== 2)
    throw new Error('bot keyboard not rendered');
    btn.click();
  toggleSelect(3);
  if (!S.selection[4]) throw new Error('album group-select failed');
  toggleSelect(3);

  // drafts + emoji
  S.current = 101; restoreDraft(101);
  document.getElementById('input').value = 'draft text';
  document.getElementById('input').dispatchEvent(new Event('input', {bubbles:true}));
  if (localStorage.getItem('tgw_draft_101') !== 'draft text') throw new Error('draft save failed');
  document.getElementById('btnEmoji').click();
  var picker = document.getElementById('emojiPicker');
  if (!picker) throw new Error('emoji picker did not open');
  var firstEmoji = picker.querySelector('.eem');
  if (firstEmoji) firstEmoji.click();
    gsClose();
  dialogRow({ id: 101, name: 'Alice', type: 'user', last: { snippet: 'hey', date: new Date().toISOString() } });

  // ---- night build 2: stories ----
    loadStoriesBar();
  'STORIES-PENDING';
`); } catch (e){ errors.push('FLOW: ' + e.message); }

/* stories bar renders async (fetch stub resolves on microtask) — poll a bit */
setTimeout(() => {
  try {
    dom.window.eval(`
      var bar = document.getElementById('storiesBar');
      if (bar.hidden) throw new Error('stories bar hidden');
      if (!bar.querySelector('[data-story="101"]')) throw new Error('alice story ring missing');
      if (!bar.querySelector('[data-story="777"]')) throw new Error('own story ring missing');
      // open the viewer for alice
      openStoryViewer(SBar.peers[0], 0, false);
      var v = document.getElementById('storyViewer');
      if (!v) throw new Error('story viewer did not open');
      if (!v.querySelector('.sv-media')) throw new Error('story media missing');
      if (v.querySelectorAll('.sv-bar').length !== 2) throw new Error('progress bars missing');
      // advance to story 2 (video)
      storyGo(SBar.peers[0], 1, false);
      if (!v.querySelector('video.sv-media')) throw new Error('video story not rendered');
      // own story: delete button present
            closeStoryViewer();
      openStoryViewer(SBar.me, 0, true);
      if (!v || !document.querySelector('.sv-del')) throw new Error('own story delete missing');
      closeStoryViewer();

      // ---- inline query ----
            var input = document.getElementById('input');
      input.value = '@storebot hello';
      input.dispatchEvent(new Event('input', {bubbles:true}));
    `);
    setTimeout(() => {
      try {
        dom.window.eval(`
          var drop = document.getElementById('inlineDrop');
          if (drop.hidden || !drop.querySelector('.il-item')) throw new Error('inline dropdown not rendered');
          sendInline(0);

          // ---- scheduled ----
          openScheduleModal('prefilled text');
          if (!document.getElementById('scGo')) throw new Error('schedule modal missing');
          if (document.getElementById('scText').value !== 'prefilled text') throw new Error('schedule prefill missing');
          closeModal();
          viewScheduled();
        `);
        setTimeout(() => {
          try {
            dom.window.eval(`
              if (!document.querySelector('.sch-row')) throw new Error('scheduled rows missing');
              if (!document.getElementById('inlineDrop').hidden) throw new Error('inline drop not closed after send');
              if (document.getElementById('input').value !== '') throw new Error('input not cleared after inline send');
              closeModal();
              playSound('send');
              'FRONTEND FLOW OK';
            `);
          } catch (e){ errors.push('FLOW3: ' + e.message); }
          done();
        }, 30);
      } catch (e){ errors.push('FLOW2: ' + e.message); done(); }
    }, 500);
  } catch (e){ errors.push('FLOW1: ' + e.message); done(); }

  function done(){
    console.log('scripts loaded:', ran + '/' + scripts.length);
    console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'NO RUNTIME ERRORS');
    process.exit(errors.length ? 1 : 0);
  }
}, 60);
