/* K1 app v0 · app.js
   The whole page. No framework and no build step: the brief asks for a page that can be
   read straight through, and this is small enough for that.

   HOW IT HANGS TOGETHER
     S            all the state, in one object, so there is one place to look
     api.*        the JSON routes; everything the page knows comes from them
     el()         builds DOM nodes. The page never assembles HTML from strings, so a
                  headline containing a < or an & is text and can never be markup.
     render()     draws the screen S says we are on; every screen is a function below
   Every clickable thing is a real <button> or <a href> (acceptance check 3), so the
   keyboard and assistive technology get it for nothing.
*/

'use strict';

/* ------------------------------------------------------------------ the state */

const S = {
  flags: {},
  knobs: {},
  screen: 'today',
  ref: 'latest',
  digest: null,
  digests: [],
  status: null,
  cost: null,
  read: {},              /* text_hash -> read_at | null */
  follows: [],
  boards: [],
  prefs: {},
  threads: {},           /* thread_id -> thread | null (null = this story has none) */
  openStoryId: null,
  lastVisit: null,
  openedAStory: false,
};

/* ------------------------------------------------------------------- the routes */

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) return null;
  return r.json();
}

async function postJSON(url, body, method) {
  const r = await fetch(url, {
    method: method || 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) return null;
  return r.json();
}

const api = {
  flags: () => getJSON('/api/flags'),
  status: () => getJSON('/api/status'),
  cost: () => getJSON('/api/cost'),
  digest: (ref) => getJSON('/api/digest?ref=' + encodeURIComponent(ref || 'latest')),
  digests: () => getJSON('/api/digests'),
  article: (h) => getJSON('/api/article/' + encodeURIComponent(h)),
  thread: (id) => getJSON('/api/thread/' + encodeURIComponent(id)),
  readState: (hashes) => postJSON('/api/read_state', { text_hashes: hashes }),
  markRead: (hashes) => postJSON('/api/read', { text_hashes: hashes }),
  keepUnread: (storyId, hashes, on) =>
    postJSON('/api/keep_unread', { story_id: storyId, text_hashes: hashes, on: on }),
  follow: (storyId, hashes, on) =>
    postJSON('/api/follow', { story_id: storyId, text_hashes: hashes, on: on }),
  follows: () => getJSON('/api/follows'),
  boards: () => getJSON('/api/boards'),
  newBoard: (name) => postJSON('/api/boards', { name: name }),
  pin: (boardId, itemType, payload, source) =>
    postJSON('/api/pin', { board_id: boardId, item_type: itemType, payload: payload,
                           source: source }),
  unpin: (pinId) => postJSON('/api/pin/' + encodeURIComponent(pinId), undefined, 'DELETE'),
  boardPins: (boardId) => getJSON('/api/boards/' + encodeURIComponent(boardId) + '/pins'),
  feedback: (kind, id, hashes, vote, note) =>
    postJSON('/api/feedback', { target_kind: kind, target_id: id, text_hashes: hashes,
                                vote: vote, note: note }),
  pref: (key) => getJSON('/api/pref/' + encodeURIComponent(key)),
  setPref: (key, value) =>
    postJSON('/api/pref/' + encodeURIComponent(key), { value: value }),
  visit: (ref, window_, opened) =>
    postJSON('/api/visit', { digest_ref: ref, window: window_, opened_story: opened }),
};

/* ------------------------------------------------------------------- building DOM */

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') throw new Error('the page never sets HTML from a string');
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === 'string' || typeof child === 'number'
      ? document.createTextNode(String(child)) : child);
  }
  return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

/* ------------------------------------------------------------------ small words */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/* Dates in the data are plain YYYY-MM-DD with no timezone, and a newspaper's date is a
   calendar date, not an instant — so they are split, never passed through Date parsing
   that would shift them by a day in some timezones. */
function parts(iso) {
  const [y, m, d] = String(iso || '').slice(0, 10).split('-').map(Number);
  return { y: y, m: m, d: d };
}

function dayName(iso) {
  const p = parts(iso);
  return DAYS[new Date(p.y, p.m - 1, p.d).getDay()];
}

function dateShort(iso) {               /* "11 Oct" */
  const p = parts(iso);
  return p.d + ' ' + MONTHS[p.m - 1];
}

function dateWithDay(iso) {             /* "Sun 11 Oct" */
  return dayName(iso) + ' ' + dateShort(iso);
}

/* The window as plain text (the brief drops the "Since last visit" chooser in v0).
   A week reads "Week 9-15 Oct"; a single day reads "Sun 11 Oct" (decision D4: the second
   digest's window is a day, not a week). */
function windowText(w) {
  if (!w) return '';
  const from = w.from, to = w.to;
  if (!from) return '';
  if (!to || to === from) return dateWithDay(from);
  const a = parts(from), b = parts(to);
  const left = a.m === b.m ? String(a.d) : dateShort(from);
  return 'Week ' + left + '–' + dateShort(to);
}

