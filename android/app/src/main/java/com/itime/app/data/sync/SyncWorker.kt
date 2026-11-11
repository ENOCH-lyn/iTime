package com.itime.app.data.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.itime.app.ItimeApplication

/** Background sync entry point for WorkManager (every 15 minutes while online). */
class SyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val container = (applicationContext as ItimeApplication).container
        return try {
            container.sync.syncNow("workmanager")
            Result.success()
        } catch (_: Exception) {
            Result.retry()
        }
    }

    companion object {
        const val NAME = "itime-periodic-sync"
    }
}
