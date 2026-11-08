import { nearestStrokeIndex, toNormalized, toPath } from './coords.js';

const POLL_MS = 2000;
const INK_COLOUR = '#c0392b';
const INK_WIDTH = 2;
const ERASE_TOLERANCE = 10;
const QUOTE_LENGTH = 80;

const state = { name: null, rev: null, doc: null, editing: false, drawing: false, erasing: false };

function ink() {
  return document.querySelector('#ink');
}

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

/* --- reading ------------------------------------------------------------ */

async function load() {
  const doc = await request(`/api/docs/${state.name}`);
  state.doc = doc;
  state.rev = doc.rev;
  await renderDoc(doc);
}

async function renderDoc(doc) {
  document.title = `${doc.title} · board`;
  document.querySelector('#rev').textContent = `rev ${doc.rev}`;
  document.querySelector('#article').innerHTML = doc.html;
  await renderDiagrams();
  placeCards(doc.annotations);
  renderInk(doc.annotations);
  if (state.editing) document.querySelector('#editor').value = doc.markdown;
}

/* A ```mermaid fence is drawn in place, offline: the bundle is vendored. A
   diagram that fails to parse stays a code block with its error on hover,
   because a silent blank is worse than the source. */
async function renderDiagrams() {
  const engine = window.mermaid;
  if (!engine) return;
  engine.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'strict' });
  const fences = Array.from(document.querySelectorAll('#article pre > code.language-mermaid'));
  let index = 0;
  for (const fence of fences) {
    const host = fence.closest('pre');
    try {
      const { svg } = await engine.render(`board-diagram-${Date.now()}-${index++}`, fence.textContent);
      const holder = document.createElement('figure');
      holder.className = 'diagram';
      holder.innerHTML = svg;
      host.replaceWith(holder);
    } catch (error) {
      host.classList.add('diagram-failed');
      host.title = String(error);
    }
  }
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

  if (annotation.kind === 'ink') {
    const summary = document.createElement('div');
    summary.className = 'note';
    summary.textContent = `✎ ${annotation.strokes.length} stroke(s) here`;
    card.appendChild(summary);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = 'erase';
    remove.addEventListener('click', () => saveInk(annotation.resolved_block, []));
    card.appendChild(remove);
    return card;
  }

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

/* --- ink ---------------------------------------------------------------- */

function sizeInk() {
  const article = document.querySelector('#article');
  const overlay = ink();
  overlay.setAttribute('width', article.offsetWidth);
  overlay.setAttribute('height', article.offsetHeight);
  overlay.setAttribute('viewBox', `0 0 ${article.offsetWidth} ${article.offsetHeight}`);
}

function renderInk(annotations) {
  const overlay = ink();
  overlay.innerHTML = '';
  sizeInk();
  const overlayRect = overlay.getBoundingClientRect();
  for (const annotation of annotations) {
    if (annotation.kind !== 'ink' || annotation.orphaned) continue;
    const block = document.querySelector(`[data-block="${annotation.resolved_block}"]`);
    if (!block) continue;
    const blockRect = block.getBoundingClientRect();
    annotation.strokes.forEach((stroke, index) => {
      overlay.appendChild(
        strokePath(stroke, blockRect, overlayRect, {
          annotation: annotation.id,
          block: annotation.resolved_block,
          index,
        }),
      );
    });
  }
}

function strokePath(stroke, blockRect, overlayRect, dataset) {
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', toPath(stroke.points || [], blockRect, overlayRect));
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', stroke.color || INK_COLOUR);
  path.setAttribute('stroke-width', String(stroke.width || INK_WIDTH));
  path.setAttribute('stroke-linecap', 'round');
  path.setAttribute('stroke-linejoin', 'round');
  if (dataset) {
    path.dataset.annotation = dataset.annotation;
    path.dataset.block = dataset.block;
    path.dataset.index = String(dataset.index);
  }
  return path;
}

function blockUnder(clientX, clientY) {
  const overlay = ink();
  overlay.style.pointerEvents = 'none';
  const element = document.elementFromPoint(clientX, clientY);
  overlay.style.pointerEvents = 'auto';
  return element ? element.closest('[data-block]') : null;
}

function inkNoteFor(blockId) {
  return (state.doc.annotations || []).find(
    (annotation) => annotation.kind === 'ink' && annotation.resolved_block === blockId,
  );
}

