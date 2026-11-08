/* Pure geometry for the pen layer: normalised points, projection back onto a
   block, and the outline of every kind of mark so one click can find it.
   Kept free of the DOM so it can be tested under `node --test`. */

const ELLIPSE_SEGMENTS = 48;
const TEXT_PLACEHOLDER = 0.22; // how much of a block a text mark is assumed to cover

export function toNormalized(point, blockRect) {
  if (!blockRect.width || !blockRect.height) return { x: 0, y: 0 };
  return {
    x: (point.x - blockRect.left) / blockRect.width,
    y: (point.y - blockRect.top) / blockRect.height,
  };
}

export function toAbsolute(point, blockRect) {
  return {
    x: blockRect.left + point.x * blockRect.width,
    y: blockRect.top + point.y * blockRect.height,
  };
}

export function project(point, blockRect, overlayRect) {
  const absolute = toAbsolute(point, blockRect);
  return { x: absolute.x - overlayRect.left, y: absolute.y - overlayRect.top };
}

export function toPath(points, blockRect, overlayRect) {
  if (!points.length) return '';
  return points
    .map((point, index) => {
      const placed = project(point, blockRect, overlayRect);
      const command = index === 0 ? 'M' : 'L';
      return `${command}${placed.x.toFixed(1)} ${placed.y.toFixed(1)}`;
    })
    .join(' ');
}

export function distanceToSegment(point, start, end) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const span = dx * dx + dy * dy;
  if (span === 0) return Math.hypot(point.x - start.x, point.y - start.y);
  const along = ((point.x - start.x) * dx + (point.y - start.y) * dy) / span;
  const clamped = Math.max(0, Math.min(1, along));
  return Math.hypot(point.x - (start.x + clamped * dx), point.y - (start.y + clamped * dy));
}

export function distanceToPolyline(point, points) {
  if (!points.length) return Infinity;
  if (points.length === 1) return Math.hypot(point.x - points[0].x, point.y - points[0].y);
  let best = Infinity;
  for (let index = 1; index < points.length; index += 1) {
    best = Math.min(best, distanceToSegment(point, points[index - 1], points[index]));
  }
  return best;
}

export function rectOutline(from, to) {
  return [
    { x: from.x, y: from.y },
    { x: to.x, y: from.y },
    { x: to.x, y: to.y },
    { x: from.x, y: to.y },
    { x: from.x, y: from.y },
  ];
}

export function ellipseOutline(from, to, segments = ELLIPSE_SEGMENTS) {
  const centreX = (from.x + to.x) / 2;
  const centreY = (from.y + to.y) / 2;
  const radiusX = Math.abs(to.x - from.x) / 2;
  const radiusY = Math.abs(to.y - from.y) / 2;
  const points = [];
  for (let index = 0; index <= segments; index += 1) {
    const angle = (index / segments) * Math.PI * 2;
    points.push({
      x: centreX + radiusX * Math.cos(angle),
      y: centreY + radiusY * Math.sin(angle),
    });
  }
  return points;
}

/* The path a click has to come near to hit a mark, in normalised coordinates.
   Every tool answers, so the eraser does not need to know what it is erasing. */
export function outlinePoints(shape) {
  const tool = shape.tool || 'pen';
  if (tool === 'line' || tool === 'arrow') return [shape.from, shape.to];
  if (tool === 'rect') return rectOutline(shape.from, shape.to);
  if (tool === 'ellipse') return ellipseOutline(shape.from, shape.to);
  if (tool === 'text') {
    const at = shape.at || { x: 0, y: 0 };
    return rectOutline(at, { x: at.x + TEXT_PLACEHOLDER, y: at.y + TEXT_PLACEHOLDER / 2 });
  }
  return shape.points || [];
}

/* The index of the mark within `tolerance` pixels of `point` (client
   coordinates), or -1. A 2px stroke is not a hit target, so marks are judged
   by distance rather than by which element the pointer landed on. */
export function nearestStrokeIndex(point, strokes, blockRect, tolerance) {
  let bestIndex = -1;
  let bestDistance = tolerance;
  strokes.forEach((stroke, index) => {
    const points = outlinePoints(stroke).map((item) => toAbsolute(item, blockRect));
    const distance = distanceToPolyline(point, points);
    if (distance <= bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  return bestIndex;
}
