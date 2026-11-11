package com.itime.app

import android.app.Application

class ItimeApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    companion object {
        lateinit var instance: ItimeApplication
            private set
    }
}
