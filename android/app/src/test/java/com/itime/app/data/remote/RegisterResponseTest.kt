package com.itime.app.data.remote

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Server contract for POST /api/v1/auth/register (nested session shape). */
class RegisterResponseTest {

    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        isLenient = true
    }

    @Test
    fun `decodes nested session for immediate login`() {
        val body = """
            {"status":"registered","message":"注册成功","verification_required":false,
             "session":{"user":{"id":"u1","email":"a@example.com","email_verified":true,
               "ai_access":"allowed","is_admin":false,"created_at":"2026-11-16T08:59:47Z"},
               "session_id":"s1","expires_at":"2026-12-16T08:59:47Z","csrf_token":"c1"}}
        """.trimIndent()
        val decoded = json.decodeFromString(RegisterResponseDto.serializer(), body)
        assertEquals("registered", decoded.status)
        assertTrue(!decoded.verification_required)
        assertEquals("a@example.com", decoded.session?.user?.email)
        assertEquals("c1", decoded.session?.csrf_token)
    }

    @Test
    fun `verification required may omit session`() {
        val body = """
            {"status":"pending_verification","message":"请验证邮箱","verification_required":true}
        """.trimIndent()
        val decoded = json.decodeFromString(RegisterResponseDto.serializer(), body)
        assertTrue(decoded.verification_required)
        assertNull(decoded.session)
    }
}
