package com.itime.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.itime.app.AppContainer
import com.itime.app.ItimeApplication
import com.itime.app.ui.components.FullScreenLoading
import com.itime.app.ui.nav.TopDestination
import com.itime.app.ui.screens.ai.AiScreen
import com.itime.app.ui.screens.auth.LoginScreen
import com.itime.app.ui.screens.calendar.CalendarScreen
import com.itime.app.ui.screens.settings.SettingsScreen
import com.itime.app.ui.screens.tasks.TasksScreen
import com.itime.app.ui.screens.today.TodayScreen
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

@Composable
fun ItimeApp() {
    val container = (LocalContext.current.applicationContext as ItimeApplication).container
    val session by container.auth.session.collectAsStateWithLifecycle()
    val restoring by container.auth.restoring.collectAsStateWithLifecycle()

    if (restoring) {
        FullScreenLoading("正在恢复登录状态…")
        return
    }
    if (session == null) {
        LoginScreen(container.auth, container.sessionStore)
        return
    }
    MainShell(container)
}

@Composable
private fun MainShell(container: AppContainer) {
    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    val syncState by container.syncStatus.state.collectAsStateWithLifecycle()

    LaunchedEffect(Unit) {
        container.sync.requestSync("app-start")
        while (isActive) {
            delay(30_000)
            container.sync.requestSync("foreground-poll")
        }
    }

    val wideScreen = LocalConfiguration.current.screenWidthDp >= 600

    fun navigateTo(destination: TopDestination) {
        navController.navigate(destination.route) {
            popUpTo(navController.graph.findStartDestination().id) { saveState = true }
            launchSingleTop = true
            restoreState = true
        }
    }

    if (wideScreen) {
        Row(Modifier.fillMaxSize()) {
            NavigationRail {
                Spacer(Modifier.height(12.dp))
                TopDestination.entries.forEach { destination ->
                    NavigationRailItem(
                        selected = currentRoute == destination.route,
                        onClick = { navigateTo(destination) },
                        icon = { Icon(destination.icon, contentDescription = null) },
                        label = { Text(destination.label) },
                    )
                }
            }
            AppNavHost(
                navController = navController,
                container = container,
                syncState = syncState,
                modifier = Modifier.weight(1f),
            )
        }
    } else {
        Scaffold(
            bottomBar = {
                if (TopDestination.entries.any { it.route == currentRoute }) {
                    NavigationBar {
                        TopDestination.entries.forEach { destination ->
                            NavigationBarItem(
                                selected = currentRoute == destination.route,
                                onClick = { navigateTo(destination) },
                                icon = { Icon(destination.icon, contentDescription = null) },
                                label = { Text(destination.label) },
                            )
                        }
                    }
                }
            },
        ) { padding ->
            AppNavHost(
                navController = navController,
                container = container,
                syncState = syncState,
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
            )
        }
    }
}

@Composable
private fun AppNavHost(
    navController: androidx.navigation.NavHostController,
    container: AppContainer,
    syncState: com.itime.app.data.SyncState,
    modifier: Modifier = Modifier,
) {
    Box(modifier) {
        NavHost(
            navController = navController,
            startDestination = TopDestination.Today.route,
        ) {
            composable(TopDestination.Today.route) {
                TodayScreen(
                    container = container,
                    syncState = syncState,
                    onOpenCalendar = { navController.navigate(TopDestination.Calendar.route) },
                    onOpenSettings = { navController.navigate(TopDestination.Settings.route) },
                )
            }
            composable(TopDestination.Calendar.route) {
                CalendarScreen(container, syncState)
            }
            composable(TopDestination.Tasks.route) {
                TasksScreen(container, syncState)
            }
            composable(TopDestination.Ai.route) {
                AiScreen(container, syncState)
            }
            composable(TopDestination.Settings.route) {
                SettingsScreen(container, syncState)
            }
        }
    }
}
