// Article2Pod front end. One state object comes from the server (pushed over
// /api/events); the page re-renders from it. Cards being edited are left alone.
'use strict';

let S = { jobs: [], engines: [], voices: [], genres: [], processing: false, comfy: { running: false, ours: false, busy: '' } };
const opened = {};      // job id -> {panel: bool, log: bool}
const rendered = {};    // job id -> JSON it was last drawn from
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = name => `<i class="fa-solid fa-${name}"></i>`;
const iconBtn = (name, title, onclick, cls = '') => `<button class="icon ${cls}" title="${esc(title)}" onclick="${onclick}">${icon(name)}</button>`;

async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
  const res = await fetch(url, opt);
  let data = {};
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) { const err = new Error(data.error || res.statusText); err.data = data; throw err; }
  return data;
}

function banner(msg) {
  const b = $('banner');
  b.textContent = msg || '';
  b.hidden = !msg;
  if (msg) setTimeout(() => { if (b.textContent === msg) b.hidden = true; }, 9000);
}

// ---- tabs -----------------------------------------------------------------
document.querySelectorAll('nav button').forEach(btn => btn.onclick = () => {
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b === btn));
  for (const t of ['queue', 'finished', 'stats', 'settings']) $('tab-' + t).hidden = t !== btn.dataset.tab;
  if (btn.dataset.tab === 'settings') loadSettings();
  if (btn.dataset.tab === 'stats') loadStats();
  if (btn.dataset.tab === 'finished') loadLibrary();
});

// ---- adding ---------------------------------------------------------------
let mode = 'url';
function setMode(m) {
  mode = m;
  $('modeUrl').classList.toggle('active', m === 'url');
  $('modeText').classList.toggle('active', m === 'text');
  $('url').hidden = m !== 'url';
  $('text').hidden = m !== 'text';
}
$('modeUrl').onclick = () => setMode('url');
$('modeText').onclick = () => setMode('text');
$('url').onkeydown = e => { if (e.key === 'Enter') $('addBtn').click(); };
$('addBtn').onclick = async () => {
  const body = mode === 'url' ? { url: $('url').value.trim() } : { text: $('text').value };
  if (!(body.url || body.text)) return;
  const btn = $('addBtn');
  btn.disabled = true; btn.textContent = 'Adding...';
  try {
    await api('POST', '/api/add', body);
    $('url').value = ''; $('text').value = '';
    banner('');
  } catch (e) {
    banner(e.message);
    if (e.data && e.data.needs_paste) setMode('text');
  } finally { btn.disabled = false; btn.textContent = 'Add'; }
};

// ---- run / stop, ComfyUI ---------------------------------------------------
$('runBtn').onclick = async () => {
  try { await api('POST', S.processing ? '/api/stop' : '/api/run'); }
  catch (e) { banner(e.message); }
};
window.comfyStartStop = async () => {
  const c = S.comfy || {};
  if (c.running && !confirm('Stop ComfyUI?')) return;
  try { await api('POST', c.running ? '/api/comfy/stop' : '/api/comfy/start'); }
  catch (e) { banner(e.message); }
};
window.comfyFree = async () => {
  try { await api('POST', '/api/free'); banner('ComfyUI memory freed.'); }
  catch (e) { banner(e.message); }
};
function comfyControls() {
  // Lives in Settings so it can't be pressed by accident.
  const box = $('comfyControls');
  if (!box) return;
  const c = S.comfy || {}, foreign = c.running && !c.ours;
  const why = foreign ? 'ComfyUI Desktop started this ComfyUI, so close it there. Article2Pod only stops a ComfyUI it started.'
    : S.processing ? 'The queue is running.' : '';
  box.innerHTML = `<div class="buttons">
      <span class="comfy-state ${c.running ? 'up' : ''}"><span class="dot"></span> ${c.busy ? `ComfyUI ${esc(c.busy)}...` : c.running ? 'Running' : 'Not running'}${foreign ? ' (opened by ComfyUI Desktop)' : ''}</span>
      <button class="ghost" onclick="comfyStartStop()" ${c.busy || S.processing || foreign ? 'disabled' : ''} title="${esc(why)}">${c.running ? `${icon('stop')} Stop ComfyUI` : `${icon('play')} Start ComfyUI`}</button>
      <button class="ghost" onclick="comfyFree()" ${!c.running || c.busy || S.processing ? 'disabled' : ''} title="Unload models from ComfyUI's memory">${icon('recycle')} Free memory</button>
    </div>${why ? `<p class="help">${esc(why)}</p>` : ''}`;
}

