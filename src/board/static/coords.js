/* Pure geometry for the pen layer: normalised points, projected back onto a
   block. Kept free of the DOM so it can be tested under `node --test`. */

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
