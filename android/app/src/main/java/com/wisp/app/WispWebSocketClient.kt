package com.wisp.app

import io.ktor.client.*
import io.ktor.client.engine.cio.*
import io.ktor.client.plugins.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.logging.*
import io.ktor.client.plugins.websocket.*
import io.ktor.client.request.*
import io.ktor.http.*
import io.ktor.serialization.kotlinx.json.*
import io.ktor.websocket.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

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

    sealed class ConnectionState {
        object Disconnected : ConnectionState()
        object Connecting : ConnectionState()
        data class Connected(val sessionId: String? = null) : ConnectionState()
        data class Error(val message: String) : ConnectionState()
    }

    fun connect(serverUrl: String, apiKey: String) {
        if (_connectionState.value is ConnectionState.Connecting) return

        _connectionState.value = ConnectionState.Connecting
        scope.launch {
            try {
                val wsUrl = serverUrl.replace("https://", "wss://").replace("http://", "ws://")
                val fullUrl = "$wsUrl/ws/agent?api_key=$apiKey"

                client = HttpClient(CIO) {
                    install(WebSockets)
                    install(Logging) {
                        level = LogLevel.NONE
                    }
                    install(HttpTimeout) {
                        requestTimeoutMillis = 30000
                        connectTimeoutMillis = 10000
                    }
                }

                client!!.webSocket(fullUrl) {
                    session = this
                    _connectionState.value = ConnectionState.Connected()

                    // Send ping every 30s to keep alive
                    val pingJob = launch {
                        while (isActive) {
                            delay(30000)
                            send(Frame.Text(json.encodeToString(PingMessage())))
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
                _connectionState.value = ConnectionState.Error(e.message ?: "Connection failed")
            } finally {
                if (_connectionState.value !is ConnectionState.Error) {
                    _connectionState.value = ConnectionState.Disconnected
                }
            }
        }
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
                else -> return
            }
            _messages.emit(message)
        } catch (e: Exception) {
            // Ignore parse errors
        }
    }

    fun sendPrompt(prompt: String, model: String? = null, sessionId: String? = null, showThinking: Boolean = true) {
        scope.launch {
            val msg = PromptMessage(
                content = prompt,
                model = model,
                session_id = sessionId,
                show_thinking = showThinking
            )
            session?.send(Frame.Text(json.encodeToString(msg)))
        }
    }

    fun approveTool(callId: String, approved: Boolean, reason: String? = null) {
        scope.launch {
            val msg = ToolApprovalMessage(
                id = callId,
                approved = approved,
                reason = reason
            )
            session?.send(Frame.Text(json.encodeToString(msg)))
        }
    }

    fun disconnect() {
        scope.launch {
            session?.close(CloseReason(CloseReason.Codes.NORMAL, "Client disconnect"))
            session = null
            client?.close()
            client = null
            _connectionState.value = ConnectionState.Disconnected
        }
    }

    fun dispose() {
        disconnect()
        scope.cancel()
    }
}

// REST API client
class WispRestClient(private val serverUrl: String, private val apiKey: String) {

    private val json = Json { ignoreUnknownKeys = true }
    private val client = HttpClient(CIO) {
        install(ContentNegotiation) {
            json(json)
        }
        install(Logging) {
            level = LogLevel.NONE
        }
        defaultRequest {
            header("X-API-Key", apiKey)
        }
    }

    suspend fun listFiles(path: String = ""): DirectoryListing {
        val response = client.get("$serverUrl/api/files") {
            parameter("path", path)
        }
        return json.decodeFromString(DirectoryListing.serializer(), response.bodyAsText())
    }

    suspend fun readFile(path: String): FileContent {
        val response = client.get("$serverUrl/api/files") {
            parameter("path", path)
        }
        return json.decodeFromString(FileContent.serializer(), response.bodyAsText())
    }

    suspend fun writeFile(path: String, content: String) {
        client.post("$serverUrl/api/files") {
            parameter("path", path)
            contentType(ContentType.Application.Json)
            setBody(mapOf("content" to content))
        }
    }

    suspend fun runBash(command: String, cwd: String? = null): BashResult {
        val response = client.post("$serverUrl/api/bash") {
            contentType(ContentType.Application.Json)
            setBody(mapOf("command" to command, "cwd" to cwd))
        }
        return json.decodeFromString(BashResult.serializer(), response.bodyAsText())
    }

    suspend fun listModels(): List<ModelInfo> {
        val response = client.get("$serverUrl/api/models")
        val body = json.decodeFromString(ModelsResponse.serializer(), response.bodyAsText())
        return body.models
    }

    fun close() {
        client.close()
    }
}
