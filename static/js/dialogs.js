/* TgWeb — dialogs.js: chat list: loading, tabs, rendering */
'use strict';
function loadDialogs(silent){
  return api('api/dialogs').then(function(r){
    S.dialogs = r.dialogs || [];
    S.dialogById = {};
    S.dialogs.forEach(function(d){ S.dialogById[d.id] = d; });
    if (r.me) S.me = r.me;
    if (S.current != null && !S.dialogById[S.current]) closeChat();
    renderDialogs();
  }).catch(function(e){
    if (!silent) toastErr(e);
    if (e.status === 409) showTgLogin(e.detail);
  });
}
function tabFilter(d){
  if (S.tab === 'saved') return d.is_self;
  if (d.archived) return false;
  switch (S.tab){
    case 'user': return d.type === 'user' && !d.is_bot && !d.is_self;
    case 'group': return d.type === 'group';
    case 'channel': return d.type === 'channel';
    case 'bot': return d.is_bot;
    default: return true;
  }
}
function previewOf(m){
  if (!m) return '';
  if (m.service) return m.html || 'Service message';
  if (m.raw) return m.raw;
  if (m.media) return m.media.label || 'Media';
  if (m.webpage) return '🔗 Link';
  return '';
}
function dialogRow(d){
  var last = d.last || {};
  var pre = '';
  if (draft) pre = '';
  else if (last.out) pre = 'You: ';
  else if (last.sender && (d.type === 'group') && last.snippet) pre = last.sender + ': ';
  var draft = '';
  try { draft = (S.current !== d.id && localStorage.getItem('tgw_draft_' + d.id)) || ''; } catch (e) {}
  var snip = draft ? '<span class="draft">Draft: </span>' + esc(draft.slice(0, 60)) :
             (last.snippet ? esc(previewOfText(last.snippet)) : '&nbsp;');
  var badge = '';
  if (d.unread > 0) badge = '<span class="badge'+(d.muted?' gray':'')+'">'+(d.unread>999?'999+':d.unread)+'</span>';
  else if (d.mentions > 0) badge = '<span class="badge">@</span>';
  var nameExtras = (d.pinned ? '📌 ' : '') + (d.muted ? '🔇 ' : '') + (d.verified ? '✔ ' : '');
  return '<div class="dlg'+(S.current===d.id?' on':'')+'" data-id="'+d.id+'">'+
    avatarHTML(d.id, d.is_self ? 'Saved' : d.name, d.has_photo)+
    '<div class="dlg-body">'+
      '<div class="dlg-row1"><span class="dlg-name">'+nameExtras+esc(d.name)+'</span>'+
      '<span class="dlg-time">'+fmtListTime(last.date)+'</span></div>'+
      '<div class="dlg-row2"><span class="dlg-last">'+pre+snip+'</span>'+badge+'</div>'+
    '</div></div>';
}
function previewOfText(s){ return s; }
function renderDialogs(){
  var box = $('#dlgList');
  var q = $('#dlgSearch').value.trim().toLowerCase();
  var shown = S.dialogs.filter(function(d){
    if (!tabFilter(d)) return false;
    if (q && d.name.toLowerCase().indexOf(q) < 0) return false;
    return true;
  });
  var archived = (S.tab === 'all' && !q) ? S.dialogs.filter(function(d){ return d.archived; }) : [];
  var html = '';
  if (archived.length){
    html += '<div class="arch-head" data-arch="1"><svg class="ic"><use href="#i-archive"/></svg> Archived chats <span style="margin-left:auto">'+archived.length+'</span></div>';
    if (S.archivedOpen) archived.forEach(function(d){ html += dialogRow(d); });
  }
  html += shown.map(dialogRow).join('');
  if (!html) html = '<div class="empty-list">No chats here</div>';
  box.innerHTML = html;
}
$('#tabs').addEventListener('click', function(e){
  var b = e.target.closest('button'); if (!b) return;
  S.tab = b.dataset.tab;
  $$('#tabs button').forEach(function(x){ x.classList.toggle('on', x === b); });
  renderDialogs();
});
$('#dlgSearch').addEventListener('input', renderDialogs);
$('#dlgList').addEventListener('click', function(e){
  var arch = e.target.closest('[data-arch]');
  if (arch){ S.archivedOpen = !S.archivedOpen; renderDialogs(); return; }
  var row = e.target.closest('.dlg'); if (!row) return;
  openChat(Number(row.dataset.id));
});

/* ============================== chat view ============================== */


