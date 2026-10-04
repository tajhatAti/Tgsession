/* TgWeb — stories.js: stories bar, story viewer, story posting */
'use strict';

var SBar = {
  peers: [],
  me: null,
  viewer: null,   /* {peer, idx, timer} */
};

/* ============================== stories bar ============================== */
function loadStoriesBar(){
  api('api/stories').then(function(r){
    SBar.peers = r.peers || [];
    SBar.me = r.me || null;
    renderStoriesBar();
  }).catch(function(){});
}

function _ringHTML(id, name, hasPhoto, unseen, extra){
  var cls = unseen > 0 ? 'sring unseen' : 'sring';
  return '<button type="button" class="' + cls + '" data-story="' + id + '">' +
    '<span class="sring2">' + avatarHTML(id, name, hasPhoto, '', colorFor(id)) + '</span>' +
    (extra || '') + '</button>';
}

function renderStoriesBar(){
  var bar = $('#storiesBar');
  if (!bar) return;
  var html = '';
  /* own story slot: plus if none, avatar if has */
  if (SBar.me && SBar.me.stories && SBar.me.stories.length){
    html += '<div class="sown">' +
      _ringHTML(SBar.me.chat_id, SBar.me.name || 'My story', SBar.me.has_photo, 0,
        '<span class="sownbadge">My story</span>') + '</div>';
  } else {
    html += '<button type="button" class="sring add" id="storyAdd" title="Post a story">' +
      '<span class="sring2">' + avatarHTML(S.me ? S.me.id : 0, S.me ? (S.me.name || 'Me') : 'Me', false, '', colorFor(S.me ? S.me.id : 1)) +
      '<span class="splus">＋</span></span>' +
      '<span class="sownbadge">My story</span></button>';
  }
  SBar.peers.forEach(function(p){
    html += _ringHTML(p.chat_id, p.name, p.has_photo, p.unseen,
      '<span class="sname">' + esc((p.name || '').split(' ')[0]) + '</span>');
  });
  bar.innerHTML = html;
  bar.hidden = !(html);
  var add = $('#storyAdd');
  if (add) add.addEventListener('click', postStoryPick);
}

$('#storiesBar').addEventListener('click', function(e){
  var b = e.target.closest('[data-story]');
  if (!b) return;
  var id = Number(b.dataset.story);
  if (SBar.me && SBar.me.chat_id === id){
    openStoryViewer(SBar.me, 0, true);
  } else {
    var p = SBar.peers.filter(function(x){ return x.chat_id === id; })[0];
    if (p) openStoryViewer(p, 0, false);
  }
});

/* ============================== story viewer ============================== */
function _storyNodeHTML(st, p, idx, total){
  var media = st.media || {};
  var isVideo = (media.kind === 'video' || (media.mime || '').startsWith('video/'));
  var inner = isVideo
    ? '<video class="sv-media" src="' + st.media_url + '" autoplay playsinline controls></video>'
    : '<img class="sv-media" src="' + st.media_url + '" alt="">';
  var cap = st.caption ? '<div class="sv-cap">' + esc(st.caption) + '</div>' : '';
  var views = (st.views != null && st.views !== '')
    ? '<div class="sv-views">👁 ' + Number(st.views).toLocaleString() + ' views</div>' : '';
  return '<div class="sv-page" data-i="' + idx + '">' + inner + cap + views + '</div>';
}

function openStoryViewer(p, idx, isSelf){
  closeStoryViewer();
  var wrap = document.createElement('div');
  wrap.id = 'storyViewer';
  var total = p.stories.length;
  wrap.innerHTML =
    '<div class="sv-top">' +
      '<div class="sv-bars">' + p.stories.map(function(s, i){
        return '<span class="sv-bar' + (i < idx ? ' done' : '') + (i === idx ? ' cur' : '') + '"></span>';
      }).join('') + '</div>' +
      '<div class="sv-head">' + avatarHTML(p.chat_id, p.name, p.has_photo, '', colorFor(p.chat_id)) +
        '<b>' + esc(p.name || '') + '</b>' +
        '<span class="sv-time">' + esc(fmtListTime(storyDateOf(p.stories[idx]))) + '</span>' +
      '</div>' +
      '<div class="sv-actions">' +
        (isSelf ? '<button type="button" class="icon-btn sv-del" title="Delete story">🗑</button>' : '') +
        '<button type="button" class="icon-btn sv-close" title="Close">✕</button>' +
      '</div>' +
    '</div>' +
    '<div class="sv-body">' +
      '<div class="sv-tap prev" title="Previous"></div>' +
      '<div class="sv-holder">' + _storyNodeHTML(p.stories[idx], p, idx, total) + '</div>' +
      '<div class="sv-tap next" title="Next"></div>' +
    '</div>';
  document.body.appendChild(wrap);
  document.body.classList.add('story-open');
  SBar.viewer = { peer: p, idx: idx, isSelf: isSelf, timer: null, expire: null };
  storyMarkSeen(p, idx);
  storyStartTimer(p, idx);

  wrap.querySelector('.sv-close').addEventListener('click', closeStoryViewer);
  var del = wrap.querySelector('.sv-del');
  if (del) del.addEventListener('click', function(){ storyDelete(p, idx); });
  wrap.querySelector('.sv-tap.prev').addEventListener('click', function(){ storyGo(p, idx - 1, isSelf); });
  wrap.querySelector('.sv-tap.next').addEventListener('click', function(){ storyGo(p, idx + 1, isSelf); });
  document.addEventListener('keydown', storyKeydown);
}

