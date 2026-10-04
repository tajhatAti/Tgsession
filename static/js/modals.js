/* TgWeb — modals.js: modal dialogs: forward picker, gallery, members, in-chat search */
'use strict';
function openModal(html, wide){
  var box = $('#modalBox');
  box.className = wide ? 'wide' : '';
  box.innerHTML = html;
  $('#modal').hidden = false;
}
function closeModal(){
  $('#modal').hidden = true;
  $('#modalBox').innerHTML = '';
}
$('#modal').addEventListener('click', function(e){ if (e.target === this) closeModal(); });
function openConfirm(title, okLabel, fn){
  openModal('<div class="mhead"><b>' + esc(title) + '</b></div>' +
    '<div class="mbody" style="display:flex;gap:8px;justify-content:flex-end">' +
    '<button class="btn" id="cfNo">Cancel</button>' +
    '<button class="btn danger" id="cfYes">' + esc(okLabel) + '</button></div>');
  $('#cfNo').onclick = closeModal;
  $('#cfYes').onclick = function(){ closeModal(); fn(); };
}
function openForwardPicker(ids){
  openModal('<div class="mhead"><b>Forward to…</b>' +
    '<button class="icon-btn" id="fpClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div style="padding:8px 12px 0"><input id="fpSearch" placeholder="Search chats" style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);outline:none"></div>' +
    '<div class="mbody" id="fpList"></div>', true);
  $('#fpClose').onclick = closeModal;
  function render(q){
    var list = S.dialogs.filter(function(d){ return !q || d.name.toLowerCase().indexOf(q) >= 0; }).slice(0, 80);
    $('#fpList').innerHTML = list.map(function(d){
      return '<div class="rowitem" data-fid="' + d.id + '">' + avatarHTML(d.id, d.name, d.has_photo, 'small') +
        '<div class="ri-body"><div class="ri-t">' + esc(d.name) + '</div></div></div>';
    }).join('') || '<div class="empty-list">No chats</div>';
  }
  render('');
  $('#fpSearch').oninput = function(){ render(this.value.trim().toLowerCase()); };
  $('#fpList').onclick = function(e){
    var row = e.target.closest('[data-fid]');
    if (!row) return;
    var to = Number(row.dataset.fid);
    var from = S.current;
    closeModal();
    api('api/forward', {method: 'POST', body: {from_chat_id: from, msg_ids: ids, to_chat_id: to}})
      .then(function(){ toast('Forwarded'); openChat(to); })
      .catch(toastErr);
  };
}
/* ---------- media gallery ---------- */
$('#btnGallery').addEventListener('click', function(){ if (S.current != null) openGallery(); });
function openGallery(){
  S.galleryTab = 'media';
  S.galleryOffset = 0;
  S.galleryDone = false;
  var tabs = [['media', 'Photos & videos'], ['files', 'Files'], ['voice', 'Voice'], ['links', 'Links']];
  openModal('<div class="mhead"><b>Media</b>' +
    '<button class="icon-btn" id="galClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div class="mtabs" id="galTabs">' + tabs.map(function(t){
      return '<button data-gt="' + t[0] + '" class="' + (t[0] === 'media' ? 'on' : '') + '">' + esc(t[1]) + '</button>';
    }).join('') + '</div>' +
    '<div class="mbody"><div class="gal-grid" id="galGrid"></div>' +
    '<div id="galMore" style="padding:10px;text-align:center"><button class="btn" id="galMoreBtn">Load more</button></div></div>', true);
  $('#galClose').onclick = closeModal;
  $('#galTabs').onclick = function(e){
    var b = e.target.closest('[data-gt]');
    if (!b) return;
    S.galleryTab = b.dataset.gt;
    S.galleryOffset = 0;
    S.galleryDone = false;
    $$('#galTabs button').forEach(function(x){ x.classList.toggle('on', x === b); });
    $('#galGrid').innerHTML = '';
    loadGallery();
  };
  $('#galMoreBtn').onclick = function(){ loadGallery(); };
  loadGallery();
}
function loadGallery(){
  var tab = S.galleryTab;
  api('api/gallery?chat_id=' + S.current + '&tab=' + tab + '&limit=60' +
      (S.galleryOffset ? '&offset_id=' + S.galleryOffset : '')).then(function(r){
    var grid = $('#galGrid');
    if (!grid) return;
    var msgs = r.messages || [];
    if (msgs.length < 60) S.galleryDone = true;
    if (msgs.length) S.galleryOffset = msgs[msgs.length - 1].id;
    var html = '';
    msgs.forEach(function(m){
      if (tab === 'media'){
        var mi = m.media;
        if (!mi || ['photo', 'video', 'gif', 'videonote'].indexOf(mi.kind) < 0) return;
        html += '<div class="gal-item" data-gview="' + m.id + '" data-gkind="' + mi.kind + '">' +
          '<img loading="lazy" src="api/media/' + S.current + '/' + m.id + '?kind=thumb" onerror="this.remove()">' +
          (mi.duration && mi.kind !== 'photo' ? '<span class="vdur">' + fmtDur(mi.duration) + '</span>' : '') + '</div>';
      } else if (tab === 'files'){
        var mi2 = m.media;
        if (!mi2 || !mi2.name) return;
        html += '<div class="rowitem"><div class="ficon" style="width:36px;height:36px;border-radius:8px;background:var(--accent);color:#fff;display:grid;place-items:center;flex:none"><svg class="ic"><use href="#i-file"/></svg></div>' +
          '<div class="ri-body"><div class="ri-t">' + esc(mi2.name) + '</div><div class="ri-s">' + fmtSize(mi2.size) + '</div></div>' +
          '<a class="fdl" href="api/media/' + S.current + '/' + m.id + '?kind=file&amp;dl=1" download="' + esc(mi2.name) + '"><svg class="ic"><use href="#i-dl"/></svg></a></div>';
      } else if (tab === 'voice'){
        if (!m.media || m.media.kind !== 'voice') return;
        html += '<div class="rowitem"><svg class="ic"><use href="#i-mic"/></svg>' +
          '<div class="ri-body"><div class="ri-s">' + esc(fmtListTime(m.date)) + '</div></div>' +
          '<audio controls preload="none" src="api/media/' + S.current + '/' + m.id + '?kind=file" style="flex:1;min-width:0"></audio></div>';
      } else if (tab === 'links'){
        var wp = m.webpage;
        var url = wp ? wp.url : (m.raw || '');
        var title = wp ? (wp.title || wp.site || url) : url;
        if (!url) return;
        html += '<div class="rowitem" data-lurl="' + esc(url) + '"><svg class="ic"><use href="#i-forward"/></svg>' +
          '<div class="ri-body"><div class="ri-t" style="color:var(--link)">' + esc(title) + '</div>' +
          '<div class="ri-s">' + esc(url) + '</div></div></div>';
      }
    });
    grid.insertAdjacentHTML('beforeend', html);
    var more = $('#galMore');
    if (more) more.hidden = S.galleryDone;
  }).catch(toastErr);
}
$('#modalBox').addEventListener('click', function(e){
  var gv = e.target.closest('[data-gview]');
  if (gv){ openViewer(Number(gv.dataset.gview), gv.dataset.gkind); return; }
  var lu = e.target.closest('[data-lurl]');
  if (lu){ window.open(lu.dataset.lurl, '_blank', 'noopener'); return; }
});
/* ---------- members ---------- */
$('#btnMembers').addEventListener('click', function(){ if (S.current != null) openMembers(); });
function openMembers(){
  openModal('<div class="mhead"><b>Members</b>' +
    '<button class="icon-btn" id="mbClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div style="padding:8px 12px 0"><input id="mbSearch" placeholder="Search members" style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);outline:none"></div>' +
    '<div class="mbody" id="mbList"><div class="spinner"></div></div>' +
    '<div class="muted" id="mbNote" style="padding:0 14px 12px;font-size:13px"></div>', true);
  $('#mbClose').onclick = closeModal;
  var tmr = null;
  function load(q){
    api('api/members?chat_id=' + S.current + (q ? '&q=' + encodeURIComponent(q) : '')).then(function(r){
      var box = $('#mbList');
      if (!box) return;
      $('#mbNote').textContent = r.note || '';
      box.innerHTML = (r.members || []).map(function(u){
        return '<div class="rowitem" data-uid="' + u.id + '">' + avatarHTML(u.id, u.name, u.has_photo, 'small') +
          '<div class="ri-body"><div class="ri-t">' + esc(u.name) +
          (u.role ? '<span class="tag">' + esc(u.role) + '</span>' : '') + '</div>' +
          '<div class="ri-s">' + esc(u.status || (u.bot ? 'bot' : (u.username ? '@' + u.username : ''))) + '</div></div></div>';
      }).join('') || '<div class="empty-list">Nobody here</div>';
    }).catch(function(e){
      var box = $('#mbList');
      if (box) box.innerHTML = '';
      var n = $('#mbNote');
      if (n) n.textContent = (e && e.detail) || 'Could not load members';
    });
  }
  load('');
  $('#mbSearch').oninput = function(){
    var q = this.value.trim();
    clearTimeout(tmr);
    tmr = setTimeout(function(){ load(q); }, 350);
  };
  $('#mbList').onclick = function(e){
    var row = e.target.closest('[data-uid]');
    if (!row) return;
    var uid = Number(row.dataset.uid);
    closeModal();
    if (S.dialogById[uid]) openChat(uid);
    else { toast('Opening chat…'); loadDialogs(false).then(function(){ openChat(uid); }); }
  };
}
/* ---------- in-chat search ---------- */
function openCSearch(){
  if (S.current == null) return;
  S.csearchOpen = true;
  $('#csearchBar').hidden = false;
  $('#csearchResults').innerHTML = '';
  $('#csearchInput').value = '';
  $('#csearchInput').focus();
}
function closeCSearch(){
  S.csearchOpen = false;
  $('#csearchBar').hidden = true;
}
$('#btnCSearch').addEventListener('click', openCSearch);
$('#csearchClose').addEventListener('click', closeCSearch);
var csTimer = null;
$('#csearchInput').addEventListener('input', function(){
  var q = this.value.trim();
  clearTimeout(csTimer);
  if (!q){ $('#csearchResults').innerHTML = ''; return; }
  csTimer = setTimeout(function(){
    api('api/search?chat_id=' + S.current + '&q=' + encodeURIComponent(q)).then(function(r){
      $('#csearchResults').innerHTML = (r.messages || []).slice(0, 60).map(function(m){
        return '<div class="search-hit" data-sh="' + m.id + '">' +
          '<div class="sh-t">' + esc(m.sender ? m.sender.name : (m.out ? 'You' : '')) + ' · ' + esc(fmtListTime(m.date)) + '</div>' +
          '<div class="sh-b">' + (m.html || esc(previewOf(m))) + '</div></div>';
      }).join('') || '<div class="empty-list">Nothing found</div>';
    }).catch(toastErr);
  }, 350);
});
$('#csearchResults').addEventListener('click', function(e){
  var hit = e.target.closest('[data-sh]');
  if (!hit) return;
  jumpTo(Number(hit.dataset.sh));
  if (window.innerWidth < 900) closeCSearch();
});
/* ---------- media viewer ---------- */
