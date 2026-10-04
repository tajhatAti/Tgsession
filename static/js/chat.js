/* TgWeb — chat.js: chat view: open/close, message rendering, polling, jumping */
'use strict';
function closeChat(){
  S.current = null; S.dlg = null;
  S.msgs = []; S.msgById = {};
  S.oldest = 0; S.newest = 0; S.hasMore = true;
  clearReply(); clearEditing(); clearPendingFile(); exitSelecting(); closeCSearch();
  stopPolling();
  document.body.classList.remove('chat-open');
  $('#chat').hidden = true;
  $('#empty').hidden = false;
  renderDialogs();
}
function updateChatHeader(){
  var d = S.dlg; if (!d) return;
  $('#chatAvaWrap').innerHTML = avatarHTML(d.id, d.is_self ? 'Saved' : d.name, d.has_photo, 'small');
  $('#chatTitle').textContent = (d.verified ? '✔ ' : '') + d.name;
  setSubDefault();
}
function setSubDefault(){
  var d = S.dlg; if (!d) return;
  var sub = '';
  if (d.is_bot) sub = 'bot';
  else if (d.type === 'user') sub = d.status || (d.username ? '@' + d.username : '');
  else if (d.members) sub = Number(d.members).toLocaleString() + (d.type === 'channel' ? ' subscribers' : ' members');
  else if (d.type === 'group') sub = 'group';
  else sub = d.username ? '@' + d.username : 'channel';
  var el = $('#chatSub');
  el.textContent = sub || ' ';
  el.classList.remove('typing');
}
function setTyping(names){
  var el = $('#chatSub');
  if (names && names.length && S.dlg){
    el.textContent = names.length > 1 ? names.length + ' people are typing…' : names[0] + ' is typing…';
    el.classList.add('typing');
  } else { setSubDefault(); }
}
function openChat(id){
  var d = S.dialogById[id];
  if (!d){ toast('Chat not found — refreshing'); loadDialogs(true); return; }
  if (S.current === id){ document.body.classList.add('chat-open'); return; }
  S.current = id; S.dlg = d;
  S.msgs = []; S.msgById = {};
  S.oldest = 0; S.newest = 0; S.hasMore = true;
  S.readIn = 0; S.readOut = 0;
  clearReply(); clearEditing(); clearPendingFile(); exitSelecting(); closeCSearch();
  document.body.classList.add('chat-open');
  $('#empty').hidden = true;
  $('#chat').hidden = false;
  $('#msgs').innerHTML = '<div class="spinner"></div>';
  updateChatHeader();
  renderDialogs();
  startPolling();
  var chatId = id;
  api('api/messages?chat_id=' + id + '&limit=50').then(function(r){
    if (S.current !== chatId) return;
    S.readIn = r.read_inbox_max_id || 0;
    S.readOut = r.read_outbox_max_id || 0;
    var list = (r.messages || []).slice().reverse();
    S.msgs = list;
    S.msgById = {};
    list.forEach(function(m){ S.msgById[m.id] = m; });
    S.oldest = list.length ? list[0].id : 0;
    S.newest = list.length ? list[list.length - 1].id : 0;
    S.hasMore = list.length >= 50;
    renderAllMessages();
    scrollBottom();
    setTyping(r.typing || []);
    updateFab();
    if (window.matchMedia('(min-width:900px)').matches) $('#input').focus();
  }).catch(function(e){
    $('#msgs').innerHTML = '';
    toastErr(e);
  });
}
$('#btnBack').addEventListener('click', closeChat);
$('#chatTitleWrap').addEventListener('click', function(){
  if (S.dlg && (S.dlg.type === 'group' || S.dlg.type === 'channel')) openMembers();
});