function storyDateOf(st){
  return st && st.date ? new Date(st.date * 1000).toISOString() : null;
}

function storyKeydown(e){
  if (!SBar.viewer) return;
  if (e.key === 'Escape'){ closeStoryViewer(); }
  else if (e.key === 'ArrowLeft'){ storyGo(SBar.viewer.peer, SBar.viewer.idx - 1, SBar.viewer.isSelf); }
  else if (e.key === 'ArrowRight' || e.key === ' '){ e.preventDefault(); storyGo(SBar.viewer.peer, SBar.viewer.idx + 1, SBar.viewer.isSelf); }
}

function storyStartTimer(p, idx){
  clearTimeout(SBar.viewer && SBar.viewer.timer);
  if (!SBar.viewer) return;
  var st = p.stories[idx];
  var isVideo = st && st.media && ((st.media.kind === 'video') || (st.media.mime || '').startsWith('video/'));
  if (isVideo) return;   /* video paces itself */
  SBar.viewer.expire = setTimeout(function(){
    if (SBar.viewer) storyGo(SBar.viewer.peer, SBar.viewer.idx + 1, SBar.viewer.isSelf);
  }, 5000);
}

function storyGo(p, idx, isSelf){
  if (!SBar.viewer) return;
  if (idx < 0){ closeStoryViewer(); return; }
  if (idx >= p.stories.length){
    /* next peer that has stories */
    var all = SBar.peers.filter(function(x){ return x.stories && x.stories.length; });
    var i = all.indexOf(p);
    var next = all[i + 1];
    if (next) openStoryViewer(next, 0, false);
    else closeStoryViewer();
    return;
  }
  var wrap = $('#storyViewer');
  if (!wrap) return;
  SBar.viewer.idx = idx;
  wrap.querySelector('.sv-holder').innerHTML = _storyNodeHTML(p.stories[idx], p, idx, p.stories.length);
  wrap.querySelectorAll('.sv-bar').forEach(function(b, i){
    b.classList.toggle('done', i < idx);
    b.classList.toggle('cur', i === idx);
  });
  wrap.querySelector('.sv-time').textContent = fmtListTime(storyDateOf(p.stories[idx]));
  var vid = wrap.querySelector('.sv-media');
  if (vid && vid.tagName === 'VIDEO'){
    vid.addEventListener('ended', function(){ storyGo(p, idx + 1, isSelf); });
  }
  storyMarkSeen(p, idx);
  storyStartTimer(p, idx);
}

function storyMarkSeen(p, idx){
  if (!p || p.chat_id === (S.me && S.me.id)) return;
  var st = p.stories[idx];
  if (!st) return;
  if (st.id <= (p.max_read_id || 0)) return;
  p.max_read_id = st.id;
  p.unseen = Math.max(0, (p.unseen || 0) - 1);
  api('api/stories/read', {method: 'POST', body: {chat_id: p.chat_id, max_id: st.id}}).catch(function(){});
}

function closeStoryViewer(){
  clearTimeout(SBar.viewer && SBar.viewer.expire);
  SBar.viewer = null;
  var w = $('#storyViewer');
  if (w) w.remove();
  document.body.classList.remove('story-open');
  document.removeEventListener('keydown', storyKeydown);
  loadStoriesBar();
}

function storyDelete(p, idx){
  var st = p.stories[idx];
  if (!st) return;
  if (!confirm('Delete this story?')) return;
  api('api/stories/delete', {method: 'POST', body: {chat_id: p.chat_id, story_id: st.id}})
    .then(function(){
      toast('Story deleted');
      p.stories.splice(idx, 1);
      if (p.stories.length) openStoryViewer(p, Math.min(idx, p.stories.length - 1), true);
      else closeStoryViewer();
    }).catch(toastErr);
}

/* ============================== posting ============================== */
var storyFileInput = null;

function postStoryPick(){
  if (!storyFileInput){
    storyFileInput = document.createElement('input');
    storyFileInput.type = 'file';
    storyFileInput.accept = 'image/*,video/*';
    storyFileInput.hidden = true;
    document.body.appendChild(storyFileInput);
    storyFileInput.addEventListener('change', function(){
      var f = this.files && this.files[0];
      this.value = '';
      if (f) postStoryModal(f);
    });
  }
  storyFileInput.click();
}

function postStoryModal(file){
  var url = URL.createObjectURL(file);
  var isImg = file.type.indexOf('image/') === 0;
  openModal('<div class="mhead"><b>New story</b>' +
    '<button class="icon-btn" id="psClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div class="ps-body">' +
      '<div class="ps-preview">' + (isImg
        ? '<img src="' + url + '" alt="">'
        : '<video src="' + url + '" controls muted></video>') + '</div>' +
      '<input id="psCaption" placeholder="Caption (optional)" maxlength="200">' +
      '<button class="btn primary" id="psPost">Share to my story</button>' +
    '</div>', true);
  $('#psClose').onclick = function(){ URL.revokeObjectURL(url); closeModal(); };
  $('#psPost').onclick = function(){
    var btn = this;
    busy(btn, true, 'Posting…');
    var fd = new FormData();
    fd.append('file', file, file.name || 'story');
    fd.append('caption', $('#psCaption').value || '');
    api('api/stories/upload', {method: 'POST', form: fd}).then(function(){
      URL.revokeObjectURL(url);
      closeModal();
      toast('Story posted');
      loadStoriesBar();
    }).catch(function(e){
      busy(btn, false);
      toastErr(e);
    });
  };
}
