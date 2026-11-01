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
  newSince: {},          /* story_id -> [text_hash] that arrived since the last read */
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
  newSince: (storyId, ref) => getJSON('/api/new_since/' + encodeURIComponent(storyId)
                                      + '?ref=' + encodeURIComponent(ref || 'latest')),
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

/* "1 article from 1 paper", never "1 articles from 1 papers". The wording rule of §7 is
   that a paper is a newspaper and an article is one piece, so both are counted out loud
   wherever both matter -- which makes getting the singular right unavoidable. */
function count(n, word) {
  return n + ' ' + word + (n === 1 ? '' : 's');
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

/* =============================================================== the story page */

/* Every coverage row of the open story, by its text_hash, so a write-up sentence's
   citation can become a chip that names the paper and the page. */
function rowsByHash(story) {
  const map = new Map();
  for (const r of ((story.coverage && story.coverage.rows) || [])) map.set(r.text_hash, r);
  return map;
}

function chipLabel(row) {
  return paperCode(row.paper) + ' ' + (row.page_link ? row.page_link.label : '');
}

/* A chip under a write-up sentence: which paper said this, and on which page.
   Clicking it opens that article; hovering says what it is. */
function articleChip(row) {
  return el('button', {
    class: 'reset chip',
    type: 'button',
    title: row.headline + ' · ' + row.paper + ', '
           + (row.page_link ? row.page_link.label : ''),
    onclick: () => openArticle(row.text_hash),
  }, chipLabel(row));
}

/* A story-so-far sentence cites a DAY of the thread, not an article, so its chip says
   the day and takes you to that entry in the thread (§8.4). */
function dayChip(entryId) {
  const date = String(entryId).split(':')[0];
  return el('button', {
    class: 'reset chip',
    type: 'button',
    title: 'That day in the thread',
    onclick: () => goToThreadEntry(entryId),
  }, dateShort(date));
}

function chipsFor(cites, byHash) {
  const chips = [];
  for (const cite of (cites || [])) {
    const row = byHash.get(cite);
    if (row) chips.push(articleChip(row));
    else if (/^\d{4}-\d{2}-\d{2}:/.test(cite)) chips.push(dayChip(cite));
  }
  return chips.length ? el('span', { class: 'chips' }, chips) : null;
}

/* §8.4.1 — "DEVELOPING · DAY 5 · TAXES & BUDGET", or "NEW · TAXES & BUDGET". */
function labelLine(story) {
  const bits = [];
  if (story.developing) {
    bits.push('Developing');
    const days = (story.story_so_far || {}).days;
    if (days) bits.push('Day ' + days);
  } else {
    bits.push('New');
  }
  const topic = (story.theme || {}).topic;
  if (topic) bits.push(topic);
  return el('p', {
    class: 'label-line',
    dataset: { developing: story.developing ? 'true' : 'false' },
    text: bits.join(' · '),
  });
}

/* §8.4.2 — only the true ones, and each behind its own flag. */
const CAVEATS = [
  ['single_source', 'caveat_single_source', 'Single source'],
  ['paid_content', 'caveat_paid_content', 'Paid content'],
  ['paid_only', 'caveat_paid_only', 'Paid feature only'],
  ['partial_text', 'caveat_partial_text', 'Partial text'],
];

function caveatTags(story) {
  const tags = CAVEATS
    .filter(([key, flag]) => S.flags[flag] && (story.caveats || {})[key] === true)
    .map(([, , label]) => el('li', { class: 'caveat', text: label }));
  return tags.length ? el('ul', { class: 'caveats' }, tags) : null;
}

function metaLine(story) {
  const head = story.headline || {};
  const source = rowsByHash(story).get(head.source_text_hash);
  const cov = story.coverage || {};
  const bits = [];
  if (source) {
    bits.push('Headline from ' + source.paper + ', '
              + (source.page_link ? source.page_link.label : ''));
  }
  bits.push(count(cov.articles, 'article') + ' from ' + count(cov.papers, 'paper'));
  return el('div', { class: 'meta-line' },
    el('p', { class: 'meta-source', text: bits.join(' · ') }),
    /* The reader's own buttons (Keep unread, Follow, Pin, the votes) arrive with A6. */
    el('div', { class: 'meta-actions', id: 'meta-actions' }));
}

/* §8.4.5 — open the first time this story is seen, folded after that (a pref per
   story id, so the box does not re-open every morning on a story already caught up on). */
function storySoFar(story) {
  const ssf = story.story_so_far;
  if (!S.flags.story_so_far || !ssf || !ssf.shown) return null;

  const key = 'ssf_seen:' + story.story_id;
  const seen = Boolean(S.prefs[key]);
  const open = !seen;
  if (!seen) { S.prefs[key] = true; api.setPref(key, true); }

  const byHash = rowsByHash(story);
  const body = el('div', { class: 'ssf-body', id: 'ssf-body' },
    (ssf.sentences || []).map((s) =>
      el('p', {}, s.text, chipsFor(s.cites, byHash))),
    keyDates(ssf.key_dates));
  body.hidden = !open;

  const toggle = el('button', {
    class: 'reset ssf-toggle', type: 'button',
    'aria-expanded': open ? 'true' : 'false', 'aria-controls': 'ssf-body',
    text: open ? 'Hide' : 'Show',
    onclick: (e) => {
      const nowOpen = body.hidden;
      body.hidden = !nowOpen;
      e.currentTarget.textContent = nowOpen ? 'Hide' : 'Show';
      e.currentTarget.setAttribute('aria-expanded', nowOpen ? 'true' : 'false');
    },
  });

  return el('section', { class: 'story-so-far' },
    el('div', { class: 'ssf-head' },
      el('h3', { class: 'ssf-title', text: 'The story so far' }), toggle),
    body);
}

/* A key date still to come is red; one already past is ink (§8.4.5). "Today" is the
   reader's own date, computed here, because K3 does not mark which date is upcoming. */
function todayISO() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-'
         + String(d.getDate()).padStart(2, '0');
}

