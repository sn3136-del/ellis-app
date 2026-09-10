// Display only the entry instructions in the already-published answer.
// Keep mode, timing and product qualifications verbatim; never infer a sibling's rule.
export function entryInstructionTexts(value) {
  return (Array.isArray(value) ? value : [value])
    .filter((text) => typeof text === 'string' && text.trim())
    .map((text) => text.trim())
}

export function publishedEntryInstructions(guidance) {
  const route = entryInstructionTexts(guidance?.entry_requirements)
  const products = (Array.isArray(guidance?.visa_products) ? guidance.visa_products : [])
    .filter((product) => product && typeof product === 'object')
    .map((product, index) => ({
      index,
      name: typeof product.type === 'string' ? product.type : '',
      texts: entryInstructionTexts(product.entry_requirements),
    }))
    .filter((product) => product.texts.length &&
      JSON.stringify(product.texts) !== JSON.stringify(route))
  return { route, products }
}
