package com.itime.app.data.remote

import kotlinx.serialization.json.JsonElement

class ApiException(
    val code: String,
    override val message: String,
    val status: Int,
    val retryable: Boolean = false,
    val details: JsonElement? = null,
    val requestId: String? = null,
) : Exception(message) {
    val isConflict: Boolean get() = status == 409
    val isUnauthenticated: Boolean get() = status == 401
    val isOffline: Boolean get() = status == 0
}
