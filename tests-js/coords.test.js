import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_TEXT_RATIO,
  distanceToPolyline,
  distanceToRect,
  distanceToSegment,
  ellipseOutline,
  nearestRectIndex,
  nearestStrokeIndex,
  outlinePoints,
  project,
  rectOutline,
  textSize,
  toAbsolute,
  toNormalized,
  toPath,
} from '../src/board/static/coords.js';

const BLOCK = { left: 100, top: 200, width: 400, height: 50 };
const OVERLAY = { left: 80, top: 60, width: 440, height: 900 };

test('a point at the block corner normalises to zero', () => {
  assert.deepEqual(toNormalized({ x: 100, y: 200 }, BLOCK), { x: 0, y: 0 });
});

test('a point in the middle of the block normalises to one half', () => {
  assert.deepEqual(toNormalized({ x: 300, y: 225 }, BLOCK), { x: 0.5, y: 0.5 });
});

test('normalising and projecting back returns the same overlay point', () => {
  const point = { x: 260, y: 230 };
  const projected = project(toNormalized(point, BLOCK), BLOCK, OVERLAY);
  assert.deepEqual(projected, { x: point.x - OVERLAY.left, y: point.y - OVERLAY.top });
});

test('drawing on a block that has since been resized keeps its relative place', () => {
  const drawn = toNormalized({ x: 300, y: 225 }, BLOCK);
  const wider = { ...BLOCK, width: 800 };
  assert.deepEqual(project(drawn, wider, OVERLAY), { x: 500 - OVERLAY.left, y: 225 - OVERLAY.top });
});

test('a zero-sized block normalises to the origin instead of dividing by zero', () => {
  assert.deepEqual(toNormalized({ x: 5, y: 5 }, { left: 0, top: 0, width: 0, height: 0 }), {
    x: 0,
    y: 0,
  });
});

test('a path starts at the first point and lines to the rest', () => {
  const path = toPath(
    [
      { x: 0, y: 0 },
      { x: 0.5, y: 0.5 },
    ],
    BLOCK,
    OVERLAY,
  );
  assert.equal(path, `M20.0 140.0 L220.0 165.0`);
});

test('an empty stroke produces no path', () => {
  assert.equal(toPath([], BLOCK, OVERLAY), '');
});

test('a stroke may run outside its block and still project', () => {
  const outside = toNormalized({ x: 40, y: 190 }, BLOCK);
  assert.ok(outside.x < 0);
  assert.deepEqual(toAbsolute(outside, BLOCK), { x: 40, y: 190 });
});

test('a point beside a segment is measured at right angles', () => {
  assert.equal(distanceToSegment({ x: 5, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 }), 3);
});

test('a point past the end of a segment is measured to that end', () => {
  assert.equal(distanceToSegment({ x: 14, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 }), 5);
});

test('a one-point stroke still has a distance', () => {
  assert.equal(distanceToPolyline({ x: 0, y: 4 }, [{ x: 0, y: 0 }]), 4);
});

test('a click ten pixels off a stroke still finds it', () => {
  const strokes = [{ points: [{ x: 0, y: 0 }, { x: 1, y: 0 }] }];
  const point = { x: BLOCK.left + BLOCK.width / 2, y: BLOCK.top + 8 };
  assert.equal(nearestStrokeIndex(point, strokes, BLOCK, 10), 0);
});

test('a click far from every stroke finds nothing', () => {
  const strokes = [{ points: [{ x: 0, y: 0 }, { x: 1, y: 0 }] }];
  const point = { x: BLOCK.left, y: BLOCK.top + 200 };
  assert.equal(nearestStrokeIndex(point, strokes, BLOCK, 10), -1);
});

test('the nearer of two strokes is the one erased', () => {
  const strokes = [
    { points: [{ x: 0, y: 0 }, { x: 1, y: 0 }] },
    { points: [{ x: 0, y: 0.8 }, { x: 1, y: 0.8 }] },
  ];
  const point = { x: BLOCK.left + 200, y: BLOCK.top + 41 };
  assert.equal(nearestStrokeIndex(point, strokes, BLOCK, 10), 1);
});