// ---- cover art: click, drop or paste ----------------------------------------
// Each card's picture can be clicked (file picker), have an image dropped on
// it, or be pasted into through the small clipboard badge on its corner.
const artTargets = {};          // key -> {kind: 'job' | 'lib', id}
function artCell(key, src, editable) {
  const k = esc(key);
  const acts = editable ? `onclick="pickArt('${k}')" ondragover="artDrag('${k}', event, true)" ondragleave="artDrag('${k}', event, false)" ondrop="artDrop('${k}', event)" title="Click or drop an image to change the cover art"` : '';
  const pic = src ? `<img class="art ${editable ? 'editable' : ''}" id="art-${k}" src="${esc(src)}" alt="" loading="lazy" ${acts}>`
                  : `<div class="art none ${editable ? 'editable' : ''}" id="art-${k}" ${acts}>&#9834;</div>`;
  const badge = editable ? `<div class="art-paste" id="paste-${k}" contenteditable="true" tabindex="0"
      onfocus="pasteHint(this)" onblur="pasteHint(null)" onpaste="artPaste('${k}', event)" oninput="artInput('${k}', event)"
      title="Click here, then paste an image (Ctrl+V)">${icon('clipboard')}</div>` : '';
  return `<div class="art-wrap">${pic}${badge}</div>`;
}
window.pickArt = key => {
  const input = document.createElement('input');
  input.type = 'file'; input.accept = 'image/*';
  input.onchange = () => { if (input.files[0]) uploadArt(key, input.files[0]); };
  input.click();
};
window.artDrag = (key, e, over) => { e.preventDefault(); const el = $('art-' + key); if (el) el.classList.toggle('drag', over); };
window.artDrop = (key, e) => {
  e.preventDefault();
  const el = $('art-' + key); if (el) el.classList.remove('drag');
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f && f.type.startsWith('image/')) uploadArt(key, f);
};
window.artPaste = (key, e) => {
  pasteHint(null);
  const badge = e.currentTarget, cd = e.clipboardData;
  let file = null, url = null;
  if (cd) {
    file = [...(cd.files || [])].find(f => f.type && f.type.startsWith('image/')) || null;      // iOS puts photos here
    if (!file) for (const it of (cd.items || [])) if (it.type && it.type.startsWith('image/')) { file = it.getAsFile(); if (file) break; }
    if (!file) {                                                     // Firefox on iOS copies an image as a link to it
      const raw = (cd.getData('text/uri-list') || cd.getData('text/plain') || '').trim();
      const first = raw.split(/\r?\n/).find(l => l && !l.startsWith('#'));
      if (first && /^https?:\/\//i.test(first.trim())) url = first.trim();
    }
  }
  if (file || url) { e.preventDefault(); badge.blur(); uploadArt(key, file || url); }
  // otherwise let the browser insert the picture into the badge: artInput picks it up
};
window.artInput = async (key, e) => {              // iOS keyboard "Paste" inserts an <img> without a paste event
  const badge = e.currentTarget, img = badge.querySelector('img');
  const src = img && img.src;
  const hadText = badge.textContent.trim();
  badge.innerHTML = icon('clipboard');
  badge.blur();
  if (!src) { if (hadText) banner('The clipboard did not contain an image.'); return; }
  try { await uploadArt(key, await (await fetch(src)).blob()); }
  catch (err) { banner('Could not read the pasted image.'); }
};
function pasteHint(el) {
  const old = $('pasteHint'); if (old) old.remove();
  if (!el || !window.matchMedia('(pointer: fine)').matches) return;
  const hint = document.createElement('div');
  hint.id = 'pasteHint';
  hint.innerHTML = `${icon('clipboard')} Press <kbd>${/Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘V' : 'Ctrl+V'}</kbd> to paste an image`;
  document.body.appendChild(hint);
  const r = el.getBoundingClientRect();
  hint.style.top = Math.max(8, r.top + r.height / 2 - hint.offsetHeight / 2) + 'px';
  hint.style.left = Math.min(r.right + 10, window.innerWidth - hint.offsetWidth - 8) + 'px';
}
window.pasteHint = pasteHint;
document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('pasteHint')) { pasteHint(null); document.activeElement.blur(); } });
window.addEventListener('scroll', () => pasteHint(null), { passive: true });

async function uploadArt(key, fileOrUrl) {
  const t = artTargets[key];
  if (!t) return;
  const el = $('art-' + key);
  if (el) el.style.opacity = '0.5';
  try {
    let body;
    if (typeof fileOrUrl === 'string') body = { image_url: fileOrUrl };
    else { body = new FormData(); body.append('image', fileOrUrl); }
    if (t.kind === 'job') await api('POST', `/api/jobs/${t.id}/art`, body);
    else { await api('POST', '/api/library/art?path=' + encodeURIComponent(t.id), body); await loadLibrary(); }
  } catch (e) { banner(e.message); }
  finally { const el2 = $('art-' + key); if (el2) el2.style.opacity = ''; }
}

// ---- queue cards ------------------------------------------------------------
const label = id => id === 'auto' ? 'Auto' : ((S.engines.find(e => e.id === id) || {}).label || id);
const voiceName = v => v === 'shuffle' ? 'Shuffle' : String(v || '').replace(/\.mp3$/i, '');

function options(list, current, first) {
  return (first ? `<option value="">${esc(first)}</option>` : '') +
    list.map(([v, t]) => `<option value="${esc(v)}" ${v === current ? 'selected' : ''}>${esc(t)}</option>`).join('');
}

function cardHtml(j) {
  const o = opened[j.id] || {};
  const badges = [];
  if (j.engine_badge) badges.push(`<span class="badge ${esc(j.engine_badge)}">${esc(label(j.engine_badge))}</span>`);
  if (j.kind !== 'article') badges.push(`<span class="badge">${esc(j.kind)}</span>`);
  if (j.status === 'stopped') badges.push(`<span class="badge warn">Stopped ${j.segments_done}/${j.segments_total}</span>`);
  if (j.status === 'failed') badges.push('<span class="badge warn">Failed</span>');
  if (j.status === 'processing') badges.push(`<span class="badge">${j.segments_total ? `Segment ${Math.min(j.segments_done + 1, j.segments_total)} of ${j.segments_total}` : 'Preparing'}</span>`);
  const voice = j.voice_used && j.status !== 'pending' ? j.voice_used : (j.voice || '');
  const detail = [j.site, j.author].filter(Boolean).map(esc).join(' &middot; ');
  const extra = [];
  if (voice) extra.push(esc(voiceName(voice)));
  if (j.words) extra.push(`${j.words} words`);
  if (j.retries) extra.push(`<span class="retries" title="Segments re-generated because the first take failed the quality check">retries (${j.retries})</span>`);

  const busy = j.status === 'processing';
  let actions = busy ? '<div class="spinner" title="Generating"></div>' : iconBtn('gear', 'Voice, engine and details', `togglePanel(${j.id})`, (j.voice || j.engine) ? 'on' : '');
  actions += iconBtn('file-lines', 'Show the log', `toggleLog(${j.id})`);
  if (!busy) {
    if (j.status === 'stopped' || (j.status === 'failed' && j.segments_done)) actions += iconBtn('rotate-left', 'Start over from the beginning', `startOver(${j.id})`);
    actions += iconBtn('xmark', 'Remove from the queue', `removeJob(${j.id})`, 'danger');
  }
  const pct = j.segments_total ? Math.round(100 * j.segments_done / j.segments_total) : 0;
  const panel = !o.panel || busy ? '' : `
    <div class="panel">
      ${j.kind === 'article' ? `
      <div><label>Voice</label><select id="voice-${j.id}">${options([['shuffle', 'Shuffle'], ...S.voices.map(v => [v, voiceName(v)])], j.voice, `Default (${voiceName(S.default_voice)})`)}</select></div>
      ${S.engines.length > 1 ? `<div><label>Engine</label><select id="engine-${j.id}">${options(S.engines.map(e => [e.id, e.label]), j.engine, 'Automatic')}</select></div>` : ''}` : ''}
      <div class="wide"><label>Title</label><input id="title-${j.id}" value="${esc(j.title)}"></div>
      <div><label>Author</label><input id="author-${j.id}" value="${esc(j.author)}"></div>
      <div><label>Site</label><input id="site-${j.id}" value="${esc(j.site)}"></div>
      <div class="wide buttons">
        <button class="primary" onclick="saveJob(${j.id})">Save</button>
        ${iconBtn('arrows-up-down', 'Swap title and author', `swapTitleAuthor(${j.id})`)}
      </div>
    </div>`;
  artTargets['j' + j.id] = { kind: 'job', id: j.id };
  return `
    <div class="row">
      ${artCell('j' + j.id, j.art, !busy)}
      <div class="info">
        <div class="title">${esc(j.title)}</div>
        <div class="meta">${detail}</div>
        <div class="meta">${badges.join('')}${extra.join(' &middot; ')}</div>
      </div>
      <div class="actions">${actions}</div>
    </div>
    ${busy || j.status === 'stopped' ? `<div class="bar"><div style="width:${pct}%"></div></div>` : ''}
    ${j.error ? `<div class="error">${esc(j.error)}</div>` : ''}
    ${panel}
    ${o.log ? `<div class="log" id="log-${j.id}">Loading...</div>` : ''}`;
}