function keyDates(dates) {
  const some = (dates || []).slice(0, 3);
  if (!some.length) return null;
  const today = todayISO();
  return el('ul', { class: 'key-dates' }, some.map((k) =>
    el('li', { class: 'key-date', dataset: { upcoming: k.date > today ? 'true' : 'false' } },
      el('span', { class: 'key-date-when', text: dateWithDay(k.date) }),
      el('span', { class: 'key-date-what', text: k.what }))));
}

function newSinceBox(story) {
  const hashes = S.newSince[story.story_id] || [];
  if (!hashes.length) return null;
  const byHash = rowsByHash(story);
  return el('section', { class: 'new-since' },
    el('h3', { class: 'new-since-title', text: 'New since you read it' }),
    el('ul', {}, hashes.map((h) => {
      const row = byHash.get(h);
      if (!row) return null;
      return el('li', {}, row.headline, chipsFor([h], byHash));
    })));
}

/* §8.4.7 — the write-up, each sentence followed by the chips of what it cites.
   With no write-up, the one-liner stands in its place and says nothing about why. */
function writeup(story) {
  const w = story.writeup;
  const byHash = rowsByHash(story);
  if (!w || !w.sentences || !w.sentences.length) {
    const capped = S.cost && S.cost.cap_usd !== null && S.cost.cap_usd !== undefined
                   && S.cost.spent_usd >= S.cost.cap_usd;
    return el('div', { class: 'one-liner-only' },
      story.one_liner.text,
      /* H5: at the cap, a missing write-up is explained; otherwise it is not. */
      capped ? el('span', { class: 'writeup-note',
                            text: ' · write-up paused: daily cap' }) : null);
  }
  return el('div', { class: 'writeup' },
    w.sentences.map((s) => el('p', {}, s.text, chipsFor(s.cites, byHash))));
}

/* §8.4.9 — the opinion and analysis pieces beside the news.
   A view has no text_hash of its own (logged as R10), so it opens the article view only
   when the same piece is also a coverage row; otherwise it opens the printed page. */
function viewsLine(story) {
  const views = story.views || [];
  if (!views.length) return null;
  const rows = (story.coverage && story.coverage.rows) || [];
  return el('section', { class: 'views' },
    el('span', { class: 'views-label', text: 'Views' }),
    el('ul', {}, views.map((v) => {
      const match = rows.find((r) => r.paper === v.paper && r.date === v.date
                                     && r.page === v.page);
      const label = v.kind + ' · ' + paperCode(v.paper) + ' '
                    + (v.page_link ? v.page_link.label : '');
      const opener = match
        ? el('button', { class: 'reset link-like', type: 'button',
                         onclick: () => openArticle(match.text_hash) }, v.headline)
        : el('a', { href: pdfHref(v.page_link), target: '_blank',
                    rel: 'noopener' }, v.headline);
      return el('li', {}, label + ' — ', opener);
    })));
}

/* §8.4.10 — the trend word only when there is one; `no_trend_because` is never printed. */
function themeLine(story) {
  const theme = story.theme;
  if (!S.flags.theme_line || !theme) return null;
  const bits = [theme.stories_21d + ' stories in 21 days'];
  if (theme.trend) bits.push(theme.trend);
  return el('p', { class: 'theme-line' },
    el('span', { class: 'theme-topic', text: theme.topic }),
    ' · ' + bits.join(' · '));
}

function pdfHref(link) {
  if (!link || !link.pdf) return '#';
  const bits = link.pdf.split('/');            /* archive/<date>/<file> */
  return '/pdf/' + encodeURIComponent(bits[1]) + '/' + encodeURIComponent(bits[2])
         + '#page=' + link.pdf_page;
}

