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
  paneEvent: null,
  appVersion: null,    /* F3 · /api/status's app_version when this page loaded */     /* L-13 · what the next pane redraw is for (paneScrollRule) */
  /* --- v0.1, the ask pane --- */
  pane: 'open',                  /* open | collapsed | wide, remembered as a pref */
  conversation: null,            /* {conversation_id, started_at, turns} */
  scope: { story_id: null, text_hashes: [], filters: {} },
  scopeLabel: null,              /* what a filters-only scope chip says */
  results: {},                   /* answer_id -> its results event, this session only */
  passages: {},                  /* passage_set_id -> its rows, read once per id (I5) */
  live: null,                    /* the answer being streamed right now */
  sorts: {},                     /* answer_id -> 'relevance' | 'newest' */
  folded: {},                    /* answer_id -> true once Think harder answered */
  resultsOpen: {},               /* answer_id -> true: an older answer's results opened (F5) */
  estimates: {},                 /* kind -> cost_usd, read once at boot */
  reloadDivider: null,           /* "CONVERSATION FROM <Day HH:MM>" (state 8) */
  suggestions: {},               /* story_id -> its suggestion set, once asked for */
  suggesting: {},                /* story_id -> true while its suggest call runs (K-3) */
  backgroundBusy: {},            /* answer_id -> true while its background call runs */
  backgroundError: {},           /* answer_id -> {message} from a refused background */
  stillRunning: {},              /* answer_id -> true: its paid action runs from an earlier press (F6) */
  widened: null,                 /* answer_id just re-asked of all papers (K-6), until the next question */
  seeAlsoOpen: {},               /* story_id -> true while D1's fold is open; not a pref */
  userLinks: {},                 /* story_id -> the links the reader made out of it */
  pinnedStories: new Set(),      /* so a dark pin is dark again after a reload */
  pinIds: {},                    /* story_id -> pin_id, for unpinning from a dark pin */
  explore: null,                 /* the Explore screen's own query, filters and picks */
  facets: null,                  /* read once, when Explore is first opened */
  screen: 'today',
  ref: 'latest',
  digest: null,
  digests: [],
  isPast: false,         /* the loaded digest is not the newest of its own kind */
  view: 'day',           /* F2 · Day | Week, the pref 'digest_view' */
  status: null,
  cost: null,
  read: {},              /* text_hash -> read_at | null */
  follows: [],
  boards: [],
  prefs: {},
  threads: {},           /* thread_id -> thread | null (null = this story has none) */
  outside: {},           /* L-4 · story_id -> a story outside the digests in memory | null */
  newSince: {},          /* story_id -> [text_hash] that arrived since the last read */
  keepUnread: new Set(),  /* story ids the reader is holding unread */
  pins: [],
  votes: {},             /* "<kind>:<id>" -> the vote already given, so it can be shown */
  lastUndo: null,        /* the one step ⌘Z and the toast's Undo both run */
  openBoardId: null,
  openStoryId: null,
  lastVisit: null,
  openedAStory: false,
  reachable: true,       /* false once a request finds nothing answering at all */
  cursor: null,          /* where j/k are, which is not the same as what is open */
  articleOpen: false,
};

/* ------------------------------------------------------------------- the routes */

/* A route that answers 404 and a server that is not running are different failures, and
   neither may take the page down. `fetch` REJECTS when nothing answers -- it does not
   return a response -- so without this catch one unreachable route leaves the reader
   looking at an empty shell with no idea why. Both come back as null; S.reachable says
   which kind it was, and the page says so on screen. */
async function getJSON(url) {
  try {
    const r = await fetch(url);
    S.reachable = true;
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    S.reachable = false;
    return null;
  }
}

/* K-4 · what the page says when nothing answers at all. A missing article and a server
   that is not running are different failures, and the reader can fix only the second. */
const NOT_RUNNING = "The app isn't running. Click the News Intelligence icon.";

/* I6 · for the posts whose refusal the reader must SEE (suggest, background, pin):
   success is the body as usual; a refusal is {error: {status, message}} with the
   server's own reason (FastAPI's `detail`); nothing answering is status 0. */
async function postOrError(url, body) {
  let r;
  try {
    r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (e) {
    S.reachable = false;
    return { error: { status: 0, message: NOT_RUNNING } };
  }
  S.reachable = true;
  let got = null;
  try { got = await r.json(); } catch (e) { got = null; }
  if (r.ok) return got;
  const detail = got && typeof got.detail === 'string' ? got.detail : null;
  return { error: { status: r.status, message: detail || 'The server refused it.' } };
}

async function postJSON(url, body, method) {
  try {
    const r = await fetch(url, {
      method: method || 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    S.reachable = true;
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    S.reachable = false;
    return null;
  }
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
  keepUnreadList: () => getJSON('/api/keep_unread'),
  boards: () => getJSON('/api/boards'),
  newBoard: (name) => postJSON('/api/boards', { name: name }),
  pin: (boardId, itemType, payload, source) =>
    postOrError('/api/pin', { board_id: boardId, item_type: itemType, payload: payload,
                           source: source }),
  unpin: (pinId) => postJSON('/api/pin/' + encodeURIComponent(pinId), undefined, 'DELETE'),
  touchBoard: (boardId) =>
    postJSON('/api/boards/' + encodeURIComponent(boardId) + '/touch', {}),
  boardPins: (boardId) => getJSON('/api/boards/' + encodeURIComponent(boardId) + '/pins'),
  votes: () => getJSON('/api/feedback'),
  feedback: (kind, id, hashes, vote, note) =>
    postJSON('/api/feedback', { target_kind: kind, target_id: id, text_hashes: hashes,
                                vote: vote, note: note }),
  pref: (key) => getJSON('/api/pref/' + encodeURIComponent(key)),
  setPref: (key, value) =>
    postJSON('/api/pref/' + encodeURIComponent(key), { value: value }),
  visit: (ref, window_, opened) =>
    postJSON('/api/visit', { digest_ref: ref, window: window_, opened_story: opened }),

  /* --- v0.1 (brief 2 §5) --- */
  search: (query) => getJSON('/api/search?' + query),
  facets: () => getJSON('/api/facets'),
  estimate: (kind, answerId) =>
    getJSON('/api/estimate?kind=' + encodeURIComponent(kind)
            + (answerId ? '&answer_id=' + encodeURIComponent(answerId) : '')),
  conversation: () => getJSON('/api/conversation'),
  passages: (setId) => getJSON('/api/passages/' + encodeURIComponent(setId)),
  newConversation: () => postJSON('/api/conversation/new', {}),
  divider: (conversationId, storyId, headline) =>
    postJSON('/api/conversation/divider', { conversation_id: conversationId,
                                            story_id: storyId, headline: headline }),
  background: (answerId) => postOrError('/api/background', { answer_id: answerId }),
  hideTurn: (answerId) => postOrError('/api/hide_turn', { answer_id: answerId }),
  unhideTurn: (answerId) => postOrError('/api/unhide_turn', { answer_id: answerId }),
  suggest: (storyId, hashes) =>
    postOrError('/api/suggest', { story_id: storyId, text_hashes: hashes }),
  raiseCap: () => postJSON('/api/cap/raise', {}),
  addUserLink: (storyId, hashes, targetId, targetHashes) =>
    postJSON('/api/user_link', { story_id: storyId, text_hashes: hashes,
                                 target_story_id: targetId,
                                 target_text_hashes: targetHashes }),
  removeUserLink: (linkId) =>
    postJSON('/api/user_link/' + encodeURIComponent(linkId), undefined, 'DELETE'),
  userLinks: (hashes) =>
    getJSON('/api/user_links?' + (hashes || []).map(
      (h) => 'hashes=' + encodeURIComponent(h)).join('&')),
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
   wherever both matter -- which makes getting the singular right unavoidable.

   v0 added a bare "s", which was right for every word it counted (article, paper, day).
   The pane counts STORIES, and "9 storys" was on the screen until a check caught it, so
   the consonant-then-y rule is here now: story -> stories, while day -> days. */
function count(n, word) {
  if (n === 1) return n + ' ' + word;
  const plural = /[^aeiou]y$/.test(word) ? word.slice(0, -1) + 'ies' : word + 's';
  return n + ' ' + plural;
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
  const switchSlot = document.getElementById('digest-switch');
  if (switchSlot) {
    clear(switchSlot);
    if (S.digest && S.digests.length) switchSlot.append(digestSwitch());
  }

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
      dataset: number !== null && S.cursor === number - 1 ? { cursor: 'true' } : {},
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

  /* Reading an older morning is easy to start and, before this, hard to stop: the window
     in the header is the only clue, and it does not say "this is not today". */
  if (S.isPast) {
    drawer.append(el('p', { class: 'past-banner' },
      el('span', { class: 'past-banner-label', text: 'Past digest' }),
      el('button', { class: 'reset past-back', type: 'button',
                     onclick: () => { S.screen = 'today';
                                      loadDigest(viewRef(S.digests, S.view), null); } },
        'Back to today')));
  }

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

/* I5 · a cite the page cannot resolve is never drawn as nothing (Kunaal, A): it keeps
   its place as a dashed chip, and pressing it says what happened in one line. `said` is
   what did the citing -- an answer in the pane, or a write-up sentence. */
const DEAD_WORDS = {
  answer: 'This answer cited an article the app can no longer find.',
  sentence: 'This sentence cited an article the app can no longer find.',
};

function deadChip(said) {
  const line = DEAD_WORDS[said] || DEAD_WORDS.answer;
  return el('button', {
    class: 'reset chip chip-dead',
    type: 'button',
    title: line,
    onclick: () => toast(line),
  }, 'source missing');
}

function chipsFor(cites, byHash, said) {
  const chips = [];
  for (const cite of (cites || [])) {
    const row = byHash.get(cite);
    if (row) chips.push(articleChip(row));
    else if (/^\d{4}-\d{2}-\d{2}:/.test(cite)) chips.push(dayChip(cite));
    else chips.push(deadChip(said || 'answer'));
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
    storyActions(story));
}

/* ============================================================== reader actions

   READ IS ON LEAVE. Nothing here marks a story read when it opens: the page waits until
   the reader goes somewhere else (another story, another screen, or away from the tab
   altogether). So the story in front of you never says READ, and running an eye down the
   drawer does not consume the morning. "Keep unread" holds a story against that, and a
   story already read can be put back to unread the same way.
*/

function storyActions(story) {
  const hashes = storyHashes(story);
  const read = isRead(story);
  const held = S.keepUnread.has(story.story_id);
  const following = isFollowed(story);

  const keep = el('button', {
    class: 'reset action', type: 'button',
    'aria-pressed': held ? 'true' : 'false',
    text: read && !held ? 'Mark unread' : 'Keep unread',
    onclick: async () => {
      const on = !held;
      if (on) S.keepUnread.add(story.story_id); else S.keepUnread.delete(story.story_id);
      const res = await api.keepUnread(story.story_id, hashes, on);
      if (res && res.read) Object.assign(S.read, res.read);
      remember(on ? 'keep unread' : 'mark unread', async () => {
        if (held) S.keepUnread.add(story.story_id);
        else S.keepUnread.delete(story.story_id);
        const back = await api.keepUnread(story.story_id, hashes, held);
        if (back && back.read) Object.assign(S.read, back.read);
        render();
      });
      render();
    },
  });

  const follow = el('button', {
    class: 'reset action', type: 'button', dataset: { you: 'true' },
    'aria-pressed': following ? 'true' : 'false',
    text: following ? '★ Following' : '☆ Follow',
    onclick: async () => {
      const res = await api.follow(story.story_id, hashes, !following);
      S.follows = (res && res.follows) || S.follows;
      remember(following ? 'unfollow' : 'follow', async () => {
        const back = await api.follow(story.story_id, hashes, following);
        S.follows = (back && back.follows) || S.follows;
        render();
      });
      render();
    },
  });

  /* A vote that leaves no mark is one you give again next week, having forgotten you
     ever gave it -- which is exactly what happened when this was first used. The button
     now stays pressed, and says so when you hover it. */
  const given = (S.votes['story:' + story.story_id] || {}).vote || null;

  const up = el('button', {
    class: 'reset action', type: 'button', dataset: { vote: 'true' },
    'aria-pressed': given === 'up' ? 'true' : 'false',
    'aria-label': 'This story was worth reading',
    title: given === 'up' ? 'You marked this worth reading' : 'Worth reading',
    onclick: async () => {
      await api.feedback('story', story.story_id, hashes, 'up', null);
      S.votes['story:' + story.story_id] = { vote: 'up' };
      toast('Thank you · noted');
      render();
    },
  }, '👍');

  const voteHolder = el('span', {});
  const down = el('button', {
    class: 'reset action', type: 'button', dataset: { vote: 'true' },
    'aria-pressed': given === 'down' ? 'true' : 'false',
    'aria-label': 'This story was not worth reading',
    title: given === 'down' ? 'You marked this not worth reading' : 'Not worth reading',
    onclick: () => {
      clear(voteHolder);
      const input = el('input', { type: 'text', 'aria-label': 'Why? (optional)',
                                  placeholder: 'Why? (optional)' });
      const send = el('button', {
        class: 'reset action', type: 'button', text: 'Send',
        onclick: async () => {
          await api.feedback('story', story.story_id, hashes, 'down',
                             input.value.trim() || null);
          S.votes['story:' + story.story_id] = { vote: 'down' };
          clear(voteHolder);
          toast('Thank you · noted');
          render();
        },
      });
      voteHolder.append(el('span', { class: 'why-field' }, input, send));
      input.focus();
    },
  }, '👎');

  return el('div', { class: 'meta-actions', id: 'meta-actions' },
    keep, follow, pinButton(story), up, down, voteHolder);
}

/* §8.6 — the boards menu: most recently used first, a new board inline, and six seconds
   to undo, because pinning to the wrong board is the easy mistake. */
/* §8.6 gives the same menu two jobs: pinning the story you are reading, and pinning one
   article from the article view. Only what goes on the board differs, so the menu, its
   keyboard and its undo stay one control and `what()` says what is being pinned. */
function pinButton(story) {
  return pinControl('story', () => pinPayload(story));
}

function articlePinButton(article) {
  return pinControl('article', () => ({
    payload: { text_hash: article.text_hash, headline: article.headline },
    source: { digest_ref: S.ref, paper: article.paper, date: article.date,
              page_link: article.page_link },
  }));
}

function pinControl(itemType, what, options) {
  const o = options || {};
  const wrap = el('span', { class: 'pin-wrap' });
  const menu = el('div', { class: 'pin-menu' });
  menu.hidden = true;

  const button = el('button', {
    class: 'reset ' + (o.class || 'action'), type: 'button', dataset: { you: 'true' },
    'aria-expanded': 'false', 'aria-haspopup': 'menu',
    'aria-label': o.label || null,
    text: o.icon ? null : (o.text || 'Pin to board'),
    onclick: async () => {
      const open = menu.hidden;
      if (open) {
        const boards = await api.boards();
        S.boards = (boards && boards.boards) || [];
        const shut = () => { menu.hidden = true;
                             button.setAttribute('aria-expanded', 'false'); };
        fillPinMenu(menu, itemType, what, shut, o.after);
        pinMenuKeys(menu, shut, button);
      }
      menu.hidden = !open;
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) {
        const first = menu.querySelector('li button') || menu.querySelector('input');
        if (first) first.focus();
      }
    },
  });

  if (o.icon) button.append(o.icon);
  wrap.append(button, menu);
  return wrap;
}

function pinPayload(story) {
  const head = story.headline || {};
  const source = rowsByHash(story).get(head.source_text_hash);
  return {
    payload: { story_id: story.story_id, headline: head.text,
               text_hashes: storyHashes(story) },
    source: { digest_ref: S.ref, paper: source ? source.paper : null,
              date: source ? source.date : null,
              page_link: source ? source.page_link : null },
  };
}

/* The menu is opened by `p` as often as by the mouse, so it has to work without one:
   the first board takes focus, the arrows move between boards, Escape closes it and
   hands focus back to the button, and Tab reaches the new-board field and Add. */
function pinMenuKeys(menu, close, button) {
  menu.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
      button.focus();
      return;
    }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const items = [...menu.querySelectorAll('li button')];
    if (!items.length) return;
    e.preventDefault();
    const here = items.indexOf(document.activeElement);
    const step = e.key === 'ArrowDown' ? 1 : -1;
    items[here < 0 ? 0 : (here + step + items.length) % items.length].focus();
  });
}

/* I6 · a pin the server refused. A board deleted since the menu was drawn is a 404
   (`pin()` raises LookupError); the boards are read again so the menu stops offering
   it. Returns true when there was nothing pinned. */
const BOARD_GONE = 'That board no longer exists';

async function pinRefused(res) {
  if (res && !res.error) return false;
  const error = (res && res.error) || { status: 0, message: NOT_RUNNING };
  if (error.status === 404) {
    toast(BOARD_GONE);
    const boards = await api.boards();
    if (boards) S.boards = boards.boards || [];
  } else {
    toast(error.status === 0 ? NOT_RUNNING : 'Not pinned · ' + error.message);
  }
  return true;
}

function fillPinMenu(menu, itemType, what, close, after) {
  clear(menu);
  const { payload, source } = what();
  /* A suggestion's pin icon has to go dark the moment it is pinned, so the menu can be
     told what to do afterwards. Everything else pins and leaves the page as it was. */
  const done = after || (() => {});

  menu.append(el('ul', {}, S.boards.map((b) => el('li', {},
    el('button', { class: 'reset', type: 'button', onclick: async () => {
      close();
      const res = await api.pin(b.board_id, itemType, payload, source);
      if (await pinRefused(res)) return;
      pinnedToast(b.name, res && res.pin_id);
      await done(b, res && res.pin_id, payload, source);
    } },
      b.name, ' ', el('span', { class: 'pin-count',
                                text: '(' + b.pin_count + ')' }))))));

  const input = el('input', { type: 'text', 'aria-label': 'New board name',
                              placeholder: 'New board…' });
  const make = el('button', {
    class: 'reset action', type: 'button', text: 'Add',
    onclick: async () => {
      const name = input.value.trim();
      if (!name) return;
      close();
      const made = await api.newBoard(name);
      if (!made) return;
      S.boards = made.boards || S.boards;
      const res = await api.pin(made.board_id, itemType, payload, source);
      if (await pinRefused(res)) return;
      pinnedToast(name, res && res.pin_id);
      await done({ board_id: made.board_id, name: name }, res && res.pin_id,
                 payload, source);
    },
  });
  menu.append(el('div', { class: 'pin-new' }, input, make));
}

const UNDO_SECONDS = 6;     /* long enough to notice the toast, short enough to ignore */
let toastTimer = null;

function toast(text, undo, seconds) {
  const node = document.getElementById('toast');
  clear(node);
  node.append(el('span', { text: text }));
  if (undo) {
    node.append(el('button', { class: 'reset', type: 'button', text: 'Undo',
                               onclick: () => { undo(); hideToast(); } }));
  }
  node.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, (seconds || UNDO_SECONDS) * 1000);
}

function hideToast() {
  const node = document.getElementById('toast');
  node.hidden = true;
  clear(node);
  if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
}

/* UNDO, in one place, so the Undo in the toast and ⌘Z are the same thing.

   One step, not a stack. A stack would mean ⌘Z pressed twice undoes something from
   several minutes ago that is no longer on screen, which is worse than no undo at all:
   the reader cannot see what they are undoing.

   It covers the three actions that change a list you would otherwise have to go and
   hunt through to put right -- a pin, a follow, a keep-unread. It deliberately does NOT
   cover read-on-leave, which happens constantly just by reading and is already undone
   by `u` on the story itself, nor a vote, which is visibly marked and changed by
   clicking the other one. An undo that might mean any of those is one you stop trusting.
*/
function remember(label, run) {
  S.lastUndo = { label: label, run: run };
}

