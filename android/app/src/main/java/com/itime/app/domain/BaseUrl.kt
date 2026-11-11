package com.itime.app.domain

/**
 * Validates and normalises the server base URL typed into settings.
 * Accepts only absolute http(s) URLs; returns null when invalid so the UI can
 * keep the previous value and show an inline error.
 */
object BaseUrl {

    fun normalize(input: String): String? {
        val trimmed = input.trim()
        if (trimmed.isEmpty()) return null
        val url = try {
            java.net.URI(trimmed)
        } catch (_: Exception) {
            return null
        }
        val scheme = url.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        val host = url.host ?: return null
        if (host.isBlank() || host.contains(' ')) return null
        if (url.port !in -1..65535) return null
        val path = url.path.orEmpty()
        val base = buildString {
            append(scheme)
            append("://")
            append(host)
            if (url.port != -1) append(":").append(url.port)
            append(if (path.isBlank()) "/" else path)
        }
        return if (base.endsWith("/")) base else "$base/"
    }
}
