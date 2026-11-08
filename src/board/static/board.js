import {
  DEFAULT_TEXT_RATIO,
  nearestRectIndex,
  nearestStrokeIndex,
  project,
  textSize,
  toNormalized,
  toPath,
} from './coords.js';

const POLL_MS = 2000;
const INK_COLOUR = '#c0392b';
const INK_WIDTH = 2;
const ERASE_TOLERANCE = 10;
const BLOCK_REACH = 48; // how far outside a block a drag may start and still belong to it
const INK_GUTTER = { left: 200, bottom: 160 }; // drawing room beside and below the words
const QUOTE_LENGTH = 80;
const OWNER = 'go'; // the browser is always Go's hand
const SAME_POINT = 1e-4;
const TEXT_MIN = 8; // guard rails, not the size control: the words tool moves in TEXT_STEP_PX
const TEXT_MAX = 120;
const TEXT_STEP_PX = 2;
const SVG = 'http://www.w3.org/2000/svg';
const TOOLS = ['pen', 'line', 'arrow', 'rect', 'ellipse', 'text', 'erase'];

const state = {
  name: null,
  rev: null,
  doc: null,
  editing: false,
  drawing: false,
  tool: 'pen',
  compose: null,
};

let draft = null;
let preview = null;

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

function hideBanner() {
  document.querySelector('#banner').classList.remove('visible');
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

/* A mermaid fence is drawn in place, offline: the bundle is vendored. A
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
      naturalSize(holder);
      host.replaceWith(holder);
    } catch (error) {
      host.classList.add('diagram-failed');
      host.title = String(error);
    }
  }
}

/* Mermaid hands the page a diagram that may be three times wider than the
   column and asks to be scaled into it, which turns a wide flowchart into a
   grey smudge at a third of its size. The figure scrolls instead, so a diagram
   keeps the size it was drawn at and the words in it stay words. */
function naturalSize(holder) {
  const svg = holder.querySelector('svg');
  const box = svg && svg.viewBox && svg.viewBox.baseVal;
  if (!box || !box.width || !box.height) return;
  svg.removeAttribute('height');
  svg.setAttribute('width', box.width);
  svg.setAttribute('height', box.height);
  svg.style.maxWidth = 'none';
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
    summary.textContent = `✎ ${annotation.strokes.length} mark(s) here`;
    card.appendChild(summary);
    /* Anyone's ink, not only your own. The browser is the human's hand, and the
       person at the desk has to be able to rub out what the agent drew on their
       document — the per-author rule exists so a writer cannot clobber another
       writer's strokes, not so a mark becomes permanent. */
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = 'erase';
    remove.addEventListener('click', () =>
      saveInk(annotation.resolved_block, [], annotation.author),
    );
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
    /* An orphan has nothing left to be resolved against, so the only useful
       thing to offer is getting rid of it — otherwise it waits for the person
       who wrote it to ask someone else to clear it by hand. */
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = 'remove';
    remove.addEventListener('click', () => removeAnnotation(annotation.id));
    card.appendChild(remove);
    container.appendChild(card);
  }
}

/* --- ink ---------------------------------------------------------------- */

/* The drawing surface reaches past the words on purpose: an arrow pointing at a
   paragraph starts in the white space beside it, and a drag that begins there
   has to reach the same overlay as one that begins on the text. The gutter is
   on the left and below only — to the right sits the margin of notes, and a
   surface that swallowed clicks there would make the notes unclickable.

   No viewBox: one user unit is one CSS pixel, so `project` can subtract the
   overlay's own offset and be done. */
function sizeInk() {
  const article = document.querySelector('#article');
  const overlay = ink();
  if (!article.offsetWidth) return; // the editor is open and the article is hidden
  overlay.style.left = `${-INK_GUTTER.left}px`;
  overlay.setAttribute('width', article.offsetWidth + INK_GUTTER.left);
  overlay.setAttribute('height', article.offsetHeight + INK_GUTTER.bottom);
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
    for (const shape of annotation.strokes) {
      overlay.appendChild(shapeElement(shape, blockRect, overlayRect));
    }
  }
}

