package com.wisp.app

import io.ktor.client.*
import io.ktor.client.engine.cio.*
import io.ktor.client.plugins.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.logging.*
import io.ktor.client.plugins.websocket.*
import io.ktor.client.request.*
import io.ktor.client.statement.*
import io.ktor.http.*
import io.ktor.serialization.kotlinx.json.*
import io.ktor.websocket.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlin.math.min

class WispWebSocketClient {

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }

    private var client: HttpClient? = null
    private var session: DefaultClientWebSocketSession? = null
    private val _connectionState = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    private val _messages = MutableSharedFlow<WispMessage>()
    val messages: SharedFlow<WispMessage> = _messages.asSharedFlow()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    // Connection params for auto-reconnect
    private var serverUrl: String = ""
    private var apiKey: String = ""
    private var autoReconnect = false
    private var reconnectJob: Job? = null
    private var reconnectAttempt = 0
    private val maxReconnectDelayMs = 30_000L

    sealed class ConnectionState {
        object Disconnected : ConnectionState()
        object Connecting : ConnectionState()
        data class Connected(val sessionId: String? = null) : ConnectionState()
        data class Error(val message: String, val willRetry: Boolean = false) : ConnectionState()
    }

    fun connect(serverUrl: String, apiKey: String, enableAutoReconnect: Boolean = true) {
        if (_connectionState.value is ConnectionState.Connecting) return
        if (_connectionState.value is ConnectionState.Connected) return

        this.serverUrl = serverUrl
        this.apiKey = apiKey
        this.autoReconnect = enableAutoReconnect
        this.reconnectAttempt = 0
        cancelReconnect()
        this._sessionIdForReconnect = null

        _connectionState.value = ConnectionState.Connecting
        scope.launch {
            doConnect()
        }
    }

    private var _sessionIdForReconnect: String? = null

    // Idle timeout: disconnect after 15 minutes of no user activity
    private var idleTimeoutJob: Job? = null
    private val IDLE_TIMEOUT_MS = 15 * 60 * 1000L

    fun resetIdleTimer() {
        idleTimeoutJob?.cancel()
        idleTimeoutJob = scope.launch {
            delay(IDLE_TIMEOUT_MS)
            disconnect()
            _connectionState.value = ConnectionState.Error("Disconnected after 15 min idle", willRetry = false)
        }
    }

    private suspend fun doConnect() {
        try {
            val wsUrl = serverUrl.replace("https://", "wss://").replace("http://", "ws://")
            // API key is NOT sent as a query parameter — it leaks in logs.
            // Auth happens via a type:"auth" frame immediately after connection.
            val fullUrl = "$wsUrl/ws/agent"

            client = HttpClient(CIO) {
                install(WebSockets)
                install(Logging) {
                    level = LogLevel.NONE
                }
                install(HttpTimeout) {
                    requestTimeoutMillis = 30000
                    connectTimeoutMillis = 10000
                }
                // Explicit TLS 1.2+ requirement (CIO default trusts system CA store)
                engine {
                    https {
                        tlsVersion = io.ktor.network.tls.TLSVersion.TLS12
                    }
                }
            }

            client!!.webSocket(fullUrl) {
                session = this
                reconnectAttempt = 0

                // Authenticate via first-message auth frame — not query param
                send(Frame.Text(json.encodeToString(AuthMessage(api_key = apiKey))))

                _connectionState.value = ConnectionState.Connected()
                resetIdleTimer()

                // Send ping every 30s to keep alive
                val pingJob = launch {
                    while (isActive) {
                        delay(30000)
                        try {
                            send(Frame.Text(json.encodeToString(PingMessage())))
                        } catch (_: Exception) {
                            break
                        }
                    }
                }

                try {
                    for (frame in incoming) {
                        when (frame) {
                            is Frame.Text -> {
                                val text = frame.readText()
                                parseAndEmit(text)
                            }
                            is Frame.Close -> {
                                _connectionState.value = ConnectionState.Disconnected
                                break
                            }
                            else -> {}
                        }
                    }
                } finally {
                    pingJob.cancel()
                }
            }
        } catch (e: Exception) {
            val msg = e.message ?: "Connection failed"
            if (autoReconnect && reconnectAttempt < 10) {
                _connectionState.value = ConnectionState.Error(msg, willRetry = true)
                scheduleReconnect()
            } else {
                _connectionState.value = ConnectionState.Error(msg, willRetry = false)
            }
        } finally {
            session = null
            if (_connectionState.value is ConnectionState.Connected) {
                _connectionState.value = ConnectionState.Disconnected
            }
            if (autoReconnect && reconnectAttempt < 10 && _connectionState.value !is ConnectionState.Error) {
                scheduleReconnect()
            }
        }
    }

    private fun scheduleReconnect() {
        if (reconnectJob?.isActive == true) return
        reconnectAttempt++
        val delayMs = min(1000L * (1 shl (reconnectAttempt - 1)), maxReconnectDelayMs)
        reconnectJob = scope.launch {
            delay(delayMs)
            if (autoReconnect && _connectionState.value !is ConnectionState.Connected && _connectionState.value !is ConnectionState.Connecting) {
                _connectionState.value = ConnectionState.Connecting
                doConnect()
            }
        }
    }

    private fun cancelReconnect() {
        reconnectJob?.cancel()
        reconnectJob = null
    }

    private suspend fun parseAndEmit(text: String) {
        try {
            val obj = json.parseToJsonElement(text).jsonObject
            val type = obj["type"]?.toString()?.trim('"') ?: return

            val message = when (type) {
                "token" -> json.decodeFromString(TokenMessage.serializer(), text)
                "tool_call" -> json.decodeFromString(ToolCallMessage.serializer(), text)
                "tool_result" -> json.decodeFromString(ToolResultMessage.serializer(), text)
                "complete" -> json.decodeFromString(CompleteMessage.serializer(), text)
                "error" -> json.decodeFromString(ErrorMessage.serializer(), text)
                "tool_blocked" -> json.decodeFromString(ToolBlockedMessage.serializer(), text)
                "tool_executing" -> json.decodeFromString(ToolExecutingMessage.serializer(), text)
                "checkpoint" -> json.decodeFromString(CheckpointMessage.serializer(), text)
                "status" -> json.decodeFromString(StatusMessage.serializer(), text)
                "pong" -> json.decodeFromString(PongMessage.serializer(), text)
                "interrupt" -> json.decodeFromString(InterruptMessage.serializer(), text)
                else -> return
            }
            _messages.emit(message)
        } catch (e: Exception) {
            // Ignore parse errors
        }
    }

    fun sendPrompt(prompt: String, model: String? = null, sessionId: String? = null, showThinking: Boolean = true) {
        resetIdleTimer()
        scope.launch {
            try {
                val msg = PromptMessage(
                    content = prompt,
                    model = model,
                    session_id = sessionId,
                    show_thinking = showThinking
                )
                session?.send(Frame.Text(json.encodeToString(msg)))
            } catch (e: Exception) {
                _connectionState.value = ConnectionState.Error("Send failed: ${e.message}", willRetry = autoReconnect)
            }
        }
    }

    fun approveTool(callId: String, approved: Boolean, reason: String? = null) {
        resetIdleTimer()
        scope.launch {
            try {
                val msg = ToolApprovalMessage(
                    id = callId,
                    approved = approved,
                    reason = reason
                )
                session?.send(Frame.Text(json.encodeToString(msg)))
            } catch (e: Exception) {
                _connectionState.value = ConnectionState.Error("Send failed: ${e.message}", willRetry = autoReconnect)
            }
        }
    }

    fun sendInterrupt() {
        scope.launch {
            try {
                val msg = InterruptMessage()
                session?.send(Frame.Text(json.encodeToString(msg)))
            } catch (e: Exception) {
                _connectionState.value = ConnectionState.Error("Send failed: ${e.message}", willRetry = autoReconnect)
            }
        }
    }

    fun disconnect() {
        autoReconnect = false
        cancelReconnect()
        scope.launch {
            try {
                session?.close(CloseReason(CloseReason.Codes.NORMAL, "Client disconnect"))
            } catch (_: Exception) {}
            session = null
            try {
                client?.close()
            } catch (_: Exception) {}
            client = null
            _connectionState.value = ConnectionState.Disconnected
        }
    }

    fun dispose() {
        disconnect()
        scope.cancel()
    }
}

