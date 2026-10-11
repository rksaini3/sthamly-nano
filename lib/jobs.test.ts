test('normalizeModelId: 96 characters per part is the maximum', () => {
  const a96 = 'a'.repeat(96)
  assert.equal(normalizeModelId(`${a96}/b`), `${a96}/b`)
  assert.equal(normalizeModelId(`${a96}/${a96}`), `${a96}/${a96}`)
  assert.equal(normalizeModelId(`${'a'.repeat(97)}/b`), null)
  assert.equal(normalizeModelId(`a/${'b'.repeat(97)}`), null)
})
