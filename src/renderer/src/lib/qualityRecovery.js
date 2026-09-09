const routeFields = ['travel_document_country', 'destination_country', 'travel_document_type', 'travel_purpose']
const routeKey = (row) => JSON.stringify(routeFields.map((f) => row[f] ?? null))
export function indexRecoveredRecords(rows) {
  const index = new Map()
  for (const row of rows) {
    const key = routeKey(row)
    index.set(key, [...(index.get(key) || []), row])
  }
  return index
}
export function recoveredForRecord(current, index) {
  const route = index.get(routeKey(current)) || []
  const exact = route.filter((r) => r.visa_type_name === current.visa_type_name)
  // Return references only. Never merge values, clear a fix, or change grades.
  return exact.length ? exact : route
}
