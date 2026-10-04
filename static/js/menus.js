/* TgWeb — menus.js: context menus, message actions, chat menu, theme, logout */
'use strict';
var menuActions = {};
function closeMenus(){
  $$('.menu').forEach(function(m){ m.remove(); });
  document.removeEventListener('click', menuOutside, true);
}
function menuOutside(e){
  if (!e.target.closest('.menu')) closeMenus();
}
function openMenu(items, x, y){
  closeMenus();
  menuActions = {};
  var m = document.createElement('div');
  m.className = 'menu';
  var html = '';
  items.forEach(function(it, idx){
    if (it.reactions){
      html += '<div class="rx">' + it.reactions.list.map(function(em){
        return '<button data-rem="' + esc(em) + '">' + esc(em) + '</button>';
      }).join('') + '</div><div class="sep"></div>';
    } else if (it.sep){
      html += '<div class="sep"></div>';
    } else {
      html += '<button data-mi="' + idx + '"' + (it.danger ? ' class="danger"' : '') + '>' +
        (it.icon ? '<svg class="ic"><use href="#' + it.icon + '"/></svg>' : '') +
        '<span>' + esc(it.label) + '</span></button>';
      menuActions[idx] = it.fn || null;
    }
  });
  m.innerHTML = html;
  document.body.appendChild(m);
  var r = m.getBoundingClientRect();
  m.style.left = Math.max(8, Math.min(x, window.innerWidth - r.width - 8)) + 'px';
  m.style.top = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + 'px';
  document.addEventListener('click', menuOutside, true);
  m.addEventListener('click', function(e){
    var rb = e.target.closest('[data-rem]');
    if (rb){
      var em = rb.getAttribute('data-rem');
      var rItem = items.filter(function(it){ return it.reactions && it.reactions.list.indexOf(em) >= 0; })[0];
      closeMenus();
      if (rItem) rItem.reactions.fn(em);
      return;
    }
    var b = e.target.closest('[data-mi]');
    if (b){
      var fn = menuActions[b.getAttribute('data-mi')];
      closeMenus();
      if (fn) fn();
    }
  });
}
function actReact(m, emoji, mine){
  if (!m || S.current == null) return;
  api('api/react', {method: 'POST', body: {chat_id: S.current, msg_id: m.id, emoji: mine ? '' : emoji}})
    .then(function(r){ if (r.message) replaceMessage(r.message); })
    .catch(toastErr);
}
function actPin(m, pinned){
  api('api/pin', {method: 'POST', body: {chat_id: S.current, msg_id: m.id, pinned: !!pinned}})
    .then(function(){ toast(pinned ? 'Pinned' : 'Unpinned'); })
    .catch(toastErr);
}
function actDelete(ids){
  openConfirm('Delete ' + (ids.length > 1 ? ids.length + ' messages' : 'message') + '?', 'Delete', function(){
    api('api/delete', {method: 'POST', body: {chat_id: S.current, msg_ids: ids, revoke: true}})
      .then(function(){ removeMessages(ids); toast('Deleted'); })
      .catch(toastErr);
  });
}
function openMsgMenu(m, x, y){
  if (!m) return;
  var items = [];
  items.push({reactions: {list: ['👍','👎','❤️','🔥','🎉','😂','😮','😢','🙏'],
                          fn: function(em){ actReact(m, em, false); }}});
  if (!m.service){
    items.push({icon: 'i-reply', label: 'Reply', fn: function(){ setReply(m); }});
    if (m.out && (m.raw || m.html)) items.push({icon: 'i-edit', label: 'Edit', fn: function(){ setEditing(m); }});
    if (m.raw) items.push({icon: 'i-copy', label: 'Copy text', fn: function(){
      try { navigator.clipboard.writeText(m.raw); toast('Copied'); }
      catch (err) { toast('Could not copy'); }
    }});
    items.push({icon: 'i-forward', label: 'Forward', fn: function(){ openForwardPicker([m.id]); }});
    items.push({icon: 'i-pin', label: 'Pin message', fn: function(){ actPin(m, true); }});
    items.push({icon: 'i-pin', label: 'Unpin message', fn: function(){ actPin(m, false); }});
  }
  items.push({icon: 'i-select', label: 'Select', fn: function(){ enterSelecting(); toggleSelect(m.id); }});
  items.push({sep: true});
  items.push({icon: 'i-trash', label: 'Delete', danger: true, fn: function(){ actDelete([m.id]); }});
  openMenu(items, x, y);
}
$('#btnChatMenu').addEventListener('click', function(e){
  e.stopPropagation();
  var r = this.getBoundingClientRect();
  var d = S.dlg || {};
  openMenu([
    {icon: 'i-user', label: 'Chat info', fn: function(){ if (S.current != null) openProfile(S.current); }},
    {icon: d.muted ? 'i-bell' : 'i-bell-off', label: d.muted ? 'Unmute' : 'Mute', fn: toggleMute},
    {icon: d.archived ? 'i-unarchive' : 'i-archive', label: d.archived ? 'Unarchive' : 'Archive', fn: toggleArchive},
    {sep: true},
    {icon: 'i-checks', label: 'Mark as read', fn: markRead},
    {icon: 'i-down', label: 'Jump to latest', fn: jumpLatest},
  ], r.right - 210, r.bottom + 6);
});
function toggleMute(){
  var d = S.dlg;
  if (!d) return;
  api('api/mute', {method: 'POST', body: {chat_id: d.id, muted: !d.muted}}).then(function(){
    d.muted = !d.muted;
    renderDialogs();
    toast(d.muted ? 'Muted' : 'Unmuted');
  }).catch(toastErr);
}
function toggleArchive(){
  var d = S.dlg;
  if (!d) return;
  var was = d.archived;
  api('api/archive', {method: 'POST', body: {chat_id: d.id, archived: !was}}).then(function(){
    toast(was ? 'Unarchived' : 'Archived');
    loadDialogs(true);
  }).catch(toastErr);
}
$('#btnMenu').addEventListener('click', function(e){
  e.stopPropagation();
  var r = this.getBoundingClientRect();
  var dark = document.documentElement.getAttribute('data-theme') !== 'light';
  openMenu([
    {icon: 'i-user', label: 'My profile', fn: function(){ openProfile(S.me ? S.me.id : null); }},
    {icon: 'i-saved', label: 'Saved Messages', fn: openSaved},
    {icon: 'i-gear', label: 'Settings', fn: openSettings},
    {icon: dark ? 'i-sun' : 'i-moon', label: dark ? 'Light theme' : 'Dark theme', fn: toggleTheme},
    {sep: true},
    {icon: 'i-lock', label: 'Lock site', fn: lockSite},
    {icon: 'i-logout', label: 'Log out Telegram', danger: true, fn: doLogout},
  ], r.left, r.bottom + 6);
});
function openSaved(){
  var d = S.dialogs.filter(function(x){ return x.is_self; })[0];
  if (d) openChat(d.id);
  else toast('Saved Messages is not in the dialog list yet');
}
function toggleTheme(){
  var cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', cur);
  try { localStorage.setItem('tgw_theme', cur); } catch (e) {}
}
function lockSite(){
  api('api/site_logout', {method: 'POST'}).catch(function(){}).finally(function(){
    stopPolling();
    if (S.dlgTimer) clearInterval(S.dlgTimer);
    showScreen('site');
  });
}
function doLogout(){
  openConfirm('Log out from Telegram?', 'Log out', function(){
    api('api/tg/logout', {method: 'POST'}).then(function(){
      stopPolling();
      if (S.dlgTimer) clearInterval(S.dlgTimer);
      S.dialogs = [];
      S.dialogById = {};
      closeChat();
      showTgLogin(null);
    }).catch(toastErr);
  });
}