/* ============================== message rendering ============================== */
function sameMinute(a, b){
  var x = a && a.date ? new Date(a.date) : null;
  var y = b && b.date ? new Date(b.date) : null;
  return !!(x && y && Math.abs(x - y) < 10 * 60 * 1000);
}
function isFirstOfGroup(m, prev){
  if (!prev) return true;
  if (prev.service || m.service) return true;
  var pid = prev.sender ? prev.sender.id : null;
  var mid = m.sender ? m.sender.id : null;
  return prev.out !== m.out || pid !== mid || !sameMinute(prev, m);
}
function checksHTML(m){
  return '<svg class="ic"><use href="#' + (m.id <= (S.readOut || 0) ? 'i-checks' : 'i-check') + '"/></svg>';
}
function quoteHTML(r){
  return '<div class="quote" data-jump="' + r.id + '"><b>' + esc(r.name || 'Message') + '</b>' +
    (r.snippet ? '<span>' + esc(r.snippet) + '</span>' : '') + '</div>';
}
function reactionsHTML(m){
  return '<div class="reacts">' + m.reactions.map(function(r){
    return '<span class="react' + (r.me ? ' mine' : '') + '" data-react="' + esc(r.emoji) + '">' +
      esc(r.emoji) + (r.count > 1 ? ' ' + r.count : '') + '</span>';
  }).join('') + '</div>';
}
function webpageHTML(m){
  var wp = m.webpage;
  if (!wp || !wp.url) return '';
  var img = wp.thumb ? '<img loading="lazy" src="api/media/' + S.current + '/' + m.id + '?kind=thumb" onerror="this.remove()">' : '';
  return '<div class="wp" data-url="' + esc(wp.url) + '">' +
    (wp.site ? '<div class="wsite">' + esc(wp.site) + '</div>' : '') +
    (wp.title ? '<div class="wtitle">' + esc(wp.title) + '</div>' : '') +
    (wp.desc ? '<div class="wdesc">' + esc(wp.desc) + '</div>' : '') + img + '</div>';
}
var TINY_GIF = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
function mediaHTML(m){
  var mi = m.media;
  if (!mi) return '';
  var u = 'api/media/' + S.current + '/' + m.id;
  var k = mi.kind;
  if (k === 'photo'){
    var st = (mi.w && mi.h) ? ' style="aspect-ratio:' + mi.w + '/' + mi.h + ';max-height:60vh;object-fit:cover"' : '';
    return '<div class="photo"><img loading="lazy"' + st + ' src="' + u + '?kind=thumb" data-act="view" data-kind="photo" data-full="' + u + '?kind=photo" onerror="this.onerror=null;this.src=this.getAttribute(&quot;data-full&quot;)"></div>';
  }
  if (k === 'video' || k === 'gif' || k === 'videonote'){
    return '<div class="video-wrap" data-act="view" data-kind="' + k + '">' +
      '<img loading="lazy" src="' + u + '?kind=thumb" style="min-width:120px;min-height:90px" onerror="this.onerror=null;this.src=\'' + TINY_GIF + '\'" alt="">' +
      '<span class="play"><svg class="ic big"><use href="#i-play"/></svg></span>' +
      (mi.duration ? '<span class="dur">' + fmtDur(mi.duration) + '</span>' : '') + '</div>';
  }
  if (k === 'voice'){
    return '<div><audio controls preload="none" src="' + u + '?kind=file"></audio>' +
      (mi.duration ? '<div class="fsize">' + fmtDur(mi.duration) + '</div>' : '') + '</div>';
  }
  if (k === 'audio'){
    return '<div style="min-width:220px"><div class="fname">' + esc(mi.title || mi.name || 'Audio') + '</div>' +
      (mi.performer ? '<div class="fsize">' + esc(mi.performer) + '</div>' : '') +
      '<audio controls preload="none" src="' + u + '?kind=file"></audio></div>';
  }
  if (k === 'sticker'){
    return '<div class="sticker"><img loading="lazy" src="' + u + '?kind=thumb" alt="" data-alt="' + esc(mi.alt || '🙂') + '" onerror="this.onerror=null;this.parentNode.textContent=this.getAttribute(&quot;data-alt&quot;)"></div>';
  }
  if (k === 'file' || k === 'picfile'){
    return '<div class="filebox"><div class="ficon"><svg class="ic big"><use href="#i-file"/></svg></div>' +
      '<div style="min-width:0"><div class="fname">' + esc(mi.name || 'File') + '</div>' +
      '<div class="fsize">' + fmtSize(mi.size) + '</div>' +
      '<a class="fdl" href="' + u + '?kind=file&amp;dl=1" download="' + esc(mi.name || ('file_' + m.id)) + '">Download</a></div></div>';
  }
  if (k === 'poll'){
    var opts = (mi.answers || []).map(function(a){
      return '<div class="opt' + (a.chosen ? ' chosen' : '') + '">' + esc(a.text) +
        (a.voters != null ? '<span class="pc">' + a.voters + '</span>' : '') + '</div>';
    }).join('');
    return '<div class="pollbox"><div class="q">' + esc(mi.question || 'Poll') + '</div>' + opts +
      (mi.total ? '<div class="fsize">' + mi.total + ' vote(s)</div>' : '') +
      (mi.closed ? '<div class="fsize">Final results</div>' : '') + '</div>';
  }
  if (k === 'geo'){
    return '<a href="https://maps.google.com/?q=' + encodeURIComponent(mi.lat + ',' + mi.lon) +
      '" target="_blank" rel="noopener noreferrer">📍 ' + (mi.lat || '?') + ', ' + (mi.lon || '?') + '</a>';
  }
  if (k === 'contact'){
    return '<div class="fname">' + esc(mi.label) + '</div><div class="fsize">' + esc(mi.phone || '') + '</div>';
  }
  if (k === 'dice'){
    return '<div style="font-size:44px;text-align:center">' + esc(mi.emoji || '🎲') + ' ' + (mi.value || '') + '</div>';
  }
  return '<div class="fsize">' + esc(mi.label || 'Media') + '</div>';
}
function messageHTML(m, prev){
  if (m.service){
    return '<div class="svc" data-id="' + m.id + '">' + (m.html || 'Service message') + '</div>';
  }
  var out = !!m.out;
  var cls = ['msg', out ? 'out' : 'in'];
  if (isFirstOfGroup(m, prev)) cls.push('first');
  var inner = '';
  if (m.forward_from) inner += '<div class="fwd">Forwarded from ' + esc(m.forward_from) + '</div>';
  var inGroup = S.dlg && (S.dlg.type === 'group');
  if (!out && inGroup && m.sender && m.sender.name){
    inner += '<div class="sender" data-uid="' + m.sender.id + '" style="color:' + colorFor(m.sender.id) + '">' + esc(m.sender.name) + '</div>';
  }
  if (m.reply_to) inner += quoteHTML(m.reply_to);
  inner += mediaHTML(m);
  if (m.webpage) inner += webpageHTML(m);
  if (m.html) inner += '<div class="text">' + m.html + '</div>';
  if (m.reactions && m.reactions.length) inner += reactionsHTML(m);
  inner += '<span class="meta">' + (m.edited ? 'edited ' : '') + esc(fmtClock(m.date)) + (out ? checksHTML(m) : '') + '</span>';
  if (m.views) inner += '<span class="fsize">👁 ' + Number(m.views).toLocaleString() + '</span>';
  var av = '';
  if (!out && inGroup && m.sender){
    av = avatarHTML(m.sender.id, m.sender.name, false, '', colorFor(m.sender.id));
  }
  return '<div class="' + cls.join(' ') + '" data-id="' + m.id + '">' + av +
    '<div class="bub">' + inner + '</div>' +
    '<div class="acts"><button class="abtn" data-a="react" title="React">👍</button>' +
    '<button class="abtn" data-a="menu" title="More"><svg class="ic"><use href="#i-more"/></svg></button></div>' +
    '</div>';
}
function buildListHTML(){
  if (!S.msgs.length) return '<div class="empty-list" style="margin:auto">No messages here yet.<br>Say hello 👋</div>';
  var html = '';
  var prev = null;
  var chipDone = false;
  S.msgs.forEach(function(m){
    var pd = prev && prev.date ? new Date(prev.date) : null;
    var d = m.date ? new Date(m.date) : null;
    if ((d && !pd) || (d && pd && !sameDay(pd, d))){
      html += '<div class="day-chip">' + esc(dayLabel(d)) + '</div>';
    }
    if (!chipDone && !m.out && !m.service && m.id > (S.readIn || 0)){
      html += '<div class="unread-chip">Unread messages</div>';
      chipDone = true;
    }
    html += messageHTML(m, prev);
    prev = m;
  });
  return html;
}
function renderAllMessages(){
  $('#msgs').innerHTML = buildListHTML();
}
function appendNodes(list){
  if (!list.length) return;
  var box = $('#msgs');
  var empty = box.querySelector('.empty-list');
  if (empty) empty.remove();
  var prevIdx = S.msgs.length - list.length - 1;
  var prev = prevIdx >= 0 ? S.msgs[prevIdx] : null;
  var html = '';
  list.forEach(function(m){
    var pd = prev && prev.date ? new Date(prev.date) : null;
    var d = m.date ? new Date(m.date) : null;
    if ((d && !pd) || (d && pd && !sameDay(pd, d))){
      html += '<div class="day-chip">' + esc(dayLabel(d)) + '</div>';
    }
    html += messageHTML(m, prev);
    prev = m;
  });
  box.insertAdjacentHTML('beforeend', html);
}
function replaceMessage(m){
  if (!m || S.msgById[m.id] === undefined) return;
  var old = S.msgById[m.id];
  var i = S.msgs.indexOf(old);
  if (i < 0) return;
  S.msgs[i] = m;
  S.msgById[m.id] = m;
  var prev = i > 0 ? S.msgs[i - 1] : null;
  var node = $('#msgs .msg[data-id="' + m.id + '"]');
  if (node) node.outerHTML = messageHTML(m, prev);
}
function removeMessages(ids){
  var idset = {};
  ids.forEach(function(i){ idset[i] = true; });
  S.msgs = S.msgs.filter(function(m){ return !idset[m.id]; });
  ids.forEach(function(i){
    delete S.msgById[i];
    var node = $('#msgs .msg[data-id="' + i + '"], #msgs .svc[data-id="' + i + '"]');
    if (node) node.remove();
  });
  S.selection = {};
}