function shapeElement(shape, blockRect, overlayRect) {
  const tool = shape.tool || 'pen';
  const colour = shape.color || INK_COLOUR;
  const group = document.createElementNS(SVG, 'g');
  group.setAttribute('stroke', colour);
  group.setAttribute('stroke-width', String(shape.width || INK_WIDTH));
  group.setAttribute('fill', 'none');
  group.setAttribute('stroke-linecap', 'round');
  group.setAttribute('stroke-linejoin', 'round');

  if (tool === 'pen') {
    const path = document.createElementNS(SVG, 'path');
    path.setAttribute('d', toPath(shape.points || [], blockRect, overlayRect));
    group.appendChild(path);
    return group;
  }
  if (tool === 'line' || tool === 'arrow') {
    const from = project(shape.from, blockRect, overlayRect);
    const to = project(shape.to, blockRect, overlayRect);
    group.appendChild(lineElement(from, to));
    if (tool === 'arrow') group.appendChild(arrowHead(from, to));
    return group;
  }
  if (tool === 'rect') {
    const from = project(shape.from, blockRect, overlayRect);
    const to = project(shape.to, blockRect, overlayRect);
    const box = document.createElementNS(SVG, 'rect');
    box.setAttribute('x', String(Math.min(from.x, to.x)));
    box.setAttribute('y', String(Math.min(from.y, to.y)));
    box.setAttribute('width', String(Math.abs(to.x - from.x)));
    box.setAttribute('height', String(Math.abs(to.y - from.y)));
    box.setAttribute('rx', '4');
    group.appendChild(box);
    return group;
  }
  if (tool === 'ellipse') {
    const from = project(shape.from, blockRect, overlayRect);
    const to = project(shape.to, blockRect, overlayRect);
    const circle = document.createElementNS(SVG, 'ellipse');
    circle.setAttribute('cx', String((from.x + to.x) / 2));
    circle.setAttribute('cy', String((from.y + to.y) / 2));
    circle.setAttribute('rx', String(Math.abs(to.x - from.x) / 2));
    circle.setAttribute('ry', String(Math.abs(to.y - from.y) / 2));
    group.appendChild(circle);
    return group;
  }
  const at = project(shape.at || { x: 0, y: 0 }, blockRect, overlayRect);
  const words = document.createElementNS(SVG, 'text');
  const size = textSize(shape.size, blockRect.height, TEXT_MIN, TEXT_MAX);
  words.setAttribute('x', String(at.x));
  words.setAttribute('y', String(at.y));
  words.setAttribute('font-size', String(size));
  words.setAttribute('font-family', 'ui-sans-serif, system-ui, sans-serif');
  words.setAttribute('fill', colour);
  words.setAttribute('stroke', 'none');
  words.textContent = shape.text || '';
  group.appendChild(words);
  return group;
}

function lineElement(from, to) {
  const line = document.createElementNS(SVG, 'line');
  line.setAttribute('x1', String(from.x));
  line.setAttribute('y1', String(from.y));
  line.setAttribute('x2', String(to.x));
  line.setAttribute('y2', String(to.y));
  return line;
}

function arrowHead(from, to) {
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const length = Math.hypot(to.x - from.x, to.y - from.y);
  const size = Math.min(16, length * 0.3);
  const barb = (offset) =>
    `${to.x - size * Math.cos(angle + offset)},${to.y - size * Math.sin(angle + offset)}`;
  const head = document.createElementNS(SVG, 'polyline');
  head.setAttribute('points', `${barb(-0.45)} ${to.x},${to.y} ${barb(0.45)}`);
  return head;
}

function blockUnder(clientX, clientY) {
  const overlay = ink();
  const previous = overlay.style.pointerEvents;
  overlay.style.pointerEvents = 'none';
  const element = document.elementFromPoint(clientX, clientY);
  overlay.style.pointerEvents = previous;
  const block = element ? element.closest('[data-block]') : null;
  return block || nearestBlock(clientX, clientY);
}

