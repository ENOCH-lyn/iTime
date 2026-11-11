package com.itime.app

import android.content.Context
import com.itime.app.data.SyncStatusHolder
import com.itime.app.data.local.ItimeDatabase
import com.itime.app.data.remote.ItimeApi
import com.itime.app.data.repository.AuthRepository
import com.itime.app.data.repository.CalendarRepository
import com.itime.app.data.repository.TaskRepository
import com.itime.app.data.session.SessionStore
import com.itime.app.data.sync.SyncEngine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import java.util.concurrent.TimeUnit

/**
 * Hand-rolled dependency container.
 *
 * The graph is small enough that a DI framework would add build time and
 * indirection without benefit; every collaborator is constructed once per
 * process and shared.
 */
class AppContainer(context: Context) {
    val json: Json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        encodeDefaults = true
        isLenient = true
    }

    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val sessionStore = SessionStore(context)

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .addInterceptor(
            HttpLoggingInterceptor().apply {
                level = if (BuildConfig.DEBUG) {
                    HttpLoggingInterceptor.Level.BASIC
                } else {
                    HttpLoggingInterceptor.Level.NONE
                }
            },
        )
        .build()

    val api = ItimeApi(client, json, sessionStore)

    val database: ItimeDatabase = ItimeDatabase.build(context)

    val syncStatus = SyncStatusHolder()

    val sync = SyncEngine(database, api, json, sessionStore, syncStatus, scope)

    val tasks = TaskRepository(database, json, sync)

    val calendar = CalendarRepository(database, json, sync)

    val auth = AuthRepository(api, sessionStore, database, sync, scope)
}