async function undoLast() {
  const step = S.lastUndo;
  if (!step) { toast('Nothing to undo'); return; }
  S.lastUndo = null;
  /* F7 · a step that could not be undone says so itself and returns false */
  if (await step.run() === false) return;
  toast('Undone · ' + step.label);
}

function pinnedToast(boardName, pinId) {
  if (!pinId) { toast('Pinned to ' + boardName); return; }
  remember('pin to ' + boardName, async () => {
    await api.unpin(pinId);
    const boards = await api.boards();
    S.boards = (boards && boards.boards) || S.boards;
    render();
  });
  toast('Pinned to ' + boardName, undoLast);
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
      el('p', {}, s.text, chipsFor(s.cites, byHash, 'sentence'))),
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
    w.sentences.map((s) => el('p', {}, s.text, chipsFor(s.cites, byHash, 'sentence'))));
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
        : pageLink(v.page_link, v.headline);
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

/* I4 (integration brief §4.2) — a page link opens THAT page: the server cuts the one
   page out of the paper (`/api/page`), so the reader is not left scrolling a 22-page PDF
   that a viewer may or may not open at `#page=N`. The whole paper stays one small link
   away, beside it. The label is unchanged ("TH PDF p2"). */
function onePageHref(link) {
  if (!link || !link.pdf || !link.pdf_page) return '#';
  return '/api/page?pdf=' + encodeURIComponent(link.pdf)
         + '&page=' + encodeURIComponent(link.pdf_page);
}

function pageLink(link, text) {
  if (!link || !link.pdf) return text || null;
  return el('span', { class: 'page-link' },
    el('a', { href: onePageHref(link), target: '_blank', rel: 'noopener' }, text),
    ' ',
    el('a', { class: 'whole-paper', href: pdfHref(link), target: '_blank', rel: 'noopener',
              'aria-label': 'Whole paper at ' + text }, 'whole paper'));
}

/* `page_links_more` (contract R5): the 0-2 other pages the same printed piece runs on,
   already in reading order, each with its reason. A reason this page does not know is
   still shown, as "also on pN", rather than dropped (brief §6 rule 6). */
const MORE_WORDS = { joined_from: 'joined from p', continues_to: 'continues on p' };

function moreLinks(row) {
  const out = [];
  for (const link of ((row && row.page_links_more) || [])) {
    if (!link || !link.pdf || !link.pdf_page) continue;
    out.push(' · ', el('a', { class: 'page-more', href: onePageHref(link), target: '_blank',
                              rel: 'noopener' },
                       (MORE_WORDS[link.reason] || 'also on p') + link.pdf_page));
  }
  return out;
}

/* Q1 B (Kunaal, 2 Nov) — the printed "see also" pointers, one muted line above Coverage.

   `story.links` items are `see_also_page`: a `from` page_link, a `to_story_id` and a
   `to_headline`. Two things in the real data shape this line (hand-off F1, D1). The
   `from` object has no `paper`, so the code comes from the PDF's file name, which is
   `<date>_<paper>.pdf`; and `label` is always "PDF p<N>" again, so printing it would
   read "TH PDF p1 · PDF p1". The eight items on the one story that has any all point at
   the SAME page, so the line groups by page -- otherwise it is one pointer eight times.
   None of the eight `to_story_id`s is in the digest (they are among the 2,359 stories
   below the top-N cut), so a headline here cannot be a link to anywhere; the count says
   what the pointer is worth and the headlines ride along as the link's tooltip. */
function paperFromPdf(pdf) {
  const file = String(pdf || '').split('/').pop().replace(/\.pdf$/i, '');
  const cut = file.indexOf('_');                 /* archive names are <date>_<paper>.pdf */
  return cut === -1 ? file : file.slice(cut + 1);
}

function seeAlsoPlan(story) {
  const links = ((story && story.links) || []).filter((l) => l && l.from && l.from.pdf);
  const pages = [];
  for (const link of links) {
    const key = link.from.pdf + '#' + link.from.pdf_page;
    let page = pages.find((p) => p.key === key);
    if (!page) {
      page = { key, link: link.from, headlines: [],
               text: paperCode(paperFromPdf(link.from.pdf)) + ' PDF p' + link.from.pdf_page };
      pages.push(page);
    }
    if (link.to_headline) page.headlines.push(link.to_headline);
  }
  if (!pages.length) return null;
  const total = pages.reduce((n, p) => n + p.headlines.length, 0);
  return { pages, total,
           tail: !total ? ''
                 : total + (total === 1 ? ' story' : ' stories')
                   + ' on the same ' + (pages.length === 1 ? 'page' : 'pages') };
}

/* Decision D1 (2 Nov). "8 stories on the same page" was a tooltip on the PDF link: a
   `title=` attribute, which no touch device shows, no screen reader reads reliably, and
   nothing in the suite could check. It is now a fold -- a real <button> with
   aria-expanded -- and the tooltip is gone.

   The eight headlines open as plain muted text, one per line, and NOT as links: every
   one of them is below the digest's top N and cannot be opened yet. A link that does
   nothing is worse than a line of text that was never offered as one.

   The fold is not remembered. It is a glance at what else was on that page, not a view
   the reader sets up and comes back to, so it closes with the story. */
function seeAlsoFold(plan, storyId) {
  const open = Boolean(S.seeAlsoOpen[storyId]);
  const fold = el('span', { class: 'see-also-fold' });
  fold.append(el('button', {
    class: 'reset see-also-toggle', type: 'button',
    'aria-expanded': open ? 'true' : 'false',
    onclick: () => { S.seeAlsoOpen[storyId] = !open; render(); },
  }, plan.tail));
  if (open) {
    const all = [];
    for (const page of plan.pages) for (const head of page.headlines) all.push(head);
    fold.append(el('span', { class: 'see-also-heads', role: 'list' },
      all.map((head) => el('span', { class: 'see-also-head', role: 'listitem',
                                     text: head }))));
  }
  return fold;
}

function seeAlso(story) {
  if (!S.flags.see_also_line) return null;
  const plan = seeAlsoPlan(story);
  if (!plan) return null;
  const bits = [];
  plan.pages.forEach((page, i) => {
    if (i) bits.push(' · ');
    bits.push(pageLink(page.link, page.text));
  });
  if (plan.tail) {
    bits.push(' · ');
    bits.push(seeAlsoFold(plan, story.story_id));
  }
  return el('p', { class: 'see-also' }, 'The paper also points to: ', bits);
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
      el('td', {}, pageLink(r.page_link, r.page_link ? r.page_link.label : ''),
         moreLinks(r)),
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

  const thread = threadSection(story);
  if (thread) pane.append(thread);
  /* §8.4: below the thread, or where the thread would have been when the story has
     none -- a story with no thread is exactly the one a connection would help. */
  const suggest = suggestSection(story);
  if (suggest) pane.append(suggest);

  const also = seeAlso(story);
  if (also) pane.append(also);
  pane.append(coverage(story));
  pane.append(el('p', { class: 'page-footer',
    text: "Charts and photos aren't read. Use the page link to see them." }));
}

/* ==================================================================== the thread

   The same entries are drawn two ways. The timeline is the default and reads downward,
   newest first, because that is the order a reader asks the question in ("what happened,
   and what led to it"). The graph reads left to right in time and shows the shape
   instead -- where a follow-up branched off, what sits beside the story. Acceptance
   check 6 is that both draw the same set of entry_ids: one of them quietly dropping an
   entry would be the easiest bug here to miss.
*/

/* The node's shape says what kind of link it is (§8.5). The newest entry is "today"
   whatever its link type: it is the top of the thread, not a kind of link. */
function nodeKind(entry, index) {
  /* R13 first, before the index rule: an entry YOU added is never "today", whatever
     its date. If the target story is more recent than the open one it sorts to the top
     of the thread, and drawing it as the red today node would say the pipeline found
     it. The hollow ochre square says who put it there. */
  if (entry.link_type === 'user') return 'user';
  if (index === 0) return 'today';
  return entry.link_type || 'untyped';
}

const KIND_WORDS = {
  follow_up: 'follow-up',
  start: 'where it began',
  run_up: 'run-up',
};

function nodeFor(entry, index) {
  return el('span', { class: 'node', dataset: { kind: nodeKind(entry, index) } });
}

function removeLinkButton(entry) {
  return el('button', {
    class: 'reset entry-wrong', type: 'button', text: 'Remove',
    'aria-label': 'Remove the link you added to ' + entry.headline,
    onclick: () => dropUserLink(entry.link_id),
  });
}

/* "Wrong link?" on an entry: the thread is K2's guess at what continues what, and this
   is the only place a reader can say it guessed wrong. The note is optional. */
function wrongLinkButton(entry) {
  const holder = el('span', {});
  const button = el('button', {
    class: 'reset entry-wrong', type: 'button', text: 'Wrong link?',
    onclick: () => {
      clear(holder);
      const input = el('input', { type: 'text', 'aria-label': 'Why? (optional)',
                                  placeholder: 'Why? (optional)' });
      const send = el('button', {
        class: 'reset', type: 'button', text: 'Send',
        onclick: async () => {
          await api.feedback('thread_link', entry.entry_id, [], 'down',
                             input.value.trim() || null);
          clear(holder);
          holder.append(el('span', { class: 'entry-kind', text: 'Thank you · noted' }));
        },
      });
      holder.append(el('span', { class: 'why-field' }, input, send));
      input.focus();
    },
  });
  holder.append(button);
  return holder;
}

function timelineView(thread) {
  const list = el('ul', { class: 'timeline' }, thread.entries.map((entry, i) => {
    const word = KIND_WORDS[entry.link_type];
    return el('li', { class: 'entry', dataset: { entryId: entry.entry_id } },
      el('p', { class: 'entry-when' },
        el('span', { class: 'entry-day', text: dayName(entry.date) }),
        dateShort(entry.date)),
      el('span', { class: 'entry-node' }, nodeFor(entry, i)),
      el('div', { class: 'entry-body' },
        el('p', { class: 'entry-papers' },
          word ? el('span', { class: 'entry-kind', text: word + ' · ' }) : null,
          count(entry.articles, 'article'),
          (entry.pages || []).flatMap((p) => [' · ',
            pageLink(p, paperCode(p.paper) + ' p' + p.pdf_page)])),
        el('h4', { class: 'entry-headline' },
          entry.link_type === 'user'
            ? el('span', { class: 'added-by-you', text: 'Added by you' }) : null,
          entry.headline),
        el('p', { class: 'entry-line', text: entry.one_liner }),
        /* K2 guessed the other links, so "Wrong link?" is the way to say it guessed
           wrong. This one was not a guess -- it was the reader's -- so the only thing
           to offer is taking it back. */
        entry.link_type === 'user' ? removeLinkButton(entry)
                                   : wrongLinkButton(entry)));
  }));

  if (!thread.related || !thread.related.length) return el('div', {}, list);
  return el('div', {}, list,
    el('ul', { class: 'related' },
      el('p', { class: 'related-title',
                text: 'Related, same subject (not part of this story)' }),
      thread.related.map((r) => el('li', {},
        dateWithDay(r.date) + ' — ' + r.headline))));
}

/* The graph: oldest on the left, follow-ups above the spine, related below it. */
const GRAPH = { col: 120, pad: 8, spineTop: 125, followTop: 27, relTop: 245, box: 104 };

/* Where each entry goes in the graph, worked out as plain data before anything is drawn.

   It is separate from the drawing for one reason: acceptance check 6 says the timeline
   and the graph must show the SAME entries, and the easiest way for that to go wrong is
   a filter here quietly dropping one. As data it can be checked without a browser, which
   is what tests/test_p3_app.py does. The timeline's own list is simply thread.entries,
   so `viewEntryIds` below is the thing the check compares.
*/
function graphPlan(thread) {
  const oldestFirst = thread.entries.slice().reverse();
  const spine = oldestFirst.filter((e) => e.link_type !== 'follow_up');
  const newestId = thread.entries.length ? thread.entries[0].entry_id : null;
  return oldestFirst.map((entry) => {
    const isFollow = entry.link_type === 'follow_up';
    /* a follow-up hangs above the day it belongs to, so it shares that day's column */
    const column = isFollow
      ? Math.max(0, spine.findIndex((e) => e.entry_id === entry.parent_id))
      : Math.max(0, spine.findIndex((e) => e.entry_id === entry.entry_id));
    const kind = entry.link_type === 'user' ? 'user'
      : (isFollow ? 'follow'
        : (entry.entry_id === newestId ? 'today'
          : (entry.link_type === 'start' ? 'start' : 'spine')));
    return { entry: entry, entry_id: entry.entry_id, isFollow: isFollow,
             column: column, kind: kind };
  });
}

/* The entries a view will draw. Both views draw all of them; this is what says so. */
function viewEntryIds(thread, view) {
  if (view === 'graph') return graphPlan(thread).map((p) => p.entry_id);
  return thread.entries.map((e) => e.entry_id);
}

function graphView(thread) {
  const oldestFirst = thread.entries.slice().reverse();
  const spine = oldestFirst.filter((e) => e.link_type !== 'follow_up');
  const canvas = el('div', { class: 'graph' });
  const selected = el('div', { class: 'graph-selected' });

  function show(entry, index) {
    clear(selected);
    for (const b of canvas.querySelectorAll('.graph-node')) {
      b.setAttribute('aria-pressed', b.dataset.entryId === entry.entry_id ? 'true' : 'false');
    }
    selected.append(
      el('h4', { text: entry.headline }),
      el('p', { text: dateWithDay(entry.date) + ' · '
                      + count(entry.articles, 'article') + ' from '
                      + count(entry.papers, 'paper') }),
      el('p', { text: entry.one_liner }));
  }

  graphPlan(thread).forEach((placed) => {
    const entry = placed.entry;
    const isFollow = placed.isFollow;
    const kind = placed.kind;
    const newest = kind === 'today';
    const left = placed.column * GRAPH.col + GRAPH.pad;
    const top = isFollow ? GRAPH.followTop : GRAPH.spineTop;

    if (isFollow) {
      canvas.append(el('span', { class: 'graph-link',
        style: 'left:' + (left + GRAPH.box / 2) + 'px; top:' + (GRAPH.followTop + 70)
               + 'px; height:' + (GRAPH.spineTop - GRAPH.followTop - 70) + 'px' }));
    }
    canvas.append(el('button', {
      class: 'reset graph-node', type: 'button', 'aria-pressed': 'false',
      dataset: { kind: kind, entryId: entry.entry_id },
      style: 'left:' + left + 'px; top:' + top + 'px',
      onclick: () => show(entry),
    },
      el('span', { class: 'graph-tag',
                   text: newest ? 'today' : (KIND_WORDS[entry.link_type] || dateShort(entry.date)) }),
      el('span', { class: 'graph-title', text: entry.headline })));
  });

  (thread.related || []).forEach((r) => {
    const column = Math.max(0, spine.findIndex((e) => e.entry_id === r.attaches_to));
    const left = column * GRAPH.col + GRAPH.pad;
    canvas.append(el('span', { class: 'graph-link', dataset: { kind: 'rel' },
      style: 'left:' + (left + GRAPH.box / 2) + 'px; top:' + (GRAPH.spineTop + 70)
             + 'px; height:' + (GRAPH.relTop - GRAPH.spineTop - 70) + 'px' }));
    canvas.append(el('div', { class: 'graph-node', dataset: { kind: 'rel' },
      style: 'left:' + left + 'px; top:' + GRAPH.relTop + 'px' },
      el('span', { class: 'graph-tag', text: 'related' }),
      el('span', { class: 'graph-title', text: r.headline })));
  });

  /* The spine stops at the last day rather than running on past it, and goes in FIRST so
     the boxes sit on top of it: appended last it painted straight through their text. */
  const lastLeft = Math.max(spine.length - 1, 0) * GRAPH.col + GRAPH.pad;
  const width = lastLeft + GRAPH.box;
  canvas.style.width = width + 'px';
  canvas.prepend(el('span', { class: 'graph-spine',
    style: 'width:' + width + 'px; left:0' }));
  if (thread.entries.length) show(thread.entries[0]);

  return el('div', {}, el('div', { class: 'graph-scroll' }, canvas), selected);
}

function threadSection(story) {
  const thread = S.threads[story.story_id];
  if (!thread) return null;                 /* H3: no thread, no section at all */

  const view = S.flags.graph_view && S.prefs.thread_view === 'graph' ? 'graph' : 'timeline';
  const body = view === 'graph' ? graphView(thread) : timelineView(thread);

  const switcher = !S.flags.graph_view ? null : el('div', { class: 'view-switch' },
    ['timeline', 'graph'].map((name) => el('button', {
      class: 'reset', type: 'button', 'aria-pressed': view === name ? 'true' : 'false',
      text: name === 'timeline' ? 'Timeline' : 'Graph',
      onclick: () => {
        S.prefs.thread_view = name;
        api.setPref('thread_view', name);
        render();
      },
    })));

  return el('section', { class: 'thread' },
    el('div', { class: 'thread-head' },
      el('h3', { class: 'thread-title' }, 'The thread',
        thread.fixture ? el('span', { class: 'fixture-tag', text: 'sample data' }) : null),
      el('span', { class: 'thread-count',
        text: count(thread.days, 'day') + ' · ' + count(thread.articles, 'article')
              + ' from ' + count(thread.papers, 'paper')
              /* R13: a thread you added to is a longer thread, and the day count
                 cannot say so -- a link is not a day (decision D9). */
              + (thread.entries_added_by_you
                   ? ' · ' + thread.entries_added_by_you + ' added by you' : '') }),
      switcher),
    body);
}

/* Q3 B (Kunaal, 2 Nov): "Earlier coverage" is gone from above the thread. The thread IS
   the list of earlier days, and v0 showed the nearest three of them twice on one screen.
   v0's `earlierCoverage` and its `.earlier` styles were deleted here rather than hidden
   behind a flag, because the decision is which list earns the place, not whether to
   show one. The jump into the thread is still the story-so-far chips and the `t` key. */

/* --------------------------------------------------------------- the article view */

