// 界面逻辑，通过 window.pywebview.api 调 Python（api.py）。直接用浏览器打开 index.html 时用文末的 MockApi。

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

let api = null;
let state = null;      // { settings, browsers, paths, version }
let current = 'home';
let previous = 'home';

// ---------------- 页面切换 ----------------
async function show(id) {
  if (id !== current) previous = current;
  if (id !== 'home' && api) state = await api.get_state();  // 刚装好的浏览器也能马上看到
  current = id;
  $$('.screen').forEach(s => s.classList.toggle('hidden', s.id !== id));
  if (id === 'fanqie') fillSaveDir(id);
  if (id === 'video') fillVideoDir();
  if (SITES.includes(id) && (noBrowser() || (LOGIN_SITES.includes(id) && loginOk[id] === false))) openLoginDlg(id);
  if (id === 'settings') fillSettings();
  if (id === 'about') fillAbout();
}

// ---------------- 关于 ----------------
function fillAbout() {
  $('#about-version').textContent = 'v' + state.version;
  $$('#about-links [data-path]').forEach(b => { b.dataset.url = state.repo + b.dataset.path; });
}

// ---------------- 检查更新 ----------------
let update = null;

function renderUpdate() {
  const b = $('#update-pill');
  b.classList.toggle('hidden', !update?.newer);
  if (update?.newer) b.textContent = `有新版本 v${update.latest}`;
}

async function checkUpdate(manual) {
  const btn = $('#about-update'), msg = $('#about-update-msg');
  if (manual) { btn.disabled = true; msg.textContent = '正在检查…'; }
  const r = await api.check_update();
  if (r.ok) { update = r; renderUpdate(); }
  if (!manual) return;  // 自动检查失败就不打扰
  btn.disabled = false;
  msg.textContent = r.message;
  if (r.newer) openUpdateDlg();
}

// 更新弹框：confirm 确认 → download 下载中 → install 正在安装（随后软件退出、换成新版）；出错是 error
let updPhase = '';
const MB = n => (n / 1048576).toFixed(1);

async function openUpdateDlg() {
  updShow('confirm', { busy: await api.busy_count() });
  $('#updatedlg').classList.remove('hidden');
}

function updShow(phase, d = {}) {
  updPhase = phase;
  const v = 'v' + update.latest;
  const put = (sel, text) => { $(sel).textContent = text; $(sel).classList.toggle('hidden', !text); };
  $('#upd-title').textContent = { confirm: `发现新版本 ${v}`, download: `正在下载 ${v}`, error: '更新失败', install: `正在安装 ${v}` }[phase];
  put('#upd-text', { confirm: `当前 v${state.version}。更新会下载新版本，装好后自动重新打开。`, error: d.message, install: '马上重新打开…' }[phase] || '');
  put('#upd-warn', phase === 'confirm' && d.busy ? `有 ${d.busy} 个任务正在进行，更新会中断它们。` : '');
  put('#upd-size', phase === 'download' ? '正在连接…' : '');
  $('#upd-bar').classList.toggle('hidden', phase !== 'download' && phase !== 'install');
  $('#upd-bar i').style.width = phase === 'install' ? '100%' : '0';
  const [a, b] = { confirm: ['稍后', '立即更新'], download: ['', '取消下载'], error: ['关闭', '重试'], install: ['', ''] }[phase];
  put('#upd-a', a);
  put('#upd-b', b);
  $('#upd-b').disabled = false;
  $('#upd-b').classList.toggle('primary', phase !== 'download');
}

function onUpdateEvent(kind, d) {
  if (kind === 'progress' && updPhase === 'download') {
    const pct = d.total ? Math.floor(d.done * 100 / d.total) : 0;
    $('#upd-bar i').style.width = pct + '%';
    $('#upd-size').textContent = d.total ? `${pct}%（${MB(d.done)} / ${MB(d.total)} MB）` : `${MB(d.done)} MB`;
  } else if (kind === 'error') updShow('error', d);
  else if (kind === 'cancelled') $('#updatedlg').classList.add('hidden');
  else if (kind === 'installing') updShow('install');
}

async function startUpdate() {
  updShow('download');
  const r = await api.start_update();
  if (r.page) $('#updatedlg').classList.add('hidden');  // 直接跑代码：打开了发布页
  else if (!r.ok) updShow('error', r);
}

function bindUpdate() {
  $('#update-pill').addEventListener('click', openUpdateDlg);
  $('#about-update').addEventListener('click', () => checkUpdate(true));
  $('#upd-a').addEventListener('click', () => $('#updatedlg').classList.add('hidden'));  // 稍后 / 关闭
  $('#upd-b').addEventListener('click', () => {
    if (updPhase !== 'download') { startUpdate(); return; }  // 立即更新 / 重试
    $('#upd-b').disabled = true;  // 取消下载：等后台停下来（cancelled 事件）再关
    api.cancel_update();
  });
  setTimeout(() => checkUpdate(false), 3000);  // 别和启动抢网络
}

function toast(text) {
  const t = $('#toast');
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('show'), 1800);
}