/* Paper codes for the chips (§8.4). Anything not named takes the first letters of its
   words, so a paper the list has never seen (Economic Times -> ET) still gets a code. */
const PAPER_CODES = {
  'Business Line': 'BL',
  'Business Standard': 'BS',
  'Mint': 'Mint',
  'The Hindu': 'TH',
  'Times of India': 'TOI',
};

function paperCode(paper) {
  if (!paper) return '';
  if (PAPER_CODES[paper]) return PAPER_CODES[paper];
  const words = String(paper).replace(/_/g, ' ').split(/\s+/).filter(Boolean);
  const skip = new Set(['the', 'of', 'and']);
  const letters = words.filter((w) => !skip.has(w.toLowerCase()))
                       .map((w) => w[0].toUpperCase()).join('');
  return letters || String(paper).slice(0, 3);
}

function money(usd) {
  if (usd === null || usd === undefined) return '';
  if (usd === 0) return '0¢';
  const cents = usd * 100;
  if (cents < 1) return '<1¢';
  if (usd < 1) return Math.round(cents) + '¢';
  return '$' + usd.toFixed(2);
}

function timeOfDay(iso) {
  const m = String(iso || '').match(/T(\d{2}):(\d{2})/);
  if (!m) return '';
  let h = Number(m[1]);
  const suffix = h < 12 ? 'am' : 'pm';
  if (h === 0) h = 12; else if (h > 12) h -= 12;
  return h + '.' + m[2] + suffix;
}

/* --------------------------------------------------------------- stories, read state */

function stories() { return (S.digest && S.digest.stories) || []; }

function storyHashes(story) {
  return ((story.coverage && story.coverage.rows) || []).map((r) => r.text_hash);
}

/* §4: a story is READ when every one of its coverage hashes has a read_at. */
function isRead(story) {
  const hashes = storyHashes(story);
  return hashes.length > 0 && hashes.every((h) => Boolean(S.read[h]));
}

function isFollowed(story) {
  return S.follows.some((f) => f.story_id === story.story_id);
}

function findStory(storyId) {
  return stories().find((s) => s.story_id === storyId) || null;
}

/* §8.3: one stamp, three meanings, and READ overrides the other two. */
function stamp(story) {
  if (isRead(story)) return { kind: 'read', label: 'Read' };
  if (story.developing) return { kind: 'developing', label: 'Developing' };
  return { kind: 'new', label: 'New' };
}

/* §7: "N unread" counts the shown digest's top ten plus followed stories, not READ. */
function unreadCount() {
  const seen = new Set();
  let n = 0;
  for (const s of stories()) {
    seen.add(s.story_id);
    if (!isRead(s)) n += 1;
  }
  for (const f of S.follows) {
    if (!seen.has(f.story_id) && !f.text_hashes.every((h) => Boolean(S.read[h]))) n += 1;
  }
  return n;
}

/* ------------------------------------------------------------------- the header */

function renderHeader() {
  document.getElementById('window-text').textContent =
    S.digest ? windowText(S.digest.window) : '';

  const today = new Date();
  document.getElementById('today-date').textContent =
    dateWithDay(today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0')
                + '-' + String(today.getDate()).padStart(2, '0'));

  const n = unreadCount();
  document.getElementById('unread-count').textContent =
    n === 0 ? 'nothing unread' : n + ' unread';

  const costNode = document.getElementById('cost-header');
  if (!S.flags.cost_display || !S.cost) {
    costNode.hidden = true;
  } else {
    costNode.hidden = false;
    const spent = S.cost.spent_usd, cap = S.cost.cap_usd;
    const capped = cap !== null && cap !== undefined && spent >= cap;
    costNode.dataset.capped = capped ? 'true' : 'false';
    /* H5: at the cap the header says so instead of counting, and says nothing in red —
       a spent budget is not news. A digest with no cap knob shows the spend alone (D1). */
    costNode.textContent = capped
      ? 'Daily cap $' + Number(cap).toFixed(2) + ' reached · resumes tomorrow'
      : (cap === null || cap === undefined
          ? 'today ' + money(spent)
          : 'today ' + money(spent) + ' of $' + Number(cap).toFixed(2));
  }

  const status = document.getElementById('status-line');
  /* H1: the latest ready digest still shows normally; the line says what is late. */
  if (S.status && S.status.state === 'processing') {
    status.hidden = false;
    status.textContent = "Today's papers still processing · showing "
      + dateWithDay(S.status.latest_ready) + ' · updated '
      + timeOfDay(S.status.updated_at);
  } else {
    status.hidden = true;
    status.textContent = '';
  }

  for (const b of document.querySelectorAll('.nav-item')) {
    if (b.dataset.screen === S.screen) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  }
}

/* ------------------------------------------------------------------- the drawer */

