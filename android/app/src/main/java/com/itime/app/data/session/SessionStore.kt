package com.itime.app.data.session

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

const val DEFAULT_BASE_URL = "http://10.0.2.2:8000/"

data class Session(
    val token: String,
    val csrfToken: String,
    val userId: String,
    val email: String,
    val expiresAt: String? = null,
    val baseUrl: String = DEFAULT_BASE_URL,
) {
    val isUsable: Boolean get() = token.isNotBlank() && userId.isNotBlank()
}

private val Context.sessionDataStore: DataStore<Preferences> by preferencesDataStore("itime_session")

/**
 * Persists the device session and the small amount of app-level configuration
 * that must survive process death.
 *
 * The session token lives in app-private storage only. It is never written to
 * logs: [toString] is overridden to redact it.
 */
class SessionStore(private val context: Context) {
    private val store = context.sessionDataStore

    @Volatile
    private var cached: Session? = null

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

    suspend fun load(): Session? {
        val session = store.data.first().toSession()
        cached = session
        return session
    }

    suspend fun save(session: Session) {
        cached = session
        store.edit { prefs ->
            prefs[Keys.token] = session.token
            prefs[Keys.csrf] = session.csrfToken
            prefs[Keys.userId] = session.userId
            prefs[Keys.email] = session.email
            prefs[Keys.expiresAt] = session.expiresAt.orEmpty()
            prefs[Keys.baseUrl] = session.baseUrl
        }
    }

    suspend fun updateCsrf(csrf: String) {
        val current = cached ?: return
        save(current.copy(csrfToken = csrf))
    }

    suspend fun clear() {
        cached = null
        store.edit { prefs -> prefs.clear() }
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
            baseUrl = this[Keys.baseUrl] ?: DEFAULT_BASE_URL,
        )
    }
}