const editingIn = el => el && el.contains(document.activeElement) && document.activeElement.matches('input, select, textarea, [contenteditable]');

function renderQueue(jobs) {
  const el = $('queue');
  const keep = new Set(jobs.map(j => 'card-' + j.id));
  [...el.children].forEach(c => { if (!keep.has(c.id)) c.remove(); });
  if (!jobs.length) { el.innerHTML = '<div class="empty" id="card-empty">Nothing queued. Paste a URL above.</div>'; return; }
  const empty = el.querySelector('#card-empty');
  if (empty) empty.remove();
  jobs.forEach((j, i) => {
    let card = $('card-' + j.id);
    const sig = JSON.stringify([j, opened[j.id], S.engines, S.voices, S.default_voice]);
    if (!card) { card = document.createElement('div'); card.id = 'card-' + j.id; rendered[j.id] = null; }
    if (rendered[j.id] !== sig && !editingIn(card)) {
      card.className = 'card ' + j.status;
      card.innerHTML = cardHtml(j);
      rendered[j.id] = sig;
      if ((opened[j.id] || {}).log) loadLog(j.id);
    }
    if (el.children[i] !== card) el.insertBefore(card, el.children[i] || null);
  });
}

function render() {
  const queue = S.jobs.filter(j => j.status !== 'done');
  renderQueue(queue);
  const doneCount = S.jobs.filter(j => j.status === 'done').length;
  if (!$('tab-finished').hidden && doneCount !== lastDone) loadLibrary();      // something just finished
  lastDone = doneCount;

  const c = S.comfy || {};
  $('comfy').classList.toggle('up', !!c.running);
  $('comfyText').textContent = c.busy ? `ComfyUI ${c.busy}...` : c.running ? 'ComfyUI running' : 'ComfyUI not running';
  comfyControls();
  drawPreview();

  const btn = $('runBtn');
  btn.textContent = S.processing ? 'Stop' : 'Generate';
  btn.classList.toggle('stop', !!S.processing);
  const waiting = queue.filter(j => j.status !== 'processing').length;
  btn.disabled = !S.processing && !waiting;
  const resumable = queue.filter(j => j.status === 'stopped').length;
  $('runInfo').textContent = S.processing ? 'Stop keeps the segments already finished.'
    : resumable ? `${resumable} stopped article${resumable > 1 ? 's' : ''} will resume where ${resumable > 1 ? 'they' : 'it'} left off.` : '';
}

window.togglePanel = id => { opened[id] = { ...(opened[id] || {}), panel: !(opened[id] || {}).panel }; document.activeElement.blur(); render(); };
window.toggleLog = id => { opened[id] = { ...(opened[id] || {}), log: !(opened[id] || {}).log }; render(); };
async function loadLog(id) {
  try {
    const d = await api('GET', `/api/jobs/${id}/log`);
    const el = $('log-' + id);
    if (el) { el.textContent = d.log.join('\n') || '(nothing yet)'; el.scrollTop = el.scrollHeight; }
  } catch (e) {}
}
window.saveJob = async id => {
  const v = n => { const el = $(`${n}-${id}`); return el ? el.value : undefined; };
  const body = { title: v('title'), author: v('author'), site: v('site') };
  if (v('voice') !== undefined) body.voice = v('voice');
  if (v('engine') !== undefined) body.engine = v('engine');
  try { await api('PATCH', `/api/jobs/${id}`, body); opened[id] = { ...(opened[id] || {}), panel: false }; document.activeElement.blur(); render(); }
  catch (e) { banner(e.message); }
};
window.swapTitleAuthor = id => { const t = $('title-' + id), a = $('author-' + id); [t.value, a.value] = [a.value, t.value]; };
window.removeJob = async id => { try { await api('DELETE', `/api/jobs/${id}`); } catch (e) { banner(e.message); } };
window.startOver = async id => {
  if (!confirm('Throw away the finished segments and start this article from the beginning?')) return;
  try { await api('POST', `/api/jobs/${id}/startover`); } catch (e) { banner(e.message); }
};

// ---- library: every MP3 in the output folder --------------------------------
let lib = [], lastDone = -1, playing = null;
const libOpen = {};       // path -> true when its edit panel is open

async function loadLibrary() {
  try {
    const d = await api('GET', '/api/library?limit=150&q=' + encodeURIComponent($('libSearch').value.trim()));
    lib = d.files;
    $('libCount').textContent = d.total > lib.length ? `showing ${lib.length} of ${d.total}` : `${d.total} file${d.total === 1 ? '' : 's'}`;
    drawLibrary();
  } catch (e) { banner(e.message); }
}
let libTimer = null;
$('libSearch').oninput = () => { clearTimeout(libTimer); libTimer = setTimeout(loadLibrary, 300); };

