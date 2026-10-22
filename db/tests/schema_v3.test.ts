import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { PGlite } from '@electric-sql/pglite'
import fixtures from '../../tests/fixtures/model_ids.json'
import licenseFixture from '../../tests/fixtures/license_key.json'

// Run from the repo root (npm run test:db does that).
const SCHEMA_SQL = readFileSync(resolve(process.cwd(), 'db', 'schema_v3.sql'), 'utf8')

type Row = Record<string, any>

// ---------------------------------------------------------------- helpers

async function freshDb(): Promise<PGlite> {
  const db = new PGlite()
  await db.exec(SCHEMA_SQL)
  return db
}

async function rows(db: PGlite, sql: string, params: unknown[] = []): Promise<Row[]> {
  return (await db.query<Row>(sql, params)).rows
}

async function one(db: PGlite, sql: string, params: unknown[] = []): Promise<Row> {
  const [row] = await rows(db, sql, params)
  return row
}

/** Run `fn` as a database role, then switch back. Used to test grants and RLS. */
async function asRole<T>(db: PGlite, role: string, fn: () => Promise<T>): Promise<T> {
  await db.exec(`set role ${role}`)
  try {
    return await fn()
  } finally {
    await db.exec('reset role')
  }
}

const JSON_COLUMNS = new Set(['steps', 'result'])

/** Insert one row. Keys ending in _at are timestamps (pass ISO strings). steps/result are JSON. */
async function insertRow(db: PGlite, table: string, row: Row): Promise<Row> {
  const cols = Object.keys(row)
  const placeholders = cols.map((c, i) => {
    const cast = JSON_COLUMNS.has(c) ? '::jsonb' : c.endsWith('_at') ? '::timestamptz' : ''
    return `$${i + 1}${cast}`
  })
  const values = cols.map((c) =>
    JSON_COLUMNS.has(c) && row[c] !== null ? JSON.stringify(row[c]) : row[c],
  )
  return one(
    db,
    `insert into public.${table} (${cols.join(', ')}) values (${placeholders.join(', ')}) returning *`,
    values,
  )
}

async function insertJob(db: PGlite, overrides: Row = {}): Promise<Row> {
  return insertRow(db, 'jobs', {
    kind: 'audit',
    hf_model: 'Qwen/Qwen2.5-0.5B-Instruct',
    requester_hash: 'test-requester',
    status: 'queued',
    progress: 0,
    steps: [],
    ...overrides,
  })
}

async function licenseHash(db: PGlite, key: string): Promise<string> {
  return (await one(db, 'select public.hash_license_key($1) as h', [key])).h
}

async function insertLicense(db: PGlite, key: string, overrides: Row = {}): Promise<Row> {
  return insertRow(db, 'licenses', {
    key_hash: await licenseHash(db, key),
    profile: 'general',
    max_devices: 2,
    revoked: false,
    ...overrides,
  })
}

async function verify(db: PGlite, key: string, fingerprint: string, os = 'test'): Promise<Row> {
  const r = await one(db, 'select public.verify_client_activation($1, $2, $3) as r', [key, fingerprint, os])
  return r.r
}

/** ISO timestamp `seconds` in the past. */
const ago = (seconds: number) => new Date(Date.now() - seconds * 1000).toISOString()

// ---------------------------------------------------------------- model ids

test('normalize_hf_model matches the shared fixtures (same file the TypeScript and Python tests use)', async () => {
  const db = await freshDb()
  assert.ok(fixtures.length > 25)
  for (const { input, expected } of fixtures as Array<{ input: string; expected: string | null }>) {
    const got = (await one(db, 'select public.normalize_hf_model($1) as v', [input])).v
    assert.equal(got, expected, JSON.stringify(input))
  }
})

test('normalize_hf_model: 96 characters per part is the maximum', async () => {
  const db = await freshDb()
  const norm = async (s: string) => (await one(db, 'select public.normalize_hf_model($1) as v', [s])).v
  const a96 = 'a'.repeat(96)
  assert.equal(await norm(`${a96}/b`), `${a96}/b`)
  assert.equal(await norm(`${a96}/${a96}`), `${a96}/${a96}`)
  assert.equal(await norm(`${'a'.repeat(97)}/b`), null)
  assert.equal(await norm(`a/${'b'.repeat(97)}`), null)
})

test('normalize_hf_model: query string and fragment are removed even across newlines (regression)', async () => {
  const db = await freshDb()
  const got = (await one(db, 'select public.normalize_hf_model($1) as v', ['Qwen/A?x=1\nzzz'])).v
  assert.equal(got, 'Qwen/A')
})

// ---------------------------------------------------------------- license keys

test('hash_license_key matches the shared fixture hash (Python and SQL agree)', async () => {
  const db = await freshDb()
  assert.equal(await licenseHash(db, licenseFixture.key), licenseFixture.hash)
  assert.equal(await licenseHash(db, ' sth abcd-efgh ijkl mnop '), licenseFixture.hash)
})

