/* TgWeb — profile.js: user/chat/channel profile view + own settings (edit profile) */
'use strict';

/* ---------- open a chat's profile (modal) ---------- */
function openProfile(chatId){
  if (chatId == null) return;
  api('api/profile?chat_id=' + chatId).then(function(p){
    renderProfile(p);
  }).catch(toastErr);
}

function renderProfile(p){
  var rows = '';
  function row(label, value, copy){
    if (!value) return '';
    return '<div class="prow"><div class="plabel">' + esc(label) + '</div>' +
      '<div class="pval" ' + (copy ? 'data-copyv="' + esc(value) + '"' : '') + '>' +
      esc(value) + (copy ? ' <button class="icon-btn mini" data-copy="' + esc(value) +
      '" title="Copy"><svg class="ic"><use href="#i-copy"/></svg></button>' : '') +
      '</div></div>';
  }
  rows += row(p.is_self ? 'Phone' : (p.type === 'user' || p.type === 'bot' ? 'Phone' : ''), p.phone, true);
  rows += row('Username', p.username ? '@' + p.username : '', true);
  rows += row('Bio', p.bio);
  if (p.type === 'user' || p.type === 'bot'){
    if (p.common != null) rows += row('Groups in common', String(p.common));
  } else {
    if (p.members != null){
      var m = Number(p.members).toLocaleString();
      rows += row(p.type === 'channel' ? 'Subscribers' : 'Members', m + (p.online ? ' · ' + Number(p.online).toLocaleString() + ' online' : ''));
    }
    if (p.link) rows += row('Link', p.link, true);
  }
  var statusLine = '';
  if (p.type === 'user' && p.status) statusLine = '<div class="pstatus">' + esc(p.status) + '</div>';
  else if (p.type === 'bot') statusLine = '<div class="pstatus">bot</div>';
  else if (p.members != null) statusLine = '<div class="pstatus">' + Number(p.members).toLocaleString() + (p.type === 'channel' ? ' subscribers' : ' members') + '</div>';

  var acts = '';
  if (!p.is_self){
    var d = S.dialogById[p.id] || {};
    acts += '<button class="btn" id="pActMute">' + (d.muted ? '🔔 Unmute' : '🔕 Mute') + '</button>';
    acts += '<button class="btn" id="pActMsg">💬 Open chat</button>';
  }
  if (p.type === 'group' || p.type === 'channel') acts += '<button class="btn" id="pActMembers">👥 Members</button>';

  openModal(
    '<div class="mhead"><b>' + esc(p.is_self ? 'My profile' : 'Chat info') + '</b>' +
      '<button class="icon-btn" id="pClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div class="profile-head">' +
      '<div class="pava">' + avatarHTML(p.id, p.name, p.has_photo, '', colorFor(p.id)) + '</div>' +
      '<div class="pname">' + (p.verified ? '✔ ' : '') + esc(p.name || '') + (p.premium ? ' ⭐' : '') + '</div>' +
      statusLine +
    '</div>' +
    (rows ? '<div class="prows">' + rows + '</div>' : '') +
    (acts ? '<div class="pacts">' + acts + '</div>' : ''), false);

  $('#pClose').onclick = closeModal;
  var box = $('#modalBox');
  box.addEventListener('click', function(e){
    var cp = e.target.closest('[data-copy]');
    if (cp){
      try { navigator.clipboard.writeText(cp.getAttribute('data-copy')); toast('Copied'); } catch (err) {}
    }
  });
  var msgBtn = $('#pActMsg');
  if (msgBtn) msgBtn.onclick = function(){ closeModal(); openChat(p.id); };
  var muteBtn = $('#pActMute');
  if (muteBtn) muteBtn.onclick = function(){
    var d = S.dialogById[p.id] || {};
    api('api/mute', {method: 'POST', body: {chat_id: p.id, muted: !d.muted}})
      .then(function(){ loadDialogs(true); closeModal(); openProfile(p.id); })
      .catch(toastErr);
  };
  var memBtn = $('#pActMembers');
  if (memBtn) memBtn.onclick = function(){ closeModal(); openMembers(); };
}

/* avatar click inside a chat -> that sender's profile */
document.addEventListener('click', function(e){
  var av = e.target.closest('.msg .ava[data-uid]');
  if (av) openProfile(Number(av.getAttribute('data-uid')));
  var snd = e.target.closest('.msg .sender[data-uid]');
  if (snd) openProfile(Number(snd.getAttribute('data-uid')));
});

/* ---------- own settings ---------- */
function openSettings(){
  Promise.all([api('api/profile?chat_id=' + S.me.id), api('api/status')]).then(function(r){
    renderSettings(r[0], r[1]);
  }).catch(toastErr);
}