const notReady = () => toast('这个功能还在开发中');
const esc = t => String(t ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const noBrowser = () => !state.browsers.length;

// ---------------- 分段按钮 ----------------
function seg(el, value, onChange) {
  const set = v => $$('button', el).forEach(b => b.classList.toggle('on', b.dataset.v === v));
  set(value);
  $$('button', el).forEach(b => b.addEventListener('click', () => { set(b.dataset.v); onChange && onChange(b.dataset.v); }));
  return { get: () => $('button.on', el)?.dataset.v, set };
}

// ---------------- 设置 ----------------
async function saveSettings(patch) {
  state = await api.save_settings(patch);
  applyTheme();
}

function applyTheme() {
  const look = state.settings.appearance;
  if (look === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = look;
}

let lookSeg = null, nomarkSeg = null, stopNomarkWait = () => {};
function fillSettings() {
  const s = state.settings;
  const sel = $('#set-browser');
  sel.disabled = noBrowser();
  sel.innerHTML = noBrowser() ? '<option>没有找到 Edge 或 Chrome</option>'
    : state.browsers.map(b => `<option value="${b.key}">${b.name}</option>`).join('');
  if (!noBrowser()) sel.value = state.browser;
  $('#set-browser-hint').innerHTML = noBrowser() ? '下载网站内容要用浏览器，请先安装 ' + downloadLinks() : '切换后番茄达人中心需要重新登录';
  lookSeg.set(s.appearance);
  nomarkSeg.set(s.video.nomark ? 'on' : 'off');
  $('#set-output').value = state.paths.output_root;
  $('#set-data').value = state.paths.data_dir;
}

function downloadLinks() {
  return Object.entries(state.browser_links).map(([name, url]) => `<a href="#" data-url="${url}">${name}</a>`).join(' 或 ');
}

document.addEventListener('click', e => {
  const a = e.target.closest('[data-url]');
  if (a) { e.preventDefault(); api.open_url(a.dataset.url); }
});

function bindSettings() {
  $('#set-browser').addEventListener('change', e => saveSettings({ browser: e.target.value }));
  lookSeg = seg($('#set-look'), state.settings.appearance, v => saveSettings({ appearance: v }));
  // 豆包视频获取无水印版本：打开前先弹说明，确认了才真正打开；关闭不用确认
  nomarkSeg = seg($('#set-nomark'), state.settings.video.nomark ? 'on' : 'off', v => {
    if (v === 'off') { saveSettings({ video: { nomark: false } }); return; }
    nomarkSeg.set('off');
    $('#nomarkdlg').classList.remove('hidden');
    stopNomarkWait = countdown($('#nomark-go'), '我已了解，开启');  // 每次开启都要看满 15 秒
  });
  $('#nomark-cancel').addEventListener('click', () => { stopNomarkWait(); $('#nomarkdlg').classList.add('hidden'); });
  $('#nomark-go').addEventListener('click', async () => {
    $('#nomarkdlg').classList.add('hidden');
    await saveSettings({ video: { nomark: true } });
    nomarkSeg.set('on');
  });
  $('#set-output').addEventListener('change', e => saveSettings({ output_dir: e.target.value.trim() }));
  $('#set-output-pick').addEventListener('click', async () => {
    const d = await api.pick_folder($('#set-output').value);
    if (d) { $('#set-output').value = d; saveSettings({ output_dir: d }); }
  });
  $('#set-data-pick').addEventListener('click', async () => {
    const d = await api.pick_folder($('#set-data').value);
    if (d) { await saveSettings({ data_dir: d }); fillSettings(); }
  });
}

// ---------------- 工具页公共部分 ----------------
const SITE_NAME = { video: '视频', fanqie: '番茄达人中心' };

function fillSaveDir(tool) {
  $(`#${tool} [data-save-dir]`).value = state.paths[tool];
}

// ---------------- 没登录的对话框（只有番茄要登录；视频游客就能下）----------------
const SITES = ['video', 'fanqie'];
const LOGIN_SITES = ['fanqie'];
const loginOk = {};  // 每个网站：true / false / undefined（还没检测完）

function renderLoginDlg(tool, waiting) {
  const site = SITE_NAME[tool];
  $('#login-title').textContent = noBrowser() ? '先安装浏览器' : `登录${site}`;
  $('#login-text').innerHTML = noBrowser()
    ? `下载网站内容要用浏览器，没有找到 Edge 或 Chrome。请先安装 ${downloadLinks()}，装好后重新打开这个页面。`
    : waiting
      ? `在弹出的浏览器里登录${site}，登录后这里会自动继续。`
      : `下载要用你自己的${site}账号。点「去登录」会打开浏览器，在里面登录一次，以后就不用再登了。`;
  const go = $('#login-go');
  go.classList.toggle('hidden', noBrowser());
  go.disabled = !!waiting;
  go.textContent = waiting ? '等待登录…' : '去登录';
  $('#logindlg').dataset.tool = tool;
}

function openLoginDlg(tool) {
  renderLoginDlg(tool, false);
  $('#logindlg').classList.remove('hidden');
}

function bindLoginDlg() {
  $('#login-go').addEventListener('click', () => {
    const tool = $('#logindlg').dataset.tool;
    renderLoginDlg(tool, true);
    api.open_site(tool);
  });
  $('#login-back').addEventListener('click', () => { $('#logindlg').classList.add('hidden'); show('home'); });
}

function onLogin(tool, ok, waiting) {
  loginOk[tool] = ok;
  const dlg = $('#logindlg');
  const shown = !dlg.classList.contains('hidden') && dlg.dataset.tool === tool;
  if (ok && shown) { dlg.classList.add('hidden'); toast(`${SITE_NAME[tool]}已登录`); }
  else if (shown) renderLoginDlg(tool, waiting);
  else if (ok === false && !waiting && current === tool && LOGIN_SITES.includes(tool)) openLoginDlg(tool);
}

function renderLogin(tool, ok, waiting) {
  const page = $('#' + tool);
  if (!LOGIN_SITES.includes(tool)) {  // 视频：不用登录，右上角只是打开浏览器（取当前页用）
    $('[data-login-state]', page).innerHTML = ok ? '<i class="dot on"></i>已登录' : '';
    $('[data-login-btn]', page).innerHTML = noBrowser() ? '安装浏览器' : '在浏览器打开<svg class="i"><use href="#i-down"/></svg>';
    $('#open-menu').classList.toggle('hidden', noBrowser());
    const side = $(`[data-login-side="${tool}"]`);
    if (side) side.innerHTML = ok ? '<i class="dot on"></i>已登录' : '免登录';
    return;
  }
  const text = noBrowser() ? '没有找到浏览器' : waiting ? '在弹出的窗口里登录…' : ok === false ? '未登录' : '检测中…';
  $('[data-login-state]', page).innerHTML = ok ? '<i class="dot on"></i>已登录' : `<i class="dot"></i><span class="muted">${text}</span>`;
  $('[data-login-btn]', page).textContent = noBrowser() ? '安装浏览器' : ok ? `打开${SITE_NAME[tool]}` : '去登录';
  const side = $(`[data-login-side="${tool}"]`);
  if (side) side.innerHTML = ok ? '<i class="dot on"></i>已登录' : `<i class="dot"></i>${ok === false ? '未登录' : '未检测'}`;
}

function log(tool, text, clear, html) {
  const box = $(`#${tool} [data-log]`);
  if (clear || box.querySelector('.muted:only-child')) box.innerHTML = '';
  const t = new Date().toTimeString().slice(0, 8);
  box.insertAdjacentHTML('beforeend', `<div><span class="t">${t}</span>${html ? text : esc(text)}</div>`);
  box.scrollTop = box.scrollHeight;
}

function needBrowser(tool) {
  if (!noBrowser()) return false;
  log(tool, '下载网站内容要用浏览器，没有找到 Edge 或 Chrome。请先安装 ' + downloadLinks() + '，装好后回到这里即可', false, true);
  return true;
}

const running = {};

function setRunning(tool, on) {
  running[tool] = on;
  const b = $(`#${tool} [data-run]`);
  b.textContent = on ? '停止' : '下载';
  b.classList.toggle('primary', !on);
  b.classList.toggle('danger', on);
}

const prog = {};   // 每个工具：已完成 i / 共 n 个

function setProgress(tool, i, n) {
  prog[tool] = { i, n };
  $(`#${tool} [data-bar]`).style.width = (n ? i / n * 100 : 0) + '%';
  $(`#${tool} [data-pct]`).textContent = n ? `${i} / ${n}` : '';
}

const mb = b => (b / 1048576).toFixed(1);
const UNIT = { fanqie: '集' };

// 正在下载的这一个文件：进度条按「已完成 + 当前文件的比例」走，右侧显示大小和速度
function setTransfer(tool, d) {
  const { i, n } = prog[tool] || { i: 0, n: 0 };
  if (!n) return;
  const frac = d.size ? d.got / d.size : 0;
  $(`#${tool} [data-bar]`).style.width = ((i + frac) / n * 100) + '%';
  const size = d.size ? `${mb(d.got)} / ${mb(d.size)} MB` : `${mb(d.got)} MB`;
  const which = n > 1 ? `第 ${Math.min(i + 1, n)} / ${n} ${UNIT[tool]}　` : '';
  $(`#${tool} [data-pct]`).textContent = `${which}${size}　${mb(d.speed)} MB/s`;
}

function showResult(tool, r) {
  const box = $(`#${tool} [data-result]`);
  box.classList.toggle('hidden', !r);
  if (!r) { box.innerHTML = ''; return; }
  const unit = r.unit || '章';
  const tail = (r.merged ? '，已合并成一个文件' : '') + (r.stopped ? '，已停止' : '');
  box.innerHTML = `<div class="file"><b>${esc(r.book_name)}</b><span class="small muted">成功 ${r.ok} ${unit}，失败 ${r.fail} ${unit}${tail}</span></div>`
    + '<button class="btn" data-open>打开文件夹</button>';
  $('[data-open]', box).addEventListener('click', () => api.open_folder(r.file || r.out_dir));
}

function startJob(tool, text, run) {
  log(tool, text, true);
  setProgress(tool, 0, 0);
  showResult(tool, null);
  setRunning(tool, true);
  run();
}

function bindRun(tool, run, canStart) {
  $(`#${tool} [data-run]`).addEventListener('click', () => {
    if (running[tool]) { api.stop(tool); log(tool, '正在停止…'); }
    else if (canStart()) startJob(tool, '开始下载', run);
  });
}

function bindTool(tool, run) {
  const page = $('#' + tool);
  renderLogin(tool, null);
  $('[data-login-btn]', page).addEventListener('click', () => {
    if (noBrowser()) api.open_url(Object.values(state.browser_links)[0]);
    else api.open_site(tool);
  });
  $('[data-grab]', page).addEventListener('click', () => needBrowser(tool) || api.grab(tool));
  bindRun(tool, run, () => !needBrowser(tool));
  const dir = $('[data-save-dir]', page);
  dir.addEventListener('change', () => saveSettings({ [tool]: { output_dir: dir.value.trim() } }).then(() => fillSaveDir(tool)));
  $('[data-pick-dir]', page).addEventListener('click', async () => {
    const d = await api.pick_folder(dir.value);
    if (d) { await saveSettings({ [tool]: { output_dir: d } }); fillSaveDir(tool); }
  });
}

// ---------------- 番茄达人中心 ----------------
let fq = {};
let fqTotal = null;   // 剧的总集数，查到后才知道

// 和 Python 里的 check_episodes 规则一致
function checkEpisodes(start, end, total) {
  if (!Number.isInteger(start) || start < 1) return '起始集数要从 1 开始';
  if (end !== null && end < start) return '结束集数不能比起始集数小';
  if (total !== null && (end ?? start) > total) return `这部剧只有 ${total} 集`;
  return '';
}

const KIND_NAME = { novel: '小说', drama: '短剧', comic: '漫剧' };

function fqSetKind(kind) {
  fq.kind.set(kind);
  fqShowKind(kind);
  saveSettings({ fanqie: { kind } });
}

function fqShowKind(kind) {
  const novel = kind === 'novel';
  $$('#fanqie .novel-only').forEach(e => e.classList.toggle('hidden', !novel));
  $$('#fanqie .video-only').forEach(e => e.classList.toggle('hidden', novel));
  fqCheck();
}

function fqCheck() {
  const video = fq.kind.get() !== 'novel';
  const range = fq.ep.get() === 'range';
  const from = $('#fq-ep-from'), to = $('#fq-ep-to');
  from.disabled = to.disabled = !range;
  const start = from.value === '' ? null : +from.value;
  const end = to.value === '' ? null : +to.value;
  const msg = video && range ? checkEpisodes(start, end, fqTotal) : '';
  from.classList.toggle('bad', !!msg && (start === null || start < 1));
  to.classList.toggle('bad', !!msg && end !== null && (end < (start ?? 1) || (fqTotal !== null && end > fqTotal)));
  $('#fq-ep-error').textContent = msg;
  $$('#fanqie .error-slot, #fq-ep-error').forEach(e => e.classList.toggle('hidden', !msg));
  $('#fanqie [data-run]').disabled = !!msg;
}

function bindFanqie() {
  const s = state.settings.fanqie;
  fq.kind = seg($('#fq-kind'), s.kind, v => { fqShowKind(v); saveSettings({ fanqie: { kind: v } }); });
  fq.scope = seg($('#fq-scope'), s.scope, v => saveSettings({ fanqie: { scope: v } }));
  fq.ep = seg($('#fq-ep-mode'), 'all', fqCheck);
  fq.mode = seg($('#fq-mode'), s.save_mode, v => saveSettings({ fanqie: { save_mode: v } }));
  const delay = $('#fq-delay');
  delay.value = s.delay;
  delay.addEventListener('change', () => {
    const v = parseFloat(delay.value);
    if (Number.isFinite(v) && v >= 1) saveSettings({ fanqie: { delay: v } });  // 最少隔 1 秒，见 fanqie.MIN_DELAY
    else delay.value = state.settings.fanqie.delay;
  });
  ['#fq-ep-from', '#fq-ep-to'].forEach(id => $(id).addEventListener('input', fqCheck));
  fqShowKind(s.kind);
}

function fqRun() {
  const kind = fq.kind.get();
  const range = kind !== 'novel' && fq.ep.get() === 'range';
  api.fanqie_download({
    book_id: $('#fq-id').value.trim(), kind,
    start: range ? $('#fq-ep-from').value : null,
    end: range ? $('#fq-ep-to').value : null,
  });
}

// ---------------- 视频 ----------------
// 后台每条任务有变化就推 task 事件，删掉推 task_remove
const tasks = new Map();
const ACTIVE = ['parsing', 'waiting', 'downloading', 'merging'];
const fsize = b => b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';
const ICON = (name, title, op) => `<button class="icon-btn" title="${title}" data-op="${op}"><svg class="i"><use href="#i-${name}"/></svg></button>`;

function taskState(t) {
  if (t.status === 'downloading') {
    const pct = t.total ? Math.floor(t.got / t.total * 100) : 0;
    const speed = t.speed ? `${fsize(t.speed)}/s` : '';
    return `<div class="prog"><b>${t.total ? pct + '%' : '下载中'}</b><span>${speed}</span></div>`
      + `<div class="bar"><i style="width:${pct}%"></i></div>`;
  }
  if (t.status === 'done') return `<b>已完成</b>${t.size ? fsize(t.size) : ''}`;
  if (t.status === 'failed' && t.wall) return `失败：${esc(t.wall)}要求登录`;
  if (t.status === 'failed') return `失败：${esc(t.error)}`;
  return { parsing: '解析中…', waiting: '排队中', merging: '合并画面和声音', stopped: '已停止' }[t.status] || '';
}

function taskOps(t) {
  if (ACTIVE.includes(t.status)) return ICON('stop', '停止', 'stop') + ICON('close', '删除', 'remove');
  if (t.status === 'done') return ICON('play', '打开', 'open') + ICON('folder', '打开所在文件夹', 'folder') + ICON('close', '从列表删除（文件保留）', 'remove');
  if (t.wall) {  // 要求登录：建议登录；换游客身份也行，但有风险
    return ICON('user', `登录${t.wall}（建议）：登好了点重试`, 'login')
      + ICON('guest', `换个游客身份重试：换得多了可能被限制 IP 或要求验证，到时只能登录`, 'new_guest')
      + ICON('retry', '重试', 'retry') + ICON('close', '删除', 'remove');
  }
  return ICON('retry', '重试', 'retry') + ICON('external', '在浏览器打开', 'browse') + ICON('close', '删除', 'remove');
}

function renderTask(t) {
  const old = tasks.get(t.id);
  tasks.set(t.id, t);
  let row = $(`#tasks [data-id="${t.id}"]`);
  if (!row) {
    row = document.createElement('div');
    row.className = 'task';
    row.dataset.id = t.id;
    row.innerHTML = '<img class="cover" alt="" referrerpolicy="no-referrer"><div class="info"><div class="title"><span class="tag"></span><b></b></div>'
      + '<div class="meta"></div></div><div class="state"></div><div class="ops"></div>';
    $('#tasks').prepend(row);
    $('.cover', row).addEventListener('error', e => {  // 加载不了就只留灰底，也别每次刷新都再去要
      e.target.dataset.failed = e.target.getAttribute('src');
      e.target.removeAttribute('src');
    });
  }
  const cover = $('.cover', row);
  if (t.cover && cover.getAttribute('src') !== t.cover && cover.dataset.failed !== t.cover) cover.src = t.cover;
  $('.tag', row).textContent = t.platform || '链接';
  $('.title b', row).textContent = t.title || t.link;
  $('.title b', row).title = t.title || t.link;
  const meta = [t.author, t.quality, t.note].filter(Boolean).join('，');
  $('.meta', row).textContent = meta;
  $('.meta', row).title = meta;
  const state = $('.state', row);
  state.innerHTML = taskState(t);
  state.classList.toggle('bad', t.status === 'failed');
  state.title = t.wall ? `${t.wall}要求登录才能看。建议登录账号；也可以换个游客身份重试，但换得多了可能被限制 IP 或要求验证，到时只能登录。`
    : t.status === 'failed' ? t.error : '';
  if (!old || old.status !== t.status) $('.ops', row).innerHTML = taskOps(t);
  renderEmpty();
}

function removeTask(id) {
  tasks.delete(id);
  $(`#tasks [data-id="${id}"]`)?.remove();
  renderEmpty();
}

function renderEmpty() {
  const empty = $('#tasks .task-empty');
  if (tasks.size && empty) empty.remove();
  if (!tasks.size && !empty) {
    $('#tasks').innerHTML = '<div class="task-empty">粘贴分享口令或链接，回车解析，解析完自动下载<br>支持抖音、快手、小红书、哔哩哔哩、豆包</div>';
  }
}

async function addLinks(text, fromInput = true) {
  if (!text.trim()) { toast('先粘贴分享口令或链接'); return; }
  const r = await api.video_add(text);  // 解析对话框由 parse_start 事件打开
  if (!r.ok) { toast(r.message); return; }
  if (fromInput) $('#video-link').value = '';  // 解析当前页不动输入框里的内容
}

// ---------------- 解析对话框 ----------------
// 后台推 parse_start → parse_step / parse_item / parse_error → parse_done
let parsing = null;  // { n, items, errors, done }

function onParse(kind, d) {
  const list = $('#parse-list');
  if (kind === 'parse_start') {
    parsing = { n: d.n, items: 0, errors: 0, done: false };
    list.innerHTML = '';
    $('#parse-sub').textContent = '正在解析…';
    $('#parse-bar').style.width = '0%';
    parseBusy(true);
    $('#parse-all-row').classList.add('hidden');
    $('#parse-go').classList.remove('hidden');
    $('#parse-cancel').textContent = '取消';
    updateParseGo();
    $('#parsedlg').classList.remove('hidden');
  } else if (!parsing) {
    return;
  } else if (kind === 'parse_step') {
    if (parsing.reparse) return;  // 只重新解析一条：保持「正在重新解析…」，进度条不往回跳
    $('#parse-sub').textContent = parsing.n > 1 ? `正在解析 ${d.i} / ${d.n}` : '正在解析…';
    $('#parse-bar').style.width = ((d.i - 1) / d.n * 100) + '%';
  } else if (kind === 'parse_item') {
    parsing.items++;
    const sec = Math.round(d.duration || 0);
    const pics = d.images || [];
    const kind = pics.length ? `图文 ${pics.length} 张` : (sec ? `${sec} 秒` : '');
    const meta = [d.platform, d.author, kind, d.quality].filter(Boolean).join('，');
    list.insertAdjacentHTML('beforeend', `<label class="parse-row"><input type="checkbox" value="${esc(d.key)}" checked>`
      + `<img alt="" referrerpolicy="no-referrer"${d.cover ? ` src="${esc(d.cover)}"` : ''}>`
      + `<span class="t"><b>${esc(d.title || d.key)}</b><span>${esc(meta)}</span></span></label>`);
    if (d.text) list.insertAdjacentHTML('beforeend', `<div class="parse-text">${esc(d.text.replace(/\s+/g, ' '))}</div>`);
    if (pics.length > 1) {
      list.insertAdjacentHTML('beforeend', `<div class="thumbs" data-of="${esc(d.key)}">`
        + pics.map((u, i) => `<label title="第 ${i + 1} 张"><input type="checkbox" data-i="${i}" checked>`
          + `<img alt="" loading="lazy" referrerpolicy="no-referrer" src="${esc(u)}"></label>`).join('') + '</div>');
    }
    list.querySelectorAll('img:not([data-err])').forEach(img => {
      img.dataset.err = '1';
      img.addEventListener('error', () => img.removeAttribute('src'));
    });
    parsing.pics = (parsing.pics || 0) + pics.length;
    updateParseGo();
  } else if (kind === 'parse_error') {
    parsing.errors++;
    if (d.wall) {  // 游客看多了要求登录：让用户自己选
      list.insertAdjacentHTML('beforeend', `<div class="parse-row bad wall" data-link="${esc(d.link)}"><img alt="">`
        + `<span class="t"><b>${esc(d.link)}</b><span>${esc(d.wall)}要求登录才能看。建议登录账号；`
        + `也可以换个游客身份继续，但换得多了可能被限制 IP 或要求验证，到时只能登录。</span></span>`
        + `<button class="btn sm" data-login="${esc(d.wall)}">登录${esc(d.wall)}</button>`
        + `<button class="btn sm" data-reparse title="登录好了，重新解析">重试</button>`
        + `<button class="btn sm" data-reparse="guest">换个游客身份</button></div>`);
      return;
    }
    list.insertAdjacentHTML('beforeend', `<div class="parse-row bad"><img alt="">`
      + `<span class="t"><b>${esc(d.link)}</b><span>${esc(d.error)}</span></span>`
      + `<button class="icon-btn" title="在浏览器打开" data-browse="${esc(d.link)}"><svg class="i"><use href="#i-external"/></svg></button></div>`);
  } else if (kind === 'parse_done') {
    parsing.done = true;
    parsing.reparse = false;
    parseBusy(false);
    $('#parse-bar').style.width = '100%';
    const keys = parseChecked();
    if (parsing.items === 1 && !parsing.errors && parsing.pics <= 1) {  // 只有一个视频（或一张图），直接下
      api.video_download(keys);
      closeParse();
      return;
    }
    const tail = parsing.errors ? `，${parsing.errors} 个链接没解析出来` : '';
    $('#parse-sub').textContent = parsing.items ? `解析出 ${parsing.items} 个作品${tail}，勾选要下载的` : `没有解析出作品${tail}`;
    $('#parse-all-row').classList.toggle('hidden', parsing.items < 2);
    $('#parse-go').classList.toggle('hidden', !parsing.items);
    $('#parse-cancel').textContent = parsing.items ? '取消' : '关闭';
    updateParseGo();
  }
}

function parseChecked() { return $$('#parse-list .parse-row > input:checked').map(c => c.value); }

// 图文选了哪几张：{作品: [下标]}
function parsePicks() {
  const picks = {};
  $$('#parse-list .thumbs').forEach(box => { picks[box.dataset.of] = $$('input:checked', box).map(c => +c.dataset.i); });
  return picks;
}

function updateParseGo() {
  const n = parseChecked().length, all = $$('#parse-list .parse-row > input').length;
  $('#parse-all').checked = n === all;
  $('#parse-go').textContent = n ? `下载所选（${n}）` : '下载所选';
  $('#parse-go').disabled = !n || !parsing || !parsing.done;
}

// 解析一个链接要开网页，可能十几秒；这期间进度条来回滑，不然像卡住了
function parseBusy(on) {
  $('#parse-bar').parentElement.classList.toggle('busy', on);
}

function closeParse() {
  parsing = null;
  parseBusy(false);
  $('#parsedlg').classList.add('hidden');
}

function bindParseDlg() {
  $('#parse-list').addEventListener('change', e => {
    const box = e.target.closest('.thumbs');
    const row = box ? $(`#parse-list .parse-row > input[value="${CSS.escape(box.dataset.of)}"]`) : null;
    if (box) row.checked = $$('input:checked', box).length > 0;  // 勾了图就算选了这篇，一张都不勾就不下
    else if (e.target.matches('.parse-row > input')) {
      const thumbs = $(`#parse-list .thumbs[data-of="${CSS.escape(e.target.value)}"]`);
      thumbs?.querySelectorAll('input').forEach(c => { c.checked = e.target.checked; });
    }
    updateParseGo();
  });
  $('#parse-list').addEventListener('click', e => {
    const b = e.target.closest('[data-browse]');
    if (b) openInBrowser(b.dataset.browse);
    const login = e.target.closest('[data-login]');
    if (login) loginPlatform(login.dataset.login);
    const again = e.target.closest('[data-reparse]');
    if (again && parsing) {  // 只重新解析这一条；换游客身份要先清掉这个平台的 cookie
      if (!parsing.done) { toast('等这次解析完再重试'); return; }
      const row = again.closest('.parse-row');
      api.video_reparse(row.dataset.link, again.dataset.reparse === 'guest');
      row.remove();
      parsing.errors--;
      parsing.done = false;
      parsing.reparse = true;
      parseBusy(true);
      $('#parse-sub').textContent = '正在重新解析…';
      $('#parse-cancel').textContent = '取消';
      updateParseGo();
    }
  });
  $('#parse-all').addEventListener('change', e => {
    $$('#parse-list input').forEach(c => { c.checked = e.target.checked; });  // 连图一起
    updateParseGo();
  });
  $('#parse-go').addEventListener('click', () => { api.video_download(parseChecked(), parsePicks()); closeParse(); });
  $('#parse-cancel').addEventListener('click', () => {
    if (parsing && !parsing.done) api.video_parse_cancel();
    closeParse();
  });
}

function loginPlatform(name) {
  if (noBrowser()) { toast('先安装 Edge 或 Chrome'); return; }
  const p = state.video_platforms.find(x => x.name === name);
  api.open_site('video', p ? p.home : '');
  toast(`在打开的浏览器里登录${name}，登好了回来重试`);
}

// 失败的链接在采风的浏览器里打开，看看是删了、要登录还是要验证；处理好了回来重试
function openInBrowser(link) {
  if (noBrowser()) { toast('先安装 Edge 或 Chrome'); return; }
  api.open_site('video', link);
  toast('已在浏览器里打开');
}

function fillVideoDir() {
  $('#video-dir').textContent = state.paths.video;
  $('#video-dir').title = state.paths.video;
}

async function bindVideo() {
  renderLogin('video', null);
  $('#video [data-login-btn]').addEventListener('click', () => {
    if (noBrowser()) api.open_url(Object.values(state.browser_links)[0]);  // 有浏览器时点按钮本身不做事，选下拉里的平台
  });
  $('#open-menu').innerHTML = state.video_platforms.map(p => `<button data-home="${esc(p.home)}">${esc(p.name)}</button>`).join('');
  $('#open-menu').addEventListener('click', e => {
    const b = e.target.closest('[data-home]');
    if (!b) return;
    b.blur();  // 收起菜单
    document.activeElement?.blur();
    api.open_site('video', b.dataset.home);
  });
  $('#video [data-grab]').addEventListener('click', () => {
    if (noBrowser()) toast('先安装 Edge 或 Chrome');
    else api.grab('video');
  });
  $('#video-add').addEventListener('click', () => addLinks($('#video-link').value));
  $('#video-clip').addEventListener('click', async () => {
    const r = await api.video_add_clipboard();
    if (!r.ok) toast(r.message);
  });
  $('#video-link').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); addLinks(e.target.value); }
  });
  $('#video-dir-pick').addEventListener('click', async () => {
    const d = await api.pick_folder(state.paths.video);
    if (d) { await saveSettings({ video: { output_dir: d } }); fillVideoDir(); }
  });
  $('#video-dir-open').addEventListener('click', async () => {
    if (!await api.open_folder(state.paths.video)) toast('还没有下载过，文件夹还没建');
  });
  $('#video-clear').addEventListener('click', () => api.video_clear_done());
  $('#tasks').addEventListener('click', e => {
    const b = e.target.closest('[data-op]');
    if (!b) return;
    const t = tasks.get(b.closest('.task').dataset.id);
    const op = b.dataset.op;
    if (op === 'open') api.open_file(t.file).then(ok => ok || toast('文件不在了'));
    else if (op === 'folder') api.open_folder(t.file).then(ok => ok || toast('文件不在了'));
    else if (op === 'browse') openInBrowser(t.link);
    else if (op === 'login') loginPlatform(t.wall);
    else api.video_task(op, t.id);
  });
  const list = await api.video_tasks();
  list.sort((a, b) => a.added - b.added).forEach(renderTask);  // 逐个放到最上面，最后加的在顶上
  renderEmpty();
}

