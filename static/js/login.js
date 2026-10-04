/* TgWeb — login.js: SITE + TELEGRAM LOGIN (session string only) */
'use strict';

/* ============================== screens ============================== */
function showScreen(name){
  $('#scr-site').hidden = name !== 'site';
  $('#scr-tg').hidden = name !== 'tg';
  $('#scr-app').hidden = name !== 'app';
  if (name === 'site') setTimeout(function(){ $('#sitePass').focus(); }, 50);
  if (name === 'tg') setTimeout(function(){ $('#tgSession').focus(); }, 50);
}
function showTgLogin(errText){
  showScreen('tg');
  var box = $('#tgErr');
  if (errText){ box.textContent = errText; box.hidden = false; } else { box.hidden = true; }
}
function tgErr(msg){
  var box = $('#tgErr');
  if (msg){ box.textContent = msg; box.hidden = false; } else { box.hidden = true; }
}
function busy(btn, on, label){
  if (on){ btn.dataset.old = btn.textContent; btn.textContent = label || 'Please wait…'; btn.disabled = true; }
  else { btn.textContent = btn.dataset.old || btn.textContent; btn.disabled = false; }
}

/* ============================== boot ============================== */
function boot(){
  return api('api/status').then(function(r){
    if (r.tg) enterApp(r.me);
    else showTgLogin(r.error);
  }).catch(function(){ /* 401 already showed site screen */ });
}
function enterApp(me){
  S.me = me || S.me;
  showScreen('app');
  loadDialogs(false);
  if (S.dlgTimer) clearInterval(S.dlgTimer);
  S.dlgTimer = setInterval(function(){
    if (!document.hidden) loadDialogs(true);
  }, 30000);
  document.addEventListener('visibilitychange', function(){
    if (!document.hidden){
      if (S.current != null) pollMessages();
      loadDialogs(true);
    }
  });
}

/* ============================== site password ============================== */
$('#siteForm').addEventListener('submit', function(e){
  e.preventDefault();
  var btn = $('#siteBtn');
  busy(btn, true, 'Checking…');
  api('api/login', {method:'POST', body:{password: $('#sitePass').value}})
    .then(function(){ $('#sitePass').value=''; $('#siteErr').hidden = true; boot(); })
    .catch(function(err){
      $('#siteErr').textContent = err.detail || 'Login failed';
      $('#siteErr').hidden = false;
    })
    .finally(function(){ busy(btn, false); });
});

/* ============================== telegram login: phone (default) or session string ============================== */
$('#tabPhone').addEventListener('click', function(){
  $('#tabPhone').classList.add('on'); $('#tabSession').classList.remove('on');
  $('#panePhone').hidden = false; $('#paneSession').hidden = true; tgErr(null);
  setTimeout(function(){ $('#tgPhone').focus(); }, 30);
});
$('#tabSession').addEventListener('click', function(){
  $('#tabSession').classList.add('on'); $('#tabPhone').classList.remove('on');
  $('#paneSession').hidden = false; $('#panePhone').hidden = true; tgErr(null);
  setTimeout(function(){ $('#tgSession').focus(); }, 30);
});
$('#tgSendCode').addEventListener('click', function(){
  var phone = $('#tgPhone').value.trim();
  if (!phone){ tgErr('Enter your phone number with country code.'); return; }
  var btn = this; busy(btn, true, 'Sending…');
  api('api/tg/login_phone', {method:'POST', body:{phone: phone}})
    .then(function(){
      tgErr(null);
      $('#tgCodeRow').hidden = false;
      $('#tgCode').focus();
    })
    .catch(function(e){ tgErr(e.detail); })
    .finally(function(){ busy(btn, false); });
});
$('#tgVerifyCode').addEventListener('click', function(){
  var code = $('#tgCode').value.trim();
  if (!code){ tgErr('Enter the login code.'); return; }
  var btn = this; busy(btn, true, 'Checking…');
  api('api/tg/login_code', {method:'POST', body:{code: code}})
    .then(function(r){
      if (r.need_password){
        tgErr(null);
        $('#tg2faRow').hidden = false;
        $('#tg2fa').focus();
      } else { enterApp(r.me); }
    })
    .catch(function(e){ tgErr(e.detail); })
    .finally(function(){ busy(btn, false); });
});
$('#tgVerifyPass').addEventListener('click', function(){
  var btn = this; busy(btn, true, 'Checking…');
  api('api/tg/login_password', {method:'POST', body:{password: $('#tg2fa').value}})
    .then(function(r){ enterApp(r.me); })
    .catch(function(e){ tgErr(e.detail); })
    .finally(function(){ busy(btn, false); });
});
$('#tgPhone').addEventListener('keydown', function(e){
  if (e.key === 'Enter'){ e.preventDefault(); $('#tgSendCode').click(); }
});
$('#tgCode').addEventListener('keydown', function(e){
  if (e.key === 'Enter'){ e.preventDefault(); $('#tgVerifyCode').click(); }
});
$('#tg2fa').addEventListener('keydown', function(e){
  if (e.key === 'Enter'){ e.preventDefault(); $('#tgVerifyPass').click(); }
});

function doImportSession(){
  var s = $('#tgSession').value.trim();
  if (!s){ tgErr('Paste the session string first.'); return; }
  var btn = $('#tgImport');
  busy(btn, true, 'Validating…');
  api('api/tg/import_session', {method:'POST', body:{session: s}})
    .then(function(r){ $('#tgSession').value=''; enterApp(r.me); })
    .catch(function(e){ tgErr(e.detail); })
    .finally(function(){ busy(btn, false); });
}
$('#tgImport').addEventListener('click', doImportSession);
$('#tgSession').addEventListener('keydown', function(e){
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)){ e.preventDefault(); doImportSession(); }
});
