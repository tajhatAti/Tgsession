/* TgWeb — util.js: helpers: $/$$, escaping, formatting, avatars, toasts, state S, api() */
'use strict';
/* ============================== helpers ============================== */
var $ = function(s, r){ return (r||document).querySelector(s); };
var $$ = function(s, r){ return Array.prototype.slice.call((r||document).querySelectorAll(s)); };
function esc(s){
  return String(s==null?'':s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
var COLORS = ['#e17076','#7bc862','#e5ca77','#65aadd','#a695e7','#ee7aae','#faa774'];
function colorFor(id){ return COLORS[Math.abs(id||0)%COLORS.length]; }
function initials(name){
  var p = String(name||'?').trim().split(/\s+/);
  var s = (p[0]?p[0][0]:'?') + (p[1]?p[1][0]:'');
  return s.toUpperCase() || '?';
}
function fmtSize(n){
  n = Number(n)||0;
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n/1024).toFixed(1) + ' KB';
  if (n < 1073741824) return (n/1048576).toFixed(1) + ' MB';
  return (n/1073741824).toFixed(2) + ' GB';
}
function fmtDur(s){
  s = Math.max(0, Math.round(Number(s)||0));
  var m = Math.floor(s/60); s = s%60;
  return m + ':' + (s<10?'0':'') + s;
}
function fmtClock(iso){
  if (!iso) return '';
  var d = new Date(iso);
  return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
}
function sameDay(a, b){
  return a.getFullYear()===b.getFullYear() && a.getMonth()===b.getMonth() && a.getDate()===b.getDate();
}
function dayLabel(d){
  var now = new Date();
  var yd = new Date(now.getTime() - 86400000);
  if (sameDay(d, now)) return 'Today';
  if (sameDay(d, yd)) return 'Yesterday';
  return d.toLocaleDateString([], {day:'numeric', month:'long', year: d.getFullYear()!==now.getFullYear()?'numeric':undefined});
}
function fmtListTime(iso){
  if (!iso) return '';
  var d = new Date(iso), now = new Date();
  if (sameDay(d, now)) return fmtClock(iso);
  var diff = (now - d) / 86400000;
  if (diff < 6) return d.toLocaleDateString([], {weekday:'short'});
  return d.toLocaleDateString([], {day:'2-digit', month:'2-digit', year: d.getFullYear()!==now.getFullYear()?'2-digit':undefined});
}
function avatarHTML(id, name, hasPhoto, cls, extraBg){
  var bg = extraBg || colorFor(id);
  var core = esc(initials(name));
  if (hasPhoto){
    return '<div class="ava '+(cls||'')+'" style="background:'+bg+'">'+core+
      '<img src="api/avatar/'+Number(id)+'" loading="lazy" alt="" onerror="this.remove()"></div>';
  }
  return '<div class="ava '+(cls||'')+'" style="background:'+bg+'">'+core+'</div>';
}
function toast(msg, kind){
  if (!msg) return;
  var box = $('#toasts');
  var t = document.createElement('div');
  t.className = 'toast' + (kind==='err' ? ' err' : '');
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(function(){ t.style.opacity='0'; t.style.transition='opacity .3s'; }, 2800);
  setTimeout(function(){ t.remove(); }, 3200);
}
var lastPollToast = 0;
function toastErr(e){
  if (e && e.status === 401) return;             // already routed to site login
  if (e && e.status === 409) return;             // already routed to tg login
  var msg = (e && e.detail) ? e.detail : 'Something went wrong';
  var now = Date.now();
  if (now - lastPollToast > 8000){ lastPollToast = now; toast(msg, 'err'); }
}

/* ============================== state ============================== */
var S = {
  me: null,
  tab: 'all',
  dialogs: [],
  dialogById: {},
  dlg: null, current: null,
  msgs: [], msgById: {},
  oldest: 0, newest: 0, hasMore: true, loadingOlder: false,
  readIn: 0, readOut: 0,
  replyTo: null, editing: null, pendingFile: null,
  selecting: false, selection: {},
  typingSent: 0, dlgTimer: null, pollTimer: null,
  archivedOpen: false, viewerOpen: false, csearchOpen: false,
  galleryTab: 'media', galleryOffset: 0, galleryDone: false,
  lastPollOk: true
};

/* ============================== api ============================== */
function api(path, opts){
  opts = opts || {};
  var init = { method: opts.method || 'GET', credentials: 'same-origin', headers: {} };
  if (opts.form){ init.body = opts.form; }
  else if (opts.body !== undefined){
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  return fetch(path, init).then(function(res){
    if (res.status === 401){ showScreen('site'); throw {status:401, detail:'Locked'}; }
    return res.json().catch(function(){ return null; }).then(function(data){
      if (!res.ok){
        var detail = (data && data.detail) ? data.detail : ('HTTP ' + res.status);
        if (res.status === 409 && /not logged in/i.test(String(detail))) showScreen('tg');
        throw {status: res.status, detail: detail};
      }
      return data;
    });
  }).catch(function(e){
    if (e && (e.status !== undefined)) throw e;
    throw {status: 0, detail: 'Network error — check your connection'};
  });
}

/* ============================== screens ============================== */
