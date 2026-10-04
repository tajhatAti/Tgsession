/* TgWeb — emoji.js: emoji picker for the message composer (no CDN, static data) */
'use strict';

/* ---------- emoji data (curated common sets per category) ---------- */
var EMOJI_CATS = [
  {id: 'recent', name: '⭐', list: []},
  {id: 'smileys', name: '😀', list: [
    '😀','😃','😄','😁','😆','😅','🤣','😂','🙂','🙃','😉','😊','😇','🥰','😍','🤩',
    '😘','😗','😚','😙','🥲','😋','😛','😜','🤪','😝','🤑','🤗','🤭','🤫','🤔','🤐',
    '🤨','😐','😑','😶','😏','😒','🙄','😬','😮‍💨','🤥','😌','😔','😪','🤤','😴','😷',
    '🤒','🤕','🤢','🤮','🤧','🥵','🥶','🥴','😵','🤯','🤠','🥳','🥸','😎','🤓','🧐',
    '😕','😟','🙁','😮','😯','😲','😳','🥺','😦','😧','😨','😰','😥','😢','😭','😱',
    '😖','😣','😞','😓','😩','😫','🥱','😤','😡','😠','🤬','😈','👿','💀','☠️','💩',
    '🤡','👹','👺','👻','👽','🤖','😺','😸','😹','😻','😼','😽','🙀','😿','😾','🙈']},
  {id: 'gestures', name: '👋', list: [
    '👋','🤚','🖐️','✋','🖖','👌','🤌','🤏','✌️','🤞','🤟','🤘','🤙','👈','👉','👆',
    '🖕','👇','☝️','👍','👎','✊','👊','🤛','🤜','👏','🙌','👐','🤲','🤝','🙏','✍️',
    '💅','🤳','💪','🦾','🦿','🦵','🦶','👂','🦻','👃','🧠','🫀','🫁','🦷','🦴','👀',
    '👁️','👅','👄','🫢','🫣','🫡','🫥','🫤']},
  {id: 'animals', name: '🐶', list: [
    '🐶','🐱','🐭','🐹','🐰','🦊','🐻','🐼','🐻‍❄️','🐨','🐯','🦁','🐮','🐷','🐸','🐵',
    '🙈','🙉','🙊','🐒','🦍','🦧','🐔','🐧','🐦','🐤','🦆','🦅','🦉','🦇','🐺','🐗',
    '🐴','🦄','🐝','🪱','🐛','🦋','🐌','🐞','🐜','🪰','🪲','🪳','🦟','🦗','🕷️','🦂',
    '🐢','🐍','🦎','🦖','🦕','🐙','🦑','🦐','🦞','🦀','🐡','🐠','🐟','🐬','🐳','🐋',
    '🦈','🐊','🐅','🐆','🦓','🦍','🦧','🐘','🦛','🦏','🐪','🐫','🦒','🦘','🐄','🐎',
    '🐖','🐏','🐑','🦙','🐐','🦌','🐕','🐩','🦮','🐈','🐈‍⬛','🐓','🦃','🦤','🦚','🦜']},
  {id: 'food', name: '🍔', list: [
    '🍏','🍎','🍐','🍊','🍋','🍌','🍉','🍇','🍓','🫐','🍈','🍒','🍑','🥭','🍍','🥥',
    '🥝','🍅','🍆','🥑','🥦','🥬','🥒','🌶️','🫑','🌽','🥕','🫒','🧄','🧅','🥔',
    '🍠','🥐','🥯','🍞','🥖','🥨','🧀','🥚','🍳','🧈','🥞','🧇','🥓','🥩','🍗','🍖',
    '🌭','🍔','🍟','🍕','🫓','🥪','🥙','🧆','🌮','🌯','🫔','🥗','🥘','🫕','🥫','🍝',
    '🍜','🍲','🍛','🍣','🍱','🥟','🦪','🍤','🍙','🍚','🍘','🍥','🥠','🥮','🍢','🍡',
    '🍧','🍨','🍦','🥧','🧁','🍰','🎂','🍮','🍭','🍬','🍫','🍿','🍩','🍪','🌰','🥜',
    '🍯','🥛','☕','🫖','🍵','🧃','🥤','🧋','🍶','🍺','🍻','🥂','🍷','🥃','🍸','🍹']},
  {id: 'activity', name: '⚽', list: [
    '⚽','🏀','🏈','⚾','🥎','🎾','🏐','🏉','🥏','🎱','🪀','🏓','🏸','🏒','🏑','🥍',
    '🏏','🪃','🥊','🥋','🎽','🛹','🛼','🛷','⛸️','🥌','🎿','⛷️','🏂','🪂','🏋️','🤼',
    '🤸','⛹️','🤺','🤾','🏌️','🏇','🧘','🏄','🏊','🤽','🚣','🧗','🚵','🚴','🏆','🥇',
    '🥈','🥉','🏅','🎖️','🏵️','🎗️','🎫','🎟️','🎪','🤹','🎭','🩰','🎨','🎬','🎤','🎧',
    '🎼','🎹','🥁','🪘','🎷','🎺','🎸','🪕','🎻','🎲','♟️','🎯','🎳','🎮','🎰','🧩']},
  {id: 'travel', name: '🚗', list: [
    '🚗','🚕','🚙','🚌','🚎','🏎️','🚓','🚑','🚒','🚐','🛻','🚚','🚛','🚜','🦯','🦽',
    '🦼','🛴','🚲','🛵','🏍️','🛺','🚨','🚔','🚍','🚘','🚖','🚡','🚠','🚟','🚃','🚋',
    '🚞','🚝','🚄','🚅','🚈','🚂','🚆','🚇','🚊','🚉','✈️','🛫','🛬','🛩️','💺','🛰️',
    '🚀','🛸','🚁','🛶','⛵','🚤','🛥️','🛳️','⛴️','🚢','⚓','🪝','⛽','🚧','🚦','🚥',
    '🗺️','🗿','🗽','🗼','🏰','🏯','🏟️','🎡','🎢','🎠','⛲','⛱️','🏖️','🏝️','🏜️','🌋',
    '⛰️','🏔️','🗻','🏕️','⛺','🏠','🏡','🏘️','🏚️','🏗️','🏭','🏢','🏬','🏣','🏤','🏥']},
  {id: 'objects', name: '⌚', list: [
    '⌚','📱','📲','💻','⌨️','🖥️','🖨️','🖱️','🖲️','🕹️','🗜️','💽','💾','💿','📀','📼',
    '📷','📸','📹','🎥','📽️','🎞️','📞','☎️','📟','📠','📺','📻','🎙️','🎚️','🎛️',
    '🧭','⏱️','⏲️','⏰','🕰️','⌛','⏳','📡','🔋','🪫','🔌','💡','🔦','🕯️','🪔','🧯',
    '🛢️','💸','💵','💴','💶','💷','🪙','💰','💳','💎','⚖️','🪜','🧰','🔧','🔨','⚒️',
    '🛠️','⛏️','🪛','🔩','⚙️','🪤','🧱','⛓️','🧲','🔫','💣','🧨','🪓','🔪','🗡️','⚔️',
    '🛡️','🚬','⚰️','🪦','⚱️','🏺','🔮','📿','🧿','💈','⚗️','🔭','🔬','🩺','💊','🩹',
    '🩸','🧬','🦠','🧫','🧪','🌡️','🧹','🪠','🧺','🧻','🚽','🚰','🚿','🛁','🛀','🧼']},
  {id: 'symbols', name: '❤️', list: [
    '❤️','🧡','💛','💚','💙','💜','🖤','🤍','🤎','💔','❣️','💕','💞','💓','💗','💖',
    '💘','💝','💟','☮️','✝️','☪️','🕉️','☸️','✡️','🔯','🕎','☯️','☦️','🛐','⛎','♈',
    '♉','♊','♋','♌','♍','♎','♏','♐','♑','♒','♓','🆔','⚛️','🉑','☢️','☣️',
    '📴','📳','🈶','🈚','🈸','🈺','🈷️','✴️','🆚','💮','🉐','㊙️','㊗️','🈴','🈵','🈹',
    '🈲','🅰️','🅱️','🆎','🆑','🅾️','🆘','❌','⭕','🛑','⛔','📛','🚫','💯','💢','♨️',
    '🚷','🚯','🚳','🚱','🔞','📵','🚭','❗','❕','❓','❔','‼️','⁉️','🔅','🔆','〽️',
    '⚠️','🚸','🔱','⚜️','🔰','♻️','✅','🈯','💹','❇️','✳️','❎','🌐','💠','Ⓜ️','🌀',
    '💤','🏧','🚾','♿','🅿️','🈳','🈂️','🛂','🛃','🛄','🛅','🛄','🧳','⭐','🌟','✨',
    '⚡','☄️','💣','🔥','🌈','☀️','🌤️','⛅','🌥️','☁️','🌦️','🌧️','⛈️','🌩️','🌨️','❄️']},
  {id: 'flags', name: '🏁', list: [
    '🏁','🚩','🎌','🏴','🏳️','🏳️‍🌈','🏳️‍⚧️','🏴‍☠️','🇧🇩','🇮🇳','🇵🇰','🇺🇸','🇬🇧','🇯🇵','🇨🇳','🇷🇺',
    '🇸🇦','🇦🇪','🇹🇷','🇮🇷','🇮🇩','🇲🇾','🇸🇬','🇹🇭','🇻🇳','🇵🇭','🇰🇷','🇳🇵','🇱🇰','🇲🇲','🇧🇹','🇲🇻',
    '🇦🇫','🇮🇶','🇸🇾','🇪🇬','🇩🇪','🇫🇷','🇮🇹','🇪🇸','🇵🇹','🇧🇷','🇨🇦','🇦🇺','🇳🇿','🇿🇦','🇳🇬','🇰🇪',
    '🇦🇷','🇲🇽','🇨🇴','🇨🇱','🇵🇪','🇻🇪','🇺🇦','🇵🇱','🇳🇱','🇧🇪','🇨🇭','🇦🇹','🇸🇪','🇳🇴','🇩🇰','🇫🇮']},
];

