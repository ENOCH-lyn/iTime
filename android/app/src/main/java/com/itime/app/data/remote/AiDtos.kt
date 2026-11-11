package com.itime.app.data.remote

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class AiConversationDto(
    val id: String,
    val title: String? = null,
    val version: Int = 1,
    val archived: Boolean = false,
    val updated_at: String? = null,
    val active_job: JsonElement? = null,
)

@Serializable
data class AiConversationEnvelope(val conversation: AiConversationDto)

@Serializable
data class AiConversationListDto(val items: List<AiConversationDto> = emptyList())

@Serializable
data class AiMessageDto(
    val id: String,
    val conversation_id: String,
    val sequence: Int = 0,
    val role: String,
    val status: String = "completed",
    val content: String = "",
    val reasoning_content: String? = null,
    val error: JsonElement? = null,
    val created_at: String? = null,
    val completed_at: String? = null,
)

@Serializable
data class AiMessageListDto(
    val conversation: AiConversationDto,
    val items: List<AiMessageDto> = emptyList(),
)

@Serializable
data class AiMessageEnvelope(
    val conversation: AiConversationDto,
    val messages: List<AiMessageDto> = emptyList(),
    val message: AiMessageDto? = null,
)

@Serializable
data class AiConversationCreateBody(
    val operation_id: String,
    val title: String? = null,
)

@Serializable
data class AiMessageCreateBody(
    val operation_id: String,
    val expected_conversation_version: Int,
    val content: String,
    val response_mode: String = "text",
)