// ---------------- 后台推来的事件 ----------------
// Python 端调用 window.caifengEvent(站点, 事件名, 数据)
window.caifengEvent = (tool, kind, d) => {
  if (tool === 'update') { onUpdateEvent(kind, d); return; }
  if (kind === 'login') { renderLogin(tool, d.ok, d.waiting); onLogin(tool, d.ok, d.waiting); }
  else if (kind === 'grab') {
    if (d.id && tool === 'video') addLinks(d.id, false);
    else if (d.id) {
      $('#fq-id').value = d.id;
      const kind = tool === 'fanqie' && d.kind && d.kind !== fq.kind.get() ? d.kind : null;
      if (kind) fqSetKind(kind);  // 当前页是短剧/漫剧，类型跟着切
      toast('已取到当前页' + (kind ? `，类型切换为${KIND_NAME[kind]}` : ''));
    }
    else toast(d.message);
  }
  else if (kind === 'log') log(tool, d.text);
  else if (kind === 'progress') setProgress(tool, d.i, d.n);
  else if (kind === 'transfer') setTransfer(tool, d);
  else if (kind.startsWith('parse_')) onParse(kind, d);
  else if (kind === 'browser' && tool === 'video') {  // 窗口开着才能解析当前页
    const b = $('#video [data-grab]');
    b.disabled = !d.open;
    b.title = d.open ? '解析浏览器里正在看的视频' : '先用「在浏览器打开」打开一个平台';
  }
  else if (kind === 'task') renderTask(d);
  else if (kind === 'task_remove') removeTask(d.id);
  else if (kind === 'done') {
    setRunning(tool, false);
    showResult(tool, d);
    if (!d.stopped) $(`#${tool} [data-bar]`).style.width = '100%';  // 最后一次进度可能比 done 晚到
  }
  else if (kind === 'error' && tool === 'video') toast(d.message);  // 视频页没有日志框
  else if (kind === 'error') { log(tool, '出错了：' + d.message); if (!running[tool]) toast(d.message); }
  else if (kind === 'busy' && !d.on && running[tool]) setRunning(tool, false);  // 出错中断时也复位按钮
};

