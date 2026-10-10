// Runs db/schema.sql + db/schema_v3.sql inside PGlite (Postgres compiled to WASM): no server, no network.
// Run with: npm run test:db
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { PGlite } from '@electric-sql/pglite'
import modelIds from '../../tests/fixtures/model_ids.json'
import licenseFixture from '../../tests/fixtures/license_key.json'

const sqlFile = (name: string) => readFileSync(join(process.cwd(), 'db', name), 'utf8')

// Supabase always has these three roles; plain Postgres does not, so create them first.
async function freshDb(): Promise<PGlite> {
  const db = new PGlite()
  await db.exec(`
    create role anon nologin;
    create role authenticated nologin;
    create role service_role nologin bypassrls;
  `)
  await db.exec(sqlFile('schema.sql'))
  await db.exec(sqlFile('schema_v3.sql'))
  return db
}

/** Pretend to be a caller coming from this IP (what PostgREST/Cloudflare put in the headers). */
async function fromIp(db: PGlite, ip: string) {
  await db.query(`select set_config('request.headers', $1, false)`, [JSON.stringify({ 'cf-connecting-ip': ip })])
}

/** Call a function as `role` (anon = the browser, service_role = the worker). */
async function rpc(db: PGlite, role: string, fn: string, ...args: unknown[]) {
  const params = args.map((_, i) => `$${i + 1}`).join(', ')
  await db.exec(`set role ${role}`)
  try {
    const r = await db.query<{ r: any }>(`select public.${fn}(${params}) as r`, args)
    return r.rows[0].r
  } finally {
    await db.exec('reset role')
  }
}

const asAnon = (db: PGlite, fn: string, ...args: unknown[]) => rpc(db, 'anon', fn, ...args)
const asWorker = (db: PGlite, fn: string, ...args: unknown[]) => rpc(db, 'service_role', fn, ...args)

async function queuedRows(db: PGlite, n: number) {
  for (let i = 0; i < n; i++) {
    await db.query(`insert into public.jobs (kind, hf_model, requester_hash) values ('audit', $1, 'seed')`, [`own/model-${i}`])
  }
}

test('anon cannot read or write any table except the public models table', async () => {
  const db = await freshDb()
  await db.query(`insert into public.models (model_name, hf_repo) values ('m', 'o/m')`)
  await db.exec('set role anon')
  for (const t of ['jobs', 'licenses', 'license_profiles', 'rate_events', 'app_settings']) {
    await assert.rejects(db.query(`select * from public.${t}`), /permission denied/, t)
  }
  await assert.rejects(db.query(`insert into public.jobs (kind, hf_model, requester_hash) values ('audit','a/b','x')`), /permission denied/)
  await assert.rejects(db.query(`insert into public.models (model_name, hf_repo) values ('x','o/x')`), /permission denied/)
  const models = await db.query(`select hf_repo from public.models`)
  assert.equal(models.rows.length, 1)
  await db.exec('reset role')
})

test('anon cannot call worker or internal functions', async () => {
  const db = await freshDb()
  for (const fn of ['claim_next_job()', 'purge_expired()', 'requester_hash()', 'client_ip()']) {
    await assert.rejects(asAnon(db, fn.replace('()', '')), /permission denied/, fn)
  }
  await assert.rejects(asAnon(db, 'fail_stale_jobs', 5), /permission denied/)
  await assert.rejects(asAnon(db, 'normalize_hf_model', 'a/b'), /permission denied/)
})

test('normalize_hf_model agrees with the shared fixtures', async () => {
  const db = await freshDb()
  for (const { input, expected } of modelIds as Array<{ input: string; expected: string | null }>) {
    const r = await db.query<{ v: string | null }>(`select public.normalize_hf_model($1) as v`, [input])
    assert.equal(r.rows[0].v, expected, JSON.stringify(input))
  }
})

test('create_audit_job: invalid model, then a valid one is queued and visible through get_job', async () => {
  const db = await freshDb()
  await fromIp(db, '10.0.0.1')
  await assert.rejects(asAnon(db, 'create_audit_job', 'not a model'), /invalid_model/)
  const made = await asAnon(db, 'create_audit_job', 'https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct')
  assert.equal(made.status, 'queued')
  assert.equal(made.deduped, false)
  const job = await asAnon(db, 'get_job', made.id)
  assert.equal(job.hf_model, 'Qwen/Qwen2.5-1.5B-Instruct')
  assert.equal(job.queue_position, 1)
  assert.equal(job.result, null)
  assert.ok(!('requester_hash' in job), 'requester_hash must never leave the database')
})

