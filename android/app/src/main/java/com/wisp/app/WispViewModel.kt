package com.wisp.app

import android.content.Context
import android.content.SharedPreferences
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore

val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "wisp_settings")

/**
 * Encrypted storage for the API key.
 * Uses AES256-GCM with a key stored in the Android Keystore.
 */
fun Context.encryptedApiKeyPrefs(): SharedPreferences {
    val masterKey = MasterKey.Builder(this)
        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
        .build()
    return EncryptedSharedPreferences.create(
        this,
        "wisp_api_key_secure",
        masterKey,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )
}

class WispViewModel(context: Context) : ViewModel() {

    private val dataStore = context.dataStore
    private val securePrefs = context.encryptedApiKeyPrefs()

    // Settings — API key stored in encrypted prefs, rest in DataStore
    private val SERVER_URL_KEY = stringPreferencesKey("server_url")
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
        // Load non-sensitive settings from DataStore
        viewModelScope.launch {
            dataStore.data.collect { prefs ->
                serverUrl.value = prefs[SERVER_URL_KEY] ?: ""
                selectedModel.value = prefs[MODEL_KEY] ?: ""
            }
        }
        // Load API key from EncryptedSharedPreferences
        apiKey.value = securePrefs.getString("api_key", "") ?: ""

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
        wsClient.connect(url, key, enableAutoReconnect = true)
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
        wsClient.sendInterrupt()
        isLoading.value = false
        currentThinking.value = ""
    }

    // Settings
    fun saveSettings(url: String, key: String, model: String) {
        // Store non-sensitive settings in unencrypted DataStore
        viewModelScope.launch {
            dataStore.edit { prefs ->
                prefs[SERVER_URL_KEY] = url
                prefs[MODEL_KEY] = model
            }
        }
        // Store API key in EncryptedSharedPreferences (AES256-GCM, keyed from Android Keystore)
        securePrefs.edit().putString("api_key", key).apply()
        apiKey.value = key
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
