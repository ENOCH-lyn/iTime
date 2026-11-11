package com.itime.app.domain

import org.junit.Assert.assertEquals
import org.junit.Test

class SyncBackoffTest {

    @Test
    fun `first failure waits five seconds`() {
        assertEquals(5_000L, SyncBackoff.delayMillis(1))
    }

    @Test
    fun `delay doubles per attempt`() {
        assertEquals(10_000L, SyncBackoff.delayMillis(2))
        assertEquals(20_000L, SyncBackoff.delayMillis(3))
        assertEquals(40_000L, SyncBackoff.delayMillis(4))
    }

    @Test
    fun `delay is capped at five minutes`() {
        assertEquals(160_000L, SyncBackoff.delayMillis(6))
        assertEquals(300_000L, SyncBackoff.delayMillis(7))
        assertEquals(300_000L, SyncBackoff.delayMillis(30))
    }

    @Test
    fun `non positive attempts do not wait`() {
        assertEquals(0L, SyncBackoff.delayMillis(0))
        assertEquals(0L, SyncBackoff.delayMillis(-3))
    }
}