test('valid_license_key_shape: dashes and spaces are optional, 0 8 9 I O are not allowed', async () => {
  const db = await freshDb()
  const valid = async (k: string) =>
    (await one(db, 'select public.valid_license_key_shape($1) as v', [k])).v
  assert.equal(await valid('STH-ABCD-EFGH-IJKL-MNOP'), true)
  assert.equal(await valid('sth abcd-efgh ijkl mnop'), true)
  assert.equal(await valid('STHABCDEFGHIJKLMNOP'), true)
  assert.equal(await valid('STH-ABCD-EFGH-IJKL-MNO9'), false)
  assert.equal(await valid('STH-ABCD-EFGH-IJKL'), false)
  assert.equal(await valid('XYZ-ABCD-EFGH-IJKL-MNOP'), false)
  assert.equal(await valid(''), false)
  assert.equal(await valid(null), false)
})

test('licenses: key_hash must be 64 lowercase hex, profile and max_devices are checked', async () => {
  const db = await freshDb()
  await assert.rejects(
    insertRow(db, 'licenses', { key_hash: 'not-a-hash', profile: 'general', max_devices: 2 }),
    /licenses_key_hash_valid/,
  )
  await assert.rejects(
    insertRow(db, 'licenses', { key_hash: 'a'.repeat(64), profile: 'admin', max_devices: 2 }),
    /licenses_profile_valid/,
  )
  await assert.rejects(
    insertRow(db, 'licenses', { key_hash: 'a'.repeat(64), profile: 'general', max_devices: 11 }),
    /licenses_max_devices_valid/,
  )
})

test('profile seeds: exactly the four allowed profiles', async () => {
  const db = await freshDb()
  const seeded = await rows(db, 'select profile from public.license_profiles order by profile')
  assert.deepEqual(
    seeded.map((r) => r.profile),
    ['finance', 'general', 'lawyer', 'student'],
  )
})

// ---------------------------------------------------------------- jobs constraints

test('jobs: valid row inserts; bad status, kind, model, progress, result and error are rejected', async () => {
  const db = await freshDb()
  const ok = await insertJob(db)
  assert.equal(ok.status, 'queued')

  await assert.rejects(insertJob(db, { status: 'pending', hf_model: 'Org/A-Model' }), /jobs_status_valid/)
  await assert.rejects(insertJob(db, { kind: 'extracting', hf_model: 'Org/B-Model' }), /jobs_kind_valid/)
  await assert.rejects(insertJob(db, { hf_model: 'datasets/x/y' }), /jobs_hf_model_valid/)
  await assert.rejects(insertJob(db, { hf_model: 'Org/C-Model', progress: 101 }), /jobs_progress_valid/)
  await assert.rejects(insertJob(db, { hf_model: 'Org/D-Model', result: [1, 2] }), /jobs_result_object/)
  await assert.rejects(
    insertJob(db, { hf_model: 'Org/E-Model', error: 'x'.repeat(2001) }),
    /jobs_error_length/,
  )
})

test('jobs: at most one active (queued or running) job per model', async () => {
  const db = await freshDb()
  await insertJob(db, { hf_model: 'Org/Same-Model' })
  await assert.rejects(
    insertJob(db, { hf_model: 'org/same-model', status: 'running' }),
    /jobs_one_active_model/,
  )
  // A finished job for the same model is fine.
  await insertJob(db, { hf_model: 'Org/Same-Model', status: 'done', finished_at: ago(10) })
})

// ---------------------------------------------------------------- worker functions

test('claim_next_job: claims the oldest queued job, sets running, returns null when empty', async () => {
  const db = await freshDb()
  const older = await insertJob(db, { hf_model: 'Org/Old-Model', created_at: ago(7200) })
  await insertJob(db, { hf_model: 'Org/New-Model', created_at: ago(60) })

  const first = (await one(db, 'select public.claim_next_job() as r')).r
  assert.equal(first.id, older.id)
  assert.equal(first.status, 'running')

  const second = (await one(db, 'select public.claim_next_job() as r')).r
  assert.notEqual(second.id, older.id)

  const third = (await one(db, 'select public.claim_next_job() as r')).r
  assert.equal(third, null)
})

test('fail_stale_jobs: fails only running jobs with an old heartbeat; rejects thresholds under 60s', async () => {
  const db = await freshDb()
  const stale = await insertJob(db, {
    hf_model: 'Org/Stale-Model', status: 'running', started_at: ago(7200), heartbeat_at: ago(7200),
  })
  const fresh = await insertJob(db, {
    hf_model: 'Org/Fresh-Model', status: 'running', started_at: ago(10), heartbeat_at: ago(10),
  })

  const changed = (await one(db, 'select public.fail_stale_jobs(900) as n')).n
  assert.equal(changed, 1)

  const staleRow = await one(db, 'select status, error from public.jobs where id = $1', [stale.id])
  assert.equal(staleRow.status, 'failed')
  assert.match(staleRow.error, /heartbeat/)

  const freshRow = await one(db, 'select status from public.jobs where id = $1', [fresh.id])
  assert.equal(freshRow.status, 'running')

  await assert.rejects(db.query('select public.fail_stale_jobs(10)'), /at least 60/)
})

