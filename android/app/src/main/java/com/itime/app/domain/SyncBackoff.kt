package com.itime.app.domain

/**
 * Exponential backoff for automatic sync retries. Keeps a flaky server or a
 * dead emulator network from being hammered every 30 seconds.
 */
object SyncBackoff {

    private const val BASE_MILLIS = 5_000L
    private const val MAX_MILLIS = 300_000L

    fun delayMillis(attempt: Int): Long {
        if (attempt <= 0) return 0L
        val exponent = (attempt - 1).coerceAtMost(6)
        val raw = BASE_MILLIS shl exponent
        return raw.coerceAtMost(MAX_MILLIS)
    }
}