/* ============================== message events ============================== */
$('#msgs').addEventListener('click', function(e){
  if (suppressClick){ suppressClick = false; return; }
  var el = e.target;
  var spoiler = el.closest('.spoiler');
  if (spoiler){ spoiler.classList.toggle('revealed'); return; }
  var msgRow = el.closest('.msg');
  if (S.selecting && msgRow){ toggleSelect(Number(msgRow.dataset.id)); return; }
  var quote = el.closest('.quote');
  if (quote){ jumpTo(Number(quote.dataset.jump)); return; }
  var react = el.closest('.react');
  if (react && msgRow){
    var m = S.msgById[msgRow.dataset.id];
    actReact(m, react.getAttribute('data-react'), react.classList.contains('mine'));
    return;
  }
  var wp = el.closest('.wp');
  if (wp && wp.dataset.url){ window.open(wp.dataset.url, '_blank', 'noopener'); return; }
  var who = el.closest('[data-uid]');
  if (who){
    var uid = Number(who.dataset.uid);
    if (uid && (!S.me || uid !== S.me.id)) openChat(uid);
    return;
  }
  var view = el.closest('[data-act="view"]');
  if (view && msgRow){ openViewer(Number(view.dataset.mid || msgRow.dataset.id), view.dataset.kind); return; }
  var act = el.closest('.abtn');
  if (act && msgRow){
    var m2 = S.msgById[msgRow.dataset.id];
    if (act.dataset.a === 'react') actReact(m2, '👍', false);
    else openMsgMenu(m2, act.getBoundingClientRect().left, act.getBoundingClientRect().bottom + 4);
    return;
  }
});
$('#msgs').addEventListener('play', function(e){
  $$('audio', $('#msgs')).forEach(function(a){ if (a !== e.target) a.pause(); });
}, true);
var lpTimer = null, suppressClick = false;
$('#msgs').addEventListener('touchstart', function(e){
  var row = e.target.closest('.msg,.svc');
  if (!row || !row.dataset.id) return;
  var t = e.touches[0];
  var x = t.clientX, y = t.clientY;
  var rid = row.dataset.id;
  lpTimer = setTimeout(function(){
    lpTimer = null;
    suppressClick = true;
    if (navigator.vibrate){ try { navigator.vibrate(10); } catch (ex) {} }
    openMsgMenu(S.msgById[rid], x, y);
  }, 480);
}, {passive: true});
['touchend', 'touchmove', 'touchcancel'].forEach(function(ev){
  $('#msgs').addEventListener(ev, function(){
    if (lpTimer){ clearTimeout(lpTimer); lpTimer = null; }
  }, {passive: true});
});
$('#msgs').addEventListener('contextmenu', function(e){
  var row = e.target.closest('.msg,.svc');
  if (!row || !row.dataset.id) return;
  e.preventDefault();
  openMsgMenu(S.msgById[row.dataset.id], e.clientX, e.clientY);
});

/* ============================== modal helpers ============================== */
