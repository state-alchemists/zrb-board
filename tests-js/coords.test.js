import assert from 'node:assert/strict';
import test from 'node:test';

import {
  distanceToPolyline,
  distanceToSegment,
  nearestStrokeIndex,
  project,
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