async function openArticle(textHash) {
  S.articleOpen = true;
  const article = await api.article(textHash);
  const pane = document.getElementById('reading');
  clear(pane);
  if (!article) {
    pane.append(el('p', { class: 'drawer-note',
                          text: S.reachable ? 'That article could not be found.'
                                            : NOT_RUNNING }));
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

  pane.append(articleBody(article));

  pane.append(el('p', { class: 'article-actions' },
    pageLink(article.page_link, 'Open page'),
    articlePinButton(article)));
  pane.scrollTop = 0;
}

/* I4 · the `dateline` ("New Delhi:") leads the first paragraph, once. The server has
   already taken it off the front of the body where the body began with it. */
function articleBody(article) {
  const paras = (article.paragraphs || []).slice();
  const lead = article.dateline
    ? el('span', { class: 'dateline', text: article.dateline }) : null;
  return el('div', { class: 'article-body' },
    lead && !paras.length ? el('p', {}, lead) : null,
    paras.map((p, i) => el('p', {}, i === 0 && lead ? [lead, ' '] : null, p)));
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

/* ------------------------------------------------------------- the other screens */

function boardsScreen(pane) {
  pane.append(el('h2', { class: 'screen-title', text: 'Boards' }));
  if (S.openBoardId) {
    const board = S.boards.find((b) => b.board_id === S.openBoardId);
    pane.append(el('button', { class: 'reset back-link', type: 'button',
      onclick: () => { S.openBoardId = null; render(); } }, '← All boards'));
    pane.append(el('h3', { class: 'plain-name', text: board ? board.name : 'Board' }));
    const pins = S.pins || [];
    if (!pins.length) {
      pane.append(el('p', { class: 'empty-note', text: 'Nothing pinned here yet.' }));
      return;
    }
    pane.append(el('ul', { class: 'plain-list' }, pins.map((p) => {
      const payload = p.payload || {};
      const source = p.source || {};
      const bits = [source.paper, source.date ? dateWithDay(source.date) : null,
                    source.page_link ? source.page_link.label : null].filter(Boolean);
      return el('li', {}, el('button', {
        class: 'reset plain-row', type: 'button',
        onclick: () => openPin(p),
      },
        el('span', { class: 'plain-name',
                     text: payload.headline || payload.text_hash || p.item_type }),
        el('span', { class: 'pin-source',
                     text: (p.item_type + (bits.length ? ' · ' + bits.join(' · ')
                                                       : '')) })));
    })));
    return;
  }

  if (!S.boards.length) {
    pane.append(el('p', { class: 'empty-note',
      text: 'No boards yet. Pin a story to make one.' }));
    return;
  }
  pane.append(el('ul', { class: 'plain-list' }, S.boards.map((b) => el('li', {},
    el('button', { class: 'reset plain-row', type: 'button',
      onclick: () => openBoard(b) },
      el('span', { class: 'plain-name', text: b.name }),
      el('span', { class: 'plain-sub',
        text: count(b.pin_count, 'pin')
              + (b.last_used_at ? ' · last used ' + dateWithDay(b.last_used_at)
                                : '') }))))));
}

/* I6 · opening a board bumps its `last_used_at` (touch_board), so the pin menu's
   most-recent-first order follows what the reader actually uses. A board that is gone
   says so; a server that is not running says that. */
async function openBoard(b) {
  const touched = await api.touchBoard(b.board_id);
  if (!touched) {
    toast(S.reachable ? BOARD_GONE : NOT_RUNNING);
    if (S.reachable) {
      const boards = await api.boards();
      if (boards) S.boards = boards.boards || [];
      render();
    }
    return;
  }
  const pins = await api.boardPins(b.board_id);
  S.pins = (pins && pins.pins) || [];
  S.openBoardId = b.board_id;
  render();
}

/* A pin goes back to what it came from: a story in its own digest, or an article. */
function openPin(p) {
  const payload = p.payload || {};
  if (p.item_type === 'article' && payload.text_hash) {
    openArticle(payload.text_hash);
    return;
  }
  const ref = (p.source || {}).digest_ref;
  S.screen = 'today';
  if (ref && ref !== S.ref) { loadDigest(ref, payload.story_id); return; }
  if (payload.story_id) openStory(payload.story_id); else render();
}

function isOpenDigest(d) {
  return Boolean(S.digest) && d.built_at === S.digest.built_at;
}

function pastScreen(pane) {
  pane.append(el('h2', { class: 'screen-title', text: 'Past digests' }));
  pane.append(el('ul', { class: 'plain-list' }, pastRows(S.digests).map((d) => el('li', {},
    el('button', {
      class: 'reset plain-row', type: 'button',
      /* "latest" is a request, not a name, so the open digest is the one whose build
         time matches what is loaded -- comparing refs would never match it. */
      'aria-current': isOpenDigest(d) ? 'true' : null,
      onclick: () => { S.screen = 'today'; loadDigest(d.ref, null); },
    },
      el('span', { class: 'plain-name' }, windowText(d.window),
        DIGEST_KIND_LABEL[digestKind(d)]
          ? el('span', { class: 'kind-label', text: DIGEST_KIND_LABEL[digestKind(d)] })
          : null),
      el('span', { class: 'plain-sub',
        text: 'built ' + (d.built_at ? dateWithDay(d.built_at) + ' '
                                       + timeOfDay(d.built_at) : '')
              + (isOpenDigest(d) ? ' · open' : '') }))))));
}

/* Another digest: its read state still applies, so the stamps are right at once. */
async function loadDigest(ref, thenOpenStoryId) {
  await leaveOpenStory();
  S.ref = ref;
  S.digest = await api.digest(ref);
  S.openStoryId = null;
  S.threads = {};
  S.newSince = {};
  markIfPast();
  await refreshRead();
  const first = thenOpenStoryId
    || (stories().length ? stories()[0].story_id : null);
  render();
  if (first) await openStory(first);
}

/* F2 (Q-F1 C) · Day | Week. Each half opens the newest digest of its `kind` (contract
   §12.3 #17), picked by kind and never by ref or name; the choice is a pref. A week
   digest older than every day digest is still Week's (its own dates in the header).
   `since_last` digests are not shown in v1. */
const DIGEST_VIEWS = ['day', 'week'];
const DIGEST_VIEW_WORDS = { day: 'Day', week: 'Week' };
const DIGEST_KIND_LABEL = { day: 'DAY', week: 'WEEK' };

function digestKind(d) {
  return d ? (d.kind || ((d.window || {}).kind) || null) : null;
}

function newestOfKind(list, kind) {
  return (list || []).find((d) => digestKind(d) === kind) || null;
}

/* The ref the chosen view opens: the newest of that kind, else the newest listed digest
   (a world with no digest of that kind still opens something), else K3's 'latest'. */
function viewRef(list, view) {
  const pick = newestOfKind(list, view) || pastRows(list)[0];
  return pick ? pick.ref : 'latest';
}

function storedView(value) {
  return DIGEST_VIEWS.includes(value) ? value : 'day';
}

/* Past digests: day and week rows (and any with no kind), server order, newest first. */
function pastRows(list) {
  return (list || []).filter((d) => digestKind(d) !== 'since_last');
}

/* Whether what is loaded is the newest digest OF ITS OWN KIND. The digests list is
   fetched at boot rather than only when Past digests is opened, because this answer
   decides what the drawer and the TODAY tab do, and it is one small request. Comparing
   with the newest of any kind would call the week digest "past" on every Week visit. */
function markIfPast() {
  const kind = digestKind(S.digest);
  const newest = newestOfKind(S.digests, kind) || S.digests[0];
  S.isPast = Boolean(S.digest) && S.digests.length > 0 && Boolean(newest)
             && S.digest.built_at !== newest.built_at;
}

/* The switch beside the digest's name. The pressed half is the open digest's kind (a
   past day digest keeps Day pressed); pressing a half opens that kind's newest digest
   and remembers the choice. A kind with no digest is drawn disabled, not hidden, so the
   switch does not change shape from one morning to the next. */
function digestSwitch() {
  const open = digestKind(S.digest);
  const pressed = DIGEST_VIEWS.includes(open) ? open : S.view;
  const box = el('span', { class: 'digest-switch', role: 'group', 'aria-label': 'Digest' });
  DIGEST_VIEWS.forEach((view, i) => {
    if (i) box.append(el('span', { class: 'digest-switch-bar', 'aria-hidden': 'true',
                                   text: '|' }));
    const none = !newestOfKind(S.digests, view);
    box.append(el('button', {
      class: 'reset digest-switch-half', type: 'button',
      'aria-pressed': view === pressed ? 'true' : 'false',
      disabled: none ? true : null,
      title: none ? 'No ' + DIGEST_VIEW_WORDS[view].toLowerCase() + ' digest yet' : null,
      onclick: () => chooseView(view),
    }, DIGEST_VIEW_WORDS[view]));
  });
  return box;
}

async function chooseView(view) {
  S.view = view;
  api.setPref('digest_view', view);
  S.screen = 'today';
  await loadDigest(viewRef(S.digests, view), null);
}

async function refreshRead() {
  const hashes = [];
  for (const s of stories()) hashes.push(...storyHashes(s));
  for (const f of S.follows) hashes.push(...(f.text_hashes || []));
  if (!hashes.length) return;
  const read = await api.readState([...new Set(hashes)]);
  S.read = (read && read.read) || {};
}

function renderReading() {
  const pane = document.getElementById('reading');
  clear(pane);

  if (!S.digest) {
    pane.append(el('h2', { class: 'screen-title',
      text: S.reachable ? 'No digest to show' : 'The server is not answering' }));
    pane.append(el('p', { class: 'empty-note', text: S.reachable
      ? 'The server is running, but it has no digest to show yet.'
      : NOT_RUNNING + ' Then reload this page.' }));
    return;
  }

  if (S.screen === 'explore' && S.flags.explore) { exploreScreen(pane); return; }
  if (S.screen === 'boards') { boardsScreen(pane); return; }
  if (S.screen === 'past') { pastScreen(pane); return; }
  const story = findStory(S.openStoryId);
  if (story) renderStory(pane, story);
  else if (S.outside[S.openStoryId]) renderOutsideStory(pane, S.outside[S.openStoryId]);
  else if (S.openStoryId && S.openStoryId in S.outside) {
    pane.append(el('p', { class: 'drawer-note', text: OUTSIDE_WORDS.gone }));
  } else if (S.openStoryId && !S.reachable) {
    pane.append(el('p', { class: 'drawer-note', text: NOT_RUNNING }));
  } else pane.append(el('p', { class: 'drawer-note', text: 'Choose a story.' }));
}

/* Leaving a story is what marks it read: opening another one, going to another screen,
   or leaving the tab. A story held unread is left alone, and the double refuses it too,
   so the rule does not depend on this function remembering to check. */
async function leaveOpenStory() {
  const story = findStory(S.openStoryId);
  if (!story) return;
  if (S.keepUnread.has(story.story_id)) return;
  const hashes = storyHashes(story);
  if (!hashes.length || hashes.every((h) => S.read[h])) return;
  const res = await api.markRead(hashes);
  if (res && res.read) Object.assign(S.read, res.read);
}

function render() {
  S.articleOpen = false;
  renderHeader();
  renderDrawer();
  renderReading();
  renderAssistant();
}

/* ------------------------------------------------------------ L-4 · outside stories */

/* K-8 option A (Kunaal, 4 Nov): a story that is in no digest in memory opens as a
   plain, free page -- headline, dates, papers, its articles, its thread if it has one.
   It is read through a browse search limited to that one story, the only per-story read
   the data layer has; that search names a story's best-placed few articles (K3's
   `articles_per_story`) and counts them all, so the page says "3 of 7". */
const OUTSIDE_WORDS = {
  label: 'Not in this digest',
  note: 'A plain page: this story has no write-up because it is not in the digest on screen.',
  gone: 'That story could not be found.',
};

async function loadOutsideStory(storyId) {
  const got = await api.search(searchQueryString('', { story_ids: [storyId] }, 'newest', 1));
  const row = got && (got.stories || []).find((s) => s.story_id === storyId);
  if (!row) return null;
  return Object.assign({}, row, { outside: true, total_articles: got.total_articles });
}

/* The pane asks about it by its story id alone: a scope's text_hashes narrow the search
   to exactly those articles, and the page knows only the few it was shown. */
function outsideScope(story) {
  S.scope.story_id = story.story_id;
  S.scope.headline = story.headline;
  S.scope.text_hashes = [];
}

function outsideDates(dates) {
  const first = dates[0];
  const last = dates[dates.length - 1];
  if (!first) return '';
  return first === last ? dateWithDay(first) : dateShort(first) + ' – ' + dateShort(last);
}

function renderOutsideStory(pane, story) {
  const articles = story.articles || [];
  const total = story.total_articles || articles.length;
  pane.append(el('p', { class: 'block-label', text: OUTSIDE_WORDS.label }));
  pane.append(el('h2', { class: 'story-headline', text: story.headline }));
  pane.append(el('p', { class: 'outside-meta',
    text: [outsideDates(story.dates || []), count(story.papers, 'paper'),
           count(total, 'article')].filter(Boolean).join(' · ') }));
  pane.append(el('p', { class: 'outside-note', text: OUTSIDE_WORDS.note }));
  pane.append(el('p', { class: 'block-label',
    text: articles.length < total
      ? 'Articles · the best-placed ' + articles.length + ' of ' + total
      : 'Articles' }));
  pane.append(el('ul', { class: 'outside-articles' }, articles.map((a) => el('li', {},
    el('button', { class: 'reset outside-open', type: 'button',
                   onclick: () => openArticle(a.text_hash) }, a.headline),
    el('p', { class: 'outside-meta' }, paperCode(a.paper) + ' ',
      pageLink(a.page_link, a.page_link ? a.page_link.label : ''),
      ' · ' + dateWithDay(a.date))))));
  const thread = threadSection(story);
  if (thread) pane.append(thread);
  pane.append(el('p', { class: 'page-footer',
    text: "Charts and photos aren't read. Use the page link to see them." }));
}

/* Opening a story: the read-on-leave rule and the rest of the reader's actions arrive
   with A6; A3 only moves which story is open. */
async function openStory(storyId) {
  if (S.openStoryId && S.openStoryId !== storyId) await leaveOpenStory();
  /* The drawer is always the digest, whatever screen is in the reading pane, so a story
     clicked there means "show me this story" -- not "open it behind the Boards screen". */
  S.screen = 'today';
  S.openBoardId = null;
  S.openStoryId = storyId;
  if (!S.openedAStory) {
    S.openedAStory = true;
    api.visit(S.ref, S.digest && S.digest.window, true);
  }

  /* Both of these decide what the story page shows, so they are fetched before it is
     drawn rather than appearing a moment later: what arrived since this story was last
     read (R4), and whether its "story so far" has been seen before. */
  const story = findStory(storyId);
  /* L-4 · a story in no digest in memory (a suggestion, a pin, an Explore row) is read
     once through a browse search limited to it; nothing answering is not remembered. */
  if (!story && !S.outside[storyId]) {
    const outsider = await loadOutsideStory(storyId);
    if (outsider || S.reachable) S.outside[storyId] = outsider;
    else delete S.outside[storyId];
  }
  const outside = story ? null : (S.outside[storyId] || null);
  if (story && !(storyId in S.newSince)) {
    const since = await api.newSince(storyId, S.ref);
    S.newSince[storyId] = (since && since.new_since_read) || [];
  }
  if ((story || outside) && !(storyId in S.threads)) {
    /* a story with no thread answers with no entries, and then no section is drawn (H3) */
    const got = await api.thread(storyId);
    S.threads[storyId] = (got && got.entries) ? got : null;
  }
  const ssfKey = 'ssf_seen:' + storyId;
  if (story && (story.story_so_far || {}).shown && !(ssfKey in S.prefs)) {
    const pref = await api.pref(ssfKey);
    S.prefs[ssfKey] = pref ? pref.value : null;
  }
  /* The pane's scope follows the open story, and puts a divider in when the story
     changes under a conversation that already has turns (§8.3 state 7). */
  if (story && S.flags.assistant) await paneStoryOpened(story);
  if (outside && S.flags.assistant) outsideScope(outside);
  /* R13 · the reader's own links are part of the thread, so they are read before the
     story is drawn rather than appearing a moment later. */
  if (story && S.flags.user_links && !(storyId in S.userLinks)) {
    await loadUserLinks(story);
  }

  render();
  document.getElementById('reading').scrollTop = 0;
}

/* What each feature flag takes off the page (§8.8, acceptance check 7).

   Written down rather than left implicit in ten `if` statements, for two reasons: a new
   flag cannot be added without saying what it hides (the test compares this map against
   the flags the server sends), and the sweep that checks a flag really does remove its
   element has something to drive it. A flag that is off removes the element completely;
   it never leaves an empty gap where it was.
*/
const FLAG_ELEMENTS = {
  graph_view: '.view-switch',
  story_so_far: '.story-so-far',
  caveat_single_source: '.caveat',
  caveat_paid_content: '.caveat',
  caveat_paid_only: '.caveat',
  caveat_partial_text: '.caveat',
  theme_line: '.theme-line',
  theme_bar_strip: '.theme-bars',
  see_also_line: '.see-also',
  assistant: '.pane',
  think_harder: '.think-harder',
  background_line: '.answer-background',
  background_on_request: '.background-button',
  cap_override: '.cap-allow',
  suggest_connections: '.suggest-offer, .suggest-box',
  explore: '.nav-item[data-screen="explore"]',
  user_links: '.suggest-actions .icon-button[aria-label="Add to thread"]',
  /* the cost header lives in the shell, so it is hidden rather than rebuilt; `hidden`
     takes it out of the layout, which is what "no empty gap" asks for */
  cost_display: '#cost-header:not([hidden])',
  agency_copy: '.agency-copy',
  followup_rewrite: '.topic-line, .turn-remove, .ask-in-story, .result-group',
};


/* ======================================================= the ask pane (brief 2 §8.2)

   Nine states (§8.3), and the thing that decides most of them is the answer itself:
   its verdict, whether it has a background line, whether something else claims it as a
   parent. So the pane is built from the conversation's turns rather than from a state
   variable -- there is no "which state am I in" to get out of step with what is on
   screen.

   The streaming is the one piece of real machinery here. `/api/ask` sends one JSON
   event per line and the page reads it with a stream reader, so a line can arrive
   split across two chunks. ndjsonSplit is the whole of that: it is pure, and tested,
   because a parser that assumes a chunk is a line works on a fast machine and loses
   the last sentence of every answer on a slow one.
*/

const PANE_WIDTHS = { open: '360px', wide: '520px', collapsed: 'var(--rail-width)' };
const PANE_RESULTS_SHOWN = 5;          /* §8.3 state 1 */

/* §7's money format, which is NOT v0's: the pane's costs are fractions of a cent, and
   v0's money() says "<1¢" for all of them, so every answer would cost the same at a
   glance. v0's wording is untouched (it is read in the header, where the numbers are
   bigger) and this is the fine one. Decision D3. */
function moneyFine(usd) {
  if (usd === null || usd === undefined) return '';
  if (!usd) return '0¢';
  const cents = usd * 100;
  if (cents < 1) return cents.toFixed(2) + '¢';
  if (usd < 1) return cents.toFixed(1) + '¢';
  return '$' + usd.toFixed(2);
}

/* A chunk is not a line. Returns the whole events in what has arrived so far and the
   half-line to carry into the next chunk. */
function ndjsonSplit(buffer, chunk) {
  const text = String(buffer || '') + String(chunk || '');
  const pieces = text.split('\n');
  const rest = pieces.pop();
  const events = [];
  for (const piece of pieces) {
    if (!piece.trim()) continue;
    events.push(JSON.parse(piece));
  }
  return { events: events, rest: rest };
}

/* §8.3 state 9, word for word. The hint is the second line; `retry` says whether the
   button is there at all, and comes from the event, not from this table -- the double
   and K3 both send `retryable`, and a reason this page has never heard of still gets
   the fallback wording rather than an empty box. */
const ERROR_WORDS = {
  model_busy: ["Couldn't get an answer · the model service is busy",
               'Usually clears within a minute.'],
  no_network: ["Couldn't get an answer · no internet connection",
               'Check the connection, then retry.'],
  key_rejected: ["Couldn't get an answer · the API key was rejected",
                 "Check the key file; retrying won't help."],
  timeout: ["Couldn't get an answer · the model took too long",
            'Retry usually works.'],
  checks_failed: ["The answer didn't pass the source checks twice",
                  'See what was found below, or try Think harder.'],
  not_running: ["Couldn't get an answer · " + NOT_RUNNING,
                'Nothing was asked and nothing was charged.'],
  other: ["Couldn't get an answer · unexpected error",
          'Retry; if it repeats, tell Claude Code.'],
};

function errorWording(event) {
  const e = event || {};
  const words = ERROR_WORDS[e.reason] || ERROR_WORDS.other;
  return { first: words[0], hint: words[1], retry: e.retryable === true };
}

/* Is there room for one more call? The answer is also the wording on a greyed Think
   harder, which is why it comes back as a pair rather than a boolean. */
function roomFor(kind) {
  const cost = S.cost || {};
  const estimate = S.estimates[kind];
  if (cost.cap_usd === null || cost.cap_usd === undefined) return { ok: true };
  if (estimate === null || estimate === undefined) return { ok: true };
  if ((cost.spent_usd || 0) + estimate > cost.cap_usd) {
    return { ok: false, why: '· not enough left today' };
  }
  return { ok: true };
}

function atCap() {
  const cost = S.cost || {};
  if (cost.cap_usd === null || cost.cap_usd === undefined) return false;
  return !roomFor('ask').ok;
}

/* Every hash the page can turn into a chip: the open digest's own rows, the articles of
   any results still in memory (the live answer's too), and -- I5 -- the passage set of
   every answer in the conversation. After a reload the results are gone (D12), so
   before I5 a cite outside the digest on screen had no chip at all: 2 of the "police"
   answer's 6 resolved. The passage set is exactly what the answer was allowed to cite,
   so every cite of a stored answer resolves from it; what still does not becomes a
   "source missing" marker in chipsFor, never nothing. Digest rows win, being the
   fullest. */
function paneHashes() {
  const map = new Map();
  for (const story of stories()) {
    for (const row of ((story.coverage && story.coverage.rows) || [])) {
      map.set(row.text_hash, row);
    }
  }
  const results = Object.values(S.results);
  if (S.live && S.live.results) results.push(S.live.results);
  for (const result of results) {
    for (const row of ((result && result.stories) || [])) {
      for (const art of (row.articles || [])) {
        if (!map.has(art.text_hash)) map.set(art.text_hash, art);
      }
    }
  }
  for (const rows of Object.values(S.passages)) {
    for (const row of (rows || [])) {
      if (!map.has(row.text_hash)) map.set(row.text_hash, row);
    }
  }
  return map;
}

/* The passage sets this conversation still needs: one per id, only for answers that
   cite something or are not covered (F3: the set is what "N articles were read"
   counts), never one already read. Think harder's answer carries its parent's
   id, so the pair costs one request. */
function passageSetsToFetch(conversation, have) {
  const ids = [];
  for (const turn of ((conversation && conversation.turns) || [])) {
    const answer = turn.kind === 'answer' ? turn.answer : null;
    if (!answer || !answer.passage_set_id) continue;
    const cites = (answer.sentences || []).some((s) => (s.cites || []).length);
    if (!(cites || answer.not_covered) || ids.includes(answer.passage_set_id)) continue;
    if (Object.prototype.hasOwnProperty.call(have || {}, answer.passage_set_id)) continue;
    ids.push(answer.passage_set_id);
  }
  return ids;
}

/* A set that is gone (404) is remembered as empty, so it is not asked for on every
   redraw and its cites draw the marker. A server that did not answer remembers nothing:
   the next load asks again. */
async function loadPassages() {
  for (const id of passageSetsToFetch(S.conversation, S.passages)) {
    const got = await api.passages(id);
    if (got) S.passages[id] = got.passages || [];
    else if (S.reachable) S.passages[id] = [];
  }
}

async function refreshConversation() {
  S.conversation = await api.conversation();
  await loadPassages();
}

/* --------------------------------------------------- what a question was asked OF

   Q3 (b) and (c), decided 2 Nov. The pane used to say "Not in your papers" whatever the
   question had been asked of, so a question about ONE open story, answered from that
   one story, reported on the whole archive. Seen on screen at C2: with the GST story
   open, "police" said "Not in your papers · searched 1 day" while the archive held four
   police stories.

   These two read the ANSWER's own stored scope, not `S.scope`, which has moved on by
   the time a reloaded conversation is drawn. `ask()` stores `{story_id, text_hashes,
   filters}` and `_scope_filters` copies an open story's id into `filters.story_ids`, so
   a story scope can arrive either way and both are counted. */
function scopeStoryCount(scope) {
  const s = scope || {};
  const ids = ((s.filters || {}).story_ids) || [];
  if (ids.length) return ids.length;
  return s.story_id ? 1 : 0;
}

/* Any filter that is not the story scope itself. `text_hashes` is not one: it travels
   with a story scope and is never a narrowing the reader chose on its own. */
function scopeHasFilters(scope) {
  const filters = (scope || {}).filters || {};
  return Object.keys(filters).some((name) => {
    if (name === 'story_ids' || name === 'text_hashes') return false;
    const value = filters[name];
    return Array.isArray(value) ? value.length > 0
                                : value !== null && value !== undefined && value !== '';
  });
}

/* Q3 (c): was this question narrowed at all? The way out is offered only when there is
   something to get out of. */
function scopeIsNarrowed(scope) {
  return scopeStoryCount(scope) > 0 || scopeHasFilters(scope);
}

/* Q3 (b): the two wordings, by scope. "Not in your papers" is the only one that claims
   anything about the archive, and it is now said only when the archive is what was
   searched. */
function scopeWords(scope) {
  const n = scopeStoryCount(scope);
  if (n === 1) return { none: 'Not in this story', weak: 'Weak match in this story' };
  if (n > 1) {
    return { none: 'Not in these ' + n + ' stories',
             weak: 'Weak match in these ' + n + ' stories' };
  }
  if (scopeHasFilters(scope)) {
    return { none: 'Not in your papers for these filters',
             weak: 'Weak match in your papers for these filters' };
  }
  return { none: 'Not in your papers', weak: 'Weak match' };
}

/* --------------------------------------------------------------- the scope chips */

/* What the question is being asked OF. The open story is one chip; a selection from
   Explore is one chip saying how many; each filter is its own. Removing a chip removes
   it from the scope, which is the only way back to the whole archive. */
function scopeChips(scope, label) {
  const s = scope || {};
  const chips = [];
  if (s.story_id && s.headline) chips.push({ key: 'story', text: s.headline });
  const filters = s.filters || {};
  if (filters.story_ids && filters.story_ids.length) {
    chips.push({ key: 'story_ids',
                 text: label || count(filters.story_ids.length, 'story') });
  }
  for (const [name, value] of Object.entries(filters)) {
    if (name === 'story_ids' || name === 'text_hashes') continue;
    const words = Array.isArray(value) ? value.join(', ') : String(value);
    if (!words) continue;
    chips.push({ key: name, text: FILTER_WORDS[name] ? FILTER_WORDS[name] + words
                                                     : words });
  }
  return chips;
}

const FILTER_WORDS = { date_from: 'from ', date_to: 'to ', papers: '', topics: '',
                       kinds: '' };

function dropScope(key) {
  if (key === 'story') { S.scope.story_id = null; S.scope.headline = null;
                         S.scope.text_hashes = []; }
  else { delete S.scope.filters[key]; if (key === 'story_ids') S.scopeLabel = null; }
  renderAssistant();
  focusComposer();
}

function renderScope(into) {
  clear(into);
  for (const chip of scopeChips(S.scope, S.scopeLabel)) {
    into.append(el('span', { class: 'scope-chip' },
      el('span', { text: chip.text, title: chip.text }),
      el('button', { class: 'reset', type: 'button',
                     'aria-label': 'Remove ' + chip.text + ' from the question',
                     onclick: () => dropScope(chip.key) }, '×')));
  }
}

/* ------------------------------------------------------------------- the results */

const MARKS = { cited: { word: 'CITED', cls: 'mark-cited' },
                found: { word: 'FOUND', cls: 'mark-found' } };

/* A row from a live search carries its articles, so the mark is worked out here from
   the answer's `cited` hashes. A row read back from a stored answer (D12) carries no
   articles -- that is what makes it compact -- so it carries its own `mark` instead,
   decided by the double when the answer was written. Either way the word on screen is
   the same word it was. */
function markFor(story, answer) {
  if (story && story.mark) return MARKS[story.mark] || MARKS.found;
  const cited = new Set((answer && answer.cited) || []);
  const hit = (story.articles || []).some((a) => cited.has(a.text_hash));
  return hit ? MARKS.cited : MARKS.found;
}

/* D12 · the results list of an answer whose search is no longer in memory, in the shape
   `resultsList` already draws. Everything it needs is on the answer: the compact rows,
   the total they were drawn out of, and the question and scope, which are what "Show
   all N in Explore" and the sort switcher re-search with. */
function storedResults(answer) {
  if (!answer || !answer.results || !answer.results.length) return null;
  return { stories: answer.results,
           total_stories: answer.results_total || answer.results.length,
           query: answer.question,
           filters: (answer.scope && answer.scope.filters) || {},
           sort: 'relevance',
           stored: true };
}

function resultRow(story, answer) {
  const mark = markFor(story, answer);
  /* A stored row has no articles, so the "BL PDF p1 · " part of the meta line is left
     off rather than invented; the date and the paper count are the row's own. */
  const best = (story.articles || [])[0];
  return el('div', { class: 'result-row' },
    el('button', { class: 'reset result-open', type: 'button',
                   onclick: () => openStory(story.story_id) },
      el('h4', { text: story.headline }),
      el('p', { class: 'result-meta',
                text: (best ? paperCode(best.paper) + ' '
                              + (best.page_link ? best.page_link.label : '') + ' · ' : '')
                      + dateShort(story.dates[story.dates.length - 1])
                      + ' · ' + count(story.papers, 'paper') })),
    el('span', { class: 'mark ' + mark.cls, text: mark.word }));
}

function resultsList(answerId, result, answer, foldHead) {
  if (!result || !result.stories || !result.stories.length) return null;
  const sort = S.sorts[answerId] || result.sort || 'relevance';
  const shown = result.stories.slice(0, PANE_RESULTS_SHOWN);
  const section = el('section', { class: 'pane-results' });

  const head = el('div', { class: 'results-head' }, foldHead
    || el('p', { class: 'block-label',
                 text: 'Found · ' + count(result.total_stories, 'story') }));
  const switcher = el('span', { class: 'sort-switch' });
  for (const name of ['relevance', 'newest']) {
    if (name === 'newest') switcher.append(' | ');
    switcher.append(el('button', {
      class: 'reset', type: 'button',
      'aria-pressed': sort === name ? 'true' : 'false',
      disabled: name === 'relevance' && !result.query ? true : null,
      onclick: () => resortResults(answerId, name, result),
    }, name === 'relevance' ? 'Relevance' : 'Newest'));
  }
  head.append(switcher);
  section.append(head);
  const groups = resultGroups(result, answer);
  if (groups) {
    for (const group of groups) {
      if (group.query) {
        section.append(el('p', { class: 'block-label result-group',
                                 text: 'For ' + group.query + ' · '
                                       + count(group.total, 'story') }));
      }
      for (const story of group.shown) section.append(resultRow(story, answer));
    }
  } else {
    for (const story of shown) section.append(resultRow(story, answer));
  }

  if (result.total_stories > shown.length) {
    section.append(paneButton('free', {
      onclick: () => openExploreFrom(result),
    }, 'Show all ' + result.total_stories + ' in Explore'));
  }
  return section;
}

/* I8 · a question about two things was searched twice (`topic: "two_searches"`), and
   each row says which search found it (`group`). The rows are drawn under one heading
   per search, in the order of `search_query`, then any search a row names that it does
   not. N counts the rows the answer holds for that search (a stored answer keeps the
   rows the pane showed plus every cited story, D12), not every hit. The pane still shows
   PANE_RESULTS_SHOWN rows in all. Flag off, or no row grouped: null, the plain list. */
function resultGroups(result, answer) {
  if (!S.flags.followup_rewrite) return null;
  const stories = (result && result.stories) || [];
  if (!stories.some((s) => s.group)) return null;
  const order = Array.isArray(answer && answer.search_query) ? answer.search_query.slice() : [];
  for (const s of stories) if (s.group && order.indexOf(s.group) < 0) order.push(s.group);
  const shown = stories.slice(0, PANE_RESULTS_SHOWN);
  const groups = order.map((query) => ({
    query: query,
    total: stories.filter((s) => s.group === query).length,
    shown: shown.filter((s) => s.group === query),
  })).filter((g) => g.total);
  const loose = shown.filter((s) => !s.group);
  if (loose.length) groups.push({ query: null, total: loose.length, shown: loose });
  return groups;
}

async function resortResults(answerId, sort, from) {
  const result = S.results[answerId] || from;
  if (!result) return;
  S.sorts[answerId] = sort;
  const again = await api.search(searchQueryString(result.query, result.filters, sort));
  S.results[answerId] = again;
  renderAssistant();
}

function searchQueryString(query, filters, sort, limit) {
  const bits = ['q=' + encodeURIComponent(query || '')];
  if (sort) bits.push('sort=' + encodeURIComponent(sort));
  if (limit) bits.push('limit=' + String(limit));
  for (const [name, value] of Object.entries(filters || {})) {
    const key = name === 'text_hashes' ? 'hashes' : name;
    for (const one of (Array.isArray(value) ? value : [value])) {
      bits.push(key + '=' + encodeURIComponent(one));
    }
  }
  return bits.join('&');
}

/* ------------------------------------------------------------------ one answer */

/* F5 (board "K1 (7)") · "Pin to board" is "Pin" everywhere in the pane; the menu it opens
   is unchanged, and the story page's pins keep their words. */
const PANE_PIN = 'Pin';

function answerPinButton(answer) {
  return pinControl('answer', () => ({
    payload: { answer_id: answer.answer_id, question: answer.question,
               sentences: answer.sentences, cited: answer.cited, model: answer.model },
    source: { conversation_id: answer.conversation_id },
  }), { class: 'btn btn-save', text: PANE_PIN });
}

function sampleTag() {
  return el('span', { class: 'fixture-tag', text: 'sample data' });
}

/* The same block, with the label the state calls for: an answer that found nothing
   says the background is not in the archive (state 6), one that found something says
   it is not from the papers (state 2). */
function backgroundBlock(answer) {
  if (!S.flags.background_line || !answer.background) return null;
  return el('div', { class: 'answer-background' },
    el('p', { class: 'block-label',
              text: answer.verdict === 'empty' ? 'Not in the archive · background'
                                               : 'Background · not from your papers' }),
    el('p', { text: answer.background }));
}

/* I6 · the background call returns an object, not a stream, so its refusal (a cap, a
   failed call, an answer that already has one: all `ValueError` -> 400) arrives as the
   server's message; it is shown under the answer with a Retry. While the call runs the
   button is disabled, so a second press cannot pay twice (as K-3 for suggestions). */
async function askBackground(answerId) {
  if (S.backgroundBusy[answerId]) return;
  S.backgroundBusy[answerId] = true;
  delete S.backgroundError[answerId];
  renderAssistant();
  const got = await api.background(answerId);
  delete S.backgroundBusy[answerId];
  if (got && got.error && got.error.status === 400 && isAlreadyRunning(got.error.message)) {
    markStillRunning(answerId);
  } else if (got && got.error) {
    S.backgroundError[answerId] = got.error;
  } else {
    const answer = answerById(answerId);
    if (answer && got) answer.background = got.text;
  }
  const cost = await api.cost();
  if (cost) S.cost = cost;
  renderAssistant();
}

function answerById(answerId) {
  for (const turn of ((S.conversation && S.conversation.turns) || [])) {
    if (turn.kind === 'answer' && turn.answer.answer_id === answerId) return turn.answer;
  }
  return null;
}

/* Q3 (c) · the way out. Take every chip off the scope and ask the same question again
   as a new turn -- a new turn, not a rewrite of this one, because the two answers are
   about different things and the narrow one is still the answer to what was asked.
   `askNow` builds its scope from `S.scope`, so clearing that first is the whole job. */
function clearScope() {
  S.scope.story_id = null;
  S.scope.headline = null;
  S.scope.text_hashes = [];
  S.scope.filters = {};
  S.scopeLabel = null;
}

async function askAllPapers(question) {
  clearScope();
  renderAssistant();
  await askNow(question);
}

/* I7 · one button shape, three roles (brief §5): `paid` spends money and always carries
   its cost, `free` changes what is shown or asked, `save` keeps something. Ask pane only;
   the left panel and the main page keep `.action`. */
const PANE_BUTTON = {               /* written out whole, so the suite can read them */
  paid: { class: 'reset btn btn-paid' },
  free: { class: 'reset btn btn-free' },
  save: { class: 'reset btn btn-save' },
};

/* `props.class` adds a class of the button's own (a flag's hook, a test's handle). */
function paneButton(role, props, ...children) {
  const own = props && props.class;
  return el('button', Object.assign({}, props, {
    class: PANE_BUTTON[role].class + (own ? ' ' + own : ''), type: 'button',
  }), ...children);
}

/* F6 · a scope correction re-asks in place (flag on), so it spends the ask price and
   says so as every spending button does; no estimate read, no price. */
function buttonPrice(kind) {
  const usd = S.estimates[kind];
  return usd === null || usd === undefined ? null : [' · ', costTag(usd)];
}

function costTag(usd) {
  return el('span', { class: 'btn-cost', text: '≈ ' + moneyFine(usd) });
}

/* F5 (pane C) · every answer starts with ONE answer line, the same for both models:
   "Haiku · 0.08¢", "Sonnet · same N passages · 2.1¢", and on a Haiku answer that folds
   the line itself is the switch, "▸ Haiku · 0.09¢" / "▾ …" (T-48 rules unchanged). It
   replaces the old fold label, the old Sonnet label and the information line above the
   buttons (I7, T-29). An empty answer cost nothing because no model was asked, and
   "Haiku · 0¢" would imply one was (§8.3 state 5); a not-covered answer names its model
   and cost like any answer; a paid empty one paid only to read a follow-up (F3). */
function answerWords(answer) {
  const none = answer.verdict === 'empty' && !answer.not_covered;
  const model = answer.model === 'sonnet' ? sonnetLabel(answer) : 'Haiku';
  const bits = [!none ? model + ' · ' + moneyFine(answer.cost_usd)
                : answer.cost_usd > 0 ? EMPTY_PAID_WORDS(moneyFine(answer.cost_usd))
                : 'no model call · 0¢'];
  if (answer.fixture) bits.push('sample data');
  return bits.join(' · ');
}

function answerLine(answer, o) {
  const opts = o || {};
  if (!opts.foldable) return el('p', { class: 'answer-line', text: answerWords(answer) });
  return el('button', {
    class: 'reset answer-line answer-fold', type: 'button',
    'aria-expanded': opts.folded ? 'false' : 'true',
    onclick: () => { S.folded[answer.answer_id] = !opts.folded; renderAssistant(); },
  }, (opts.folded ? '▸ ' : '▾ ') + answerWords(answer));
}

const EMPTY_PAID_WORDS = (cost) => 'No answer written · ' + cost + ' to read your follow-up';

/* F3 (sheet item 6 B) · not covered inside a story. N is the story's articles the
   writer was given: distinct text_hash in the answer's passage set, once it is read
   (I5); until then the count is left out rather than guessed. */
const NOT_COVERED_WORDS = {
  head: 'Not in this story',
  read: (n) => n ? "The story's " + count(n, 'article') + (n === 1 ? ' was' : ' were')
                   + ' read; none says this.'
                 : "The story's articles were read; none says this.",
};

function notCoveredCount(answer) {
  const rows = S.passages[answer.passage_set_id];
  if (!rows || !rows.length) return null;
  return new Set(rows.map((r) => r.text_hash)).size;
}

/* In the order the brief gives: paid, then free, then save. "Ask all papers instead"
   is free now (it was ochre, Q3 c): it widens the question, and the question it then
   asks is what costs, at the usual price. */
function actionsRow(answer) {
  if (S.stillRunning[answer.answer_id]) return stillWorkingLine();
  const none = answer.verdict === 'empty';
  const row = el('div', { class: 'answer-actions' });

  if (S.flags.think_harder && answer.model === 'haiku' && answer.verdict !== 'empty') {
    const room = roomFor('think_harder');
    row.append(paneButton('paid', { class: 'think-harder',
      disabled: room.ok ? null : true,
      onclick: () => runThinkHarder(answer.answer_id),
    }, 'Think harder · ', costTag(S.estimates.think_harder),
       room.ok ? null : ' ' + room.why));
  }

  /* While it runs the button says so and carries no price: the call is already made. */
  if (S.flags.background_on_request && !answer.background) {
    const busy = Boolean(S.backgroundBusy[answer.answer_id]);
    row.append(paneButton('paid', { class: 'background-button',
      disabled: busy ? true : null, 'aria-busy': busy ? 'true' : null,
      onclick: () => askBackground(answer.answer_id),
    }, busy ? 'Writing background…'
            : [none ? 'Answer from background knowledge · ' : 'Background · ',
               costTag(S.estimates.background)]));
  }

  /* Only when there is a scope to drop: on an answer that found nothing or almost
     nothing, widening the question is the likeliest next move, and on an unscoped
     answer there is nothing to widen to. */
  if ((none || answer.verdict === 'thin') && scopeIsNarrowed(answer.scope)) {
    row.append(paneButton('free', { class: 'ask-all-papers',
      onclick: () => (S.flags.followup_rewrite
        /* A Think harder answer has no question of its own: its parent is re-asked. */
        ? reaskInPlace(answer.parent_answer_id || answer.answer_id, { scope: ALL_SCOPE })
        : askAllPapers(answer.question)),
    }, 'Ask all papers instead', S.flags.followup_rewrite ? buttonPrice('ask') : null));
  }

  /* I8 · the other way: asked of all papers while a story is open. Flag only. */
  if (S.flags.followup_rewrite && !answer.parent_answer_id
      && !(answer.scope || {}).story_id && S.scope.story_id) {
    row.append(paneButton('free', { class: 'ask-in-story',
      onclick: () => reaskInPlace(answer.answer_id, { scope: {
        story_id: S.scope.story_id, text_hashes: S.scope.text_hashes || [],
        filters: (answer.scope || {}).filters || {} } }),
    }, 'Ask in this story instead', buttonPrice('ask')));
  }

  row.append(answerPinButton(answer));
  return row;
}

/* States 2, 4, 5 and 6 are all this function: what differs is the note above the
   sentences and whether there are any. */
/* I6 · a refused call, in the place its button was: the server's own message and a
   Retry. Used by Background and Suggest connections. */
function refusedBox(error, retry, what, paidCost) {
  return el('div', { class: 'pane-error' },
    el('p', { class: 'error-first',
              text: error.status === 0 ? NOT_RUNNING : what + ' · ' + error.message }),
    paidCost === undefined || paidCost === null
      ? el('button', { class: 'reset action', type: 'button', onclick: retry }, 'Retry')
      : paneButton('paid', { onclick: retry }, 'Retry · ', costTag(paidCost)));
}

/* I6 · meaning search is off: K3's search() then says `mode: "fts_only"` (absent in
   normal operation, F-3), and the results are word matches only. */
const WORDS_ONLY = "Word search only: meaning search isn't running";

function searchModeNote(result) {
  if (!result || result.mode !== 'fts_only') return null;
  return el('p', { class: 'mode-note', text: WORDS_ONLY });
}

/* F3 · on a finished answer the note is read from the STORED answer, so it survives a
   reload; an answer stored before K3's FP-2 has no `mode` and gets no note (T-11). */
function answerModeNote(answer) {
  return searchModeNote({ mode: (answer || {}).mode });
}

/* I7 · the turn layout (brief §4.5, always): a marker column, Q beside the question and
   A beside the answer. The marker letters are real text, so a screen reader hears
   "Q, what happened…" and "A, …" in the order they are drawn. */
const TURN_ROW = { Q: { class: 'turn-row turn-q' }, A: { class: 'turn-row turn-a' } };

/* F7 · `end` is what sits at the right end of the row's first line (Remove), in a third
   column of its own, so its right edge is the same on every turn. */
function turnRow(which, content, end) {
  return el('div', { class: TURN_ROW[which].class + (end ? ' has-end' : '') },
            el('span', { class: 'turn-mark', text: which }), content, end || null);
}

function questionRow(question, end) {
  return turnRow('Q', el('p', { class: 'pane-you', text: question }), end);
}

function answerTurn(answer, opts) {
  const o = opts || {};
  const byHash = paneHashes();
  /* F5 · only the latest answer shows its button row and results; an older one is quiet
     (`is-older`: its row shows on hover or keyboard focus, CSS; its results fold). */
  const block = el('div', { class: 'pane-turn' + (o.older ? ' is-older' : '') });
  /* F7 (call 6 C) · Remove at the right end of the line it belongs to: the question's
     line (the question, both answers and the background go), or a Think harder answer's
     answer line (only it goes; the Haiku answer unfolds). A Haiku answer's own line,
     folded or not, has none. */
  if (!o.noQuestion) block.append(questionRow(answer.question, removeButton(answer)));
  const body = el('div', { class: 'turn-body' });
  block.append(turnRow('A', body, answer.parent_answer_id ? removeButton(answer) : null));

  /* L-2 · a Haiku answer with a Sonnet answer under it: its answer line is the switch,
     "▸" folded, "▾" open, both ways. Folded, the line is all there is. */
  body.append(answerLine(answer, o));
  if (o.foldable && o.folded) return block;

  if (answer.not_covered) {
    body.append(el('p', { class: 'answer-none' },
      el('strong', { text: NOT_COVERED_WORDS.head })));
    body.append(el('p', { class: 'answer-hint',
                          text: NOT_COVERED_WORDS.read(notCoveredCount(answer)) }));
  } else if (answer.verdict === 'empty') {
    /* The number comes from the answer itself, falling back to the results while they
       are still in memory. If neither has it, the clause is left off rather than
       printed as "searched 0 days", which would be a claim about the archive that the
       page cannot stand behind. */
    const days = answer.searched_days
                 || (S.results[answer.answer_id] || {}).searched_days
                 || null;
    body.append(el('p', { class: 'answer-none' },
      el('strong', { text: scopeWords(answer.scope).none }),
      days ? ' · searched ' + count(days, 'day') : ''));
    if (answer.closest && answer.closest.length) {
      body.append(el('p', { class: 'block-label', text: 'Closest stories' }));
      body.append(el('ul', { class: 'closest' }, answer.closest.map((story) =>
        el('li', {},
          el('button', { class: 'reset', type: 'button',
                         onclick: () => openStory(story.story_id) }, story.headline),
          el('span', { class: 'when',
                       text: ' · ' + dateShort(story.dates[story.dates.length - 1]) })))));
    }
  } else {
    /* Never "Weak match" inside a story (F3): there the verdict is the outcome. */
    if (answer.verdict === 'thin' && !(answer.scope || {}).story_id) {
      body.append(el('p', { class: 'answer-weak',
                             text: scopeWords(answer.scope).weak + ' · '
                                   + count(answer.found_count, 'article') + ' found' }));
    }
    const text = el('div', { class: 'answer-text' });
    for (const sentence of (answer.sentences || [])) {
      text.append(el('p', {}, sentence.text, ' ',
                     chipsFor(sentence.cites, byHash)));
    }
    body.append(text);
  }

  const back = backgroundBlock(answer);
  if (back) body.append(back);
  const topic = topicLine(answer, o.first);
  if (topic) body.append(topic);
  const widen = widenLine(answer);
  if (widen) body.append(widen);
  body.append(actionsRow(answer));
  const refused = S.backgroundError[answer.answer_id];
  if (refused) {
    body.append(refusedBox(refused, () => askBackground(answer.answer_id),
                           'No background line', S.estimates.background));
  }

  if (!o.noResults) {
    const result = S.results[answer.answer_id] || storedResults(answer);
    const note = answerModeNote(answer);
    if (note) body.append(note);
    const list = o.older ? foldedResults(answer.answer_id, result, answer)
                         : resultsList(answer.answer_id, result, answer);
    if (list) body.append(list);
  }
  return block;
}

/* F5 · an older answer's results fold to one small grey line, "▸ Found · N stories";
   a click opens the list in place (in memory, like the Haiku fold), and the open list's
   heading is the same line with "▾", which folds it again. */
function foldedResults(answerId, result, answer) {
  if (!result || !result.stories || !result.stories.length) return null;
  const open = Boolean(S.resultsOpen[answerId]);
  const words = (open ? '▾ ' : '▸ ') + 'Found · ' + count(result.total_stories, 'story');
  const fold = el('button', { class: 'reset results-fold', type: 'button',
                              'aria-expanded': open ? 'true' : 'false',
                              onclick: () => { S.resultsOpen[answerId] = !open;
                                               renderAssistant(); } }, words);
  if (!open) return el('section', { class: 'pane-results is-folded' }, fold);
  return resultsList(answerId, result, answer, fold);
}

/* Does the streamed turn stay on screen once the stream has ended?

   This is the bug that hid behind a frame. A finished ANSWER becomes a conversation
   turn, so dropping the live copy is right -- the redraw puts the same answer back from
   `current_conversation()`. An ERROR never becomes a turn: the double returns straight
   after the error event. So dropping the live copy destroys the error one frame after
   it was drawn, and state 9's Retry button can never be reached. It is kept instead,
   until the next question replaces it.

   The cap is the exception. It has its own band above the composer and greys the
   composer with it (§8.3 state 9), so a live error box would say the same thing twice
   -- and say it wrongly, because a cap_reached event carries no `reason` and
   errorWording() would call it "unexpected error". */
function keepLiveAfterStream(live) {
  if (!live || !live.error) return false;
  return live.error.code !== 'cap_reached';
}

/* §8.3 state 3: "SONNET · SAME 8 PASSAGES". The count is the passage set -- how many
   articles the first answer was allowed to cite -- which is the whole claim Think harder
   makes: the same passages, read better. D12 puts `passage_count` on the answer itself,
   so the number survives a reload; the in-memory parent result is the fallback for an
   answer written before that field existed, and only if neither has it does the label
   say "the same passages" rather than an invented number. */
function sonnetLabel(answer) {
  if (answer.passage_count) {
    return 'Sonnet · same ' + count(answer.passage_count, 'passage');
  }
  const parent = S.results[answer.parent_answer_id];
  if (!parent) return 'Sonnet · the same passages';
  const passages = (parent.stories || [])
    .reduce((n, s) => n + (s.articles || []).length, 0);
  return 'Sonnet · same ' + count(passages, 'passage');
}

/* ------------------------------------------------------------------ the streaming */

async function streamAnswer(url, body, onEvent) {
  let response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (e) {
    /* K-4 · nothing answered, so nothing was asked or charged: say so, offer Retry. */
    S.reachable = false;
    onEvent({ type: 'error', code: 'unreachable', reason: 'not_running', retryable: true });
    return;
  }
  S.reachable = true;
  if (!response.ok || !response.body) {
    onEvent(await refusalEvent(response));
    return;
  }
  const reader = response.body.getReader();
  const decode = new TextDecoder();
  let buffer = '';
  for (;;) {
    const step = await reader.read();
    if (step.done) break;
    const split = ndjsonSplit(buffer, decode.decode(step.value, { stream: true }));
    buffer = split.rest;
    for (const event of split.events) onEvent(event);
  }
  const last = ndjsonSplit(buffer, '\n');
  for (const event of last.events) onEvent(event);
}

/* F6 · a stream refused before its first event (K3's `ValueError` -> 400, `LookupError`
   -> 404) carries K3's reason, shown with Retry as every other refusal (T-23). One reason
   is not a failure: the same paid action is still running from an earlier press (TC-4),
   so the page waits for it instead (`already_running`, no Retry). Anything without a
   reason (a 500, an unreadable body) keeps the old "unexpected error". */
async function refusalEvent(response) {
  let got = null;
  try { got = await response.json(); } catch (e) { got = null; }
  const message = got && typeof got.detail === 'string' ? got.detail : null;
  if (!message || (response.status !== 400 && response.status !== 404)) {
    return { type: 'error', code: 'model_error', reason: 'other', retryable: true };
  }
  if (response.status === 400 && isAlreadyRunning(message)) {
    return { type: 'error', code: 'already_running', message: message, retryable: false };
  }
  return { type: 'error', code: 'refused', status: response.status, message: message,
           retryable: true };
}

/* The contract says the 400's message STARTS with "already running" (§12.2 #9); K3's
   real message is "<action> is already running for <id>" (p3_search `_in_flight`). The
   real code wins on a shape (brief header), so the phrase is matched anywhere. */
const ALREADY_RUNNING = /\balready running\b/i;
function isAlreadyRunning(message) { return ALREADY_RUNNING.test(String(message || '')); }

/* L-3 · while a stream runs, words say what is working, not only the caret. */
function workingWords(live) {
  return (live.parentOf ? 'Sonnet' : 'Haiku') + ' is writing…';
}

/* L-1 · Think harder's live turn has no question of its own (its parent shows it), so
   it is drawn as the stored one will be: an A row only. */
function liveTurn() {
  const live = S.live;
  if (!live) return null;
  const byHash = paneHashes();
  const block = el('div', { class: 'pane-turn' });
  /* F5 · a Think harder turn has no question row and no label above it: its working
     line "Sonnet is writing…" is the A row's first line, as the answer line will be. */
  if (!live.parentOf) block.append(questionRow(live.question));
  const body = el('div', { class: 'turn-body' });
  block.append(turnRow('A', body));

  if (live.error && live.error.code === 'cap_reached') {
    /* Nothing here: the band above the composer already says the cap is reached, with
       the real numbers in it. Drawing a box too would repeat it in the wrong words. */
    void 0;
  } else if (live.error && live.error.code === 'already_running') {
    body.append(stillWorkingLine());
  } else if (live.error) {
    const words = live.error.code === 'refused'
      ? { first: "Couldn't get an answer · " + live.error.message, hint: REFUSED_HINT,
          retry: true }
      : errorWording(live.error);
    const box = el('div', { class: 'pane-error' },
      el('p', { class: 'error-first', text: words.first }),
      el('p', { class: 'error-hint', text: words.hint }));
    if (words.retry) {
      /* L-11 · a failed Think harder retries Think harder, at its own price */
      box.append(paneButton('paid', {
        onclick: () => (live.retry ? live.retry() : askNow(live.question)) },
                            'Retry · ', costTag(live.parentOf ? S.estimates.think_harder
                                                              : S.estimates.ask)));
    }
    body.append(box);
  } else {
    /* L-12 · before any words arrive the working line IS the A row's first line (the box
       centres on its capitals, K-7); an empty text block drawn first left the box on a
       blank line with the words one line below. Once text streams, it goes first. */
    const working = live.answer ? null
      : el('p', { class: 'answer-working', role: 'status', text: workingWords(live) });
    if (live.sentences.length || live.answer) {
      const text = el('div', { class: 'answer-text' });
      for (const sentence of live.sentences) {
        text.append(el('p', {}, sentence.text, ' ', chipsFor(sentence.cites, byHash)));
      }
      text.append(el('span', { class: 'answer-caret', 'aria-hidden': 'true' }));
      body.append(text);
    }
    if (working) body.append(working);
  }

  if (live.results) {
    const note = searchModeNote(live.results);
    if (note) body.append(note);
    const list = resultsList('live', live.results, null);
    if (list) body.append(list);
  }
  return block;
}

function takeLiveEvent(event) {
  if (!S.live) return;
  if (event.type === 'results') { S.live.results = event.search; }
  else if (event.type === 'sentence') { S.live.sentences.push(event); }
  else if (event.type === 'error') { S.live.error = event; }
  else if (event.type === 'done') { S.live.answer = event.answer; }
  renderAssistant();
}

async function askNow(question, options) {
  const o = options || {};
  if (!question || !question.trim()) return;
  S.widened = null;              /* the next question has been asked: the offer is spent */
  const scope = { story_id: S.scope.story_id, text_hashes: S.scope.text_hashes || [],
                  filters: S.scope.filters || {} };
  const divider = o.body ? null
    : dividerFor(S.conversation, Object.assign({ label: S.scopeLabel }, S.scope));
  if (divider) {
    await api.divider(conversationId(), divider.story_id, divider.headline);
    await refreshConversation();
  }
  S.live = { question: question, sentences: [], results: null, error: null };
  S.paneEvent = 'ask';           /* L-13 · jump to the new question */
  renderAssistant();

  await streamAnswer(o.url || '/api/ask',
                     o.body || { question: question, scope: scope,
                                 conversation_id: conversationId() },
                     takeLiveEvent);

  const live = S.live;
  if (!keepLiveAfterStream(live)) S.live = null;
  if (live && live.answer) {
    if (live.results) S.results[live.answer.answer_id] = live.results;
    if (live.parentOf) S.folded[live.parentOf] = true;
  }
  await refreshConversation();
  S.cost = await api.cost();
  renderAssistant();
}

async function runThinkHarder(answerId) {
  const parent = answerById(answerId);
  S.live = { question: (parent ? parent.question : ''), sentences: [], results: null,
             error: null, parentOf: answerId,
             retry: () => runThinkHarder(answerId) };    /* L-11 · not a new question */
  delete S.folded[answerId];     /* L-2 · folds on the press (foldState), whatever was chosen */
  renderAssistant();
  await streamAnswer('/api/think_harder', { answer_id: answerId }, (event) => {
    if (!S.live) return;
    if (event.type === 'sentence') S.live.sentences.push(event);
    else if (event.type === 'error') S.live.error = event;
    else if (event.type === 'done') S.live.answer = event.answer;
    renderAssistant();
  });
  const live = S.live;
  if (stillRunningFrom(live, answerId)) S.live = null;
  if (!keepLiveAfterStream(S.live)) S.live = null;
  if (live && live.answer) S.results[live.answer.answer_id] = S.results[answerId];
  await refreshConversation();
  S.cost = await api.cost();
  renderAssistant();
}

function conversationId() {
  return S.conversation ? S.conversation.conversation_id : null;
}

/* ------------------------------------------------------------------ the cap band */

function capBand() {
  if (!atCap()) return null;
  const cost = S.cost || {};
  const band = el('div', { class: 'cap-band' },
    el('p', {}, el('strong', { text: 'Daily cap $' + (cost.cap_usd || 0).toFixed(2)
                                     + ' reached' }),
      ' · spent $' + (cost.spent_usd || 0).toFixed(2) + ' today'));
  if (!S.flags.cap_override) return band;

  const actions = el('div', { class: 'cap-actions' });
  actions.append(paneButton('paid', { class: 'cap-allow',
    onclick: () => showCapConfirm(actions),
  }, 'Allow ', el('span', { class: 'btn-cost', text: '$1' }), ' more today'));
  actions.append(el('span', { class: 'cap-ends', text: 'Ends at midnight · logged' }));
  band.append(actions);
  return band;
}

/* In the page, never a browser dialog: confirm() cannot be styled, cannot be checked
   for contrast, and freezes everything behind it. */
function showCapConfirm(actions) {
  const cost = S.cost || {};
  clear(actions);
  actions.append(el('span', { text: "Raise today's cap to $"
                                    + ((cost.cap_usd || 0) + 1).toFixed(2) + '?' }));
  actions.append(paneButton('paid', { class: 'cap-allow',
    onclick: async () => {
      S.cost = await api.raiseCap();
      renderAssistant();
      focusComposer();
    },
  }, 'Yes, allow ', el('span', { class: 'btn-cost', text: '+$1' })));
  actions.append(paneButton('free', { onclick: () => renderAssistant() }, 'Cancel'));
}

/* ======================================================= I8 · follow-ups (brief §4.5)

   Behind flag `followup_rewrite`, off until K3's final pass (contract v2.4 §11.2) lands
   `reask`, `hide_turn` and the answer's `topic`. Each answer after a conversation's first
   says in one grey line what was done with it -- carried the earlier topic, searched it
   alone, searched twice, searched once -- and offers the one opposite, which re-asks it
   in place. `topic` missing or unknown: no line (§6 rule 6: nothing to say honestly). */

const TOPIC_WORDS = {
  /* F6 · with K3's `earlier_label` (2-5 words naming the earlier topic) the line names
     the topic; without it, T-37's quote of the earlier question. */
  follow_up: { said: (e, named) => named ? 'Follow-up of the ' + e + ' topic'
                                         : 'Follow-up of your ' + e + ' question',
               redone: (e) => 'Asked in the ' + e + ' topic',
               act: () => 'Treat as new topic', mode: 'as_typed' },
  new_topic: { said: () => 'New topic', redone: () => 'Asked as a new topic',
               act: (e) => 'Ask in the ' + e + ' topic instead', mode: 'carry' },
  two_searches: { said: () => 'Two searches', redone: () => 'Searched as two topics',
                  act: () => 'Search as one question', mode: 'one_search' },
  /* F6 · K3's `two_searches` mode (call 5 A) reuses the two queries of the nearest split
     answer up the chain; drawn only here, on an answer searched as one. */
  one_search: { said: () => 'One search', redone: () => 'Searched as one question',
                act: () => 'Search as two topics', mode: 'two_searches' },
};

const EARLIER_WORDS = 4;
const ALL_SCOPE = { story_id: null, text_hashes: [], filters: {} };
const TURN_REMOVED = 'Removed from this conversation';
const TURN_UNDONE = 'remove from this conversation';     /* "Undone · …" after an Undo */
const REMOVE_TOAST_SECONDS = 5;

/* "[earlier]": the earlier question, cut to its first four words, in quotes so it reads
   as the reader's own words inside the sentence. No question found: the word "earlier". */
function earlierWords(question) {
  const words = String(question || '').replace(/[?.!\s]+$/, '').split(/\s+/).filter(Boolean);
  if (!words.length) return 'earlier';
  const cut = words.slice(0, EARLIER_WORDS).join(' ');
  return '“' + cut + (words.length > EARLIER_WORDS ? '…' : '') + '”';
}

/* The question whose topic was carried (`carried_from`), or, with none named or that
   one gone, the answer just before this one: the topic a carry would take. If that one
   was itself a follow-up, its own topic's question names the topic better ("the EPFO
   topic", not "the and-what-did-the-unions-say topic"). */
function earlierQuestion(answer) {
  const carried = answer.carried_from ? answerById(answer.carried_from) : null;
  if (carried) return carried.question;
  let before = null;
  for (const turn of ((S.conversation && S.conversation.turns) || [])) {
    if (turn.kind !== 'answer') continue;
    if (turn.answer.answer_id === answer.answer_id) break;
    if (!turn.answer.parent_answer_id) before = turn.answer;
  }
  const root = before && before.carried_from ? answerById(before.carried_from) : null;
  return (root || before || {}).question || null;
}

/* F6 · the earlier topic's name: K3's `earlier_label` when the rewriter gave one, else
   the 4-word quote. `named` says which, for the one wording that differs. */
function earlierName(answer) {
  const label = String(answer.earlier_label || '').trim();
  return label ? { text: label, named: true }
               : { text: earlierWords(earlierQuestion(answer)), named: false };
}

/* F6 (call 10 A) · a correction re-asks, at the ask price: "· ≈ 0.08¢" after it, in the
   line's grey. No estimate read: no price rather than a guessed one. */
function correctionPrice() {
  const ask = S.estimates.ask;
  if (ask === null || ask === undefined) return null;
  return el('span', { class: 'topic-price', text: ' · ≈ ' + moneyFine(ask) });
}

function topicLine(answer, first) {
  if (!S.flags.followup_rewrite || first || answer.parent_answer_id) return null;
  const words = TOPIC_WORDS[answer.topic];
  if (!words) return null;
  const earlier = earlierName(answer);
  return el('p', { class: 'topic-line' },
    (answer.replaces_answer_id ? words.redone(earlier.text)
                               : words.said(earlier.text, earlier.named)) + ' · ',
    el('button', { class: 'reset topic-act', type: 'button',
                   onclick: () => reaskInPlace(answer.answer_id, { mode: words.mode }) },
       words.act(earlier.text)),
    correctionPrice());
}

/* F6 (TC-4, sheet item 8 A) · the same paid action is still running from an earlier
   press (another window, or a reload mid-answer). In place of the answer's button row,
   one italic grey line and no Retry; the pane re-reads the conversation when the window
   regains focus and once after 30 s, and each re-read gives the row back (a press while
   it still runs is refused again, free, and says so again). */
const STILL_WORKING = 'Still working on this · it appears here when done';
const STILL_REREAD_MS = 30000;
const REFUSED_HINT = 'Nothing was asked and nothing was charged.';

function stillWorkingLine() {
  return el('p', { class: 'still-working', role: 'status', text: STILL_WORKING });
}

function markStillRunning(answerId) {
  S.stillRunning[answerId] = true;
  setTimeout(rereadIfStillRunning, STILL_REREAD_MS);
}

/* After a stream: was it refused as already running? Then the answer it was for waits. */
function stillRunningFrom(live, answerId) {
  if (!live || !live.error || live.error.code !== 'already_running') return false;
  markStillRunning(answerId);
  return true;
}

async function rereadIfStillRunning() {
  if (!Object.keys(S.stillRunning).length) return;
  S.stillRunning = {};
  await refreshConversation();
  renderAssistant();
}

/* K-6 (Kunaal, 4 Nov, option c) · a scope correction re-asks one answer and leaves the
   chip alone (T-40), so after "Ask all papers instead" the NEXT question still goes to
   the story. One grey line, in the same pattern as the corrections, says so and offers
   to follow: tapping it takes the story off the chip and asks nothing. Only on the answer
   just widened, only while it is the last answer and a story is still on the chip; the
   next question, a new conversation or a reload ends it (in memory only). */
const WIDEN_WORDS = { said: 'Next question: this story', act: 'Switch to all papers' };

function widenLine(answer) {
  if (!S.flags.followup_rewrite || !S.widened || answer.answer_id !== S.widened) return null;
  if (!S.scope.story_id) return null;
  const answers = paneTurns(S.conversation)
    .filter((t) => t.kind === 'answer' && !t.answer.parent_answer_id);
  const last = answers.length ? answers[answers.length - 1].answer : null;
  if (!last || last.answer_id !== answer.answer_id) return null;
  return el('p', { class: 'topic-line' }, WIDEN_WORDS.said + ' · ',
    el('button', { class: 'reset topic-act', type: 'button',
                   onclick: () => { S.widened = null; clearScope(); renderAssistant();
                                    focusComposer(); } },
       WIDEN_WORDS.act));
}

/* The order the pane draws: an answer that names the one it replaces takes that one's
   place (contract: same position), whatever order the conversation lists them in, and a
   chain of re-asks ends at the newest. With K3 hiding the old turn this changes nothing;
   it is the page's own guard for "same position". */
/* L-2 · whether a Haiku answer can fold, and whether it is folded now. It folds while
   Think harder runs on it and while a Sonnet answer to it is in the conversation, so
   the fold is read from the conversation and survives a reload; a failed Think harder
   leaves it open. S.folded holds only what the reader chose with the switch. */
function foldState(answerId) {
  const turns = (S.conversation && S.conversation.turns) || [];
  const running = Boolean(S.live && S.live.parentOf === answerId && !S.live.error);
  const foldable = running || turns.some((t) => t.kind === 'answer'
                                         && t.answer.parent_answer_id === answerId);
  if (!foldable) return { foldable: false, folded: false };
  return { foldable: true,
           folded: answerId in S.folded ? Boolean(S.folded[answerId]) : true };
}

function paneTurns(conversation) {
  const turns = (conversation && conversation.turns) || [];
  const ids = new Set(turns.filter((t) => t.kind === 'answer').map((t) => t.answer.answer_id));
  const by = {};
  for (const t of turns) {
    const old = t.kind === 'answer' && t.answer.replaces_answer_id;
    if (old && ids.has(old)) by[old] = t;
  }
  const out = [];
  for (const t of turns) {
    if (t.kind !== 'answer') { out.push(t); continue; }
    if (ids.has(t.answer.replaces_answer_id)) continue;
    let shown = t;
    const seen = new Set();
    while (by[shown.answer.answer_id] && !seen.has(shown)) {
      seen.add(shown);
      shown = by[shown.answer.answer_id];
    }
    out.push(shown);
  }
  return out;
}

/* Re-ask in place: the same stream as a question, drawn where the answer was. One at a
   time, so a second press cannot pay twice; a kept error from earlier does not block. */
async function reaskInPlace(answerId, change) {
  if (!S.flags.followup_rewrite) return;
  if (S.live && !S.live.error) return;
  const old = answerById(answerId);
  S.live = { question: old ? old.question : '', sentences: [], results: null,
             error: null, replaces: answerId,
             retry: () => reaskInPlace(answerId, change) };
  renderAssistant();
  await streamAnswer('/api/reask', Object.assign({ answer_id: answerId }, change),
                     takeLiveEvent);
  const live = S.live;
  if (stillRunningFrom(live, answerId)) S.live = null;
  if (!keepLiveAfterStream(S.live)) S.live = null;
  if (live && live.answer && live.results) S.results[live.answer.answer_id] = live.results;
  /* K-6: widened to all papers while the chip still names a story -> offer to follow. */
  S.widened = live && live.answer && change.scope && !change.scope.story_id
    ? live.answer.answer_id : null;
  await refreshConversation();
  S.cost = await api.cost();
  renderAssistant();
}

/* Remove: shown on hover and on keyboard focus (CSS). F7 (call 7 A): hide is reversible,
   so the toast offers Undo for 5 s, and ⌘Z is the same Undo (`remember`). */
function removeButton(answer) {
  if (!S.flags.followup_rewrite) return null;
  return el('button', { class: 'reset turn-remove', type: 'button',
                        'aria-label': 'Remove this turn',
                        onclick: () => removeTurn(answer.answer_id) }, 'Remove');
}

async function removeTurn(answerId) {
  if (!S.flags.followup_rewrite) return;
  const got = await api.hideTurn(answerId);
  if (got && got.error) {
    toast(got.error.status === 0 ? NOT_RUNNING : 'Not removed · ' + got.error.message);
    return;
  }
  await refreshConversation();
  renderAssistant();
  remember(TURN_UNDONE, () => restoreTurn(answerId));
  toast(TURN_REMOVED + ' ·', undoLast, REMOVE_TOAST_SECONDS);
}

/* F7 · Undo = `unhide_turn` with the id that was passed to `hide_turn`: K3 reverses
   exactly that one hide action. The pane re-reads and keeps its place (L-13 "keep": no
   pane event is set). A refusal says why and returns false, so no "Undone" follows. */
async function restoreTurn(answerId) {
  const got = await api.unhideTurn(answerId);
  if (got && got.error) {
    toast(got.error.status === 0 ? NOT_RUNNING : 'Not restored · ' + got.error.message);
    return false;
  }
  await refreshConversation();
  renderAssistant();
  return true;
}

/* F5 · "latest" = the last answer turn in pane order (`paneTurns`), a Think harder
   answer included; every other answer is drawn quiet. */
function latestAnswerId(conversation) {
  const answers = paneTurns(conversation).filter((t) => t.kind === 'answer');
  return answers.length ? answers[answers.length - 1].answer.answer_id : null;
}

/* ------------------------------------------------------------------ the whole pane */

function paneWidth() {
  return PANE_WIDTHS[S.pane] || PANE_WIDTHS.open;
}

async function setPane(which) {
  S.pane = which;
  document.documentElement.style.setProperty('--pane-width', paneWidth());
  renderAssistant();
  await api.setPref('pane', which);
}

function focusComposer() {
  const input = document.getElementById('pane-input');
  if (input && !input.disabled) input.focus();
}

function collapsedRail(label, onclick) {
  return el('div', { class: 'pane-collapsed' },
    el('button', {
      class: 'reset rail-button', type: 'button',
      disabled: onclick ? null : true,
      'aria-label': onclick ? 'Open the assistant'
                            : 'Assistant, version 0.1, not available yet',
      onclick: onclick || null,
    }, el('span', { class: 'rail-label', text: label })));
}

/* L-13 · the ask pane keeps its place (Kunaal, 4 Nov). Every redraw builds a new
   .pane-body, whose scrollTop starts at 0, so any click used to throw the reader back to
   the first question. Which rule an event gets is this one pure function; the measuring
   is holdPane / placePane below. An event the table does not name keeps the place. */
const PANE_SCROLL_LATEST = ['load', 'reload', 'new_conversation'];
function paneScrollRule(event, id) {
  if (event === 'ask') return { rule: 'to_question', id: id };
  if (PANE_SCROLL_LATEST.includes(event)) return { rule: 'to_latest' };
  return { rule: 'keep' };      /* fold, unfold, pin, a menu, a click, a refusal, an error,
                                   Think harder, a stream chunk, any other redraw */
}

function anchored(node, key) {
  if (node) node.setAttribute('data-anchor', key);
  return node;
}

function anchorsOf(body) {
  return Array.from(body.children).filter((k) => k.dataset && k.dataset.anchor);
}

/* Scroll anchoring, before the redraw: the first block whose bottom edge is below the
   pane's top, and every block after it (a fallback if the first is gone), each with its
   offset from the pane's top. Restoring the raw scrollTop is not enough: a fold above
   the view changes the heights. Null where there is no layout (jsc) or no old pane. */
function holdPane(body) {
  if (!body || typeof body.getBoundingClientRect !== 'function') return null;
  const top = body.getBoundingClientRect().top;
  const kids = anchorsOf(body);
  const first = kids.findIndex((k) => k.getBoundingClientRect().bottom > top);
  if (first < 0) return null;
  return kids.slice(first).map((k) => ({ key: k.dataset.anchor,
                                        offset: k.getBoundingClientRect().top - top }));
}

function lastQuestionBlock(body) {
  const asked = anchorsOf(body).filter((k) => k.querySelector('.turn-q'));
  return asked.length ? asked[asked.length - 1] : null;
}

/* After the redraw. The spacer lets the latest question reach the pane's top however
   short its answer is (else the browser clamps the jump); it shrinks as the answer grows.
   Jumps are instant. Keep with nothing held (first draw, pane reopened) opens at the
   latest question. */
function placePane(body, held, rule) {
  if (typeof body.getBoundingClientRect !== 'function') return;
  const spacer = body.querySelector('.pane-spacer');
  const top = body.getBoundingClientRect().top;
  const offsetOf = (node) => node.getBoundingClientRect().top - top + body.scrollTop;
  const lastQ = lastQuestionBlock(body);
  if (spacer) {
    spacer.style.height = '0px';
    spacer.style.height = lastQ ? Math.max(0, body.clientHeight
      - (body.scrollHeight - offsetOf(lastQ))) + 'px' : '0px';
  }
  const find = (key) => anchorsOf(body).find((k) => k.dataset.anchor === key);
  if (rule.rule === 'keep' && held) {
    for (const a of held) {
      const node = find(a.key);
      if (node) { body.scrollTop = offsetOf(node) - a.offset; return; }
    }
    return;
  }
  const target = rule.rule === 'to_question' ? find(rule.id) : lastQ;
  if (target) body.scrollTop = offsetOf(target);
}

function renderAssistant() {
  const pane = document.getElementById('assistant');
  if (!pane) return;
  const held = holdPane(pane.querySelector('.pane-body'));
  clear(pane);
  document.documentElement.style.setProperty('--pane-width', paneWidth());

  if (!S.flags.assistant) {
    pane.append(collapsedRail('Assistant · v0.1', null));
    return;
  }
  if (S.pane === 'collapsed') {
    pane.append(collapsedRail('Assistant', () => setPane('open')));
    return;
  }

  const shell = el('div', { class: 'pane' });

  const header = el('div', { class: 'pane-header' },
    el('p', { class: 'pane-title', text: 'Assistant' }),
    /* F5 · three plain links, no boxed button */
    el('button', { class: 'reset', type: 'button',
                   onclick: () => startNewConversation() }, 'New conversation'),
    el('button', { class: 'reset', type: 'button',
                   onclick: () => setPane(S.pane === 'wide' ? 'open' : 'wide') },
      S.pane === 'wide' ? 'Narrow' : 'Widen'),
    el('button', { class: 'reset', type: 'button',
                   onclick: () => setPane('collapsed') }, 'Collapse'));
  shell.append(header);

  const chips = el('div', { class: 'pane-scope' });
  renderScope(chips);
  shell.append(chips);

  const body = el('div', { class: 'pane-body' });
  if (S.reloadDivider) {
    body.append(anchored(el('p', { class: 'pane-divider',
                                   text: 'Conversation from ' + S.reloadDivider }),
                         'reload-divider'));
  }
  /* I8 · a re-ask streams where its answer was: the old answer gives way to the live
     turn, or, if the re-ask failed, stays and the error follows it. */
  const inPlace = S.live && S.live.replaces;
  let placed = false;
  let firstSeen = false;
  const latest = latestAnswerId(S.conversation);
  for (const turn of paneTurns(S.conversation)) {
    if (turn.kind === 'divider') {
      body.append(anchored(el('p', { class: 'pane-divider ask-divider',
                                     text: 'Now asking about · ' + turn.headline }),
                           'divider:' + turn.at + ':' + (turn.story_id || '')));
    } else if (turn.kind === 'answer') {
      const answer = turn.answer;
      const here = inPlace && answer.answer_id === inPlace;
      if (here) placed = true;
      const older = answer.answer_id !== latest;
      if (answer.parent_answer_id) {
        body.append(anchored(answerTurn(answer, { noQuestion: true, noResults: true,
                                                  older: older }),
                             answer.answer_id));
      } else {
        if (!here || S.live.error) {
          const fold = foldState(answer.answer_id);
          body.append(anchored(answerTurn(answer, { foldable: fold.foldable,
                                                    folded: fold.folded,
                                                    first: !firstSeen, older: older }),
                               answer.answer_id));
        }
        firstSeen = true;
        if (here) { const live = liveTurn(); if (live) body.append(anchored(live, liveKey())); }
      }
    }
  }
  const live = placed ? null : liveTurn();
  if (live) body.append(anchored(live, liveKey()));
  body.append(el('div', { class: 'pane-spacer', 'aria-hidden': 'true' }));
  shell.append(body);

  const band = capBand();
  const foot = el('div', {});
  if (band) foot.append(band);

  const capped = atCap();
  const composer = el('form', {
    class: 'pane-composer',
    onsubmit: (e) => {
      e.preventDefault();
      const input = document.getElementById('pane-input');
      const question = input.value;
      input.value = '';
      askNow(question);
    },
  });
  composer.append(el('input', {
    id: 'pane-input', type: 'text', name: 'question', autocomplete: 'off',
    'aria-label': 'Ask the assistant',
    disabled: capped ? true : null,
    placeholder: capped
      ? 'Daily cap $' + ((S.cost || {}).cap_usd || 0).toFixed(2)
        + ' reached · resumes tomorrow'
      : (S.scope.story_id ? 'Ask about this story… ( / )'
                          : 'Ask about your papers…'),
  }));
  composer.append(el('button', { class: 'reset ask-button', type: 'submit',
                                 disabled: capped ? true : null }, 'Ask'));
  foot.append(composer);
  shell.append(foot);

  pane.append(shell);
  const event = S.paneEvent || 'redraw';
  S.paneEvent = null;
  placePane(body, held, paneScrollRule(event, 'live'));
}

/* The live turn's anchor: 'live' while it streams, its answer's id once done, so the
   redraw that swaps it for the stored answer keeps the place. */
function liveKey() {
  return S.live && S.live.answer ? S.live.answer.answer_id : 'live';
}

async function startNewConversation() {
  await api.newConversation();
  await refreshConversation();
  S.results = {};
  S.folded = {};
  S.live = null;                 /* a kept error from the old conversation goes with it */
  S.reloadDivider = null;
  S.widened = null;
  S.paneEvent = 'new_conversation';
  renderAssistant();
  focusComposer();
}

/* State 7 — opening a different story while the conversation has something in it puts
   a divider in, so an answer is never read against the wrong story. */
/* I6 · call 8: browsing moves the pane's scope and adds nothing to the conversation.
   The divider is the question's business (askNow). */
async function paneStoryOpened(story) {
  S.scope.story_id = story.story_id;
  S.scope.headline = story.headline.text;
  S.scope.text_hashes = storyHashes(story);
}

/* What the conversation was last asked about: the story of its last answer or divider,
   null for all papers. `undefined` when it has neither -- a first question needs no
   divider, the pane's scope chip already says what it is about. */
function lastAskedStory(conversation) {
  const turns = (conversation && conversation.turns) || [];
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const t = turns[i];
    if (t.kind === 'divider') return t.story_id || null;
    if (t.kind === 'answer') return ((t.answer && t.answer.scope) || {}).story_id || null;
  }
  return undefined;
}

const ALL_PAPERS = 'All papers';

/* The divider a question needs before it is sent, or null. Scope = the story (or no
   story); filters alone do not change it (brief: "another story, or no story"). */
function dividerFor(conversation, scope) {
  const last = lastAskedStory(conversation);
  if (last === undefined) return null;
  const now = (scope && scope.story_id) || null;
  if (now === last) return null;
  /* No story but Explore's picked stories: name them ("3 stories"), not "All papers". */
  return { story_id: now, headline: now ? (scope.headline || '') : (scope.label || ALL_PAPERS) };
}

/* §8.5 — "Show all N in Explore" hands the question's own query and scope straight to
   the Explore screen, so the list the reader asked to see more of is the list they get. */
function openExploreFrom(result) {
  if (!S.flags.explore) return;
  openExplore({ query: result.query, filters: result.filters });
}

/* ============================================= Suggest connections (brief 2 §8.4)

   On demand only (D4). The button is REPLACED by the suggestions -- no button left
   behind -- because pressing it again would cost again, and K3 caches the set anyway.
   The box has no Pin of its own: a box of suggestions is not a finding, and the two
   icon buttons on each row are the only things that commit anything.

   Light icon = not done, dark icon = done, and both survive a reload because they are
   read back from the boards' pins and from list_user_links, not from a variable in
   this page.
*/

/* Below this many suggestions the box says how few there are. Three because that was
   §4.4's old floor: a set of three or more reads as a set, and one or two does not.
   What would change it: Kunaal finding that three rows also want the line. */
const SUGGEST_FEW = 3;

const SVG_NS = 'http://www.w3.org/2000/svg';

/* Inline stroke SVG, 16px inside a 32px target. `currentColor` is the whole trick:
   the dark state only has to change the button's colour and the icon follows. */
function icon(kind) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('width', '16');
  svg.setAttribute('height', '16');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.4');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  for (const d of (kind === 'plus' ? ['M8 3.5v9', 'M3.5 8h9']
                                   : ['M5 2h6', 'M8 2v6', 'M4.5 8h7l-1 2h-5z',
                                      'M8 10v4'])) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    svg.appendChild(path);
  }
  return svg;
}