// ---------------- 首页风纹 ----------------
// 文字沿曲线排；曲线往下平移一点画成实线托着文字，长度到最后一个字后面，再接上旋
function placeWindCurl() {
  const path = $('#windpath'), line = $('#wind-line'), under = $('#wind-under'), curl = $('#wind-curl');
  if (!line.getNumberOfChars()) return;
  const total = path.getTotalLength();
  const len = Math.min(total, total * 0.02 + line.getComputedTextLength() + 8);  // 2% 是文字的 startOffset
  under.style.strokeDasharray = `${len} ${total}`;
  const p = path.getPointAtLength(len);
  curl.setAttribute('transform', `translate(${p.x} ${p.y + 9}) scale(.8)`);
}

// ---------------- 启动 ----------------
async function init() {
  state = await api.get_state();
  applyTheme();
  $$('[data-go]').forEach(b => b.addEventListener('click', () => show(b.dataset.go)));
  $$('[data-back]').forEach(b => b.addEventListener('click', () => show(previous === current ? 'home' : previous)));
  $$('[data-soon]').forEach(b => b.addEventListener('click', notReady));
  placeWindCurl();
  document.fonts?.ready.then(placeWindCurl);
  bindSettings();
  bindVideo();
  bindParseDlg();
  bindTool('fanqie', fqRun);
  bindFanqie();
  bindLoginDlg();
  bindNotice();
  bindUpdate();
  for (const site of LOGIN_SITES) {  // 视频不用登录，启动时不必为了检测登录在后台开浏览器
    if (noBrowser()) renderLogin(site, false);
    else api.check_login(site);
  }
  const page = location.hash.slice(1);
  show(page && $('#' + page) ? page : 'home');
}