/* A drag often starts in the white space beside a paragraph rather than on its
   words, which is exactly where an arrow pointing at it begins. When the point
   hits no block, the mark belongs to the block whose box is nearest — as long
   as it is close enough to have been meant. */
function nearestBlock(clientX, clientY) {
  const nodes = Array.from(document.querySelectorAll('#article [data-block]'));
  const rects = nodes.map((node) => node.getBoundingClientRect());
  const index = nearestRectIndex({ x: clientX, y: clientY }, rects, BLOCK_REACH);
  return index < 0 ? null : nodes[index];
}

function ownInkFor(blockId) {
  return (state.doc.annotations || []).find(
    (annotation) =>
      annotation.kind === 'ink' &&
      annotation.author === OWNER &&
      annotation.resolved_block === blockId,
  );
}

async function saveInk(blockId, strokes, author = OWNER) {
  const block = (state.doc.blocks || []).find((item) => item.id === blockId);
  const quote = (block ? block.text : '').slice(0, QUOTE_LENGTH);
  await request(`/api/docs/${state.name}/ink/${blockId}`, {
    method: 'PUT',
    body: JSON.stringify({ quote, strokes, author }),
  });
  await load();
}

async function removeAnnotation(annotationId) {
  await request(`/api/docs/${state.name}/annotations/${annotationId}`, { method: 'DELETE' });
  await load();
}

/* --- drawing ------------------------------------------------------------ */

/* The block's box right now. A drag can straddle a reflow — a mermaid fence
   that finishes painting, an image that lands — so the rect taken on
   pointerdown is only a fallback: coordinates are worked out against where the
   paragraph actually is at the moment they are needed. */
function liveRect(blockId, fallback) {
  const node = document.querySelector(`[data-block="${blockId}"]`);
  return node ? node.getBoundingClientRect() : fallback;
}

/* The shape being drawn, as fractions of its block. The pointer trail is kept
   in client pixels and normalised here, so a reflow during the drag moves the
   mark with its paragraph instead of stranding it where the paragraph used to
   be. */
function draftShape() {
  const rect = liveRect(draft.blockId, draft.rect);
  if (draft.tool === 'pen') {
    const points = draft.trail.map((point) => toNormalized(point, rect));
    return { tool: 'pen', points, color: INK_COLOUR, width: INK_WIDTH };
  }
  const from = toNormalized(draft.trail[0], rect);
  const to = toNormalized(draft.trail[draft.trail.length - 1], rect);
  return { tool: draft.tool, from, to, color: INK_COLOUR, width: INK_WIDTH };
}

function previewMark() {
  if (preview) preview.remove();
  sizeInk();
  const rect = liveRect(draft.blockId, draft.rect);
  preview = shapeElement(draftShape(), rect, ink().getBoundingClientRect());
  ink().appendChild(preview);
}

function beginMark(event) {
  if (!state.drawing) return;
  if (state.tool === 'erase') {
    eraseAt(event);
    return;
  }
  const block = blockUnder(event.clientX, event.clientY);
  if (!block) return;
  const rect = block.getBoundingClientRect();
  if (state.tool === 'text') {
    openTextCompose(block.dataset.block, toNormalized({ x: event.clientX, y: event.clientY }, rect), event);
    return;
  }
  ink().setPointerCapture(event.pointerId);
  draft = {
    tool: state.tool,
    blockId: block.dataset.block,
    rect,
    trail: [{ x: event.clientX, y: event.clientY }],
  };
  previewMark();
}

function extendMark(event) {
  if (!draft) return;
  draft.trail.push({ x: event.clientX, y: event.clientY });
  previewMark();
}