function suggestionPinned(storyId) {
  return S.pinnedStories.has(storyId);
}

function userLinkTo(storyId) {
  return (S.userLinks[S.openStoryId] || [])
    .find((l) => l.target_story_id === storyId) || null;
}

/* Every story the reader has pinned, whatever board it went to, so a dark pin is dark
   again after a reload. The boards are few and this is read once, when the suggestions
   are asked for -- the alternative is a route that answers "is this pinned", which is
   a question the reader never asks. */
async function loadPinnedStories() {
  const boards = await api.boards();
  S.boards = (boards && boards.boards) || [];
  const pinned = new Set();
  for (const board of S.boards) {
    const got = await api.boardPins(board.board_id);
    for (const pin of ((got && got.pins) || [])) {
      if (pin.item_type === 'story' && pin.payload && pin.payload.story_id) {
        pinned.add(pin.payload.story_id);
        /* The board is kept with the pin id, because undoing an unpin has to put the
           story back on the board it came off -- a toast that offers Undo and then
           cannot do it is worse than no toast. */
        S.pinIds[pin.payload.story_id] = { pin_id: pin.pin_id, board_id: board.board_id,
                                           name: board.name, payload: pin.payload,
                                           source: pin.source };
      }
    }
  }
  S.pinnedStories = pinned;
}

