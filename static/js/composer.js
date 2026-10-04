/* TgWeb — composer.js: message input: send, reply, edit, upload, selection */
'use strict';
var input = $('#input');
function autoresize(){
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 132) + 'px';
}
function draftKey(id){ return 'tgw_draft_' + id; }
function restoreDraft(id){
  input.value = '';
  try {
    var d = localStorage.getItem(draftKey(id));
    if (d){ input.value = d; }
  } catch (e){}
  autoresize();
}
input.addEventListener('input', function(){
  autoresize();
  if (S.current != null){
    try {
      if (input.value) localStorage.setItem(draftKey(S.current), input.value);
      else localStorage.removeItem(draftKey(S.current));
    } catch (e){}
  }
  var now = Date.now();
  if (S.current != null && input.value && now - S.typingSent > 4000){
    S.typingSent = now;
    api('api/typing', {method: 'POST', body: {chat_id: S.current}}).catch(function(){});
  }
});
input.addEventListener('keydown', function(e){
  if (e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); doSend(); }
});
$('#btnSend').addEventListener('click', doSend);
function setReply(m){
  S.replyTo = {id: m.id};
  S.editing = null;
  $('#editBar').hidden = true;
  $('#replyTitle').textContent = 'Reply to ' + (m.out ? 'yourself' : (m.sender ? m.sender.name : 'message'));
  $('#replySnip').textContent = previewOf(m);
  $('#replyBar').hidden = false;
  input.focus();
}
function clearReply(){ S.replyTo = null; $('#replyBar').hidden = true; }
$('#replyCancel').addEventListener('click', clearReply);
function setEditing(m){
  S.editing = m;
  S.replyTo = null;
  $('#replyBar').hidden = true;
  $('#editSnip').textContent = previewOf(m);
  $('#editBar').hidden = false;
  input.value = m.html || m.raw || '';
  autoresize();
  input.focus();
}
function clearEditing(){ S.editing = null; $('#editBar').hidden = true; }
$('#editCancel').addEventListener('click', function(){ clearEditing(); input.value = ''; autoresize(); });
function doSend(){
  if (S.current == null) return;
  var text = input.value.trim();
  if (S.pendingFile){ doUpload(text); return; }
  if (!text) return;
  if (S.editing){
    var em = S.editing;
    clearEditing();
    input.value = ''; autoresize();
    api('api/edit', {method: 'POST', body: {chat_id: S.current, msg_id: em.id, text: text}})
      .then(function(r){ if (r.message) replaceMessage(r.message); })
      .catch(function(e){ toastErr(e); });
    return;
  }
  var replyTo = S.replyTo ? S.replyTo.id : null;
  input.value = ''; autoresize();
  clearReply();
  try { if (S.current != null) localStorage.removeItem(draftKey(S.current)); } catch (e) {}
  api('api/send', {method: 'POST', body: {chat_id: S.current, text: text, reply_to: replyTo}})
    .then(function(r){
      if (r.message && S.msgById[r.message.id] === undefined){
        S.msgs.push(r.message);
        S.msgById[r.message.id] = r.message;
        if (r.message.id > (S.newest || 0)) S.newest = r.message.id;
        appendNodes([r.message]);
        scrollBottom();
        updateDialogPreview(r.message);
      }
    })
    .catch(function(e){
      toastErr(e);
      input.value = text;
      autoresize();
    });
}
/* file upload */
$('#btnAttach').addEventListener('click', function(){ $('#fileInput').click(); });
$('#fileInput').addEventListener('change', function(){
  var f = this.files && this.files[0];
  if (!f) return;
  S.pendingFile = f;
  $('#attachName').textContent = f.name + ' (' + fmtSize(f.size) + ')';
  $('#attachIcon').textContent = (f.type && f.type.indexOf('image/') === 0) ? '🖼' : '📎';
  $('#attachBar').hidden = false;
  this.value = '';
});
function clearPendingFile(){ S.pendingFile = null; $('#attachBar').hidden = true; }
$('#attachCancel').addEventListener('click', clearPendingFile);
function doUpload(caption){
  var f = S.pendingFile;
  if (!f || S.current == null) return;
  clearPendingFile();
  input.value = ''; autoresize();
  var fd = new FormData();
  fd.append('chat_id', S.current);
  fd.append('caption', caption || '');
  fd.append('file', f, f.name);
  toast('Uploading ' + f.name + '…');
  api('api/upload', {method: 'POST', form: fd}).then(function(r){
    if (r.message && S.msgById[r.message.id] === undefined){
      S.msgs.push(r.message);
      S.msgById[r.message.id] = r.message;
      if (r.message.id > (S.newest || 0)) S.newest = r.message.id;
      appendNodes([r.message]);
      scrollBottom();
      updateDialogPreview(r.message);
    }
  }).catch(toastErr);
}
/* mark read (manual, never automatic) */
$('#btnMarkRead').addEventListener('click', markRead);
function markRead(){
  if (S.current == null) return;
  api('api/read', {method: 'POST', body: {chat_id: S.current, max_id: S.newest || null}}).then(function(){
    if (S.dlg) S.dlg.unread = 0;
    S.readIn = Math.max(S.readIn || 0, S.newest || 0);
    $$('#msgs .unread-chip').forEach(function(c){ c.remove(); });
    renderDialogs();
    updateFab();
    toast('Marked as read');
  }).catch(toastErr);
}

