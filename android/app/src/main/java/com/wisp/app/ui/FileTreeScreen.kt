package com.wisp.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.wisp.app.WispViewModel
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FileTreeScreen(viewModel: WispViewModel) {
    val currentPath by viewModel.currentPath.collectAsState()
    val files by viewModel.fileList.collectAsState()
    val fileContent by viewModel.currentFileContent.collectAsState()
    var selectedFile by remember { mutableStateOf<String?>(null) }
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    // Load root on first launch
    LaunchedEffect(Unit) {
        viewModel.loadFiles("")
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Files") },
                navigationIcon = {
                    if (currentPath.isNotBlank()) {
                        IconButton(onClick = {
                            val parent = currentPath.substringBeforeLast("/", "")
                            viewModel.loadFiles(parent)
                        }) {
                            Icon(Icons.Default.ArrowBack, "Back")
                        }
                    }
                }
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { padding ->
        Row(modifier = Modifier.padding(padding)) {
            // File tree
            LazyColumn(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxHeight()
            ) {
                item {
                    Text(
                        text = if (currentPath.isBlank()) "/" else "/$currentPath",
                        style = MaterialTheme.typography.labelSmall,
                        modifier = Modifier.padding(16.dp)
                    )
                }

                items(files) { file ->
                    FileItemRow(
                        file = file,
                        onClick = {
                            if (file.type == "directory") {
                                viewModel.loadFiles(file.path)
                            } else {
                                selectedFile = file.path
                                viewModel.readFile(file.path)
                            }
                        }
                    )
                }
            }

            // File content viewer
            if (selectedFile != null) {
                VerticalDivider()
                Surface(
                    modifier = Modifier
                        .weight(1.5f)
                        .fillMaxHeight(),
                    tonalElevation = 1.dp
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text(
                            text = selectedFile ?: "",
                            style = MaterialTheme.typography.titleSmall,
                            modifier = Modifier.padding(bottom = 8.dp)
                        )
                        Text(
                            text = fileContent,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.fillMaxSize()
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun FileItemRow(
    file: com.wisp.app.FileItem,
    onClick: () -> Unit
) {
    val icon = when (file.type) {
        "directory" -> Icons.Default.Folder
        else -> Icons.Default.InsertDriveFile
    }
    val tint = when (file.type) {
        "directory" -> MaterialTheme.colorScheme.primary
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }

    ListItem(
        headlineContent = { Text(file.name) },
        leadingContent = {
            Icon(icon, contentDescription = null, tint = tint)
        },
        trailingContent = if (file.type == "file" && file.size != null) {
            { Text(formatSize(file.size), style = MaterialTheme.typography.labelSmall) }
        } else null,
        modifier = Modifier.clickable(onClick = onClick)
    )
}

fun formatSize(bytes: Long): String {
    return when {
        bytes < 1024 -> "$bytes B"
        bytes < 1024 * 1024 -> "${bytes / 1024} KB"
        bytes < 1024 * 1024 * 1024 -> "${bytes / (1024 * 1024)} MB"
        else -> "${bytes / (1024 * 1024 * 1024)} GB"
    }
}