async function loadUserLinks(story) {
  const got = await api.userLinks(storyHashes(story));
  S.userLinks[story.story_id] = (got && got.user_links) || [];
}

/* K-3 (Kunaal, 3 Nov) · the call takes seconds, and the page used to show nothing
   while it ran -- and nothing at all if it was refused, because a null answer redrew
   the button. Now: from the press until the answer or the refusal, the button is
   disabled and says it is working, and a second press does nothing, so it cannot pay
   twice. A refusal is shown in the same place with Retry (brief §4.4). */
async function askForSuggestions(story) {
  const id = story.story_id;
  if (S.suggesting[id]) return;
  S.suggesting[id] = true;
  delete S.suggestions[id];
  render();
  let got;
  try {
    got = await api.suggest(id, storyHashes(story));
  } finally {
    delete S.suggesting[id];
  }
  S.suggestions[id] = got || { error: { status: 0, message: NOT_RUNNING } };
  if (!S.suggestions[id].error) {
    await loadPinnedStories();
    await loadUserLinks(story);
  }
  const cost = await api.cost();
  if (cost) S.cost = cost;
  render();
}

async function addUserLink(story, suggestion) {
  const target = findStory(suggestion.story_id);
  const hashes = target ? storyHashes(target)
                        : (suggestion.articles || []).map((a) => a.text_hash);
  await api.addUserLink(story.story_id, storyHashes(story), suggestion.story_id, hashes);
  await loadUserLinks(story);
  /* The thread above has to be re-read, not just redrawn: the entry is new in it. */
  const again = await api.thread(story.story_id);
  S.threads[story.story_id] = (again && again.entries) ? again : null;
  render();
}

