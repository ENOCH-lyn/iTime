const DATABASE_NAME = 'itime-sync-cache'
const STORE_NAME = 'responses'
const DATABASE_VERSION = 1
const MAX_ENTRY_BYTES = 2 * 1024 * 1024
const MAX_ACCOUNT_BYTES = 20 * 1024 * 1024
const MAX_ACCOUNT_ENTRIES = 200
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

interface StoredResponse {
  id: string
  account_id: string
  key: string
  iv: ArrayBuffer
  ciphertext: ArrayBuffer
  byte_size: number
  cached_at: string
  accessed_at: string
}

export interface CachedResponse<T = unknown> {
  value: T
  cached_at: string
}

let activeAccountId: string | null = null
let activeKey: Promise<CryptoKey> | null = null
let accountRevision = 0

function requestResult<T>(request: IDBRequest<T>) {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function transactionDone(transaction: IDBTransaction) {
  return new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve()
    transaction.onerror = () => reject(transaction.error)
    transaction.onabort = () => reject(transaction.error)
  })
}

function openDatabase() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION)
    request.onupgradeneeded = () => {
      const database = request.result
      if (database.objectStoreNames.contains(STORE_NAME)) return
      const store = database.createObjectStore(STORE_NAME, { keyPath: 'id' })
      store.createIndex('account_id', 'account_id')
      store.createIndex('accessed_at', 'accessed_at')
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function deleteEntries(ids: string[]) {
  if (!ids.length) return
  const database = await openDatabase()
  try {
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    const store = transaction.objectStore(STORE_NAME)
    for (const id of ids) store.delete(id)
    await transactionDone(transaction)
  } finally {
    database.close()
  }
}

async function accountEntries(accountId: string) {
  const database = await openDatabase()
  try {
    const transaction = database.transaction(STORE_NAME, 'readonly')
    const index = transaction.objectStore(STORE_NAME).index('account_id')
    const rows = await requestResult(
      index.getAll(IDBKeyRange.only(accountId)),
    ) as StoredResponse[]
    await transactionDone(transaction)
    return rows
  } finally {
    database.close()
  }
}

export async function clearAccountCache(accountId: string) {
  try {
    const rows = await accountEntries(accountId)
    await deleteEntries(rows.map(row => row.id))
  } catch {
    // Browser storage is optional; inability to clear must not break logout.
  }
}

export async function setCacheAccount(accountId: string | null) {
  if (accountId === activeAccountId && activeKey !== null) return
  const previous = activeAccountId
  const revision = ++accountRevision
  activeAccountId = accountId
  activeKey = null
  if (accountId === null) {
    if (previous !== null) await clearAccountCache(previous)
    return
  }
  const setup = (async () => {
    if (previous !== null) await clearAccountCache(previous)
    if (previous !== accountId) await clearAccountCache(accountId)
    return crypto.subtle.generateKey(
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    )
  })()
  activeKey = setup
  await setup
  if (revision !== accountRevision || activeAccountId !== accountId) return
}

export function cacheableGet(path: string) {
  if (!path.startsWith('/')) return false
  return ![
    '/auth',
    '/admin',
    '/sync',
    '/voice-transcriptions',
  ].some(prefix => path === prefix || path.startsWith(`${prefix}/`))
    && !path.endsWith('/download')
    && !path.includes('/events')
}

export async function pruneAccountCache(accountId: string) {
  try {
    const rows = await accountEntries(accountId)
    const cutoff = Date.now() - MAX_AGE_MS
    const sorted = rows.sort(
      (left, right) =>
        Date.parse(right.accessed_at) - Date.parse(left.accessed_at),
    )
    let bytes = 0
    let count = 0
    const removals: string[] = []
    for (const row of sorted) {
      const expired = Date.parse(row.cached_at) < cutoff
      if (
        expired
        || count >= MAX_ACCOUNT_ENTRIES
        || bytes + row.byte_size > MAX_ACCOUNT_BYTES
      ) {
        removals.push(row.id)
        continue
      }
      count += 1
      bytes += row.byte_size
    }
    await deleteEntries(removals)
  } catch {
    // Cache cleanup is best effort and never blocks online data.
  }
}

export async function putCachedResponse(path: string, value: unknown) {
  const accountId = activeAccountId
  const keyPromise = activeKey
  if (accountId === null || keyPromise === null || !cacheableGet(path)) return
  try {
    const encoded = new TextEncoder().encode(JSON.stringify(value))
    if (encoded.byteLength > MAX_ENTRY_BYTES) return
    const key = await keyPromise
    if (activeAccountId !== accountId || activeKey !== keyPromise) return
    const iv = crypto.getRandomValues(new Uint8Array(12))
    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv },
      key,
      encoded,
    )
    const timestamp = new Date().toISOString()
    const row: StoredResponse = {
      id: `${accountId}|${path}`,
      account_id: accountId,
      key: path,
      iv: iv.buffer,
      ciphertext,
      byte_size: ciphertext.byteLength,
      cached_at: timestamp,
      accessed_at: timestamp,
    }
    const database = await openDatabase()
    try {
      const transaction = database.transaction(STORE_NAME, 'readwrite')
      transaction.objectStore(STORE_NAME).put(row)
      await transactionDone(transaction)
    } finally {
      database.close()
    }
    void pruneAccountCache(accountId)
  } catch {
    // Cache writes are optional and must not fail successful network requests.
  }
}

export async function getCachedResponse<T>(
  path: string,
): Promise<CachedResponse<T> | null> {
  const accountId = activeAccountId
  const keyPromise = activeKey
  if (accountId === null || keyPromise === null || !cacheableGet(path)) return null
  const id = `${accountId}|${path}`
  try {
    const database = await openDatabase()
    let row: StoredResponse | undefined
    try {
      const transaction = database.transaction(STORE_NAME, 'readonly')
      row = await requestResult(
        transaction.objectStore(STORE_NAME).get(id),
      ) as StoredResponse | undefined
      await transactionDone(transaction)
    } finally {
      database.close()
    }
    if (
      row === undefined
      || row.account_id !== accountId
      || row.key !== path
      || Date.now() - Date.parse(row.cached_at) > MAX_AGE_MS
    ) {
      if (row !== undefined) await deleteEntries([id])
      return null
    }
    const key = await keyPromise
    if (activeAccountId !== accountId || activeKey !== keyPromise) return null
    const plaintext = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: new Uint8Array(row.iv) },
      key,
      row.ciphertext,
    )
    const value = JSON.parse(new TextDecoder().decode(plaintext)) as T
    row.accessed_at = new Date().toISOString()
    const writeDatabase = await openDatabase()
    try {
      const transaction = writeDatabase.transaction(STORE_NAME, 'readwrite')
      transaction.objectStore(STORE_NAME).put(row)
      await transactionDone(transaction)
    } finally {
      writeDatabase.close()
    }
    return { value, cached_at: row.cached_at }
  } catch {
    await deleteEntries([id]).catch(() => undefined)
    return null
  }
}
