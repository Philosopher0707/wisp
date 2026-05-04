package com.wisp.app

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
sealed class WispMessage {
    abstract val type: String
}

@Serializable
data class TokenMessage(
    override val type: String = "token",
    val phase: String,  // "thinking" or "content"
    val text: String
) : WispMessage()

@Serializable
data class ToolCallMessage(
    override val type: String = "tool_call",
    val id: String,
    val name: String,
    val arguments: JsonObject
) : WispMessage()

@Serializable
data class ToolResultMessage(
    override val type: String = "tool_result",
    val id: String,
    val output: String,
    val error: String? = null
) : WispMessage()

@Serializable
data class CompleteMessage(
    override val type: String = "complete",
    val session_id: String? = null
) : WispMessage()

@Serializable
data class ErrorMessage(
    override val type: String = "error",
    val message: String,
    val error_type: String? = null
) : WispMessage()

@Serializable
data class PromptMessage(
    override val type: String = "prompt",
    val content: String,
    val model: String? = null,
    val session_id: String? = null,
    val show_thinking: Boolean = true
) : WispMessage()

@Serializable
data class ToolApprovalMessage(
    override val type: String = "tool_approval",
    val id: String,
    val approved: Boolean,
    val reason: String? = null
) : WispMessage()

@Serializable
data class PingMessage(
    override val type: String = "ping"
) : WispMessage()

@Serializable
data class PongMessage(
    override val type: String = "pong"
) : WispMessage()

@Serializable
data class StatusMessage(
    override val type: String = "status",
    val message: String
) : WispMessage()

@Serializable
data class ToolBlockedMessage(
    override val type: String = "tool_blocked",
    val id: String,
    val name: String,
    val arguments: JsonObject,
    val reason: String
) : WispMessage()

@Serializable
data class ToolExecutingMessage(
    override val type: String = "tool_executing",
    val id: String,
    val name: String
) : WispMessage()

@Serializable
data class CheckpointMessage(
    override val type: String = "checkpoint",
    val hash: String
) : WispMessage()

// REST API models
@Serializable
data class FileItem(
    val name: String,
    val path: String,
    val type: String,  // "file" or "directory"
    val size: Long? = null
)

@Serializable
data class DirectoryListing(
    val type: String,
    val path: String,
    val items: List<FileItem>
)

@Serializable
data class FileContent(
    val type: String,
    val path: String,
    val content: String
)

@Serializable
data class BashResult(
    val exit_code: Int,
    val stdout: String,
    val stderr: String,
    val truncated: Boolean
)

@Serializable
data class ModelInfo(
    val name: String,
    val size: Long? = null,
    val modified_at: String? = null
)

@Serializable
data class ModelsResponse(
    val models: List<ModelInfo>
)
