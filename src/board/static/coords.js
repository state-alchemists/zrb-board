/* Pure geometry for the pen layer: normalised points, projected back onto a
   block, and the hit test that lets a click erase a stroke it did not land on
   exactly. Kept free of the DOM so it can be tested under `node --test`. */

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

/* The index of the stroke within `tolerance` pixels of `point` (client
   coordinates), or -1. A 2px stroke is not a hit target, so the ink is judged
   by distance rather than by which element the pointer landed on. */
export function nearestStrokeIndex(point, strokes, blockRect, tolerance) {
  let bestIndex = -1;
  let bestDistance = tolerance;
  strokes.forEach((stroke, index) => {
    const points = (stroke.points || []).map((item) => toAbsolute(item, blockRect));
    const distance = distanceToPolyline(point, points);
    if (distance <= bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  return bestIndex;
}
