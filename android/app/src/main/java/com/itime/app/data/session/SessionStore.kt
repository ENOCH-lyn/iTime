package com.itime.app.data.session

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

const val DEFAULT_BASE_URL = "http://10.0.2.2:8000/"

data class Session(
    val token: String,
    val csrfToken: String,
    val userId: String,
    val email: String,
    val expiresAt: String? = null,
) {
    val isUsable: Boolean get() = token.isNotBlank() && userId.isNotBlank()
}

private val Context.sessionDataStore: DataStore<Preferences> by preferencesDataStore("itime_session")

/**
 * Persists the device session plus the server base URL.
 *
 * The session token lives in app-private storage only and is never logged;
 * [Session] carries no debug rendering of the token.
 */
class SessionStore(private val context: Context) {
    private val store = context.sessionDataStore

    @Volatile
    private var cached: Session? = null

    @Volatile
    private var cachedBaseUrl: String = DEFAULT_BASE_URL

    private val _baseUrl = MutableStateFlow(DEFAULT_BASE_URL)
    val baseUrlFlow: StateFlow<String> = _baseUrl.asStateFlow()

    private object Keys {
        val token = stringPreferencesKey("token")
        val csrf = stringPreferencesKey("csrf")
        val userId = stringPreferencesKey("user_id")
        val email = stringPreferencesKey("email")
        val expiresAt = stringPreferencesKey("expires_at")
        val baseUrl = stringPreferencesKey("base_url")
    }

    val sessionFlow: Flow<Session?> = store.data.map { prefs -> prefs.toSession() }

    fun current(): Session? = cached

    fun baseUrl(): String = cachedBaseUrl

    suspend fun load(): Session? {
        val prefs = store.data.first()
        cached = prefs.toSession()
        cachedBaseUrl = normalize(prefs[Keys.baseUrl])
        _baseUrl.value = cachedBaseUrl
        return cached
    }

    suspend fun save(session: Session) {
        cached = session
        store.edit { prefs ->
            prefs[Keys.token] = session.token
            prefs[Keys.csrf] = session.csrfToken
            prefs[Keys.userId] = session.userId
            prefs[Keys.email] = session.email
            prefs[Keys.expiresAt] = session.expiresAt.orEmpty()
        }
    }

    suspend fun updateCsrf(csrf: String) {
        val current = cached ?: return
        save(current.copy(csrfToken = csrf))
    }

    suspend fun setBaseUrl(url: String) {
        cachedBaseUrl = normalize(url)
        _baseUrl.value = cachedBaseUrl
        store.edit { prefs -> prefs[Keys.baseUrl] = cachedBaseUrl }
    }

    suspend fun clear() {
        cached = null
        store.edit { prefs ->
            prefs.remove(Keys.token)
            prefs.remove(Keys.csrf)
            prefs.remove(Keys.userId)
            prefs.remove(Keys.email)
            prefs.remove(Keys.expiresAt)
        }
    }

    private fun normalize(value: String?): String {
        val raw = value?.takeIf { it.isNotBlank() } ?: return DEFAULT_BASE_URL
        return if (raw.endsWith("/")) raw else "$raw/"
    }

    private fun Preferences.toSession(): Session? {
        val token = this[Keys.token].orEmpty()
        val userId = this[Keys.userId].orEmpty()
        if (token.isBlank() || userId.isBlank()) return null
        return Session(
            token = token,
            csrfToken = this[Keys.csrf].orEmpty(),
            userId = userId,
            email = this[Keys.email].orEmpty(),
            expiresAt = this[Keys.expiresAt]?.takeIf { it.isNotBlank() },
        )
    }
}