async function dropUserLink(linkId) {
  const story = findStory(S.openStoryId);
  if (!story) return;
  await api.removeUserLink(linkId);
  await loadUserLinks(story);
  const again = await api.thread(story.story_id);
  S.threads[story.story_id] = (again && again.entries) ? again : null;
  render();
}

async function unpinStory(storyId) {
  const was = S.pinIds[storyId];
  if (!was) return;
  await api.unpin(was.pin_id);
  delete S.pinIds[storyId];
  S.pinnedStories.delete(storyId);
  remember('unpin from ' + was.name, async () => {
    const again = await api.pin(was.board_id, 'story', was.payload, was.source);
    if (await pinRefused(again)) { render(); return; }
    S.pinnedStories.add(storyId);
    S.pinIds[storyId] = { pin_id: again ? again.pin_id : null,
                          board_id: was.board_id, name: was.name,
                          payload: was.payload, source: was.source };
    render();
  });
  toast('Unpinned from ' + was.name, undoLast);
  render();
}

/* One row: the story, why it is being offered, and the two things you can do with it. */
function suggestionRow(story, suggestion) {
  const linked = userLinkTo(suggestion.story_id);
  const pinned = suggestionPinned(suggestion.story_id);
  const best = (suggestion.articles || [])[0];
  const when = suggestion.dates[suggestion.dates.length - 1];

  const actions = el('div', { class: 'suggest-actions' });
  if (pinned) {
    actions.append(el('button', {
      class: 'reset icon-button is-done', type: 'button', 'aria-pressed': 'true',
      'aria-label': 'Pinned · click to unpin ' + suggestion.headline,
      onclick: () => unpinStory(suggestion.story_id),
    }, icon('pin')));
  } else {
    actions.append(pinControl('story', () => ({
      payload: { story_id: suggestion.story_id, headline: suggestion.headline,
                 text_hashes: (findStory(suggestion.story_id)
                   ? storyHashes(findStory(suggestion.story_id))
                   : (suggestion.articles || []).map((a) => a.text_hash)) },
      source: { suggested_for: story.story_id, digest_ref: S.ref },
    }), { class: 'icon-button', icon: icon('pin'), label: 'Pin to board',
          after: async (board, pinId, payload, source) => {
            S.pinnedStories.add(suggestion.story_id);
            S.pinIds[suggestion.story_id] = { pin_id: pinId, board_id: board.board_id,
                                              name: board.name, payload: payload,
                                              source: source };
            render();
          } }));
  }

  if (S.flags.user_links) {
    actions.append(el('button', {
      class: 'reset icon-button' + (linked ? ' is-done' : ''), type: 'button',
      'aria-pressed': linked ? 'true' : 'false',
      'aria-label': linked ? 'Remove from the thread' : 'Add to thread',
      onclick: () => (linked ? dropUserLink(linked.link_id)
                             : addUserLink(story, suggestion)),
    }, icon('plus')));
  }

  return el('div', { class: 'suggest-row' },
    el('div', {},
      el('button', { class: 'reset suggest-open', type: 'button',
                     onclick: () => openStory(suggestion.story_id) },
        el('h4', { text: suggestion.headline })),
      el('p', { class: 'suggest-why' },
        (best ? paperCode(best.paper) + ' · ' : '') + dateShort(when)
          + ' · Why: ' + suggestion.why,
        linked ? el('span', { class: 'added-note', text: ' · Added to the thread' })
               : null)),
    actions);
}