function drawLibrary() {
  const el = $('finished');
  if (editingIn(el)) return;
  if (!lib.length) { el.innerHTML = '<div class="empty">No MP3 files found.</div>'; return; }
  el.innerHTML = lib.map((f, i) => {
    const q = encodeURIComponent(f.path);
    const extra = [new Date(f.modified * 1000).toLocaleDateString(), `${(f.size / 1048576).toFixed(1)} MB`];
    if (f.voice) extra.push(esc(voiceName(f.voice)));
    if (f.genre) extra.push(esc(f.genre));
    if (f.retries) extra.push(`<span class="retries" title="Segments re-generated because the first take failed the quality check">retries (${f.retries})</span>`);
    const badge = f.engine ? `<span class="badge ${esc(f.engine)}">${esc(label(f.engine))}</span>` : (f.kind && f.kind !== 'article' ? `<span class="badge">${esc(f.kind)}</span>` : '');
    const panel = !libOpen[f.path] ? '' : `
      <div class="panel">
        <div class="wide"><label>Title</label><input id="lt-${i}" value="${esc(f.title)}"></div>
        <div><label>Author</label><input id="la-${i}" value="${esc(f.author)}"></div>
        <div><label>Site</label><input id="ls-${i}" value="${esc(f.site)}"></div>
        <div class="wide buttons"><button class="primary" onclick="saveLib(${i})">Save</button>
          <span class="muted">Saving renames and moves the file.</span></div>
      </div>`;
    artTargets['l' + i] = { kind: 'lib', id: f.path };
    const isPlaying = playing === f.path;
    return `<div class="card done ${isPlaying ? 'playing' : ''}"><div class="row">
        ${artCell('l' + i, f.has_art ? `/api/library/art?path=${q}&v=${Math.round(f.modified)}` : '', true)}
        <div class="info"><div class="title">${esc(f.title)}</div>
          <div class="meta">${[f.site, f.author].filter(Boolean).map(esc).join(' &middot; ')}</div>
          <div class="meta">${badge}${extra.join(' &middot; ')}</div></div>
        <div class="actions">
          ${iconBtn(isPlaying && !audio.paused ? 'pause' : 'play', 'Play or pause', `playLib(${i})`, isPlaying ? 'on' : '')}
          ${iconBtn('gear', 'Edit title, author and site', `toggleLib(${i})`)}
          <a class="icon" title="Download the MP3" href="/api/library/download?path=${q}">${icon('download')}</a>
          ${iconBtn('trash', 'Delete the MP3', `deleteLib(${i})`, 'danger')}
        </div>
      </div>${panel}</div>`;
  }).join('');
}
window.toggleLib = i => { libOpen[lib[i].path] = !libOpen[lib[i].path]; document.activeElement.blur(); drawLibrary(); };
window.saveLib = async i => {
  const f = lib[i];
  try {
    const d = await api('PATCH', '/api/library', { path: f.path, title: $('lt-' + i).value, author: $('la-' + i).value, site: $('ls-' + i).value });
    if (playing === f.path) playing = d.path;
    delete libOpen[f.path]; document.activeElement.blur(); await loadLibrary();
  } catch (e) { banner(e.message); }
};
window.deleteLib = async i => {
  if (!confirm(`Delete "${lib[i].title}"? The MP3 file is removed.`)) return;
  try {
    if (playing === lib[i].path) closePlayer();
    await api('DELETE', '/api/library?path=' + encodeURIComponent(lib[i].path)); await loadLibrary();
  } catch (e) { banner(e.message); }
};

// ---- player: one bar at the bottom, so playback survives list updates -------
const audio = $('audio');
window.playLib = i => {
  const f = lib[i];
  if (playing === f.path) { audio.paused ? audio.play() : audio.pause(); return; }
  playing = f.path;
  audio.src = '/api/library/play?path=' + encodeURIComponent(f.path);
  $('playerTitle').textContent = f.title;
  $('playerMeta').textContent = [f.site, f.author].filter(Boolean).join(' · ');
  $('player').hidden = false;
  document.body.classList.add('has-player');
  audio.play();
};
function closePlayer() {
  audio.pause(); audio.removeAttribute('src'); audio.load();
  playing = null;
  $('player').hidden = true;
  document.body.classList.remove('has-player');
  drawLibrary();
}
$('playerClose').onclick = closePlayer;
audio.onpause = audio.onplay = audio.onended = () => drawLibrary();      // keep the card's button in step

// ---- statistics -------------------------------------------------------------
const pct = x => x == null ? '\u2013' : `${(x * 100).toFixed(1)}%`;
const fixed = (x, d = 2) => x == null ? '\u2013' : Number(x).toFixed(d);
$('statsDays').onchange = () => loadStats();

async function loadStats() {
  try { drawStats(await api('GET', '/api/stats?days=' + $('statsDays').value)); }
  catch (e) { banner(e.message); }
}
window.importStats = async btn => {
  btn.disabled = true; btn.textContent = 'Importing...';
  try { const r = await api('POST', '/api/stats/import'); banner(`Imported ${r.runs} articles and ${r.takes} segments from the original app.`); loadStats(); }
  catch (e) { banner(e.message); btn.disabled = false; }
};

