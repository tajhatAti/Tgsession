/* TgWeb — composer.js: message input: send, reply, edit, upload, selection */
'use strict';
var input = $('#input');
function autoresize(){
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 132) + 'px';
}
input.addEventListener('input', function(){
  autoresize();
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
  if (S.selection[id]) delete S.selection[id];
  else S.selection[id] = true;
  var node = $('#msgs .msg[data-id="' + id + '"]');
  if (node) node.classList.toggle('selected', !!S.selection[id]);
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
