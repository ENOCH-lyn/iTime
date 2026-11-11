package com.itime.app.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BaseUrlTest {

    @Test
    fun `accepts http and https and adds trailing slash`() {
        assertEquals("http://10.0.2.2:8000/", BaseUrl.normalize("http://10.0.2.2:8000"))
        assertEquals("https://itime.example.com/", BaseUrl.normalize(" https://itime.example.com "))
        assertEquals("http://192.168.1.5:8000/", BaseUrl.normalize("http://192.168.1.5:8000/"))
    }

    @Test
    fun `keeps sub paths and normalises scheme case`() {
        assertEquals("http://example.com/api/", BaseUrl.normalize("HTTP://example.com/api"))
    }

    @Test
    fun `rejects empty or malformed values`() {
        assertNull(BaseUrl.normalize(""))
        assertNull(BaseUrl.normalize("10.0.2.2:8000"))
        assertNull(BaseUrl.normalize("ftp://example.com"))
        assertNull(BaseUrl.normalize("http://"))
        assertNull(BaseUrl.normalize("not a url"))
    }
}
