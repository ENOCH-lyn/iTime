package com.itime.app.data.repository

import com.itime.app.data.remote.AiConversationCreateBody
import com.itime.app.data.remote.AiConversationDto
import com.itime.app.data.remote.AiConversationEnvelope
import com.itime.app.data.remote.AiConversationListDto
import com.itime.app.data.remote.AiMessageCreateBody
import com.itime.app.data.remote.AiMessageDto
import com.itime.app.data.remote.AiMessageEnvelope
import com.itime.app.data.remote.AiMessageListDto
import com.itime.app.data.remote.ItimeApi
import kotlinx.coroutines.delay
import java.util.UUID

/**
 * AI conversation is intentionally online-only: the server owns the model
 * endpoint, budget checks and the background worker.  This repository keeps
 * the tiny amount of client state (which conversation to open) and turns the
 * asynchronous job protocol into a suspend call that returns the reply.
 */
class AiRepository(private val api: ItimeApi) {

    suspend fun ensureConversation(): AiConversationDto {
        val list: AiConversationListDto = api.getAiConversations(limit = 1)
        list.items.firstOrNull()?.let { return it }
        return api.createAiConversation(
            AiConversationCreateBody(operation_id = UUID.randomUUID().toString()),
        ).conversation
    }

    suspend fun messages(conversationId: String): List<AiMessageDto> =
        api.aiMessages(conversationId).items

    /**
     * Sends one user message and waits for the assistant reply.
     *
     * The server answers with 202 and a queued job; the reply appears as a new
     * assistant message once the worker finishes.  Polling keeps the client
     * dependency-free while still surfacing reasoning content and errors.
     */
    suspend fun send(
        conversation: AiConversationDto,
        content: String,
        onProgress: suspend (List<AiMessageDto>) -> Unit = {},
    ): List<AiMessageDto> {
        val body = AiMessageCreateBody(
            operation_id = UUID.randomUUID().toString(),
            expected_conversation_version = conversation.version,
            content = content,
        )
        val accepted: AiMessageEnvelope = api.sendAiMessage(conversation.id, body)
        var latest = accepted.messages.ifEmpty {
            api.aiMessages(conversation.id).items
        }.sortedBy { it.sequence }
        onProgress(latest)
        val baseline = latest.size
        repeat(150) { attempt ->
            val pending = latest.lastOrNull()?.let { it.role == "assistant" && it.status != "complete" } == true
            val grew = latest.size > baseline
            if (grew && !pending) return latest
            delay(if (attempt < 10) 800 else 1500)
            latest = api.aiMessages(conversation.id).items.sortedBy { it.sequence }
            onProgress(latest)
        }
        return latest
    }
}
