const POLL_MS = 2000;

const state = { name: null, rev: null, doc: null, editing: false, selection: null };

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'content-type': 'application/json' },
    ...options,
  });
  if (response.status === 409) return { conflict: await response.json() };
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  const type = response.headers.get('content-type') || '';
  return type.includes('json') ? response.json() : response.text();
}

function toast(message) {
  const node = document.querySelector('#toast');
  node.textContent = message;
  node.classList.add('visible');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.remove('visible'), 2200);
}

function showBanner(message) {
  const banner = document.querySelector('#banner');
  banner.textContent = message;
  banner.classList.add('visible');
}

function hideBanner() {
  document.querySelector('#banner').classList.remove('visible');
}

/* --- reading ------------------------------------------------------------ */

async function load() {
  const doc = await request(`/api/docs/${state.name}`);
  state.doc = doc;
  state.rev = doc.rev;
  renderDoc(doc);
}

function renderDoc(doc) {
  document.title = `${doc.title} · board`;
  document.querySelector('#rev').textContent = `rev ${doc.rev}`;
  const article = document.querySelector('#article');
  article.innerHTML = doc.html;
  placeCards(doc.annotations);
}

function placeCards(annotations) {
  const rail = document.querySelector('#rail');
  const orphans = document.querySelector('#orphans');
  rail.querySelectorAll('.card').forEach((card) => card.remove());
  orphans.innerHTML = '';
  const nextTop = new Map();

  for (const annotation of annotations.filter((item) => !item.orphaned)) {
    const block = document.querySelector(`[data-block="${annotation.resolved_block}"]`);
    if (!block) continue;
    block.classList.add('annotated');
    const card = buildCard(annotation);
    /* #article and #rail start at the same y, so a block's offsetTop inside
       #article is a usable top for an absolute card inside #rail. */
    const top = Math.max(block.offsetTop, nextTop.get(annotation.resolved_block) || 0);
    card.style.top = `${top}px`;
    rail.appendChild(card);
    nextTop.set(annotation.resolved_block, top + card.offsetHeight + 10);
  }

  const lost = annotations.filter((item) => item.orphaned);
  if (lost.length) renderOrphans(orphans, lost);
}

function buildCard(annotation) {
  const card = document.createElement('div');
  card.className = 'card';
  card.dataset.id = annotation.id;

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = `${annotation.id} · ${annotation.author}`;
  card.appendChild(meta);

  if (annotation.quote) {
    const quote = document.createElement('span');
    quote.className = 'quote';
    quote.textContent = annotation.quote;
    card.appendChild(quote);
  }

  const note = document.createElement('div');
  note.className = 'note';
  note.textContent = annotation.note;
  card.appendChild(note);
  return card;
}

function renderOrphans(container, annotations) {
  const heading = document.createElement('h3');
  heading.textContent = 'the text moved — these lost their anchor';
  container.appendChild(heading);
  for (const annotation of annotations) {
    const card = buildCard(annotation);
    card.classList.add('orphaned');
    container.appendChild(card);
  }
}

/* --- writing ------------------------------------------------------------ */

function setEditing(editing) {
  state.editing = editing;
  document.body.classList.toggle('editing', editing);
  document.querySelector('#editor').value = editing ? state.doc.markdown : '';
  document.querySelector('#edit').textContent = editing ? 'cancel' : 'edit';
}

async function save() {
  const markdown = document.querySelector('#editor').value;
  const result = await request(`/api/docs/${state.name}`, {
    method: 'PUT',
    body: JSON.stringify({ markdown, rev: state.rev }),
  });
  if (result.conflict) {
    showBanner(
      `Someone wrote while you had the pen (their rev ${result.conflict.rev}). ` +
        'Copy your paragraph, press cancel, then re-read and paste it back.',
    );
    return;
  }
  hideBanner();
  setEditing(false);
  await load();
  toast('saved');
}

/* --- annotating --------------------------------------------------------- */

function selectionBlock(range) {
  const node = range.startContainer;
  const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
  return element ? element.closest('[data-block]') : null;
}

function offsetsWithin(block, range) {
  const before = document.createRange();
  before.selectNodeContents(block);
  before.setEnd(range.startContainer, range.startOffset);
  const start = before.toString().length;
  return { start, end: start + range.toString().length };
}

function onSelection() {
  if (state.editing) return;
  const selection = window.getSelection();
  const quote = selection ? selection.toString().trim() : '';
  if (!quote) return hideCompose();
  const range = selection.getRangeAt(0);
  const block = selectionBlock(range);
  if (!block) return hideCompose();
  const { start, end } = offsetsWithin(block, range);
  openCompose({ block: block.dataset.block, quote, start, end, rect: range.getBoundingClientRect() });
}

function openCompose(selection) {
  state.selection = selection;
  const compose = document.querySelector('#compose');
  compose.classList.add('visible');
  const frame = compose.getBoundingClientRect();
  const left = Math.min(selection.rect.left, window.innerWidth - frame.width - 24);
  compose.style.left = `${Math.max(24, left)}px`;
  compose.style.top = `${selection.rect.bottom + window.scrollY + 8}px`;
  compose.querySelector('textarea').value = '';
  compose.querySelector('textarea').focus();
}

function hideCompose() {
  document.querySelector('#compose').classList.remove('visible');
  state.selection = null;
}

async function submitCompose() {
  if (!state.selection) return;
  const note = document.querySelector('#compose textarea').value.trim();
  if (!note) return hideCompose();
  const { block, quote, start, end } = state.selection;
  hideCompose();
  await request(`/api/docs/${state.name}/annotations`, {
    method: 'POST',
    body: JSON.stringify({ block, quote, start, end, note, author: 'go' }),
  });
  await load();
  toast('noted');
}

async function notify() {
  await request('/api/inbox', {
    method: 'POST',
    body: JSON.stringify({ text: `board: ${state.name}` }),
  });
  toast('marked — read on my next turn');
}

/* --- wiring ------------------------------------------------------------- */

function wire() {
  document.addEventListener('mouseup', onSelection);
  document.querySelector('#edit').addEventListener('click', () => setEditing(!state.editing));
  document.querySelector('#save').addEventListener('click', save);
  document.querySelector('#notify').addEventListener('click', notify);
  document.querySelector('#compose-save').addEventListener('click', submitCompose);
  document.querySelector('#compose-cancel').addEventListener('click', hideCompose);
  window.setInterval(poll, POLL_MS);
}

async function poll() {
  if (!state.name || state.editing) return;
  if (document.querySelector('#compose').classList.contains('visible')) return;
  const head = await request(`/api/docs/${state.name}`);
  if (head.rev && head.rev !== state.rev) {
    state.doc = head;
    state.rev = head.rev;
    renderDoc(head);
  }
}

state.name = window.location.pathname.split('/d/').pop().replace(/\.md$/, '');
wire();
load().catch((error) => showBanner(String(error)));
