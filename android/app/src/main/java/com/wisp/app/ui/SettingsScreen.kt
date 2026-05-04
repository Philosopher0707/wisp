package com.wisp.app.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Link
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.wisp.app.WispViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(viewModel: WispViewModel) {
    val serverUrl by viewModel.serverUrl.collectAsState()
    val apiKey by viewModel.apiKey.collectAsState()
    val selectedModel by viewModel.selectedModel.collectAsState()
    val connectionState by viewModel.connectionState.collectAsState()

    var urlInput by remember { mutableStateOf(serverUrl) }
    var keyInput by remember { mutableStateOf(apiKey) }
    var modelInput by remember { mutableStateOf(selectedModel) }
    var showSaved by remember { mutableStateOf(false) }

    val isConnected = connectionState is com.wisp.app.WispWebSocketClient.ConnectionState.Connected

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Settings") })
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // Server URL
            OutlinedTextField(
                value = urlInput,
                onValueChange = { urlInput = it },
                label = { Text("Server URL") },
                placeholder = { Text("wss://your-server.com or ws://192.168.1.5:8000") },
                leadingIcon = { Icon(Icons.Default.Link, null) },
                modifier = Modifier.fillMaxWidth()
            )

            // API Key
            OutlinedTextField(
                value = keyInput,
                onValueChange = { keyInput = it },
                label = { Text("API Key") },
                leadingIcon = { Icon(Icons.Default.Key, null) },
                modifier = Modifier.fillMaxWidth()
            )

            // Model
            OutlinedTextField(
                value = modelInput,
                onValueChange = { modelInput = it },
                label = { Text("Default Model (optional)") },
                placeholder = { Text("e.g. deepseek-v4-flash:cloud") },
                leadingIcon = { Icon(Icons.Default.Cloud, null) },
                modifier = Modifier.fillMaxWidth()
            )

            // Connection status
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = when {
                        isConnected -> MaterialTheme.colorScheme.primaryContainer
                        connectionState is com.wisp.app.WispWebSocketClient.ConnectionState.Connecting -> MaterialTheme.colorScheme.tertiaryContainer
                        connectionState is com.wisp.app.WispWebSocketClient.ConnectionState.Error -> MaterialTheme.colorScheme.errorContainer
                        else -> MaterialTheme.colorScheme.surfaceVariant
                    }
                )
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = when (connectionState) {
                            is com.wisp.app.WispWebSocketClient.ConnectionState.Connected -> "Connected"
                            is com.wisp.app.WispWebSocketClient.ConnectionState.Connecting -> "Connecting..."
                            is com.wisp.app.WispWebSocketClient.ConnectionState.Error -> {
                            val err = connectionState as com.wisp.app.WispWebSocketClient.ConnectionState.Error
                            if (err.willRetry) "Reconnecting… (${err.message})" else "Error: ${err.message}"
                        }
                            else -> "Disconnected"
                        },
                        modifier = Modifier.weight(1f)
                    )
                    if (isConnected) {
                        Icon(Icons.Default.Check, "Connected", tint = MaterialTheme.colorScheme.primary)
                    }
                }
            }

            // Action buttons
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Button(
                    onClick = {
                        viewModel.saveSettings(urlInput, keyInput, modelInput)
                        showSaved = true
                    },
                    modifier = Modifier.weight(1f)
                ) {
                    Text("Save Settings")
                }

                if (isConnected) {
                    OutlinedButton(
                        onClick = { viewModel.disconnect() },
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("Disconnect")
                    }
                } else {
                    Button(
                        onClick = { viewModel.connect() },
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("Connect")
                    }
                }
            }

            if (showSaved) {
                Text(
                    text = "Settings saved!",
                    color = MaterialTheme.colorScheme.primary,
                    style = MaterialTheme.typography.labelMedium
                )
                LaunchedEffect(Unit) {
                    kotlinx.coroutines.delay(2000)
                    showSaved = false
                }
            }

            // Help text
            Text(
                text = "The server must be running Wisp Cloud with WebSocket support. " +
                        "Get your API key from the server logs or environment variable.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}
