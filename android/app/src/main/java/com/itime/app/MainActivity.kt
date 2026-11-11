package com.itime.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.itime.app.ui.ItimeApp
import com.itime.app.ui.theme.ItimeTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        handleShareIntent(intent)
        setContent {
            ItimeTheme {
                ItimeApp()
            }
        }
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleShareIntent(intent)
    }

    /** Adds shared plain text as a new-task draft. */
    private fun handleShareIntent(intent: android.content.Intent?) {
        if (intent?.action != android.content.Intent.ACTION_SEND) return
        if (intent.type?.startsWith("text/") != true) return
        val text = intent.getStringExtra(android.content.Intent.EXTRA_TEXT)?.trim()
        if (text.isNullOrEmpty()) return
        (application as ItimeApplication).container.pendingShare.value = text
    }
}