test('a box is hit on its edge, not through its middle', () => {
  const box = { tool: 'rect', from: { x: 0, y: 0 }, to: { x: 1, y: 1 } };
  const onEdge = { x: BLOCK.left + 3, y: BLOCK.top + 25 };
  const inMiddle = { x: BLOCK.left + 200, y: BLOCK.top + 25 };
  assert.equal(nearestStrokeIndex(onEdge, [box], BLOCK, 10), 0);
  assert.equal(nearestStrokeIndex(inMiddle, [box], BLOCK, 10), -1);
});

test('a circle is hit on its rim', () => {
  const circle = { tool: 'ellipse', from: { x: 0, y: 0 }, to: { x: 1, y: 1 } };
  const onRim = { x: BLOCK.left + 400, y: BLOCK.top + 25 };
  const inMiddle = { x: BLOCK.left + 200, y: BLOCK.top + 25 };
  assert.equal(nearestStrokeIndex(onRim, [circle], BLOCK, 10), 0);
  assert.equal(nearestStrokeIndex(inMiddle, [circle], BLOCK, 10), -1);
});

test('a line is hit anywhere between its ends', () => {
  const line = { tool: 'line', from: { x: 0.1, y: 0.5 }, to: { x: 0.9, y: 0.5 } };
  assert.equal(nearestStrokeIndex({ x: BLOCK.left + 300, y: BLOCK.top + 28 }, [line], BLOCK, 10), 0);
});

test('a word is hit near where it was written', () => {
  const word = { tool: 'text', at: { x: 0.5, y: 0.5 }, text: 'hello' };
  assert.equal(nearestStrokeIndex({ x: BLOCK.left + 210, y: BLOCK.top + 26 }, [word], BLOCK, 10), 0);
  assert.equal(nearestStrokeIndex({ x: BLOCK.left + 20, y: BLOCK.top + 45 }, [word], BLOCK, 10), -1);
});

test('a box and a circle report their outlines in normalised coordinates', () => {
  assert.deepEqual(rectOutline({ x: 0.2, y: 0.4 }, { x: 0.6, y: 0.8 })[1], { x: 0.6, y: 0.4 });
  const circle = ellipseOutline({ x: 0, y: 0 }, { x: 1, y: 1 }, 4);
  assert.equal(circle.length, 5);
  assert.ok(Math.abs(circle[0].x - 0) < 1e-9 || Math.abs(circle[0].x - 1) < 1e-9);
});

test('an old pen stroke with no tool is still treated as a pen stroke', () => {
  assert.deepEqual(outlinePoints({ points: [{ x: 0, y: 0 }] }), [{ x: 0, y: 0 }]);
});

test('a point inside a block is zero away from it', () => {
  assert.equal(distanceToRect({ x: 200, y: 220 }, BLOCK), 0);
});

test('a point in the margin is its distance from the nearest edge', () => {
  assert.equal(distanceToRect({ x: 90, y: 220 }, BLOCK), 10);
  assert.equal(distanceToRect({ x: 200, y: BLOCK.top + BLOCK.height + 30 }, BLOCK), 30);
});

test('a drag that starts in the margin belongs to the block beside it', () => {
  const lower = { left: 100, top: 300, width: 400, height: 50 };
  const rects = [BLOCK, lower];
  assert.equal(nearestRectIndex({ x: 60, y: 220 }, rects, 48), 0);
  assert.equal(nearestRectIndex({ x: 60, y: 330 }, rects, 48), 1);
});

test('a drag that starts far from every block belongs to none', () => {
  assert.equal(nearestRectIndex({ x: 200, y: 900 }, [BLOCK], 48), -1);
});

test('a word defaults to a share of its paragraph', () => {
  assert.equal(textSize(undefined, 40, 8, 120), 18);
});

test('a word keeps the size it was given, scaled to the block', () => {
  assert.equal(textSize(1, 20, 8, 120), 20);
  assert.equal(textSize(1, 40, 8, 120), 40);
});

test('a word never grows past the guard rails', () => {
  assert.equal(textSize(5, 40, 8, 120), 120);
  assert.equal(textSize(0.01, 10, 8, 120), 8);
});