async function endMark() {
  if (!draft) return;
  const { blockId, tool } = draft;
  const shape = draftShape();
  const empty =
    tool === 'pen'
      ? shape.points.length < 2
      : Math.abs(shape.to.x - shape.from.x) < SAME_POINT &&
        Math.abs(shape.to.y - shape.from.y) < SAME_POINT;
  draft = null;
  if (preview) {
    preview.remove();
    preview = null;
  }
  if (empty) {
    await load();
    return;
  }
  const existing = ownInkFor(blockId);
  await saveInk(blockId, existing ? [...existing.strokes, shape] : [shape]);
}

async function eraseAt(event) {
  if (!state.doc) return;
  const point = { x: event.clientX, y: event.clientY };
  for (const annotation of state.doc.annotations) {
    if (annotation.kind !== 'ink' || annotation.orphaned) continue;
    const block = document.querySelector(`[data-block="${annotation.resolved_block}"]`);
    if (!block) continue;
    const rect = block.getBoundingClientRect();
    const index = nearestStrokeIndex(point, annotation.strokes, rect, ERASE_TOLERANCE);
    if (index < 0) continue;
    /* The stroke is dropped from the list of whoever drew it, which is why the
       author is passed on: a write replaces one author's ink on the block and
       leaves the other's alone. */
    const strokes = annotation.strokes.filter((_, position) => position !== index);
    await saveInk(annotation.resolved_block, strokes, annotation.author);
    return;
  }
}

function setTool(tool) {
  state.tool = tool;
  for (const button of document.querySelectorAll('#tools button')) {
    button.classList.toggle('active', button.dataset.tool === tool);
  }
  ink().style.pointerEvents = state.drawing ? 'auto' : 'none';
}

function setDrawing(drawing) {
  state.drawing = drawing;
  document.body.classList.toggle('drawing', drawing);
  document.querySelector('#draw').textContent = drawing ? 'stop ink' : 'ink';
  document.querySelector('#tools').classList.toggle('visible', drawing);
  setTool(state.tool);
  if (!drawing) {
    ink().style.pointerEvents = 'none';
    hideCompose();
  }
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
  hideBanner();
  setEditing(false);
  await load();
  toast('saved');
}

/* --- composing ---------------------------------------------------------- */

function showCompose(clientX, clientY, placeholder, withSize = false) {
  const compose = document.querySelector('#compose');
  compose.classList.add('visible');
  compose.classList.toggle('with-size', withSize);
  const frame = compose.getBoundingClientRect();
  const left = Math.min(clientX, window.innerWidth - frame.width - 24);
  compose.style.left = `${Math.max(24, left)}px`;
  compose.style.top = `${clientY + window.scrollY + 8}px`;
  const area = compose.querySelector('textarea');
  area.placeholder = placeholder;
  area.value = '';
  area.focus();
}

function hideCompose() {
  document.querySelector('#compose').classList.remove('visible');
  state.compose = null;
}

function openTextCompose(blockId, at, event) {
  const rect = liveRect(blockId, { height: 0 });
  state.compose = { mode: 'text', blockId, at, rect, size: DEFAULT_TEXT_RATIO };
  showCompose(event.clientX, event.clientY, 'words to put on the page', true);
  showSize();
}

/* The size is stored as a fraction of the block, but it is set in pixels: the
   readout says what the words will actually measure, and two pixels at a time
   is about as fine as anyone wants to press. */
function showSize() {
  const compose = state.compose;
  if (!compose || compose.mode !== 'text') return;
  const pixels = textSize(compose.size, compose.rect.height, TEXT_MIN, TEXT_MAX);
  document.querySelector('#size-readout').textContent = `${Math.round(pixels)} px`;
}

function stepSize(pixels) {
  const compose = state.compose;
  if (!compose || compose.mode !== 'text' || !compose.rect.height) return;
  const current = textSize(compose.size, compose.rect.height, TEXT_MIN, TEXT_MAX);
  const next = Math.max(TEXT_MIN, Math.min(TEXT_MAX, current + pixels));
  compose.size = next / compose.rect.height;
  showSize();
}

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
  const rect = range.getBoundingClientRect();
  state.compose = { mode: 'note', block: block.dataset.block, quote, start, end };
  showCompose(rect.left, rect.bottom, 'what do you want to say about this?');
}

