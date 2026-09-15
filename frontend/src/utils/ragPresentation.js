export function formatDistance(distance) {
  const numericDistance = Number(distance)
  return Number.isFinite(numericDistance) ? numericDistance.toFixed(4) : '—'
}

export function formatSourceLocation(item) {
  const range =
    item.source_start === item.source_end
      ? `${item.source_start}`
      : `${item.source_start}–${item.source_end}`
  return `${item.source_type} ${range}`
}