/* Decision D11: the set is never padded, so it can be short or empty, and the box has
   to say which rather than letting two rows read as the whole answer. The count is the
   only thing it reads -- there is no field on the response saying "this set is short". */
function shortfallWords(n) {
  return 'Only ' + n + (n === 1 ? ' looks' : ' look') + ' related.';
}

const SUGGEST_NO_MEANING = "Can't look for related stories right now: meaning search "
  + "isn't running. Click the News Intelligence icon to start it.";

function suggestionsBox(story, got) {
  const box = el('section', { class: 'suggest-box' });
  const rows = got.suggestions || [];
  /* At zero there is nothing to head and nothing to caveat -- "Not checked" is a
     warning about suggestions, and there are none -- so the box says what happened and
     prints what the press cost, which the reader paid either way. */
  if (!rows.length) {
    /* I6 · with meaning search off nothing can clear the similarity bar, so "nothing
       related" would be a claim about the archive that was never checked. */
    box.append(el('p', { class: 'suggest-none',
                         text: got.mode === 'fts_only' ? SUGGEST_NO_MEANING
                                                       : 'Nothing related found.' }));
  } else {
    box.append(el('div', { class: 'suggest-head' },
      el('p', { class: 'block-label', text: 'Suggested by assistant' }),
      el('p', { class: 'block-label', text: 'Not checked' })));
    for (const suggestion of rows) box.append(suggestionRow(story, suggestion));
    if (rows.length < SUGGEST_FEW) {
      box.append(el('p', { class: 'suggest-short', text: shortfallWords(rows.length) }));
    }
  }
  box.append(el('p', { class: 'suggest-cost' },
    'Suggestions · ' + moneyFine(got.cost_usd),
    got.fixture ? sampleTag() : null));
  return box;
}

function suggestSection(story) {
  if (!S.flags.suggest_connections) return null;
  const got = S.suggestions[story.story_id];
  if (got && got.error) {
    return el('div', { class: 'suggest-offer' },
      refusedBox(got.error, () => askForSuggestions(story), 'No suggestions',
                 S.estimates.suggest));
  }
  if (got) return suggestionsBox(story, got);
  const busy = Boolean(S.suggesting[story.story_id]);
  return el('div', { class: 'suggest-offer' },
    el('button', { class: 'reset action suggest-button', type: 'button',
                   disabled: busy ? true : null, 'aria-busy': busy ? 'true' : null,
                   onclick: () => askForSuggestions(story) },
      busy ? 'Looking for related stories…'
           : 'Suggest connections · ≈ ' + moneyFine(S.estimates.suggest)));
}

/* ===================================================== the Explore screen (§8.5)

   The archive, filtered. Everything on it is one `search()` call and everything that
   changes a filter runs it again -- there is no second idea of "what matches" kept in
   the page to drift out of step with the one the server has.

   Two switches change what a row IS, not how it is sorted: Stories | Articles flattens
   each story into its own articles. The selection is kept by story_id across both, so
   ticking rows, flattening, and then asking about them does not lose any.
*/

const EXPLORE_LIMIT = 200;           /* one screen's worth; the corpus is smaller */

const FILTER_MENUS = [
  ['papers', 'Papers'],
  ['topics', 'Topics'],
  ['kinds', 'Kinds'],
];

function exploreState() {
  if (!S.explore) {
    S.explore = { query: '', filters: {}, sort: 'newest', rows: 'stories',
                  result: null, selected: new Set(), openMenu: null };
  }
  return S.explore;
}

async function runExplore() {
  const ex = exploreState();
  const sort = ex.query ? ex.sort : 'newest';
  ex.result = await api.search(searchQueryString(ex.query, ex.filters, sort,
                                                 EXPLORE_LIMIT));
  ex.unreachable = !ex.result && !S.reachable;
  render();
}

async function openExplore(from) {
  const ex = exploreState();
  if (from) {
    ex.query = from.query || '';
    ex.filters = Object.assign({}, from.filters || {});
    ex.sort = ex.query ? 'relevance' : 'newest';
  }
  if (!S.facets) S.facets = await api.facets();
  await leaveOpenStory();
  S.screen = 'explore';
  await runExplore();
}

function setExploreFilter(name, value) {
  const ex = exploreState();
  if (!value || (Array.isArray(value) && !value.length)) delete ex.filters[name];
  else ex.filters[name] = value;
  runExplore();
}

function toggleExploreValue(name, value) {
  const ex = exploreState();
  const have = (ex.filters[name] || []).slice();
  const at = have.indexOf(value);
  if (at >= 0) have.splice(at, 1); else have.push(value);
  ex.openMenu = name;
  setExploreFilter(name, have);
}

/* The chips say what is actually narrowing the screen, and each one can be taken off.
   Pure, so the wording is checked without a browser. */
function exploreChips(ex) {
  const chips = [];
  if (ex.query) chips.push({ key: 'query', text: '“' + ex.query + '”' });
  for (const [name, value] of Object.entries(ex.filters || {})) {
    if (name === 'story_ids') {
      chips.push({ key: name, text: count(value.length, 'story') + ' chosen' });
      continue;
    }
    if (name === 'text_hashes' || name === 'exclude_story_ids') continue;
    const words = Array.isArray(value) ? value.join(', ') : String(value);
    const prefix = name === 'date_from' ? 'from ' : (name === 'date_to' ? 'to ' : '');
    chips.push({ key: name, text: prefix + words });
  }
  return chips;
}

function clearExplore() {
  const ex = exploreState();
  ex.query = '';
  ex.filters = {};
  ex.sort = 'newest';
  runExplore();
}

/* One row per article rather than per story. The story's own counts stay on it, so a
   flattened row still says which story it belongs to when it is opened. */
function exploreArticleRows(result) {
  const rows = [];
  for (const story of ((result && result.stories) || [])) {
    for (const article of (story.articles || [])) {
      rows.push({ story: story, article: article });
    }
  }
  return rows;
}

function exploreCounts(result) {
  const r = result || {};
  return count(r.total_stories || 0, 'story') + ' · '
         + count(r.total_articles || 0, 'article');
}

function exploreRow(story, article) {
  const ex = exploreState();
  const best = article || (story.articles || [])[0];
  const when = story.dates[story.dates.length - 1];
  const box = el('input', {
    type: 'checkbox', id: 'pick-' + story.story_id + (article ? '-' + best.text_hash : ''),
    'aria-label': 'Choose ' + story.headline,
    onchange: (e) => {
      if (e.currentTarget.checked) ex.selected.add(story.story_id);
      else ex.selected.delete(story.story_id);
      renderExploreActions();
    },
  });
  if (ex.selected.has(story.story_id)) box.setAttribute('checked', '');

  return el('div', { class: 'explore-row' }, box,
    el('div', {},
      el('button', {
        class: 'reset explore-open', type: 'button',
        onclick: () => (article ? openArticle(best.text_hash)
                                : openStory(story.story_id)),
      }, el('h4', { text: article ? best.headline : story.headline })),
      best ? el('p', { class: 'explore-best' },
                paperCode(best.paper) + ' ',
                pageLink(best.page_link, best.page_link ? best.page_link.label : ''),
                ' · ' + best.snippet) : null,
      el('p', { class: 'explore-meta',
                text: dateShort(article ? best.date : when) + ' · '
                      + count(story.papers, 'paper') + ' · '
                      + count((story.articles || []).length, 'matching article') })));
}

/* The holder is passed in when the screen is built, and looked up by id afterwards --
   a checkbox changing only needs the two buttons redrawn, not the list. Taking it as an
   argument is what lets the screen be built before it is in the document. */
function renderExploreActions(into) {
  const holder = into || document.getElementById('explore-actions');
  if (!holder) return;
  const ex = exploreState();
  clear(holder);
  const n = ex.selected.size;
  holder.append(el('button', {
    class: 'reset action', type: 'button', disabled: n ? null : true,
    onclick: () => askAboutSelected(),
  }, 'Ask about these ' + n));
  holder.append(el('button', {
    class: 'reset action', type: 'button', disabled: n ? null : true,
    onclick: () => pinSelected(),
  }, 'Pin selected'));
  holder.append(el('span', { class: 'answer-cost', text: 'no model call · 0¢' }));
}

/* §8.5 — the chosen stories go into the pane as ONE chip, because "these 4 stories" is
   one thought; four chips would read as four separate narrowings. */
function askAboutSelected() {
  const ex = exploreState();
  const ids = [...ex.selected];
  if (!ids.length) return;
  S.scope = { story_id: null, headline: null, text_hashes: [],
              filters: Object.assign({}, ex.filters, { story_ids: ids }) };
  S.scopeLabel = count(ids.length, 'story');
  if (S.pane === 'collapsed') setPane('open');
  renderAssistant();
  focusComposer();
}

async function pinSelected() {
  const ex = exploreState();
  const ids = [...ex.selected];
  if (!ids.length) return;
  const boards = await api.boards();
  S.boards = (boards && boards.boards) || [];
  const board = S.boards[0];
  if (!board) { toast('Make a board first, on a story'); return; }
  const rows = (ex.result && ex.result.stories) || [];
  let done = 0;
  for (const id of ids) {
    const story = rows.find((r) => r.story_id === id);
    if (!story) continue;
    const res = await api.pin(board.board_id, 'story',
                              { story_id: id, headline: story.headline,
                                text_hashes: (story.articles || []).map((a) => a.text_hash) },
                              { digest_ref: S.ref, from: 'explore' });
    if (await pinRefused(res)) break;
    done += 1;
  }
  toast(count(done, 'story') + ' pinned to ' + board.name);
  await loadPinnedStories();
  render();
}

function exploreMenu(name, label) {
  const ex = exploreState();
  const chosen = ex.filters[name] || [];
  const values = ((S.facets || {})[name]) || [];
  const menu = el('div', { class: 'pin-menu explore-menu' });
  menu.hidden = ex.openMenu !== name;
  menu.append(el('ul', {}, values.map((value) => el('li', {},
    el('button', {
      class: 'reset', type: 'button',
      'aria-pressed': chosen.indexOf(value) >= 0 ? 'true' : 'false',
      onclick: () => toggleExploreValue(name, value),
    }, (chosen.indexOf(value) >= 0 ? '✓ ' : '') + value)))));

  const button = el('button', {
    class: 'reset action', type: 'button', 'aria-haspopup': 'menu',
    'aria-expanded': ex.openMenu === name ? 'true' : 'false',
    onclick: () => { ex.openMenu = ex.openMenu === name ? null : name; render(); },
  }, label + (chosen.length ? ' (' + chosen.length + ')' : '') + ' ▾');
  return el('span', { class: 'pin-wrap' }, button, menu);
}