test('create_audit_job: the 4th new model from one IP is rate limited; a different IP is not', async () => {
  const db = await freshDb()
  await fromIp(db, '10.0.0.2')
  for (const m of ['a/one', 'a/two', 'a/three']) await asAnon(db, 'create_audit_job', m)
  await assert.rejects(asAnon(db, 'create_audit_job', 'a/four'), /rate_limited/)
  await fromIp(db, '10.0.0.3')
  const ok = await asAnon(db, 'create_audit_job', 'a/four')
  assert.equal(ok.status, 'queued')
})

test('create_audit_job: same model (any spelling) returns the same job and costs no daily credit', async () => {
  const db = await freshDb()
  await fromIp(db, '10.0.0.4')
  const first = await asAnon(db, 'create_audit_job', 'Qwen/Qwen2.5-0.5B-Instruct')
  const again = await asAnon(db, 'create_audit_job', 'https://huggingface.co/qwen/qwen2.5-0.5b-instruct/tree/main')
  assert.equal(again.id, first.id)
  assert.equal(again.deduped, true)
  for (const m of ['b/one', 'b/two']) await asAnon(db, 'create_audit_job', m) // 3 distinct jobs now
  assert.equal((await asAnon(db, 'create_audit_job', 'Qwen/Qwen2.5-0.5B-Instruct')).id, first.id) // still allowed at the limit
  await assert.rejects(asAnon(db, 'create_audit_job', 'b/three'), /rate_limited/)
})

test('create_audit_job: a finished job is reused for 7 days, then a new one is made', async () => {
  const db = await freshDb()
  await fromIp(db, '10.0.0.5')
  const first = await asAnon(db, 'create_audit_job', 'c/model')
  await db.query(`update public.jobs set status = 'done', finished_at = now() - interval '2 days' where id = $1`, [first.id])
  assert.equal((await asAnon(db, 'create_audit_job', 'c/model')).id, first.id)
  await db.query(`update public.jobs set finished_at = now() - interval '8 days' where id = $1`, [first.id])
  const second = await asAnon(db, 'create_audit_job', 'c/model')
  assert.notEqual(second.id, first.id)
})

test('create_audit_job: queue_full at 25 queued jobs', async () => {
  const db = await freshDb()
  await queuedRows(db, 25)
  await fromIp(db, '10.0.0.6')
  await assert.rejects(asAnon(db, 'create_audit_job', 'd/model'), /queue_full/)
})

test('get_job: result only when done, error only when failed or refused, null when unknown or expired', async () => {
  const db = await freshDb()
  await fromIp(db, '10.0.0.7')
  const { id } = await asAnon(db, 'create_audit_job', 'e/model')
  await db.query(`update public.jobs set result = '{"kind":"audit"}', error = 'secret-ish' where id = $1`, [id])
  let job = await asAnon(db, 'get_job', id)
  assert.equal(job.result, null)
  assert.equal(job.error, null)
  await db.query(`update public.jobs set status = 'done' where id = $1`, [id])
  job = await asAnon(db, 'get_job', id)
  assert.deepEqual(job.result, { kind: 'audit' })
  assert.equal(job.error, null)
  await db.query(`update public.jobs set status = 'refused' where id = $1`, [id])
  job = await asAnon(db, 'get_job', id)
  assert.equal(job.result, null)
  assert.equal(job.error, 'secret-ish')
  assert.equal(await asAnon(db, 'get_job', '00000000-0000-4000-8000-000000000000'), null)
  await db.query(`update public.jobs set expires_at = now() - interval '1 minute' where id = $1`, [id])
  assert.equal(await asAnon(db, 'get_job', id), null)
})

test('claim_next_job: oldest first, each job only once, empty queue gives null', async () => {
  const db = await freshDb()
  await queuedRows(db, 2)
  const a = await asWorker(db, 'claim_next_job')
  const b = await asWorker(db, 'claim_next_job')
  assert.equal(a.hf_model, 'own/model-0')
  assert.equal(b.hf_model, 'own/model-1')
  assert.notEqual(a.id, b.id)
  assert.equal(await asWorker(db, 'claim_next_job'), null)
  const r = await db.query<{ status: string }>(`select status from public.jobs where id = $1`, [a.id])
  assert.equal(r.rows[0].status, 'running')
})