/* ============================== selection ============================== */
function enterSelecting(){
  S.selecting = true;
  S.selection = {};
  $('#selBar').hidden = false;
  $$('#msgs .msg').forEach(function(n){ n.classList.add('selecting'); });
}
function exitSelecting(){
  S.selecting = false;
  S.selection = {};
  $('#selBar').hidden = true;
  $$('#msgs .msg').forEach(function(n){ n.classList.remove('selecting', 'selected'); });
}
function toggleSelect(id){
  var node = $('#msgs .msg[data-id="' + id + '"]');
  /* albums: selecting one tile selects the whole group */
  var ids = [id];
  if (node && node.dataset.gids) ids = node.dataset.gids.split(',').map(Number);
  var target = !S.selection[ids[0]];
  ids.forEach(function(i){
    if (target) S.selection[i] = true;
    else delete S.selection[i];
    var n = $('#msgs .msg[data-id="' + i + '"]');
    if (n) n.classList.toggle('selected', target && i === ids[0]);
  });
  if (node) node.classList.toggle('selected', target);
  $('#selCount').textContent = Object.keys(S.selection).length + ' selected';
}
$('#selCancel').addEventListener('click', exitSelecting);
$('#selForward').addEventListener('click', function(){
  var ids = Object.keys(S.selection).map(Number);
  if (!ids.length) return;
  exitSelecting();
  openForwardPicker(ids);
});
$('#selDelete').addEventListener('click', function(){
  var ids = Object.keys(S.selection).map(Number);
  if (!ids.length) return;
  exitSelecting();
  actDelete(ids);
});

/* ============================== menus ============================== */


/* ============================== emoji picker ============================== */
$('#btnEmoji').addEventListener('click', function(e){
  e.stopPropagation();
  toggleEmojiPicker(this);
});

/* ============================== voice messages ============================== */
var rec = {mr: null, chunks: [], t0: 0, timer: null, dur: 0, chat: null, send: false};
function recTick(){
  rec.dur = Math.floor((Date.now() - rec.t0) / 1000);
  if (rec.dur >= 300){ recStop(true); return; }
  var mm = Math.floor(rec.dur / 60), ss = rec.dur % 60;
  $('#recTime').textContent = mm + ':' + (ss < 10 ? '0' : '') + ss;
}
function recStart(){
  if (S.current == null) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === 'undefined'){
    toast('Voice recording is not supported in this browser');
    return;
  }
  navigator.mediaDevices.getUserMedia({audio: true}).then(function(stream){
    var mime = '';
    ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].some(function(t){
      if (typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported(t)){ mime = t; return true; }
      return false;
    });
    rec.mr = mime ? new MediaRecorder(stream, {mimeType: mime}) : new MediaRecorder(stream);
    rec.chunks = [];
    rec.mr.ondataavailable = function(ev){ if (ev.data && ev.data.size) rec.chunks.push(ev.data); };
    rec.mr.onstop = function(){
      stream.getTracks().forEach(function(t){ t.stop(); });
      if (rec.send){
        var blob = new Blob(rec.chunks, {type: rec.mr.mimeType || 'audio/webm'});
        if (blob.size > 0 && rec.dur > 0) recUpload(blob);
      }
      rec.mr = null; rec.send = false;
    };
    rec.chat = S.current;
    rec.t0 = Date.now(); rec.dur = 0;
    $('#recTime').textContent = '0:00';
    $('#recBar').hidden = false;
    input.blur();
    rec.mr.start(250);
    rec.timer = setInterval(recTick, 500);
  }).catch(function(){
    toast('Microphone permission denied');
  });
}
function recStop(send){
  if (!rec.mr) return;
  if (send) rec.send = true;
  clearInterval(rec.timer);
  $('#recBar').hidden = true;
  try { rec.mr.stop(); } catch (e) {}
}
function recUpload(blob){
  var ext = (blob.type.indexOf('mp4') >= 0) ? 'm4a' : (blob.type.indexOf('ogg') >= 0 ? 'ogg' : 'webm');
  var fd = new FormData();
  fd.append('chat_id', rec.chat != null ? rec.chat : S.current);
  fd.append('voice', '1');
  fd.append('duration', String(Math.max(1, rec.dur)));
  fd.append('file', blob, 'voice.' + ext);
  toast('Sending voice message…');
  api('api/upload', {method: 'POST', form: fd}).then(function(r){
    if (r.message && S.msgById[r.message.id] === undefined){
      S.msgs.push(r.message);
      S.msgById[r.message.id] = r.message;
      if (r.message.id > (S.newest || 0)) S.newest = r.message.id;
      appendNodes([r.message]);
      scrollBottom();
      updateDialogPreview(r.message);
    }
  }).catch(toastErr);
}
$('#btnMic').addEventListener('click', function(){
  if (rec.mr) return;
  if (!$('#recBar').hidden){ return; }
  recStart();
});
$('#recCancel').addEventListener('click', function(){ recStop(false); });
$('#recSend').addEventListener('click', function(){ recStop(true); });
