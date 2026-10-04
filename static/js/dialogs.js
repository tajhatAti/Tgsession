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
  if (last.out) pre = 'You: ';
  else if (last.sender && (d.type === 'group') && last.snippet) pre = last.sender + ': ';
  var snip = last.snippet ? esc(previewOfText(last.snippet)) : '&nbsp;';
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