async function saveInk(blockId, strokes) {
  const block = (state.doc.blocks || []).find((item) => item.id === blockId);
  const quote = (block ? block.text : '').slice(0, QUOTE_LENGTH);
  await request(`/api/docs/${state.name}/ink/${blockId}`, {
    method: 'PUT',
    body: JSON.stringify({ quote, strokes, author: 'go' }),
  });
  await load();
}

let draft = null;

function beginStroke(event) {
  if (!state.drawing || state.erasing) return;
  const block = blockUnder(event.clientX, event.clientY);
  if (!block) return;
  ink().setPointerCapture(event.pointerId);
  draft = { blockId: block.dataset.block, rect: block.getBoundingClientRect(), points: [], path: null };
  draft.path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  draft.path.setAttribute('fill', 'none');
  draft.path.setAttribute('stroke', INK_COLOUR);
  draft.path.setAttribute('stroke-width', String(INK_WIDTH));
  draft.path.setAttribute('stroke-linecap', 'round');
  ink().appendChild(draft.path);
  extendStroke(event);
}

function extendStroke(event) {
  if (!draft) return;
  const overlayRect = ink().getBoundingClientRect();
  draft.points.push(toNormalized({ x: event.clientX, y: event.clientY }, draft.rect));
  draft.path.setAttribute('d', toPath(draft.points, draft.rect, overlayRect));
}

async function endStroke() {
  if (!draft) return;
  const { blockId, points } = draft;
  draft = null;
  if (points.length < 2) {
    await load();
    return;
  }
  const existing = inkNoteFor(blockId);
  const strokes = existing ? [...existing.strokes, { points, color: INK_COLOUR, width: INK_WIDTH }] : [{ points, color: INK_COLOUR, width: INK_WIDTH }];
  await saveInk(blockId, strokes);
}

async function eraseAt(event) {
  if (!state.drawing || !state.erasing) return;
  const point = { x: event.clientX, y: event.clientY };
  for (const annotation of state.doc.annotations) {
    if (annotation.kind !== 'ink' || annotation.orphaned) continue;
    const block = document.querySelector(`[data-block="${annotation.resolved_block}"]`);
    if (!block) continue;
    const rect = block.getBoundingClientRect();
    const index = nearestStrokeIndex(point, annotation.strokes, rect, ERASE_TOLERANCE);
    if (index < 0) continue;
    const strokes = annotation.strokes.filter((_, position) => position !== index);
    await saveInk(annotation.resolved_block, strokes);
    return;
  }
}

function setDrawing(drawing) {
  state.drawing = drawing;
  if (!drawing) state.erasing = false;
  document.body.classList.toggle('drawing', state.drawing);
  document.body.classList.toggle('erasing', state.erasing);
  document.querySelector('#draw').textContent = state.drawing ? 'stop drawing' : 'draw';
  document.querySelector('#erase').textContent = state.erasing ? 'erase: on' : 'erase';
  ink().style.pointerEvents = state.drawing ? 'auto' : 'none';
}

function setErasing(erasing) {
  state.erasing = erasing;
  document.body.classList.toggle('erasing', erasing);
  document.querySelector('#erase').textContent = erasing ? 'erase: on' : 'erase';
}

/* --- writing ------------------------------------------------------------ */

function setEditing(editing) {
  state.editing = editing;
  document.body.classList.toggle('editing', editing);
  document.querySelector('#editor').value = editing ? state.doc.markdown : '';
  document.querySelector('#edit').textContent = editing ? 'cancel' : 'edit markdown';
  if (editing) setDrawing(false);
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
  if (state.editing || state.drawing) return;
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
  const overlay = ink();
  document.addEventListener('mouseup', onSelection);
  overlay.addEventListener('pointerdown', (event) => {
    eraseAt(event);
    beginStroke(event);
  });
  overlay.addEventListener('pointermove', extendStroke);
  overlay.addEventListener('pointerup', endStroke);
  overlay.addEventListener('pointercancel', endStroke);
  document.querySelector('#edit').addEventListener('click', () => setEditing(!state.editing));
  document.querySelector('#save').addEventListener('click', save);
  document.querySelector('#draw').addEventListener('click', () => setDrawing(!state.drawing));
  document.querySelector('#erase').addEventListener('click', () => setErasing(!state.erasing));
  document.querySelector('#notify').addEventListener('click', notify);
  document.querySelector('#compose-save').addEventListener('click', submitCompose);
  document.querySelector('#compose-cancel').addEventListener('click', hideCompose);
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 's' && state.editing) {
      event.preventDefault();
      save();
    }
  });
  window.addEventListener('resize', () => renderInk(state.doc ? state.doc.annotations : []));
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
