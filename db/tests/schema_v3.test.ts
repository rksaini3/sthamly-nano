test('normalize_hf_model: 96 characters per part is the maximum', async () => {
  const db = await freshDb()
  const norm = async (s: string) =>
    (await db.query<{ v: string | null }>(`select public.normalize_hf_model($1) as v`, [s])).rows[0].v
  const a96 = 'a'.repeat(96)
  assert.equal(await norm(`${a96}/b`), `${a96}/b`)
  assert.equal(await norm(`${a96}/${a96}`), `${a96}/${a96}`)
  assert.equal(await norm(`${'a'.repeat(97)}/b`), null)
  assert.equal(await norm(`a/${'b'.repeat(97)}`), null)
})
