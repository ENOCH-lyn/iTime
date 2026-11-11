package com.itime.app

import android.app.Application
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.itime.app.data.sync.SyncWorker
import java.util.concurrent.TimeUnit

class ItimeApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        instance = this
        schedulePeriodicSync()
    }

    private fun schedulePeriodicSync() {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            SyncWorker.NAME,
            ExistingPeriodicWorkPolicy.KEEP,
            request,
        )
    }

    companion object {
        lateinit var instance: ItimeApplication
            private set
    }
}