async function submitCompose() {
  const compose = state.compose;
  const area = document.querySelector('#compose textarea');
  const text = area.value.trim();
  if (!compose || !text) return hideCompose();
  hideCompose();

  if (compose.mode === 'text') {
    const shape = {
      tool: 'text',
      at: compose.at,
      size: compose.size,
      text,
      color: INK_COLOUR,
      width: INK_WIDTH,
    };
    const existing = ownInkFor(compose.blockId);
    await saveInk(compose.blockId, existing ? [...existing.strokes, shape] : [shape]);
    return;
  }

  await request(`/api/docs/${state.name}/annotations`, {
    method: 'POST',
    body: JSON.stringify({
      block: compose.block,
      quote: compose.quote,
      start: compose.start,
      end: compose.end,
      note: text,
      author: OWNER,
    }),
  });
  await load();
  toast('noted');
}

async function notify() {
  await request('/api/inbox', {
    method: 'POST',
    body: JSON.stringify({ text: `board: ${state.name}` }),
  });
  toast('marked — read before my next message');
}

/* --- wiring ------------------------------------------------------------- */

/* A handler that fails silently is indistinguishable from a tool that does not
   work. Every pointer and button path goes through this, so an exception ends
   up on the page instead of in a console nobody is looking at. */
function guarded(handler) {
  return (event) => {
    try {
      const result = handler(event);
      if (result && typeof result.catch === 'function') result.catch(reportError);
    } catch (error) {
      reportError(error);
    }
  };
}

function reportError(error) {
  const detail = error && error.message ? error.message : String(error);
  showBanner(`that did not go through: ${detail}`);
}

function wire() {
  const overlay = ink();
  document.addEventListener('mouseup', onSelection);
  overlay.addEventListener('pointerdown', guarded(beginMark));
  overlay.addEventListener('pointermove', guarded(extendMark));
  overlay.addEventListener('pointerup', guarded(endMark));
  overlay.addEventListener('pointercancel', guarded(endMark));
  for (const button of document.querySelectorAll('#tools button')) {
    button.addEventListener('click', guarded(() => setTool(button.dataset.tool)));
  }
  document.querySelector('#draw').addEventListener('click', guarded(() => setDrawing(!state.drawing)));
  document.querySelector('#edit').addEventListener('click', guarded(() => setEditing(!state.editing)));
  document.querySelector('#save').addEventListener('click', guarded(save));
  document.querySelector('#notify').addEventListener('click', guarded(notify));
  document.querySelector('#compose-save').addEventListener('click', guarded(submitCompose));
  document.querySelector('#compose-cancel').addEventListener('click', hideCompose);
  document.querySelector('#size-smaller').addEventListener('click', guarded(() => stepSize(-TEXT_STEP_PX)));
  document.querySelector('#size-bigger').addEventListener('click', guarded(() => stepSize(TEXT_STEP_PX)));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') hideCompose();
    if ((event.metaKey || event.ctrlKey) && event.key === 's' && state.editing) {
      event.preventDefault();
      guarded(save)();
    }
  });
  window.addEventListener('resize', () => renderInk(state.doc ? state.doc.annotations : []));
  window.setInterval(poll, POLL_MS);
  setTool(TOOLS[0]);
}

async function poll() {
  if (!state.name || state.editing) return;
  if (document.querySelector('#compose').classList.contains('visible')) return;
  const head = await request(`/api/docs/${state.name}`);
  if (head.rev && head.rev !== state.rev) {
    state.doc = head;
    state.rev = head.rev;
    await renderDoc(head);
  }
}

state.name = window.location.pathname.split('/d/').pop().replace(/\.md$/, '');
wire();
load().catch((error) => showBanner(String(error)));