/* ---------- recent emojis (localStorage) ---------- */
function emojiRecent(){
  try { return JSON.parse(localStorage.getItem('tgw_emoji_recent') || '[]'); }
  catch (e) { return []; }
}
function emojiPushRecent(em){
  try {
    var r = emojiRecent().filter(function(x){ return x !== em; });
    r.unshift(em);
    localStorage.setItem('tgw_emoji_recent', JSON.stringify(r.slice(0, 32)));
  } catch (e) {}
}

/* ---------- picker UI ---------- */
var emojiPickerEl = null;
var emojiCat = 'smileys';

function buildEmojiPicker(){
  if (emojiPickerEl) return emojiPickerEl;
  emojiPickerEl = document.createElement('div');
  emojiPickerEl.id = 'emojiPicker';
  emojiPickerEl.className = 'emoji-picker';
  emojiPickerEl.hidden = true;
  document.body.appendChild(emojiPickerEl);
  return emojiPickerEl;
}

function renderEmojiPicker(){
  var el = buildEmojiPicker();
  var cats = EMOJI_CATS.slice();
  cats[0] = {id: 'recent', name: '⭐', list: emojiRecent()};
  var tabs = cats.map(function(c){
    if (!c.list.length && c.id === 'recent') return '';
    return '<button type="button" class="ecat' + (c.id === emojiCat ? ' on' : '') +
      '" data-ecat="' + c.id + '" title="' + c.id + '">' + c.name + '</button>';
  }).join('');
  var cur = cats.filter(function(c){ return c.id === emojiCat; })[0] || cats[1];
  var list = cur.list.length ? cur.list : cats[1].list;
  var grid = list.map(function(em){
    return '<button type="button" class="eem" data-em="' + em + '">' + em + '</button>';
  }).join('');
  el.innerHTML = '<div class="etabs">' + tabs + '</div>' +
    '<div class="egrid">' + grid + '</div>';
}

