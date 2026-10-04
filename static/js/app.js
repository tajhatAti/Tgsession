/* TgWeb — app.js: keyboard shortcuts + boot */
'use strict';
document.addEventListener('keydown', function(e){
  if (e.key === 'Escape'){
    if (S.viewerOpen){ closeViewer(); return; }
    if (!$('#modal').hidden){ closeModal(); return; }
    if ($$('.menu').length){ closeMenus(); return; }
    if (S.selecting){ exitSelecting(); return; }
    if (S.csearchOpen){ closeCSearch(); return; }
    if (S.current != null){ closeChat(); return; }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')){
    e.preventDefault();
    document.body.classList.remove('chat-open');
    var q = $('#dlgSearch');
    q.focus();
    q.select();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.key === 'F')){
    if (S.current != null){ e.preventDefault(); openCSearch(); }
  }
});
/* go */
boot();
