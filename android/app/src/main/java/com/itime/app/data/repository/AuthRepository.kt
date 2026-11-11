package com.itime.app.data.repository

import com.itime.app.data.local.ItimeDatabase
import com.itime.app.data.remote.ApiException
import com.itime.app.data.remote.ItimeApi
import com.itime.app.data.session.Session
import com.itime.app.data.session.SessionStore
import com.itime.app.data.sync.SyncEngine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class AuthRepository(
    private val api: ItimeApi,
    private val sessionStore: SessionStore,
    private val db: ItimeDatabase,
    private val sync: SyncEngine,
    scope: CoroutineScope,
) {
    private val _session = MutableStateFlow<Session?>(null)
    val session: StateFlow<Session?> = _session.asStateFlow()

    private val _restoring = MutableStateFlow(true)
    val restoring: StateFlow<Boolean> = _restoring.asStateFlow()

    init {
        scope.launch {
            val saved = runCatching { sessionStore.load() }.getOrNull()
            if (saved != null) {
                _session.value = saved
                // Refresh the CSRF token; a revoked session silently signs out.
                runCatching { api.me() }
                    .onSuccess { me ->
                        sessionStore.updateCsrf(me.csrf_token)
                        _session.value = sessionStore.current()
                        sync.requestSync("session-restored")
                    }
                    .onFailure { error ->
                        if (error is ApiException && error.isUnauthenticated) {
                            sessionStore.clear()
                            _session.value = null
                        }
                    }
            }
            _restoring.value = false
        }
    }

    suspend fun login(email: String, password: String, deviceName: String): Result<Unit> = try {
        val result = api.login(email.trim(), password, deviceName)
        val session = Session(
            token = result.token,
            csrfToken = result.session.csrf_token,
            userId = result.session.user.id,
            email = result.session.user.email,
            expiresAt = result.session.expires_at,
        )
        sessionStore.save(session)
        _session.value = session
        sync.requestSync("login")
        Result.success(Unit)
    } catch (error: ApiException) {
        Result.failure(error)
    }

    suspend fun register(email: String, password: String, deviceName: String): Result<Unit> = try {
        val result = api.register(email.trim(), password, deviceName)
        if (result == null) {
            Result.failure(
                ApiException(
                    code = "verification_required",
                    message = "注册成功，请先到邮箱完成验证，再回来登录。",
                    status = 202,
                ),
            )
        } else {
            val session = Session(
                token = result.token,
                csrfToken = result.session.csrf_token,
                userId = result.session.user.id,
                email = result.session.user.email,
                expiresAt = result.session.expires_at,
        )
            sessionStore.save(session)
            _session.value = session
            sync.requestSync("register")
            Result.success(Unit)
        }
    } catch (error: ApiException) {
        Result.failure(error)
    }

    suspend fun logout() {
        runCatching { api.logout() }
        sessionStore.clear()
        clearLocalData()
        _session.value = null
    }

    private suspend fun clearLocalData() {
        db.clearAllTables()
    }

    suspend fun updateBaseUrl(url: String) {
        sessionStore.setBaseUrl(url)
        sync.requestSync("server-url-changed")
    }
}