function exploreScreen(pane) {
  const ex = exploreState();
  const facets = S.facets || {};
  pane.append(el('h2', { class: 'screen-title', text: 'Explore' }));

  const filters = el('form', {
    class: 'explore-filters',
    onsubmit: (e) => {
      e.preventDefault();
      ex.query = document.getElementById('explore-q').value;
      ex.sort = ex.query ? 'relevance' : 'newest';
      runExplore();
    },
  });
  const keyword = el('input', { type: 'text', id: 'explore-q', autocomplete: 'off',
                                'aria-label': 'Keyword', placeholder: 'Keyword…' });
  keyword.value = ex.query;
  filters.append(keyword);
  filters.append(el('button', { class: 'reset action', type: 'submit' }, 'Search'));

  for (const [which, label] of [['date_from', 'From'], ['date_to', 'To']]) {
    const field = el('input', {
      type: 'date', 'aria-label': label,
      min: facets.date_min || null, max: facets.date_max || null,
      onchange: (e) => setExploreFilter(which, e.currentTarget.value),
    });
    field.value = ex.filters[which]
      || (which === 'date_from' ? (facets.date_min || '') : (facets.date_max || ''));
    filters.append(el('label', { class: 'explore-date' }, label + ' ', field));
  }
  for (const [name, label] of FILTER_MENUS) filters.append(exploreMenu(name, label));
  pane.append(filters);

  const chips = el('div', { class: 'explore-chips' });
  for (const chip of exploreChips(ex)) {
    chips.append(el('span', { class: 'scope-chip' },
      el('span', { text: chip.text, title: chip.text }),
      el('button', { class: 'reset', type: 'button',
                     'aria-label': 'Remove ' + chip.text,
                     onclick: () => {
                       if (chip.key === 'query') { ex.query = ''; runExplore(); }
                       else setExploreFilter(chip.key, null);
                     } }, '×')));
  }
  pane.append(chips);

  const result = ex.result;
  const head = el('div', { class: 'explore-head' },
    el('p', { class: 'explore-count', text: exploreCounts(result) }));
  const sortSwitch = el('span', { class: 'sort-switch' });
  for (const name of ['relevance', 'newest']) {
    if (name === 'newest') sortSwitch.append(' | ');
    sortSwitch.append(el('button', {
      class: 'reset', type: 'button',
      'aria-pressed': (ex.query ? ex.sort : 'newest') === name ? 'true' : 'false',
      disabled: name === 'relevance' && !ex.query ? true : null,
      onclick: () => { ex.sort = name; runExplore(); },
    }, name === 'relevance' ? 'Relevance' : 'Newest'));
  }
  const rowSwitch = el('span', { class: 'sort-switch' });
  for (const name of ['stories', 'articles']) {
    if (name === 'articles') rowSwitch.append(' | ');
    rowSwitch.append(el('button', {
      class: 'reset', type: 'button',
      'aria-pressed': ex.rows === name ? 'true' : 'false',
      onclick: () => { ex.rows = name; render(); },
    }, name === 'stories' ? 'Stories' : 'Articles'));
  }
  head.append(sortSwitch, rowSwitch);
  pane.append(head);

  if (!result) {
    pane.append(el('p', { class: 'empty-note',
                          text: ex.unreachable ? NOT_RUNNING : 'Searching…' }));
    return;
  }
  if (!result.stories.length) {
    /* H6 — the chips are left on screen, because the reason there is nothing is in
       them, and "Clear all" is the one way out that always works. */
    pane.append(el('p', { class: 'screen-title', text: 'No stories match' }));
    pane.append(el('button', { class: 'reset action', type: 'button',
                               onclick: () => clearExplore() }, 'Clear all'));
    return;
  }

  const list = el('div', { class: 'explore-list' });
  if (ex.rows === 'articles') {
    for (const row of exploreArticleRows(result)) {
      list.append(exploreRow(row.story, row.article));
    }
  } else {
    for (const story of result.stories) list.append(exploreRow(story, null));
  }
  pane.append(list);

  const actions = el('div', { class: 'explore-actions', id: 'explore-actions' });
  pane.append(actions);
  renderExploreActions(actions);
}


/* ================================================================= the keyboard

   §8.9. Which key means what is a pure function, so all nine can be checked without a
   browser (acceptance check 2) -- including the rule that matters most: NOTHING fires
   while the reader is typing. A global handler that steals "f" while someone is writing
   in "Why? (optional)" is the classic way to make a page impossible to type in, and the
   only defence against it is a test.
*/

const KEYS = {
  j: 'next',
  k: 'previous',
  o: 'open',
  Escape: 'back',
  u: 'keep_unread',
  f: 'follow',
  p: 'pin',
  t: 'thread_view',
  '?': 'help',
  '/': 'ask',          /* v0.1: focuses the pane's composer (§8.7) */
};

/* ⌘Z (⌃Z elsewhere) is the tenth key, and the only one that needs a modifier -- asked
   for because the toast already offers Undo and the hand is already on the keyboard.
   It is kept apart from KEYS because it is the one binding where the modifier is part
   of the key: a bare "z" must stay free, and inside a text field ⌘Z has to remain the
   browser's own undo, which is why `inField` refuses it like every other key. */
function keyAction(key, opts) {
  const o = opts || {};
  if (o.inField) return null;
  if (String(key).toLowerCase() === 'z' && (o.meta || o.ctrl)) return 'undo';
  if (o.meta || o.ctrl || o.alt) return null;
  return Object.prototype.hasOwnProperty.call(KEYS, key) ? KEYS[key] : null;
}

function isTypingTarget(node) {
  if (!node) return false;
  const tag = (node.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select'
         || Boolean(node.isContentEditable);
}

/* j and k move a cursor down the drawer without opening anything; o opens what it is
   on. Moving and opening are separate so a reader can look down the list without
   marking stories read on the way past. */
function moveCursor(step) {
  const list = stories();
  if (!list.length) return;
  const open = list.findIndex((s) => s.story_id === S.openStoryId);
  const from = (S.cursor === null || S.cursor === undefined) ? open : S.cursor;
  S.cursor = Math.min(list.length - 1, Math.max(0, (from < 0 ? 0 : from) + step));
  renderDrawer();
  const row = document.querySelector('.story-row[data-cursor="true"]');
  if (row) row.scrollIntoView({ block: 'nearest' });
}

function clickAction(match) {
  const button = [...document.querySelectorAll('.meta-actions button')]
    .find((b) => (b.textContent || '').includes(match));
  if (button) { button.click(); return true; }
  return false;
}

function runKeyAction(action) {
  const list = stories();
  switch (action) {
    case 'next': moveCursor(1); return;
    case 'previous': moveCursor(-1); return;
    case 'open': {
      const target = list[(S.cursor === null || S.cursor === undefined) ? 0 : S.cursor];
      if (target) openStory(target.story_id);
      return;
    }
    case 'back':
      hideToast();
      if (S.articleOpen) { S.articleOpen = false; render(); }
      return;
    case 'keep_unread': clickAction('unread'); return;
    case 'follow': if (!clickAction('Follow')) clickAction('Following'); return;
    case 'pin': clickAction('Pin to board'); return;
    case 'ask': focusComposer(); return;
    case 'thread_view': {
      if (!S.flags.graph_view) return;
      /* Seven stories of ten have no thread, and on the rest it is usually below the
         fold -- so the switch happened where it could not be seen and the key looked
         dead. Now it either says there is no thread, or shows you what it changed. */
      const open = findStory(S.openStoryId);
      if (!open || !S.threads[open.story_id]) {
        toast('This story has no thread');
        return;
      }
      const next = S.prefs.thread_view === 'graph' ? 'timeline' : 'graph';
      S.prefs.thread_view = next;
      api.setPref('thread_view', next);
      render();
      const drawn = document.querySelector('.thread');
      if (drawn) drawn.scrollIntoView({ block: 'start' });
      return;
    }
    case 'undo': undoLast(); return;
    case 'help': toggleCheatSheet(); return;
    default:
  }
}

const CHEAT_SHEET = [
  ['j', 'next story'], ['k', 'previous story'], ['o', 'open it'],
  ['Esc', 'back'], ['u', 'keep unread'], ['f', 'follow'],
  ['p', 'pin to a board'], ['t', 'timeline or graph'],
  ['\u2318Z', 'undo the last pin, follow or keep unread'],
  ['/', 'ask the assistant'], ['?', 'this list'],
];

/* The thread's nodes are not keys, so they get their own short list in the same sheet
   (§8.4 asks for the new one to be in "the timeline legend / ? sheet"; v0 has no
   legend, and the sheet is the one place a reader already goes to ask what something
   means). */
const CHEAT_MARKS = [
  ['today', 'the newest day in the thread'],
  ['follow_up', 'a follow-up that branched off'],
  ['start', 'where the story began'],
  ['run_up', 'the day before it began'],
  ['user', 'an entry you added yourself'],
];

function toggleCheatSheet() {
  const already = document.getElementById('cheat-sheet');
  if (already) { already.remove(); return; }
  const sheet = el('div', { class: 'cheat-sheet', id: 'cheat-sheet', role: 'dialog',
                            'aria-modal': 'false', 'aria-label': 'Keyboard shortcuts' },
    el('h2', { text: 'Keys' }),
    el('dl', {}, CHEAT_SHEET.map(([key, what]) =>
      el('div', {}, el('dt', { text: key }), el('dd', { text: what })))),
    el('div', { class: 'cheat-marks' },
      el('h2', { text: 'The thread\'s marks' }),
      el('dl', {}, CHEAT_MARKS.map(([kind, what]) =>
        el('div', {},
          el('dt', {}, el('span', { class: 'node', dataset: { kind: kind } })),
          el('dd', { text: what }))))),
    el('button', { class: 'reset action', type: 'button', text: 'Close',
                   onclick: () => toggleCheatSheet() }));
  document.body.append(sheet);
  sheet.querySelector('button').focus();
}

function startKeyboard() {
  document.addEventListener('keydown', (e) => {
    const action = keyAction(e.key, {
      inField: isTypingTarget(e.target),
      meta: e.metaKey, ctrl: e.ctrlKey, alt: e.altKey,
    });
    if (!action) return;
    e.preventDefault();
    runKeyAction(action);
  });
}

/* ------------------------------------------------------------ the version line

   F3 · after a build the open Safari tab kept running the old page (the launcher brings
   the tab forward and never reloads it, T-14), so Kunaal saw bugs already fixed. The
   page remembers /api/status's `app_version` at load and asks again on focus and every
   few minutes. Look: italic grey, no border, no fill; wording approved (Kunaal, 4 Nov). */

/* KNOB · how long an open tab may run an old page unnoticed when it never loses focus.
   5 minutes is the brief's number; what would change it is Kunaal seeing the line late. */
const VERSION_CHECK_MINUTES = 5;

const VERSION_WORDS = {
  reload: ['App updated · ', 'Reload'],                        /* app_version differs */
  restart: 'App updated · Quit News Intelligence from the Dock and open it again',
};                                     /* server_on_disk differs from server_version */

/* Which line, if any: 'restart' when the route code changed and the server still runs
   the old one (a reload cannot help, so it goes first), 'reload' when the page files
   changed, null when both match or the status call failed (K-4 already says that). */
function versionNotice(loaded, status) {
  if (!status) return null;
  if (status.server_version && status.server_on_disk
      && status.server_version !== status.server_on_disk) return 'restart';
  if (loaded && status.app_version && status.app_version !== loaded) return 'reload';
  return null;
}

function reloadPage() { globalThis.location.reload(); }

function renderVersionLine(kind) {
  const line = document.getElementById('version-line');
  if (!line) return;
  clear(line);
  line.hidden = !kind;
  if (kind === 'reload') {
    line.append(VERSION_WORDS.reload[0],
      el('button', { class: 'reset version-act', type: 'button', onclick: reloadPage },
         VERSION_WORDS.reload[1]));
  } else if (kind === 'restart') {
    line.append(VERSION_WORDS.restart);
  }
}

async function checkVersion() {
  renderVersionLine(versionNotice(S.appVersion, await api.status()));
}

/* --------------------------------------------------------------------- the boot */

async function boot() {
  const flags = await api.flags();
  S.flags = (flags && flags.flags) || {};
  S.knobs = (flags && flags.knobs) || {};

  /* F2 · the list and the stored Day | Week choice come first: they pick the digest. */
  const [status, list, viewPref, cost, follows] =
    await Promise.all([api.status(), api.digests(), api.pref('digest_view'), api.cost(),
                       api.follows()]);
  S.digests = (list && list.digests) || [];
  S.view = storedView(viewPref ? viewPref.value : null);
  S.ref = viewRef(S.digests, S.view);
  const digest = await api.digest(S.ref);
  S.appVersion = status ? status.app_version : null;     /* F3 · as loaded */
  window.addEventListener('focus', checkVersion);
  window.addEventListener('focus', rereadIfStillRunning);
  if (typeof setInterval === 'function') {        /* jsc (the suite's boot) has none */
    setInterval(checkVersion, VERSION_CHECK_MINUTES * 60 * 1000);
  }
  if (!digest) { S.digest = null; render(); return; }
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

  markIfPast();

  const voted = await api.votes();
  S.votes = (voted && voted.votes) || {};

  const held = await api.keepUnreadList();
  for (const k of ((held && held.keep_unread) || [])) S.keepUnread.add(k.story_id);

  const view = await api.pref('thread_view');
  S.prefs.thread_view = view ? view.value : null;

  /* --- the ask pane (v0.1) ---------------------------------------------------
     The conversation is read before the first render, because state 8 is what the
     pane looks like on every reload: the kept conversation, with a divider saying
     when it started. The estimates are read once, for the buttons that print their
     own price; they are knobs on the double's side, not per-question numbers. */
  if (S.flags.assistant) {
    const panePref = await api.pref('pane');
    if (panePref && PANE_WIDTHS[panePref.value]) S.pane = panePref.value;
    await refreshConversation();
    S.paneEvent = 'load';        /* L-13 · open at the latest question */
    if (S.conversation && (S.conversation.turns || []).length) {
      S.reloadDivider = dayName(S.conversation.started_at.slice(0, 10)) + ' '
                        + timeOfDay(S.conversation.started_at);
    }
    for (const kind of ['ask', 'think_harder', 'background', 'suggest']) {
      const got = await api.estimate(kind);
      S.estimates[kind] = got ? got.cost_usd : null;
    }
  }

  /* The visit is read BEFORE it is recorded: once this visit is stored it is itself the
     last one, so "since last visit" would always mean "since a moment ago" (D10). */
  const visit = await api.visit(S.ref, S.digest && S.digest.window, false);
  S.lastVisit = visit && visit.last_visit;

  for (const b of document.querySelectorAll('.nav-item')) {
    b.addEventListener('click', async () => {
      if (b.dataset.screen !== 'today') await leaveOpenStory();
      if (b.dataset.screen === 'today' && S.isPast) {
        S.screen = 'today';
        await loadDigest(viewRef(S.digests, S.view), null);
        return;
      }
      if (b.dataset.screen === 'boards') {
        const boards = await api.boards();
        S.boards = (boards && boards.boards) || [];
        S.openBoardId = null;
      }
      if (b.dataset.screen === 'explore') {
        await openExplore(null);
        return;
      }
      if (b.dataset.screen === 'past') {
        const fresh = await api.digests();
        S.digests = (fresh && fresh.digests) || S.digests;
      }
      S.screen = b.dataset.screen;
      render();
    });
  }

  /* ... and so does leaving the tab. `keepalive` is what makes the request survive the
     page going away; without it the last story read of the morning would be lost. */
  window.addEventListener('pagehide', () => {
    const story = findStory(S.openStoryId);
    if (!story || S.keepUnread.has(story.story_id)) return;
    const hashes = storyHashes(story).filter((h) => !S.read[h]);
    if (!hashes.length) return;
    fetch('/api/read', {
      method: 'POST', keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text_hashes: hashes }),
    });
  });

  startKeyboard();
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
  paperFromPdf, seeAlsoPlan, pdfHref, onePageHref, pageLink, moreLinks, MORE_WORDS,
  articleBody, coverage, viewsLine,
  moneyFine, ndjsonSplit, errorWording, ERROR_WORDS, scopeChips, markFor, roomFor,
  atCap, searchQueryString, paneWidth, PANE_WIDTHS, PANE_RESULTS_SHOWN,
  paneHashes, passageSetsToFetch, chipsFor, deadChip, DEAD_WORDS,
  dividerFor, lastAskedStory, ALL_PAPERS, suggestSection, refusedBox, searchModeNote,
  answerModeNote, NOT_COVERED_WORDS, notCoveredCount, EMPTY_PAID_WORDS,
  WORDS_ONLY, SUGGEST_NO_MEANING, NOT_RUNNING, BOARD_GONE, pinRefused, openBoard,
  askForSuggestions, askBackground, openArticle, exploreMenu, streamAnswer, api,
  openStory, askNow, openExplore,
  storedResults, shortfallWords, SUGGEST_FEW, suggestionsBox,
  scopeWords, scopeStoryCount, scopeHasFilters, scopeIsNarrowed, seeAlso, seeAlsoFold,
  actionsRow, askAllPapers, paneButton, PANE_BUTTON, answerLine, answerWords, turnRow, questionRow,
  showCapConfirm,
  TOPIC_WORDS, EARLIER_WORDS, ALL_SCOPE, TURN_REMOVED, REMOVE_TOAST_SECONDS, earlierWords,
  earlierQuestion, topicLine, paneTurns, reaskInPlace, removeButton, removeTurn,
  restoreTurn, TURN_UNDONE, undoLast,
  resultGroups, takeLiveEvent, toast, widenLine, WIDEN_WORDS, clearScope,
  earlierName, correctionPrice, buttonPrice, STILL_WORKING, STILL_REREAD_MS, REFUSED_HINT,
  stillWorkingLine, markStillRunning, stillRunningFrom, rereadIfStillRunning,
  refusalEvent, isAlreadyRunning, runThinkHarder,
  /* The pane's DOM builders are exported too. v0 left the DOM-building functions to
     the eye for want of a browser; the tests give jsc an 80-line DOM instead, which
     is the difference between checking that the nine states render and hoping. */
  answerTurn, liveTurn, resultsList, foldedResults, latestAnswerId, PANE_PIN, capBand, renderScope, actionsRow,
  backgroundBlock, resultRow, sonnetLabel, foldState, workingWords, loadOutsideStory,
  renderOutsideStory, outsideScope, renderAssistant, collapsedRail,
  keepLiveAfterStream,
  suggestSection, suggestionsBox, suggestionRow, icon, userLinkTo, suggestionPinned,
  exploreChips, exploreCounts, exploreArticleRows, exploreRow, exploreScreen,
  exploreState, EXPLORE_LIMIT, FILTER_MENUS,
  threadSection, timelineView, graphView, removeLinkButton,
  nodeKind, KIND_WORDS, graphPlan, viewEntryIds, markIfPast,
  DIGEST_VIEWS, DIGEST_VIEW_WORDS, DIGEST_KIND_LABEL, digestKind, newestOfKind, viewRef,
  storedView, pastRows, digestSwitch, chooseView, pastScreen, renderHeader,
  keyAction, KEYS, CHEAT_SHEET, CHEAT_MARKS, FLAG_ELEMENTS, render, remember, undoLast,
  versionNotice, renderVersionLine, checkVersion, VERSION_WORDS, VERSION_CHECK_MINUTES,
  paneScrollRule, PANE_SCROLL_LATEST, liveKey, holdPane, placePane,
  isRead, isFollowed, stamp, unreadCount, storyHashes, S,
};

if (typeof window !== 'undefined') {
  window.APP = EXPORTS;
  document.addEventListener('DOMContentLoaded', boot);
} else {
  globalThis.APP = EXPORTS;
}