/* ============================== scrolling ============================== */
function isNearBottom(){
  var b = $('#msgs');
  return b.scrollHeight - b.scrollTop - b.clientHeight < 160;
}
function scrollBottom(){
  var b = $('#msgs');
  b.scrollTop = b.scrollHeight;
}
function unreadLoadedCount(){
  var n = 0;
  for (var i = S.msgs.length - 1; i >= 0; i--){
    var m = S.msgs[i];
    if (m.id <= (S.readIn || 0)) break;
    if (!m.out && !m.service) n++;
  }
  return n;
}
function updateFab(){
  var fab = $('#scrollDown');
  fab.hidden = S.current == null || isNearBottom();
  var n = unreadLoadedCount();
  var b = $('#sdBadge');
  b.hidden = !n;
  b.textContent = n > 99 ? '99+' : n;
}
$('#msgs').addEventListener('scroll', function(){
  if (this.scrollTop < 80 && S.hasMore && !S.loadingOlder && S.current != null) loadOlder();
  updateFab();
});
$('#scrollDown').addEventListener('click', scrollBottom);
function loadOlder(){
  if (S.loadingOlder || !S.hasMore || S.current == null || !S.oldest) return;
  S.loadingOlder = true;
  var box = $('#msgs');
  var prevH = box.scrollHeight, prevTop = box.scrollTop;
  var chatId = S.current;
  api('api/messages?chat_id=' + chatId + '&offset_id=' + S.oldest + '&limit=50').then(function(r){
    if (S.current !== chatId) return;
    var older = (r.messages || []).filter(function(x){ return S.msgById[x.id] === undefined; }).reverse();
    if (older.length < 50) S.hasMore = false;
    if (older.length){
      older.forEach(function(m){ S.msgs.unshift(m); S.msgById[m.id] = m; });
      S.oldest = older[0].id;
      renderAllMessages();
      box.scrollTop = box.scrollHeight - prevH + prevTop;
    } else { S.hasMore = false; }
  }).catch(toastErr).finally(function(){ S.loadingOlder = false; });
}