test('purge_old_jobs: removes only old finished jobs, keeps queued and running ones', async () => {
  const db = await freshDb()
  const oldDone = await insertJob(db, {
    hf_model: 'Org/Old-Done', status: 'done', created_at: ago(200 * 86400), finished_at: ago(200 * 86400),
  })
  const recentDone = await insertJob(db, {
    hf_model: 'Org/Recent-Done', status: 'done', created_at: ago(10), finished_at: ago(10),
  })
  const oldQueued = await insertJob(db, { hf_model: 'Org/Old-Queued', created_at: ago(200 * 86400) })

  const deleted = (await one(db, 'select public.purge_old_jobs(90) as n')).n
  assert.equal(deleted, 1)

  const ids = (await rows(db, 'select id from public.jobs')).map((r) => r.id)
  assert.ok(!ids.includes(oldDone.id))
  assert.ok(ids.includes(recentDone.id))
  assert.ok(ids.includes(oldQueued.id))

  await assert.rejects(db.query('select public.purge_old_jobs(0)'), /at least 1/)
})

// ---------------------------------------------------------------- public read

test('get_job: public shape, no requester_hash, queue position counts queued jobs in order', async () => {
  const db = await freshDb()
  const first = await insertJob(db, { hf_model: 'Org/First-Model', created_at: ago(120) })
  const second = await insertJob(db, { hf_model: 'Org/Second-Model', created_at: ago(60) })

  const a = (await one(db, 'select public.get_job($1::uuid) as r', [first.id])).r
  const b = (await one(db, 'select public.get_job($1::uuid) as r', [second.id])).r
  assert.equal(a.queue_position, 1)
  assert.equal(b.queue_position, 2)
  assert.ok(!('requester_hash' in a))
  assert.ok(!('requester_hash' in b))

  const missing = (await one(db, 'select public.get_job($1::uuid) as r', ['00000000-0000-4000-8000-000000000000'])).r
  assert.equal(missing, null)
})

// ---------------------------------------------------------------- desk activation

test('verify_client_activation: activates a device, keeps the same device from counting twice, enforces max_devices', async () => {
  const db = await freshDb()
  const key = licenseFixture.key
  await insertLicense(db, key, { profile: 'lawyer', max_devices: 2 })

  const first = await verify(db, key, 'device-fingerprint-A', 'windows')
  assert.equal(first.success, true)
  assert.equal(first.profile, 'lawyer')
  assert.equal(first.label, 'Sthamly Lawyer Desk')

  const again = await verify(db, key, 'device-fingerprint-A', 'windows')
  assert.equal(again.success, true)

  const second = await verify(db, key, 'device-fingerprint-B', 'linux')
  assert.equal(second.success, true)

  const third = await verify(db, key, 'device-fingerprint-C', 'mac')
  assert.equal(third.success, false)
  assert.match(third.message, /limit/)  // desk_license.py maps "limit" to the device-limit message
})

test('verify_client_activation: wrong key, short fingerprint and revoked key are refused with the agreed messages', async () => {
  const db = await freshDb()
  const key = licenseFixture.key
  await insertLicense(db, key)

  assert.deepEqual(await verify(db, 'STH-ZZZZ-ZZZZ-ZZZZ-ZZZZ', 'device-fingerprint-A'), {
    success: false, message: 'invalid_key',
  })
  assert.equal((await verify(db, 'not a key', 'device-fingerprint-A')).message, 'invalid_key')
  assert.equal((await verify(db, key, 'short')).message, 'invalid_device')

  await db.exec(`update public.licenses set revoked = true where key_hash = '${await licenseHash(db, key)}'`)
  const revoked = await verify(db, key, 'device-fingerprint-A')
  assert.equal(revoked.success, false)
  assert.match(revoked.message, /deactivated/)  // desk_license.py maps "deactivated" to the revoked message
})

// ---------------------------------------------------------------- roles, RLS, grants

test('anon role: RLS hides every job row, worker functions are denied, get_job and activation work', async () => {
  const db = await freshDb()
  await db.exec('create role anon nologin')
  await db.exec('grant usage on schema public to anon')
  await db.exec('grant select on public.jobs to anon')  // RLS must still hide the rows

  const job = await insertJob(db, { hf_model: 'Org/Hidden-Model' })

  const visible = await asRole(db, 'anon', () => rows(db, 'select id from public.jobs'))
  assert.equal(visible.length, 0, 'RLS with no policies must hide every row from anon')

  await assert.rejects(
    asRole(db, 'anon', () => db.query('select public.claim_next_job()')),
    /permission denied/,
  )
  await assert.rejects(
    asRole(db, 'anon', () => db.query('select public.fail_stale_jobs(900)')),
    /permission denied/,
  )

  const viaRpc = await asRole(db, 'anon', () =>
    one(db, 'select public.get_job($1::uuid) as r', [job.id]),
  )
  assert.equal(viaRpc.r.id, job.id)
  assert.ok(!('requester_hash' in viaRpc.r))

  await insertLicense(db, licenseFixture.key)
  const activation = await asRole(db, 'anon', () => verify(db, licenseFixture.key, 'device-fingerprint-A'))
  assert.equal(activation.success, true)
})