function storyRow(story, number) {
  const st = stamp(story);
  return el('li', {},
    el('button', {
      class: 'reset story-row',
      type: 'button',
      'aria-current': story.story_id === S.openStoryId ? 'true' : null,
      onclick: () => openStory(story.story_id),
    },
      el('span', { class: 'row-number', text: number === null ? '' : String(number) }),
      el('span', { class: 'row-main' },
        el('span', { class: 'stamp', dataset: { stamp: st.kind }, text: st.label }),
        el('span', { class: 'row-headline', text: story.headline.text }))
    )
  );
}

function renderDrawer() {
  const drawer = document.getElementById('drawer');
  clear(drawer);

  /* H2: every story in the digest already read, so there is nothing new to point at. */
  if (stories().length && stories().every(isRead)) {
    drawer.append(el('p', { class: 'drawer-note', text:
      'Nothing new since ' + (S.lastVisit ? timeOfDay(S.lastVisit) : 'your last visit')
      + ' · next papers tomorrow morning' }));
  }

  /* The Followed strip comes first, and only when something is followed (§8.3). */
  const followed = stories().filter(isFollowed);
  if (followed.length) {
    drawer.append(el('p', { class: 'drawer-heading', text: 'Followed' }));
    drawer.append(el('ul', { class: 'story-list' },
      followed.map((s) => storyRow(s, null))));
  }

  drawer.append(el('p', { class: 'drawer-heading', text: 'Top ' + stories().length }));
  drawer.append(el('ul', { class: 'story-list' },
    stories().map((s) => storyRow(s, s.rank))));
}

/* The story page itself is A4. Until then the pane shows what the drawer selected, so
   the shell and the drawer can be seen working on real data. */
function renderStory(pane, story) {
  pane.append(el('h2', { class: 'story-headline', text: story.headline.text }));
  pane.append(el('p', { class: 'story-oneliner', text: story.one_liner.text }));
  pane.append(el('p', { class: 'drawer-note',
                        text: 'The story page arrives with step A4.' }));
}

/* ------------------------------------------------------------------- the screens */

function renderReading() {
  const pane = document.getElementById('reading');
  clear(pane);
  if (S.screen === 'today') {
    const story = findStory(S.openStoryId);
    if (story) renderStory(pane, story);
    else pane.append(el('p', { class: 'drawer-note', text: 'Choose a story.' }));
  }
}

function render() {
  renderHeader();
  renderDrawer();
  renderReading();
}

/* Opening a story: the read-on-leave rule and the rest of the reader's actions arrive
   with A6; A3 only moves which story is open. */
function openStory(storyId) {
  S.openStoryId = storyId;
  if (!S.openedAStory) {
    S.openedAStory = true;
    api.visit(S.ref, S.digest && S.digest.window, true);
  }
  render();
  document.getElementById('reading').scrollTop = 0;
}

/* --------------------------------------------------------------------- the boot */

async function boot() {
  const flags = await api.flags();
  S.flags = (flags && flags.flags) || {};
  S.knobs = (flags && flags.knobs) || {};

  const [status, digest, cost, follows] =
    await Promise.all([api.status(), api.digest(S.ref), api.cost(), api.follows()]);
  S.status = status;
  S.digest = digest;
  S.cost = cost;
  S.follows = (follows && follows.follows) || [];

  const hashes = [];
  for (const s of stories()) hashes.push(...storyHashes(s));
  for (const f of S.follows) hashes.push(...(f.text_hashes || []));
  if (hashes.length) {
    const read = await api.readState([...new Set(hashes)]);
    S.read = (read && read.read) || {};
  }

  /* The visit is read BEFORE it is recorded: once this visit is stored it is itself the
     last one, so "since last visit" would always mean "since a moment ago" (D10). */
  const visit = await api.visit(S.ref, S.digest && S.digest.window, false);
  S.lastVisit = visit && visit.last_visit;

  for (const b of document.querySelectorAll('.nav-item')) {
    b.addEventListener('click', () => { S.screen = b.dataset.screen; render(); });
  }

  /* §8.3: Today opens with the first story already open. */
  if (stories().length) S.openStoryId = stories()[0].story_id;
  render();
}

/* --------------------------------------------------------- reachable for testing

   The sandbox this was built in has no headless browser and cannot bind a port, so the
   brief expected the page's logic to go untested and be replaced by a click list for
   Kunaal. It need not be: macOS ships JavaScriptCore (`jsc`), which runs this file's
   pure functions with no browser and no install. tests/test_p3_app.py drives it, and
   skips that part if jsc is missing. Everything below is a function that takes data and
   returns a value -- the DOM-building ones still need the click list.
*/
const EXPORTS = {
  windowText, paperCode, money, timeOfDay, dateWithDay, dateShort, dayName,
  isRead, isFollowed, stamp, unreadCount, storyHashes, S,
};

if (typeof window !== 'undefined') {
  window.APP = EXPORTS;
  document.addEventListener('DOMContentLoaded', boot);
} else {
  globalThis.APP = EXPORTS;
}