/* §8.4.12 — folded by default; a row that arrived since the last read gets a red dot. */
function coverage(story) {
  const cov = story.coverage || {};
  const rows = cov.rows || [];
  const newHashes = new Set(S.newSince[story.story_id] || []);

  const table = el('table', { class: 'coverage-table', id: 'coverage-table' },
    el('thead', {}, el('tr', {},
      el('th', { text: 'Paper' }), el('th', { text: 'Page' }), el('th', { text: 'Words' }),
      el('th', { text: 'Kind' }), el('th', { text: 'Byline' }))),
    el('tbody', {}, rows.map((r) => el('tr', {},
      el('td', {}, newHashes.has(r.text_hash)
          ? el('span', { class: 'row-new-dot', title: 'New since you read it',
                         text: '● ' }) : null, r.paper),
      el('td', {}, el('a', { href: pdfHref(r.page_link), target: '_blank',
                             rel: 'noopener' }, r.page_link ? r.page_link.label : '')),
      el('td', { text: r.words === null || r.words === undefined ? '' : String(r.words) }),
      el('td', { text: r.kind || '' }),
      el('td', { text: r.byline || '' })))));
  table.hidden = true;

  const toggle = el('button', {
    class: 'reset coverage-toggle', type: 'button',
    'aria-expanded': 'false', 'aria-controls': 'coverage-table',
    onclick: (e) => {
      const open = table.hidden;
      table.hidden = !open;
      e.currentTarget.setAttribute('aria-expanded', open ? 'true' : 'false');
      e.currentTarget.firstChild.textContent = coverageLabel(cov, open);
    },
  }, coverageLabel(cov, false));

  return el('section', { class: 'coverage' }, toggle, table);
}

function coverageLabel(cov, open) {
  return 'Coverage · ' + count(cov.articles, 'article') + ' from '
         + count(cov.papers, 'paper') + ' ' + (open ? '▾' : '▸');
}

function renderStory(pane, story) {
  pane.append(labelLine(story));
  const tags = caveatTags(story);
  if (tags) pane.append(tags);
  pane.append(el('h2', { class: 'story-headline', text: story.headline.text }));
  pane.append(metaLine(story));

  const ssf = storySoFar(story);
  if (ssf) pane.append(ssf);
  const since = newSinceBox(story);
  if (since) pane.append(since);

  pane.append(writeup(story));

  const views = viewsLine(story);
  if (views) pane.append(views);
  const theme = themeLine(story);
  if (theme) pane.append(theme);

  /* The thread section is A5; a story with no thread shows none at all (H3). */
  pane.append(coverage(story));
  pane.append(el('p', { class: 'page-footer',
    text: "Charts and photos aren't read. Use the page link to see them." }));
}

/* --------------------------------------------------------------- the article view */

async function openArticle(textHash) {
  const article = await api.article(textHash);
  const pane = document.getElementById('reading');
  clear(pane);
  if (!article) {
    pane.append(el('p', { class: 'drawer-note', text: 'That article could not be found.' }));
    return;
  }
  pane.append(el('button', {
    class: 'reset back-link', type: 'button',
    onclick: () => { render(); },
  }, '← Back to story'));

  pane.append(el('h2', { class: 'article-headline' }, article.headline,
    article.fixture ? el('span', { class: 'fixture-tag', text: 'sample data' }) : null));

  const bits = [article.paper, article.date ? dateWithDay(article.date) : null,
                article.page_link ? article.page_link.label : null]
    .filter(Boolean).join(' · ');
  pane.append(el('p', { class: 'article-meta' }, bits,
    article.byline ? ' · ' + article.byline : ''));

  pane.append(el('div', { class: 'article-body' },
    (article.paragraphs || []).map((p) => el('p', { text: p }))));

  pane.append(el('p', {},
    el('a', { href: pdfHref(article.page_link), target: '_blank', rel: 'noopener' },
      'Open page')));
  pane.scrollTop = 0;
}

/* Scrolling to a day of the thread is A5; until then the chip is inert but real. */
function goToThreadEntry(entryId) {
  const node = document.querySelector('[data-entry-id="' + CSS.escape(entryId) + '"]');
  if (node) {
    node.scrollIntoView({ block: 'center' });
    node.classList.add('entry-flash');
  }
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
async function openStory(storyId) {
  S.openStoryId = storyId;
  if (!S.openedAStory) {
    S.openedAStory = true;
    api.visit(S.ref, S.digest && S.digest.window, true);
  }

  /* Both of these decide what the story page shows, so they are fetched before it is
     drawn rather than appearing a moment later: what arrived since this story was last
     read (R4), and whether its "story so far" has been seen before. */
  const story = findStory(storyId);
  if (story && !(storyId in S.newSince)) {
    const since = await api.newSince(storyId, S.ref);
    S.newSince[storyId] = (since && since.new_since_read) || [];
  }
  const ssfKey = 'ssf_seen:' + storyId;
  if (story && (story.story_so_far || {}).shown && !(ssfKey in S.prefs)) {
    const pref = await api.pref(ssfKey);
    S.prefs[ssfKey] = pref ? pref.value : null;
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

  render();
  /* §8.3: Today opens with the first story already open. */
  if (stories().length) await openStory(stories()[0].story_id);
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
  windowText, paperCode, money, count, timeOfDay, dateWithDay, dateShort, dayName,
  isRead, isFollowed, stamp, unreadCount, storyHashes, S,
};

if (typeof window !== 'undefined') {
  window.APP = EXPORTS;
  document.addEventListener('DOMContentLoaded', boot);
} else {
  globalThis.APP = EXPORTS;
}