/* ============================== polling ============================== */
function startPolling(){
  stopPolling();
  S.pollTimer = setInterval(pollMessages, 8000);
}
function stopPolling(){
  if (S.pollTimer){ clearInterval(S.pollTimer); S.pollTimer = null; }
}
function updateChecks(){
  S.msgs.forEach(function(m){
    if (!m.out) return;
    var node = $('#msgs .msg[data-id="' + m.id + '"] .meta use');
    if (node) node.setAttribute('href', m.id <= (S.readOut || 0) ? '#i-checks' : '#i-check');
  });
}
function updateDialogPreview(m){
  var d = S.dialogById[S.current];
  if (!d || !m) return;
  d.last = {id: m.id, snippet: previewOf(m), date: m.date, out: m.out,
            sender: m.sender ? m.sender.name : null};
  renderDialogs();
}
function pollMessages(){
  var id = S.current;
  if (id == null || document.hidden || S.viewerOpen || !S.newest) return;
  api('api/messages?chat_id=' + id + '&min_id=' + S.newest + '&limit=100').then(function(r){
    if (S.current !== id) return;
    if (typeof r.read_outbox_max_id === 'number') S.readOut = Math.max(S.readOut || 0, r.read_outbox_max_id);
    if (typeof r.read_inbox_max_id === 'number') S.readIn = Math.max(S.readIn || 0, r.read_inbox_max_id);
    var fresh = (r.messages || []).filter(function(x){ return S.msgById[x.id] === undefined; }).reverse();
    if (fresh.length){
      var nearBottom = isNearBottom();
      fresh.forEach(function(m){ S.msgs.push(m); S.msgById[m.id] = m; });
      S.newest = Math.max.apply(null, [S.newest].concat(fresh.map(function(x){ return x.id; })));
      appendNodes(fresh);
      if (nearBottom || fresh[fresh.length - 1].out) scrollBottom();
      updateDialogPreview(fresh[fresh.length - 1]);
    }
    setTyping(r.typing || []);
    updateChecks();
    updateFab();
  }).catch(toastErr);
}
function jumpLatest(){
  if (S.current == null) return;
  var chatId = S.current;
  api('api/messages?chat_id=' + chatId + '&limit=50').then(function(r){
    if (S.current !== chatId) return;
    var list = (r.messages || []).slice().reverse();
    S.msgs = list;
    S.msgById = {};
    list.forEach(function(m){ S.msgById[m.id] = m; });
    S.oldest = list.length ? list[0].id : 0;
    S.newest = list.length ? list[list.length - 1].id : 0;
    S.hasMore = list.length >= 50;
    if (typeof r.read_inbox_max_id === 'number') S.readIn = r.read_inbox_max_id;
    if (typeof r.read_outbox_max_id === 'number') S.readOut = r.read_outbox_max_id;
    renderAllMessages();
    scrollBottom();
  }).catch(toastErr);
}
function jumpTo(id){
  if (!id || S.current == null) return;
  var chatId = S.current;
  api('api/messages?chat_id=' + chatId + '&anchor_id=' + id + '&limit=30').then(function(r){
    if (S.current !== chatId) return;
    var list = (r.messages || []).slice().reverse();
    if (!list.length){ toast('Message not found'); return; }
    S.msgs = list;
    S.msgById = {};
    list.forEach(function(m){ S.msgById[m.id] = m; });
    S.oldest = list[0].id;
    S.newest = list[list.length - 1].id;
    S.hasMore = true;
    if (typeof r.read_inbox_max_id === 'number') S.readIn = Math.max(S.readIn || 0, r.read_inbox_max_id);
    if (typeof r.read_outbox_max_id === 'number') S.readOut = Math.max(S.readOut || 0, r.read_outbox_max_id);
    renderAllMessages();
    var node = $('#msgs .msg[data-id="' + id + '"]');
    if (node){ node.classList.add('flash'); node.scrollIntoView({block: 'center'}); }
    else scrollBottom();
  }).catch(toastErr);
}

/* ============================== composer ============================== */
