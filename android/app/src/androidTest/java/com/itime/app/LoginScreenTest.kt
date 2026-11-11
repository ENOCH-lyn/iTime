package com.itime.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.itime.app.data.SyncStatusHolder
import com.itime.app.data.local.ItimeDatabase
import com.itime.app.data.remote.ItimeApi
import com.itime.app.data.repository.AuthRepository
import com.itime.app.data.session.SessionStore
import com.itime.app.data.sync.SyncEngine
import com.itime.app.ui.screens.auth.LoginScreen
import com.itime.app.ui.theme.ItimeTheme
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Renders the real Material 3 login screen with real collaborators backed by an
 * in-memory database; no network call happens until the user submits.
 */
@RunWith(AndroidJUnit4::class)
class LoginScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    private lateinit var db: ItimeDatabase

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext(),
            ItimeDatabase::class.java,
        ).allowMainThreadQueries().build()
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val json = Json {
            ignoreUnknownKeys = true
            explicitNulls = false
            encodeDefaults = true
        }
        val store = SessionStore(context)
        val api = ItimeApi(OkHttpClient(), json, store)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val sync = SyncEngine(db, api, json, store, SyncStatusHolder(), scope)
        val auth = AuthRepository(api, store, db, sync, scope)
        composeRule.setContent {
            ItimeTheme(dynamicColor = false) {
                LoginScreen(auth, store)
            }
        }
    }

    @After
    fun tearDown() {
        db.close()
    }

    @Test
    fun loginScreenRendersAndSwitchesMode() {
        composeRule.onNodeWithText("登录 iTime").assertIsDisplayed()
        composeRule.onNodeWithText("邮箱").assertIsDisplayed()
        composeRule.onNodeWithText("密码").assertIsDisplayed()
        composeRule.onNodeWithText("登录").assertIsDisplayed()

        composeRule.onNodeWithText("没有账号？创建一个").performClick()
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithText("创建 iTime 账号").fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText("创建 iTime 账号").assertIsDisplayed()
        composeRule.onNodeWithText("注册并登录").assertIsDisplayed()
    }
}