// REST API client with retry logic
class WispRestClient(private val serverUrl: String, private val apiKey: String) {

    private val json = Json { ignoreUnknownKeys = true }
    private val client = HttpClient(CIO) {
        install(ContentNegotiation) {
            json(json)
        }
        install(Logging) {
            level = LogLevel.NONE
        }
        install(HttpTimeout) {
            requestTimeoutMillis = 30000
            connectTimeoutMillis = 10000
        }
        // Send API key via header (query param removed — leaks to logs)
        defaultRequest {
            header("X-API-Key", apiKey)
        }
    }

    private suspend inline fun <T> withRetry(
        retries: Int = 2,
        crossinline block: suspend () -> T
    ): T {
        var lastException: Exception? = null
        for (i in 0..retries) {
            try {
                return block()
            } catch (e: Exception) {
                lastException = e
                if (i < retries) delay(500L * (i + 1))
            }
        }
        throw lastException ?: Exception("Request failed")
    }

    suspend fun listFiles(path: String = ""): DirectoryListing = withRetry {
        val response = client.get("$serverUrl/api/files") {
            parameter("path", path)
        }
        json.decodeFromString(DirectoryListing.serializer(), response.bodyAsText())
    }

    suspend fun readFile(path: String): FileContent = withRetry {
        val response = client.get("$serverUrl/api/files") {
            parameter("path", path)
        }
        json.decodeFromString(FileContent.serializer(), response.bodyAsText())
    }

    suspend fun writeFile(path: String, content: String) = withRetry {
        client.post("$serverUrl/api/files") {
            parameter("path", path)
            contentType(ContentType.Application.Json)
            setBody(mapOf("content" to content))
        }
    }

    suspend fun runBash(command: String, cwd: String? = null): BashResult = withRetry {
        val response = client.post("$serverUrl/api/bash") {
            contentType(ContentType.Application.Json)
            setBody(mapOf("command" to command, "cwd" to cwd))
        }
        json.decodeFromString(BashResult.serializer(), response.bodyAsText())
    }

    suspend fun listModels(): List<ModelInfo> = withRetry {
        val response = client.get("$serverUrl/api/models")
        val body = json.decodeFromString(ModelsResponse.serializer(), response.bodyAsText())
        body.models
    }

    fun close() {
        client.close()
    }
}