/* ============================== global search ============================== */
var gsTimer = null;
var gsToken = 0;
function gsClose(){
  var box = $('#gsearch');
  if (box) box.hidden = true;
}
function gsRender(r, q){
  var box = $('#gsearch');
  if (!box) return;
  var html = '';
  (r.chats || []).slice(0, 7).forEach(function(d){
    html += '<div class="gs-item" data-open="' + d.id + '">' +
      avatarHTML(d.id, d.is_self ? 'Saved' : d.name, d.has_photo, '', colorFor(d.id)) +
      '<div class="gs-body"><div class="gs-name">' + esc(d.name) + '</div>' +
      '<div class="gs-sub">' + esc(d.username ? '@' + d.username : d.type) + '</div></div></div>';
  });
  (r.messages || []).slice(0, 7).forEach(function(m){
    var name = (m.chat && m.chat.name) || 'Chat';
    html += '<div class="gs-item" data-open="' + m.chat_id + '" data-jump="' + m.id + '">' +
      avatarHTML(m.chat_id, name, m.chat ? m.chat.has_photo : false, '', colorFor(m.chat_id)) +
      '<div class="gs-body"><div class="gs-name">' + esc(name) + '</div>' +
      '<div class="gs-sub">' + esc((m.raw || m.snippet || '').slice(0, 70)) + '</div></div></div>';
  });
  if (!html) html = '<div class="gs-item gs-none">No results for “' + esc(q) + '”</div>';
  html = '<div class="gs-head">Global search</div>' + html;
  box.innerHTML = html;
  box.hidden = false;
}
$('#dlgSearch').addEventListener('input', function(){
  clearTimeout(gsTimer);
  var q = this.value.trim();
  if (q.length < 2){ gsClose(); return; }
  gsTimer = setTimeout(function(){
    var tok = ++gsToken;
    api('api/search_global?q=' + encodeURIComponent(q)).then(function(r){
      if (tok === gsToken) gsRender(r, q);
    }).catch(function(){});
  }, 350);
});
$('#dlgSearch').addEventListener('keydown', function(e){
  if (e.key === 'Escape'){ gsClose(); }
});
$('#gsearch').addEventListener('mousedown', function(e){
  var it = e.target.closest('.gs-item');
  if (!it) return;
  e.preventDefault();
  gsClose();
  var id = Number(it.dataset.open);
  if (!id) return;
  var jump = it.dataset.jump;
  openChat(id);
  if (jump) setTimeout(function(){ jumpTo(Number(jump)); }, 600);
});
document.addEventListener('click', function(e){
  if (!e.target.closest('#gsearch') && !e.target.closest('#dlgSearch')) gsClose();
});

/* ============================== new chat / join by username ============================== */
$('#btnNew').addEventListener('click', function(){
  openModal('<div class="mhead"><b>New chat</b>' +
    '<button class="icon-btn" id="ncClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div style="padding:12px">' +
      '<p class="hint" style="margin:0 0 8px">Enter a @username or t.me link to open a chat or join a channel/group.</p>' +
      '<input id="ncUser" placeholder="@username" autocomplete="off" style="width:100%;padding:10px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);outline:none">' +
      '<div id="ncResult" style="margin-top:10px"></div>' +
    '</div>');
  $('#ncClose').onclick = closeModal;
  setTimeout(function(){ $('#ncUser').focus(); }, 50);
  $('#ncUser').addEventListener('keydown', function(e){
    if (e.key === 'Enter'){ e.preventDefault(); ncResolve(this.value); }
  });
  var t = null;
  $('#ncUser').addEventListener('input', function(){
    clearTimeout(t);
    var v = this.value.trim();
    if (v.length < 3) return;
    t = setTimeout(function(){ ncResolve(v); }, 500);
  });
});

function ncResolve(name){
  if (!name) return;
  api('api/resolve?username=' + encodeURIComponent(name)).then(function(r){
    var c = r.chat || {};
    var box = $('#ncResult');
    if (!box) return;
    box.innerHTML = '<div class="rowitem" data-nc="' + c.id + '" data-join="' + (r.can_join ? '1' : '') + '">' +
      avatarHTML(c.id, c.name, c.has_photo, 'small', colorFor(c.id)) +
      '<div class="ri-body"><div class="ri-t">' + esc(c.name) + '</div>' +
      '<div class="ri-s">' + esc((c.username ? '@' + c.username : c.type) +
        (c.participants_count ? ' · ' + Number(c.participants_count).toLocaleString() + ' members' : '')) + '</div></div>' +
      '<button class="btn primary" style="margin-left:auto">' + (r.can_join ? 'Join' : 'Open') + '</button></div>';
    box.onclick = function(e){
      var row = e.target.closest('[data-nc]');
      if (!row) return;
      var id = Number(row.dataset.nc);
      if (row.dataset.join){
        api('api/join', {method: 'POST', body: {username: c.username}})
          .then(function(){ closeModal(); loadDialogs(true); setTimeout(function(){ openChat(id); }, 400); })
          .catch(toastErr);
      } else {
        /* chat may not be in the dialog list yet — add a temporary entry */
        if (!S.dialogById[id]){
          var d = {id: id, type: c.type, name: c.name, username: c.username,
                   has_photo: c.has_photo, verified: c.verified, unread: 0, mentions: 0,
                   pinned: false, muted: false, archived: false, last: {}};
          S.dialogs.unshift(d);
          S.dialogById[id] = d;
        }
        closeModal();
        openChat(id);
      }
    };
  }).catch(function(e){
    var box = $('#ncResult');
    if (box) box.innerHTML = '<div class="empty-list">' + esc(e.detail || 'Not found') + '</div>';
  });
}
