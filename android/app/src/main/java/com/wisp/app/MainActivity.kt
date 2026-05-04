package com.wisp.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.wisp.app.ui.ChatScreen
import com.wisp.app.ui.FileTreeScreen
import com.wisp.app.ui.SettingsScreen
import com.wisp.app.ui.theme.WispTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            WispTheme {
                WispApp()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WispApp() {
    val navController = rememberNavController()
    val viewModel: WispViewModel = viewModel(factory = WispViewModelFactory(LocalContext.current))

    val connectionState by viewModel.connectionState.collectAsState()
    val isConnected = connectionState is WispWebSocketClient.ConnectionState.Connected

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Wisp") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                    titleContentColor = MaterialTheme.colorScheme.primary
                ),
                actions = {
                    // Connection indicator
                    val indicatorColor = when (connectionState) {
                        is WispWebSocketClient.ConnectionState.Connected -> MaterialTheme.colorScheme.primary
                        is WispWebSocketClient.ConnectionState.Connecting -> MaterialTheme.colorScheme.tertiary
                        is WispWebSocketClient.ConnectionState.Error -> MaterialTheme.colorScheme.error
                        else -> MaterialTheme.colorScheme.outline
                    }
                    Badge(
                        containerColor = indicatorColor,
                        content = {}
                    )
                }
            )
        },
        bottomBar = {
            BottomNavigationBar(navController)
        }
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = "chat",
            modifier = Modifier.padding(innerPadding)
        ) {
            composable("chat") {
                ChatScreen(viewModel = viewModel)
            }
            composable("files") {
                FileTreeScreen(viewModel = viewModel)
            }
            composable("settings") {
                SettingsScreen(viewModel = viewModel)
            }
        }
    }
}

@Composable
fun BottomNavigationBar(navController: NavHostController) {
    val items = listOf(
        BottomNavItem("chat", "Chat", Icons.Default.Chat),
        BottomNavItem("files", "Files", Icons.Default.Folder),
        BottomNavItem("settings", "Settings", Icons.Default.Settings)
    )
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = navBackStackEntry?.destination?.route

    NavigationBar {
        items.forEach { item ->
            NavigationBarItem(
                icon = { Icon(item.icon, contentDescription = item.label) },
                label = { Text(item.label) },
                selected = currentRoute == item.route,
                onClick = {
                    navController.navigate(item.route) {
                        popUpTo(navController.graph.startDestinationId) {
                            saveState = true
                        }
                        launchSingleTop = true
                        restoreState = true
                    }
                }
            )
        }
    }
}

data class BottomNavItem(val route: String, val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector)