function renderSettings(p, st){
  var me = (st && st.me) || S.me || {};
  var dark = document.documentElement.getAttribute('data-theme') !== 'light';
  var rows = '';
  rows += '<div class="prow"><div class="plabel">Phone</div><div class="pval">' + esc(p.phone || '') + '</div></div>';
  rows += '<div class="prow"><div class="plabel">Username</div><div class="pval">' + esc(p.username ? '@' + p.username : '') + '</div></div>';
  rows += '<div class="prow"><div class="plabel">Bio</div><div class="pval">' + esc(p.bio || '') + '</div></div>';
  openModal(
    '<div class="mhead"><b>Settings</b>' +
      '<button class="icon-btn" id="stClose"><svg class="ic"><use href="#i-close"/></svg></button></div>' +
    '<div class="profile-head"><div class="pava pava-edit" id="stAva" title="Change photo">' + avatarHTML(me.id, me.name, p.has_photo) + '<span class="pava-plus">📷</span></div>' +
      '<div class="pname">' + esc(me.name || '') + '</div>' +
      '<div class="pstatus">' + esc(p.status || '') + '</div></div>' +
    '<div class="stform">' +
      '<label>First name<input id="stFirst" maxlength="64" value="' + esc(firstNameOf(me.name)) + '"></label>' +
      '<label>Last name<input id="stLast" maxlength="64" value="' + esc(lastNameOf(me.name)) + '"></label>' +
      '<label>Bio<textarea id="stAbout" rows="2" maxlength="70">' + esc(p.bio || '') + '</textarea></label>' +
      '<button class="btn primary" id="stSave">Save profile</button>' +
    '</div>' +
    '<div class="prows">' + rows + '</div>' +
    '<div class="stform"><label>Chat wallpaper<div class="wpicks" id="stWp">' +
      [['doodles','Pattern'],['plain','Plain'],['blue','Blue'],['peach','Peach'],['mint','Mint']].map(function(w){
        return '<button type="button" class="wpick' + (wpNow() === w[0] ? ' on' : '') + '" data-wp="' + w[0] + '">' + w[1] + '</button>';
      }).join('') + '</div></label></div>' +
    '<div class="pacts">' +
      '<button class="btn" id="stTheme">' + (dark ? '☀️ Light theme' : '🌙 Dark theme') + '</button>' +
      '<button class="btn" id="stSound">' + (soundOn() ? '🔔 Sounds: on' : '🔕 Sounds: off') + '</button>' +
      '<button class="btn" id="stLock">🔒 Lock site</button>' +
      '<button class="btn danger" id="stLogout">Log out Telegram</button>' +
    '</div>', false);
  $('#stClose').onclick = closeModal;
  $('#stTheme').onclick = toggleTheme;
  $('#stSound').onclick = function(){
    try {
      var cur = localStorage.getItem('tgw_sound') !== '0';
      localStorage.setItem('tgw_sound', cur ? '0' : '1');
      if (!cur) playSound('send');
      this.textContent = cur ? '🔕 Sounds: off' : '🔔 Sounds: on';
    } catch (e) {}
  };
  $('#stAva').onclick = stPickPhoto;
  var wpBox = $('#stWp');
  if (wpBox) wpBox.onclick = function(e){
    var b = e.target.closest('[data-wp]');
    if (!b) return;
    try { localStorage.setItem('tgw_wp', b.dataset.wp); } catch (err) {}
    applyWallpaper();
    $$('.wpick', wpBox).forEach(function(x){ x.classList.toggle('on', x === b); });
  };
  $('#stLock').onclick = lockSite;
  $('#stLogout').onclick = doLogout;
  $('#stSave').onclick = function(){
    var b = this;
    busy(b, true, 'Saving…');
    api('api/tg/me/edit', {method: 'POST', body: {
      first_name: $('#stFirst').value.trim(),
      last_name: $('#stLast').value.trim(),
      about: $('#stAbout').value
    }}).then(function(r){
      busy(b, false);
      if (r.me) S.me = r.me;
      toast('Profile saved');
      closeModal();
    }).catch(function(e){ busy(b, false); toastErr(e); });
  };
}

function firstNameOf(name){
  var parts = String(name || '').trim().split(/\s+/);
  return parts[0] || '';
}
function lastNameOf(name){
  var parts = String(name || '').trim().split(/\s+/);
  parts.shift();
  return parts.join(' ');
}


/* ============================== change own photo ============================== */
var stPhotoInput = null;
function stPickPhoto(){
  if (!stPhotoInput){
    stPhotoInput = document.createElement('input');
    stPhotoInput.type = 'file';
    stPhotoInput.accept = 'image/*';
    stPhotoInput.hidden = true;
    document.body.appendChild(stPhotoInput);
    stPhotoInput.addEventListener('change', function(){
      var f = this.files && this.files[0];
      this.value = '';
      if (!f) return;
      var fd = new FormData();
      fd.append('file', f, f.name || 'photo.png');
      toast('Uploading photo…');
      api('api/tg/me/photo', {method: 'POST', form: fd}).then(function(){
        toast('Photo updated');
        openSettings();
      }).catch(toastErr);
    });
  }
  stPhotoInput.click();
}
function soundOn(){
  try { return localStorage.getItem('tgw_sound') !== '0'; } catch (e) { return true; }
}

function wpNow(){
  try { return localStorage.getItem('tgw_wp') || 'doodles'; } catch (e) { return 'doodles'; }
}
function applyWallpaper(){
  document.body.setAttribute('data-wp', wpNow());
}
applyWallpaper();