function table(cols, rows) {
  return `<div class="table-wrap"><table><thead><tr>${cols.map(c => `<th class="${c.num ? 'num' : ''}" title="${esc(c.help || '')}">${esc(c.label)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr>${cols.map(c => `<td class="${c.num ? 'num' : ''}">${c.cell(r)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function drawStats(d) {
  const t = d.totals;
  $('statsNote').textContent = d.since ? `Data from ${new Date(d.since * 1000).toLocaleDateString()}` : '';
  if (!t.articles && !t.failed && !t.segments) {
    $('stats').innerHTML = `<div class="empty">Nothing recorded in this period yet.</div>
      ${d.imported ? '' : '<div style="text-align:center"><button class="ghost" onclick="importStats(this)">Import history from the original app</button></div>'}`;
    return;
  }
  const worst = d.voices.filter(v => v.segments >= 5 && v.retry_rate).slice(0, 3);
  const engineName = id => (S.engines.find(e => e.id === id) || {}).label || id;
  $('stats').innerHTML = `
    <div class="tiles">
      <div class="tile"><b>${t.articles}</b><span>articles read</span></div>
      <div class="tile"><b>${t.downloads}</b><span>downloads</span></div>
      <div class="tile"><b>${t.failed}</b><span>failed</span></div>
      <div class="tile"><b>${(t.words / 1000).toFixed(0)}k</b><span>words</span></div>
      <div class="tile"><b>${t.generate_hours}</b><span>hours generating</span></div>
      <div class="tile"><b>${pct(t.retry_rate)}</b><span>segments retried</span></div>
      <div class="tile"><b>${pct(t.match)}</b><span>average match</span></div>
    </div>
    ${worst.length ? `<div class="sect"><h2>Voices that need the most retries</h2>
      <p class="help">${worst.map(v => `<b>${esc(v.voice)}</b>: ${pct(v.retry_rate)} of ${v.segments} segments needed another take`).join(' &middot; ')}</p></div>` : ''}
    <div class="sect"><h2>Voices</h2>
      <p class="help">Match is how closely Whisper\u2019s transcript of the audio matched the text (100% = every word heard as written). A segment is retried when its first take scores below the threshold; the best take is kept. Hover a heading for details.</p>
      ${table([
        { label: 'Voice', cell: v => esc(v.voice) },
        { label: 'Retried', num: 1, help: 'Share of segments whose first take was not good enough', cell: v => pct(v.retry_rate) },
        { label: 'Engines', cell: v => v.engines.map(e => `<span class="badge ${esc(e)}">${esc(engineName(e))}</span>`).join('') },
        { label: 'Articles', num: 1, cell: v => v.articles },
        { label: 'Failed', num: 1, help: 'Articles that failed outright (timeouts, errors)', cell: v => v.failed || '' },
        { label: 'Segments', num: 1, help: 'Segments or chunks checked by Whisper', cell: v => v.segments || '' },
        { label: 'First take', num: 1, help: 'Average match of the first take', cell: v => pct(v.first_match) },
        { label: 'Kept', num: 1, help: 'Average match of the take that was kept', cell: v => pct(v.match) },
        { label: 'Worst', num: 1, help: 'Lowest match kept in a finished file', cell: v => pct(v.worst_match) },
        { label: 's/word', num: 1, help: 'Seconds of generation per word, including retries', cell: v => fixed(v.seconds_per_word) },
      ], d.voices)}</div>
    <div class="sect"><h2>Engines</h2>
      ${table([
        { label: 'Engine', cell: e => esc(engineName(e.engine)) },
        { label: 'Articles', num: 1, cell: e => e.articles },
        { label: 'Failed', num: 1, cell: e => e.failed || '' },
        { label: 'Segments', num: 1, cell: e => e.segments },
        { label: 'Retried', num: 1, cell: e => pct(e.retry_rate) },
        { label: 'Kept match', num: 1, cell: e => pct(e.match) },
        { label: 's/word', num: 1, cell: e => fixed(e.seconds_per_word) },
      ], d.engines)}</div>
    ${d.genres.length ? `<div class="sect"><h2>Genres (Breeze)</h2>${table([
        { label: 'Genre', cell: g => esc(g.genre) }, { label: 'Articles', num: 1, cell: g => g.articles },
        { label: 'Average match', num: 1, cell: g => pct(g.match) }], d.genres)}</div>` : ''}
    <div class="sect"><h2>Recent failures</h2>
      ${d.failures.length ? table([
        { label: 'When', cell: f => new Date(f.ts * 1000).toLocaleString() },
        { label: 'Article', cell: f => esc(f.title) },
        { label: 'Voice', cell: f => esc(f.voice) },
        { label: 'Reason', cell: f => esc(f.error) }], d.failures) : '<p class="help">None in this period.</p>'}</div>
    ${d.imported ? '' : '<p class="help"><button class="ghost" onclick="importStats(this)">Import history from the original app</button> to include everything made before this app.</p>'}`;
}

// ---- settings -------------------------------------------------------------
// The saved overrides (ov) are edited in place and sent back whole. Each
// section shows a list to pick from first, then the fields for that one item.
let ov = null, defs = null, paths = [], rawDirty = false;
const sel = { voice: '', genre: '', site: '', other: '' };
const dig = (o, path) => path.reduce((x, k) => (x == null ? undefined : x[k]), o);
const get = (path, d) => dig(ov, path) ?? dig(defs, path) ?? d;
const dirty = () => { $('saveNote').textContent = 'Unsaved changes'; };
function set(path, value) {
  let o = ov;
  for (const k of path.slice(0, -1)) o = (o[k] = (o[k] && typeof o[k] === 'object') ? o[k] : {});
  const last = path[path.length - 1];
  if (value === '' || value === undefined || value === null) delete o[last]; else o[last] = value;
  dirty();
}
const num = v => v === '' ? '' : Number(v);
function deepMerge(base, over) {
  if (!over || typeof over !== 'object' || Array.isArray(over)) return over === undefined ? base : over;
  const out = { ...(base && typeof base === 'object' ? base : {}) };
  for (const k of Object.keys(over)) out[k] = deepMerge(out[k], over[k]);
  return out;
}
const P = path => paths.push(path) - 1;       // paths are passed by index, never inlined (names may contain quotes)
window.S_set = (i, el, kind) => {
  if (kind === 'num') {
    // Typed as text (a decimal keypad on phones), so "1,3" works as well as "1.3".
    const raw = el.value.trim().replace(',', '.');
    const n = raw === '' ? '' : Number(raw);
    const bad = raw !== '' && !Number.isFinite(n);
    el.classList.toggle('bad', bad);
    if (bad) { $('saveNote').textContent = `"${el.value}" is not a number`; return; }
    return set(paths[i], n);
  }
  set(paths[i], kind === 'bool' ? el.checked : el.value);
};
// Fields update the page's settings as you type (oninput), so Preview always hears exactly what is in the boxes.
const field = (lbl, path, kind = 'text', cls = '', placeholder) => {
  const i = P(path);
  const attrs = kind === 'num' ? 'inputmode="decimal" autocomplete="off" spellcheck="false"' : '';
  return `<div class="field ${cls}"><label>${esc(lbl)}</label><input type="text" ${attrs} value="${esc(dig(ov, path) ?? '')}" placeholder="${esc(placeholder ?? dig(defs, path) ?? '')}" oninput="S_set(${i}, this, '${kind}')" onchange="S_set(${i}, this, '${kind}')"></div>`;
};
const pick = (lbl, path, list, first) => `<div class="field"><label>${esc(lbl)}</label><select onchange="S_set(${P(path)}, this)">${options(list, dig(ov, path) ?? '', first)}</select></div>`;
const check = (lbl, path) => `<label><input type="checkbox" ${get(path) ? 'checked' : ''} onchange="S_set(${P(path)}, this, 'bool')">${esc(lbl)}</label>`;
const chooser = (key, list, first) => `<select class="chooser" onchange="choose('${key}', this.value)">${options(list, sel[key], first)}</select>`;
window.choose = (key, value) => { sel[key] = value; redraw(); };
function redraw() { const note = $('saveNote') ? $('saveNote').textContent : ''; drawSettings(); $('saveNote').textContent = note; }

async function loadSettings() {
  const d = await api('GET', '/api/settings');
  defs = d.defaults;
  if (d.error) {
    $('settings').innerHTML = `<div class="sect"><h2>settings.json has a problem</h2><p class="help">${esc(d.error)}</p>
      <textarea id="rawJson" class="code">${esc(d.raw)}</textarea><div class="savebar"><button class="primary" onclick="saveRaw()">Save</button><span id="saveNote" class="muted"></span></div></div>`;
    return;
  }
  ov = d.overrides;
  drawSettings();
}

const voiceKey = v => Object.keys(ov.voices || {}).find(k => k.replace(/\.mp3$/i, '').toLowerCase() === v.replace(/\.mp3$/i, '').toLowerCase()) || v;
const OTHER = {       // settings without a form of their own: label and how to edit them
  strip_lines: ['Junk lines to remove', 'lines'], ad_markers: ['Cut the article at these phrases', 'lines'],
  strip_pull_quotes: ['Remove pull quotes', 'bool'], engines: ['Engine timings and limits', 'json'],
  quality: ['Whisper check thresholds', 'json'], whisper_model_dir: ['Whisper model folder', 'text'],
  cleanup_options: ['Clean-up limits', 'json'], art: ['Generated art options', 'json'],
  text_model_timeout: ['Text model timeout (seconds)', 'num'], comfy: ['ComfyUI address and launch options', 'json'],
  voices_dir: ['Voices folder', 'text'], output_dir: ['Output folder', 'text'], port: ['Web page port', 'num'],
};

function drawSettings() {
  paths = [];
  const engineList = S.engines.map(e => [e.id, e.label]);
  const voiceList = [['shuffle', 'Shuffle'], ...S.voices.map(v => [v, voiceName(v)])];
  const genres = Object.keys(get(['genres'], {}));
  const genreList = genres.map(g => [g, g]);
  const sites = Object.keys(ov.sites || {});
  const custom = v => { const e = (ov.voices || {})[voiceKey(v)]; return e && Object.keys(e).length; };

  // -- one voice
  let voiceHtml = '';
  if (sel.voice && S.voices.includes(sel.voice)) {
    const v = sel.voice, k = voiceKey(v), vi = S.voices.indexOf(v);
    const b = ['voices', k, 'breeze'], vv = ['voices', k, 'vibevoice'];
    const over = Object.keys(dig(ov, [...b, 'genres']) || {});
    const free = genreList.filter(([g]) => !over.includes(g));
    const more = ['quote_guidance', 'temperature', 'narrator'].some(f => dig(ov, [...b, f]) != null);
    // The fields shown are those of the engine this voice will use.
    const picked = dig(ov, ['voices', k, 'engine']) || '';
    const engine = S.engines.length === 1 ? S.engines[0].id : (picked || get(['default_engine'], ''));
    const engineSelect = S.engines.length > 1 ? `<div class="field"><label>Engine</label><select onchange="setVoiceEngine(${P(['voices', k, 'engine'])}, this)">${options(engineList, picked, `Automatic (${label(get(['default_engine'], ''))})`)}</select></div>` : '';
    const previewBox = `<div class="field"><label>Preview${engine === 'breeze' ? ' with genre' : ''}</label><div class="inline">
        ${engine === 'breeze' ? `<select id="pg-${vi}">${options(genreList, get(['default_genre'], ''), 'No genre')}</select>` : `<input type="hidden" id="pg-${vi}" value="">`}
        <button class="ghost" id="pb-${vi}" onclick="preview(${vi})">${icon('play')} Preview</button></div></div>`;
    if (engine === 'vibevoice') {
      voiceHtml = `<div class="item">
      ${engineSelect}
      ${field('Speed (0.8 to 1.2)', [...vv, 'speed'], 'num', '', '1.0 = normal')}
      ${previewBox}
      <div class="wide" id="pa-${vi}"></div>
      <details class="wide" ${dig(ov, [...vv, 'guidance']) != null ? 'open' : ''}>
        <summary>More for this voice</summary>
        <div class="grid">${field('Guidance (0.5 to 3.5)', [...vv, 'guidance'], 'num', '', 'use the workflow’s')}</div>
      </details>
      <p class="help wide">VibeVoice settings. This voice’s Breeze settings are kept and come back if you switch the engine.</p></div>`;
    } else {
    voiceHtml = `<div class="item">
      ${engineSelect}
      ${field('Guidance (all genres)', [...b, 'guidance'], 'num', '', 'use the genre’s')}
      ${previewBox}
      <div class="wide" id="pa-${vi}"></div>
      <details class="wide" ${more ? 'open' : ''}>
        <summary>More for this voice</summary>
        <div class="grid">${field('Quote guidance', [...b, 'quote_guidance'], 'num', '', 'use the genre’s')}
          ${field('Temperature', [...b, 'temperature'], 'num')}
          ${field('Narration direction (replaces the genre’s)', [...b, 'narrator'], 'text', 'span2', 'use the genre’s')}</div>
      </details>
      <details class="wide" ${over.length ? 'open' : ''}>
        <summary>Overrides for one genre${over.length ? ` (${over.length})` : ''}</summary>
        ${over.map(g => `<div class="sub"><div class="name"><span>${esc(g)}</span>${iconBtn('trash', 'Remove this override', `delKey(${P([...b, 'genres', g])})`, 'danger')}</div>
          <div class="grid">${field('Guidance', [...b, 'genres', g, 'narrator_guidance'], 'num', '', 'as above')}
            ${field('Quote guidance', [...b, 'genres', g, 'quote_guidance'], 'num', '', 'as above')}
            ${field('Temperature', [...b, 'genres', g, 'temperature'], 'num', '', 'as above')}
            ${field('Narration direction', [...b, 'genres', g, 'narrator'], 'text', 'span2', 'as above')}</div></div>`).join('')}
        ${free.length ? `<div class="inline" style="margin-top:8px"><select id="newOverride">${options(free, '', '')}</select>
          <button class="ghost" onclick="addOverride(${P([...b, 'genres'])})">${icon('plus')} Add override</button></div>` : ''}
      </details></div>`;
    }
  }

  // -- one genre
  let genreHtml = '';
  if (sel.genre && genres.includes(sel.genre)) {
    const g = sel.genre;
    genreHtml = `<div class="item">
      ${field('What belongs here (shown to the text model)', ['genres', g, 'description'], 'text', 'wide')}
      ${field('Narration direction', ['genres', g, 'narrator'], 'text', 'wide')}
      ${field('Narration guidance', ['genres', g, 'narrator_guidance'], 'num', '', get(['engines', 'breeze', 'guidance']))}
      ${field('Quote guidance', ['genres', g, 'quote_guidance'], 'num', '', get(['engines', 'breeze', 'quote_guidance']))}
      ${field('Spoken before quotes', ['genres', g, 'quote_cue'], 'text', '', 'nothing')}
      ${field('Pause after a quote (s)', ['genres', g, 'quote_end_pause'], 'num', '', get(['engines', 'breeze', 'switch_gap']))}
      ${field('Quote direction', ['genres', g, 'quote'], 'text', 'wide')}
      <div class="wide"><button class="ghost danger" onclick="delKey(${P(['genres', g])}, 'genre')">${icon('trash')} Remove this genre</button></div></div>`;
  }

  // -- one site
  let siteHtml = '';
  if (sel.site && sites.includes(sel.site)) {
    const d = sel.site;
    siteHtml = `<div class="item">
      ${pick('Voice', ['sites', d, 'voice'], voiceList, 'Default')}
      ${S.engines.length > 1 ? pick('Engine', ['sites', d, 'engine'], engineList, 'Automatic') : ''}
      ${pick('Genre', ['sites', d, 'genre'], genreList, 'Let the text model decide')}
      ${field('Cover art file', ['sites', d, 'art'])}
      <div class="field"><label>&nbsp;</label>${check('Blocks scraping: always paste', ['sites', d, 'paste_only'])}</div>
      <div class="wide"><button class="ghost danger" onclick="delKey(${P(['sites', d])}, 'site')">${icon('trash')} Remove this site</button></div></div>`;
  }

  // -- one other setting
  let otherHtml = '';
  if (sel.other === '*') {
    otherHtml = `<p class="help">Every saved setting as JSON. Saving this replaces the forms above.</p>
      <textarea id="rawJson" class="code" oninput="rawDirty = true; dirty()">${esc(JSON.stringify(ov, null, 2))}</textarea>`;
  } else if (OTHER[sel.other]) {
    const key = sel.other, kind = OTHER[key][1], isDefault = !(key in ov);
    const val = kind === 'json' ? deepMerge(defs[key], ov[key]) : get([key]);        // show every key, not only the changed ones
    const editor = kind === 'bool' ? `<div class="checks">${check(OTHER[key][0], [key])}</div>`
      : kind === 'lines' ? `<textarea class="code" onchange="setLines('${key}', this.value)">${esc((val || []).join('\n'))}</textarea><p class="help">One per line. Start a line with re: to use a pattern.</p>`
      : kind === 'json' ? `<textarea class="code" onchange="setJson('${key}', this)">${esc(JSON.stringify(val, null, 2))}</textarea>`
      : `<input ${kind === 'num' ? 'type="number"' : ''} value="${esc(dig(ov, [key]) ?? '')}" placeholder="${esc(defs[key] ?? '')}" onchange="S_set(${P([key])}, this, '${kind}')">`;
    otherHtml = `<div class="item"><div class="wide">${editor}</div>
      <div class="wide buttons"><span class="muted">${isDefault ? 'Using the built-in default.' : 'Changed from the default.'}</span>
      ${isDefault ? '' : `<button class="ghost" onclick="delKey(${P([key])})">${icon('rotate-left')} Back to default</button>`}</div></div>`;
  }

  $('settings').innerHTML = `
    <div class="sect"><h2>General</h2>
      <div class="grid">
        ${pick('Default voice', ['default_voice'], voiceList, '')}
        ${S.engines.length > 1 ? pick('Default engine', ['default_engine'], engineList, '') : ''}
        ${pick('Genre when the text model is off or fails', ['default_genre'], genreList, 'None')}
      </div>
      <div class="checks">
        ${check('Clean up articles with the text model', ['cleanup'])}
        ${check('Pick genre and quotes with the text model', ['text_model'])}
        ${check('Check every segment with Whisper and retry bad ones', ['quality_check'])}
        ${check('Normalize loudness', ['normalize'])}
        ${check('Generate cover art when an article has none', ['generate_art'])}
        ${check('Text model writes the art prompt', ['art_prompt'])}
        ${check('Group the queue by engine', ['group_by_engine'])}
      </div></div>
    <div class="sect"><div class="head"><h2>Voices</h2>${chooser('voice', S.voices.map(v => [v, voiceName(v) + (custom(v) ? '  •' : '')]), 'Choose a voice...')}</div>
      <p class="help">Guidance is how strongly the genre’s direction is applied to this voice: lower keeps more of the sample’s own personality. A dot marks voices you have changed.</p>${voiceHtml}</div>
    <div class="sect"><div class="head"><h2>Genres</h2>${chooser('genre', genreList, 'Choose a genre...')}
      <span class="inline"><input id="newGenre" placeholder="new genre"><button class="ghost" onclick="addKey('genres', 'newGenre', 'genre')">${icon('plus')} Add</button></span></div>${genreHtml}</div>
    <div class="sect"><div class="head"><h2>Sites</h2>${chooser('site', sites.map(d => [d, d]), 'Choose a site...')}
      <span class="inline"><input id="newSite" placeholder="example.com"><button class="ghost" onclick="addKey('sites', 'newSite', 'site')">${icon('plus')} Add</button></span></div>${siteHtml}</div>
    <div class="sect"><h2>Pronunciations</h2><p class="help">Written form on the left, how to say it on the right.</p>
      ${Object.entries(ov.pronunciations || {}).map(([w, s], i) => `<div class="pair"><input value="${esc(w)}" onchange="renamePron(${i}, this.value)"><input value="${esc(s)}" onchange="S_set(${P(['pronunciations', w])}, this)">
        ${iconBtn('trash', 'Remove', `delKey(${P(['pronunciations', w])})`, 'danger')}</div>`).join('')}
      <div class="pair"><input id="newWord" placeholder="written"><input id="newSay" placeholder="spoken"><button class="ghost" onclick="addPron()">${icon('plus')} Add</button></div></div>
    <div class="sect"><h2>ComfyUI</h2><div id="comfyControls"></div></div>
    <div class="sect"><div class="head"><h2>Other settings</h2>${chooser('other', [...Object.entries(OTHER).map(([k, v]) => [k, v[0] + (k in ov ? '  •' : '')]), ['*', 'Everything (raw JSON)']], 'Choose a setting...')}</div>${otherHtml}</div>
    <div class="savebar"><button class="primary" onclick="saveSettings()">Save settings</button><span id="saveNote" class="muted"></span></div>`;
  rawDirty = false;
  comfyControls();
  drawPreview();
}
window.setVoiceEngine = (i, el) => { set(paths[i], el.value); redraw(); dirty(); };
window.delKey = (i, selKey) => { const path = paths[i]; let o = ov; for (const k of path.slice(0, -1)) o = o[k] || {}; delete o[path[path.length - 1]]; if (selKey) sel[selKey] = ''; drawSettings(); dirty(); };
window.addKey = (section, inputId, selKey) => { const name = $(inputId).value.trim().toLowerCase(); if (!name) return; (ov[section] = ov[section] || {})[name] = ov[section][name] || {}; sel[selKey] = name; drawSettings(); dirty(); };
window.addOverride = i => { const g = $('newOverride').value; if (!g) return; let o = ov; for (const k of paths[i]) o = (o[k] = o[k] || {}); o[g] = o[g] || {}; drawSettings(); dirty(); };
window.addPron = () => { const w = $('newWord').value, s = $('newSay').value; if (!w.trim() || !s.trim()) return; (ov.pronunciations = ov.pronunciations || {})[w] = s; drawSettings(); dirty(); };
window.renamePron = (i, name) => { const e = Object.entries(ov.pronunciations); if (!name.trim()) return; e[i][0] = name; ov.pronunciations = Object.fromEntries(e); drawSettings(); dirty(); };
window.setLines = (key, text) => set([key], text.split('\n').map(l => l.trim()).filter(Boolean));
window.setJson = (key, el) => { try { set([key], JSON.parse(el.value)); el.classList.remove('bad'); } catch (e) { el.classList.add('bad'); $('saveNote').textContent = 'That is not valid JSON: ' + e.message; } };

function prune(o) {          // drop empty objects left behind by cleared boxes
  for (const k of Object.keys(o)) if (o[k] && typeof o[k] === 'object' && !Array.isArray(o[k])) { prune(o[k]); if (!Object.keys(o[k]).length) delete o[k]; }
  return o;
}
window.saveSettings = async () => {
  try {
    if (rawDirty) ov = JSON.parse($('rawJson').value);
    if (ov.voices) prune(ov.voices);
    await api('PUT', '/api/settings', ov);
    drawSettings();
    $('saveNote').textContent = 'Saved. Applies to the next article.';
  } catch (e) { $('saveNote').textContent = 'Not saved: ' + e.message; }
};
window.saveRaw = async () => {
  try { await api('PUT', '/api/settings', JSON.parse($('rawJson').value)); loadSettings(); }
  catch (e) { $('saveNote').textContent = 'Not saved: ' + e.message; }
};
// Previews are made in the background on the server, so they survive the
// phone locking or the page being closed; the page shows whatever state the
// server reports and plays the result when it is ready.
window.preview = async vi => {
  const voice = S.voices[vi];
  try {
    // The page's settings as they are now, saved or not: previews are for trying changes before saving.
    const current = rawDirty ? JSON.parse($('rawJson').value) : ov;
    await api('POST', '/api/preview', { voice, genre: $('pg-' + vi).value, settings: current,
      engine: dig(current, ['voices', voiceKey(voice), 'engine']) || dig(current, ['default_engine']) || get(['default_engine'], '') });
  } catch (e) { const box = $('pa-' + vi); if (box) box.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
};

function drawPreview() {
  if (!sel.voice || !ov) return;
  const vi = S.voices.indexOf(sel.voice), box = $('pa-' + vi), btn = $('pb-' + vi);
  if (!box || !btn) return;
  const p = S.preview || {}, mine = p.voice === sel.voice;
  const running = p.status === 'running';
  btn.disabled = running;
  btn.innerHTML = running && mine ? 'Generating...' : `${icon('play')} Preview`;
  let html, key;
  if (running && mine) {
    const secs = Math.round(Date.now() / 1000 - p.started);
    key = 'running';
    html = `<p class="help">Making a preview (started ${secs < 90 ? secs + ' seconds' : Math.round(secs / 60) + ' minutes'} ago). It keeps going if you leave this page or your phone locks; come back to play it. VibeVoice previews can take several minutes while the model loads.</p>`;
  } else if (running) {
    key = 'busy';
    html = `<p class="help">A preview for ${esc(voiceName(p.voice))} is being made; one at a time.</p>`;
  } else if (mine && p.status === 'failed') {
    key = 'failed' + p.finished;
    html = `<span class="muted">The preview failed: ${esc(p.error)}</span>`;
  } else {
    key = 'audio' + (mine ? p.finished : '');
    const src = `/api/preview/audio?voice=${encodeURIComponent(sel.voice)}&v=${mine ? p.finished : 'last'}`;
    html = `<audio controls preload="metadata" src="${src}" onerror="this.parentNode.innerHTML=''"></audio>
      <p class="help">${mine && p.status === 'done' ? `New preview ready${p.unsaved ? ', made with the settings on this page (saved or not)' : ''}.` : 'Last preview made for this voice.'} Previews use the settings as they are on this page, so you can try a change before saving it.</p>`;
  }
  if (box.dataset.key === key && key !== 'running') return;         // don't restart a playing preview
  box.dataset.key = key;
  box.innerHTML = html;
}
setInterval(() => { if ((S.preview || {}).status === 'running') drawPreview(); }, 5000);

// ---- live updates ---------------------------------------------------------
let lostSince = 0;
function connect() {
  const es = new EventSource('/api/events');
  es.onmessage = e => {
    const d = JSON.parse(e.data);
    if (d.error) { banner(d.error); return; }
    if (lostSince) { lostSince = 0; banner(''); }
    S = d; render();
  };
  es.onerror = () => {
    es.close();
    if (!lostSince) lostSince = Date.now();
    else if (Date.now() - lostSince > 5000) banner('Cannot reach Article2Pod. Is it running? Retrying...');
    setTimeout(connect, 3000);
  };
}
api('GET', '/api/state').then(d => { S = d; render(); }).catch(e => banner(e.message));
connect();
