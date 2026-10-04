/* TgWeb — viewer.js: fullscreen media viewer */
'use strict';
function openViewer(msgId, kind){
  var m = S.msgById[msgId];
  if (!m || S.current == null) return;
  var u = 'api/media/' + S.current + '/' + m.id;
  var name = (m.media && (m.media.name || m.media.title)) || ('media_' + m.id);
  var v = $('#viewer');
  var mediaHtml;
  if (kind === 'photo'){
    mediaHtml = '<img src="' + u + '?kind=photo" alt="">';
  } else {
    var attrs = kind === 'gif' ? ' autoplay muted loop playsinline' : ' autoplay controls playsinline';
    mediaHtml = '<video src="' + u + '?kind=file"' + attrs + '></video>';
  }
  v.innerHTML = '<div class="vtop">' +
    '<button class="icon-btn" id="vClose" title="Close"><svg class="ic"><use href="#i-close"/></svg></button>' +
    '<b>' + esc(name) + '</b>' +
    '<a class="icon-btn" href="' + u + '?kind=file&amp;dl=1" download="' + esc(name) + '" title="Download"><svg class="ic"><use href="#i-dl"/></svg></a></div>' +
    '<div class="vbody">' + mediaHtml + '</div>';
  v.hidden = false;
  S.viewerOpen = true;
  $('#vClose').onclick = closeViewer;
  v.onclick = function(e){ if (e.target === v || e.target.classList.contains('vbody')) closeViewer(); };
}
function closeViewer(){
  var v = $('#viewer');
  v.innerHTML = '';
  v.hidden = true;
  S.viewerOpen = false;
}
/* ---------- keyboard shortcuts ---------- */
