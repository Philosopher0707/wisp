package com.wisp.app

import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import android.content.Context

val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "wisp_settings")

class WispViewModel(context: Context) : ViewModel() {

    private val dataStore = context.dataStore

    // Settings
    private val SERVER_URL_KEY = stringPreferencesKey("server_url")
    private val API_KEY_KEY = stringPreferencesKey("api_key")
    private val MODEL_KEY = stringPreferencesKey("model")

    val serverUrl = MutableStateFlow("")
    val apiKey = MutableStateFlow("")
    val selectedModel = MutableStateFlow("")

    // Connection
    private val wsClient = WispWebSocketClient()
    val connectionState = wsClient.connectionState

    // Chat
    data class ChatMessage(
        val id: String,
        val role: String,  // "user" or "assistant"
        val text: String,
        val isThinking: Boolean = false,
        val toolCalls: List<ToolCallRequest> = emptyList()
    )

    data class ToolCallRequest(
        val id: String,
        val name: String,
        val arguments: String,
        var approved: Boolean? = null  // null = pending
    )

    private val _messages = mutableStateListOf<ChatMessage>()
    val messages: List<ChatMessage> get() = _messages

    val isLoading = mutableStateOf(false)
    val currentThinking = mutableStateOf("")

    // Files
    private val _currentPath = MutableStateFlow("")
    val currentPath: StateFlow<String> = _currentPath.asStateFlow()

    private val _fileList = MutableStateFlow<List<FileItem>>(emptyList())
    val fileList: StateFlow<List<FileItem>> = _fileList.asStateFlow()

    private val _currentFileContent = MutableStateFlow("")
    val currentFileContent: StateFlow<String> = _currentFileContent.asStateFlow()

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage.asStateFlow()

    init {
        viewModelScope.launch {
            dataStore.data.collect { prefs ->
                serverUrl.value = prefs[SERVER_URL_KEY] ?: ""
                apiKey.value = prefs[API_KEY_KEY] ?: ""
                selectedModel.value = prefs[MODEL_KEY] ?: ""
            }
        }

        // Collect WebSocket messages
        viewModelScope.launch {
            wsClient.messages.collect { msg ->
                handleMessage(msg)
            }
        }
    }

    private fun handleMessage(msg: WispMessage) {
        when (msg) {
            is TokenMessage -> {
                if (msg.phase == "thinking") {
                    currentThinking.value += msg.text
                } else {
                    // Append to last assistant message or create new
                    val lastIndex = _messages.indexOfLast { it.role == "assistant" && !it.text.endsWith("[complete]") }
                    if (lastIndex >= 0 && _messages[lastIndex].toolCalls.isEmpty()) {
                        _messages[lastIndex] = _messages[lastIndex].copy(
                            text = _messages[lastIndex].text + msg.text
                        )
                    } else {
                        _messages.add(ChatMessage(
                            id = "msg_${System.currentTimeMillis()}",
                            role = "assistant",
                            text = msg.text
                        ))
                    }
                }
            }
            is ToolCallMessage -> {
                val lastIndex = _messages.indexOfLast { it.role == "assistant" }
                if (lastIndex >= 0) {
                    val existing = _messages[lastIndex].toolCalls.toMutableList()
                    existing.add(ToolCallRequest(
                        id = msg.id,
                        name = msg.name,
                        arguments = msg.arguments.toString()
                    ))
                    _messages[lastIndex] = _messages[lastIndex].copy(
                        toolCalls = existing
                    )
                }
            }
            is ToolResultMessage -> {
                // Update tool call status
                val lastIndex = _messages.indexOfLast { it.role == "assistant" }
                if (lastIndex >= 0) {
                    val updated = _messages[lastIndex].toolCalls.map {
                        if (it.id == msg.id) it.copy(approved = true) else it
                    }
                    _messages[lastIndex] = _messages[lastIndex].copy(toolCalls = updated)
                }
            }
            is CompleteMessage -> {
                isLoading.value = false
                currentThinking.value = ""
            }
            is ErrorMessage -> {
                isLoading.value = false
                _errorMessage.value = msg.message
            }
            is ToolBlockedMessage -> {
                _errorMessage.value = "Blocked ${msg.name}: ${msg.reason}"
            }
            else -> {}
        }
    }

    fun connect() {
        val url = serverUrl.value
        val key = apiKey.value
        if (url.isBlank() || key.isBlank()) {
            _errorMessage.value = "Please configure server URL and API key"
            return
        }
        wsClient.connect(url, key)
    }

    fun disconnect() {
        wsClient.disconnect()
    }

    fun sendPrompt(prompt: String) {
        _messages.add(ChatMessage(
            id = "msg_${System.currentTimeMillis()}",
            role = "user",
            text = prompt
        ))
        isLoading.value = true
        currentThinking.value = ""
        wsClient.sendPrompt(
            prompt = prompt,
            model = selectedModel.value.takeIf { it.isNotBlank() },
            showThinking = true
        )
    }

    fun approveTool(callId: String, approved: Boolean) {
        wsClient.approveTool(callId, approved)
    }

    fun interrupt() {
        wsClient.sendPrompt("")  // Empty to trigger interrupt handling
        // Actually, let's send an interrupt message
        // For now, just set loading false
        isLoading.value = false
    }

    // Settings
    fun saveSettings(url: String, key: String, model: String) {
        viewModelScope.launch {
            dataStore.edit { prefs ->
                prefs[SERVER_URL_KEY] = url
                prefs[API_KEY_KEY] = key
                prefs[MODEL_KEY] = model
            }
        }
    }

    // File operations
    fun loadFiles(path: String = "") {
        viewModelScope.launch {
            try {
                val rest = WispRestClient(serverUrl.value, apiKey.value)
                val result = rest.listFiles(path)
                _currentPath.value = path
                _fileList.value = result.items
                rest.close()
            } catch (e: Exception) {
                _errorMessage.value = "Failed to load files: ${e.message}"
            }
        }
    }

    fun readFile(path: String) {
        viewModelScope.launch {
            try {
                val rest = WispRestClient(serverUrl.value, apiKey.value)
                val result = rest.readFile(path)
                _currentFileContent.value = result.content
                rest.close()
            } catch (e: Exception) {
                _errorMessage.value = "Failed to read file: ${e.message}"
            }
        }
    }

    fun clearError() {
        _errorMessage.value = null
    }

    override fun onCleared() {
        super.onCleared()
        wsClient.dispose()
    }
}