// 每次启动都弹；第一次要看满 15 秒才能点同意，同意过以后就不用等
function bindNotice() {
  $('#notice-terms').innerHTML = $$('#about .disclaimer p, #about .disclaimer ol').map(e => e.outerHTML).join('');
  const ok = $('#notice-ok');
  ok.addEventListener('click', () => {
    if (!state.settings.notice_agreed) saveSettings({ notice_agreed: true });
    $('#noticedlg').classList.add('hidden');
  });
  $('#notice-no').addEventListener('click', () => api.quit());
  $('#noticedlg').classList.remove('hidden');
  if (!state.settings.notice_agreed) countdown(ok, '同意并继续');
}

// 按钮先灰着倒数 seconds 秒才能点（要求看完说明再确认）。返回一个函数，调用就停止倒数
function countdown(btn, label, seconds = 15) {
  let left = seconds, timer = 0;
  const tick = () => {
    btn.disabled = left > 0;
    btn.textContent = left > 0 ? `${label}（${left}）` : label;
    if (left-- > 0) timer = setTimeout(tick, 1000);
  };
  tick();
  return () => clearTimeout(timer);
}

class MockApi {
  constructor() {
    this.s = { browser: '', appearance: 'system', notice_agreed: false, output_dir: '', data_dir: '',
      video: { output_dir: '', nomark: false }, fanqie: { output_dir: '', kind: 'novel', scope: 'free', delay: 1, save_mode: 'split' } };
  }
  async get_state() {
    const root = this.s.output_dir || 'C:\\Users\\me\\Downloads\\采风';
    return { settings: this.s, version: 'dev', repo: 'https://github.com/mrxxaxx/caifeng',
      browsers: [{ key: 'edge', name: 'Microsoft Edge' }], browser: 'edge',
      video_platforms: [{ name: '抖音', home: 'https://www.douyin.com/' }, { name: '快手', home: 'https://www.kuaishou.com/new-reco' }],
      browser_links: { 'Microsoft Edge': 'https://www.microsoft.com/edge', 'Google Chrome': 'https://www.google.cn/chrome/' },
      paths: { output_root: root, data_dir: this.s.data_dir || 'C:\\Users\\me\\AppData\\Local\\caifeng',
        video: this.s.video.output_dir || root + '\\视频', fanqie: this.s.fanqie.output_dir || root + '\\番茄' } };
  }
  async save_settings(patch) {
    for (const [k, v] of Object.entries(patch)) {
      if (v && typeof v === 'object') Object.assign(this.s[k], v); else this.s[k] = v;
    }
    return this.get_state();
  }
  async pick_folder() { return null; }
  async open_url() {}
  quit() { document.body.innerHTML = ''; }
  async open_folder() {}
  check_login(site) { setTimeout(() => caifengEvent(site, 'login', { ok: false }), 300); }
  open_site(site) { caifengEvent(site, 'login', { ok: false, waiting: true }); }
  grab(site) { caifengEvent(site, 'grab', site === 'fanqie' ? { id: '7404417471504976921', kind: 'drama' } : { id: 'https://www.douyin.com/video/7694146838416779944' }); }
  stop() {}
  fanqie_download() { this.fail('fanqie'); }
  async video_tasks() { return []; }
  async video_add() { return { ok: false, n: 0, message: '预览里不能下载' }; }
  async video_add_clipboard() { return this.video_add(); }
  video_parse_cancel() {}
  video_download() {}
  video_task() {}
  video_clear_done() {}
  async open_file() { return false; }
  async check_update() { return { ok: true, newer: true, latest: '0.3.0', url: 'https://github.com/mrxxaxx/caifeng/releases', message: '有新版本 v0.3.0', assets: {} }; }
  async busy_count() { return 2; }
  async start_update() {  // 预览：假装下载 70 MB
    const total = 73400320;
    let done = 0;
    this.updTimer = setInterval(() => {
      done = Math.min(total, done + 5242880);
      caifengEvent('update', 'progress', { done, total });
      if (done === total) { clearInterval(this.updTimer); caifengEvent('update', 'installing', {}); }
    }, 300);
    return { ok: true, message: '', page: false };
  }
  cancel_update() { clearInterval(this.updTimer); setTimeout(() => caifengEvent('update', 'cancelled', {}), 200); }
  fail(site) { caifengEvent(site, 'error', { message: '预览里不能下载' }); caifengEvent(site, 'busy', { on: false }); }
}

// 软件里页面由本机 http 服务提供，永远等 pywebview；只有直接双击打开 html 文件（file:）时才用假接口
if (location.protocol === 'file:') { api = new MockApi(); init(); }
else if (window.pywebview?.api) { api = window.pywebview.api; init(); }
else window.addEventListener('pywebviewready', () => { api = window.pywebview.api; init(); }, { once: true });