function toggleEmojiPicker(btn){
  var el = buildEmojiPicker();
  if (!el.hidden){ el.hidden = true; return; }
  renderEmojiPicker();
  el.hidden = false;
  var r = btn.getBoundingClientRect();
  el.style.left = Math.max(8, Math.min(window.innerWidth - el.offsetWidth - 8, r.left - el.offsetWidth + 40)) + 'px';
  el.style.top = (r.top - el.offsetHeight - 8) + 'px';
  var input = $('#input');
  if (input) input.focus();
}

function insertEmoji(em){
  var input = $('#input');
  if (!input) return;
  var s = input.selectionStart || input.value.length;
  var e = input.selectionEnd || s;
  input.value = input.value.slice(0, s) + em + input.value.slice(e);
  var pos = s + em.length;
  try {
    input.setSelectionRange(pos, pos);
  } catch (err) {}
  input.focus();
  input.dispatchEvent(new Event('input'));
  emojiPushRecent(em);
}

document.addEventListener('click', function(e){
  var el = emojiPickerEl;
  if (!el || el.hidden) return;
  if (e.target.closest('#emojiPicker')) return;
  var btn = e.target.closest('[data-emoji-btn]');
  if (btn) return;
  el.hidden = true;
});
document.addEventListener('click', function(e){
  var el = emojiPickerEl;
  if (!el || el.hidden) return;
  var cat = e.target.closest('[data-ecat]');
  if (cat){ emojiCat = cat.getAttribute('data-ecat'); renderEmojiPicker(); return; }
  var em = e.target.closest('[data-em]');
  if (em){ insertEmoji(em.getAttribute('data-em')); if (emojiCat === 'recent') renderEmojiPicker(); }
});