test('fail_stale_jobs fails only running jobs older than the limit', async () => {
  const db = await freshDb()
  await queuedRows(db, 2)
  const old = await asWorker(db, 'claim_next_job')
  const fresh = await asWorker(db, 'claim_next_job')
  await db.query(`update public.jobs set started_at = now() - interval '3 hours' where id = $1`, [old.id])
  assert.equal(await asWorker(db, 'fail_stale_jobs', 90), 1)
  const rows = await db.query<{ id: string; status: string; error: string | null }>(`select id, status, error from public.jobs`)
  const byId = Object.fromEntries(rows.rows.map((x) => [x.id, x]))
  assert.equal(byId[old.id].status, 'failed')
  assert.equal(byId[old.id].error, 'worker_timeout')
  assert.equal(byId[fresh.id].status, 'running')
})

test('activate_license: Python hash == SQL hash, devices, limits, revoke and wrong-key rate limit', async () => {
  const db = await freshDb()
  await fromIp(db, '10.0.1.1')
  await db.query(`insert into public.licenses (key_hash, profile, note) values ($1, 'lawyer', 'fixture')`, [licenseFixture.hash])

  // the same key typed sloppily still activates (this also proves the Python-made hash matches the SQL one)
  const a = await asAnon(db, 'activate_license', 'sth abcd-efgh ijkl mnop', 'device-aaaaaaaa')
  assert.equal(a.ok, true)
  assert.equal(a.profile, 'lawyer')
  assert.equal(a.label, 'Sthamly Legal Desk')
  assert.equal(a.devices_used, 1)
  assert.equal(a.max_devices, 2)
  assert.match(a.system_prompt, /vakeel nahi/)
  assert.ok(Array.isArray(a.actions) && a.actions.length > 0)

  // same device again: not counted twice
  assert.equal((await asAnon(db, 'activate_license', licenseFixture.key, 'device-aaaaaaaa')).devices_used, 1)
  assert.equal((await asAnon(db, 'activate_license', licenseFixture.key, 'device-bbbbbbbb')).devices_used, 2)
  assert.deepEqual(await asAnon(db, 'activate_license', licenseFixture.key, 'device-cccccccc'), { ok: false, error: 'device_limit' })
  assert.deepEqual(await asAnon(db, 'activate_license', licenseFixture.key, 'bad device!'), { ok: false, error: 'invalid_device' })

  await db.query(`update public.licenses set revoked = true where key_hash = $1`, [licenseFixture.hash])
  assert.deepEqual(await asAnon(db, 'activate_license', licenseFixture.key, 'device-aaaaaaaa'), { ok: false, error: 'revoked' })
  await db.query(`update public.licenses set revoked = false where key_hash = $1`, [licenseFixture.hash])

  // 20 wrong keys are answered "invalid_key"; from the 21st on even the right key is refused for the day
  for (let i = 0; i < 20; i++) {
    const r = await asAnon(db, 'activate_license', `STH-AAAA-BBBB-CCCC-${String(1000 + i)}`, 'device-aaaaaaaa')
    assert.deepEqual(r, { ok: false, error: 'invalid_key' })
  }
  assert.deepEqual(await asAnon(db, 'activate_license', licenseFixture.key, 'device-aaaaaaaa'), { ok: false, error: 'rate_limited' })

  // another IP is unaffected
  await fromIp(db, '10.0.1.2')
  assert.equal((await asAnon(db, 'activate_license', licenseFixture.key, 'device-aaaaaaaa')).ok, true)
})

test('activate_license: malformed keys count as wrong attempts, and the raw key is never stored', async () => {
  const db = await freshDb()
  await fromIp(db, '10.0.1.3')
  assert.deepEqual(await asAnon(db, 'activate_license', '', 'device-aaaaaaaa'), { ok: false, error: 'invalid_key' })
  assert.deepEqual(await asAnon(db, 'activate_license', null, 'device-aaaaaaaa'), { ok: false, error: 'invalid_key' })
  const n = await db.query<{ n: number }>(`select count(*)::int as n from public.rate_events where kind = 'bad_key'`)
  assert.equal(n.rows[0].n, 2)
  const cols = await db.query<{ column_name: string }>(
    `select column_name from information_schema.columns where table_name = 'licenses'`
  )
  assert.ok(!cols.rows.some((c) => /^key$|raw/.test(c.column_name)))
})
