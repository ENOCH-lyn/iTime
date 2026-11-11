package com.itime.app.data.repository

import com.itime.app.data.remote.AiConversationCreateBody
import com.itime.app.data.remote.AiConversationDto
import com.itime.app.data.remote.AiConversationEnvelope
import com.itime.app.data.remote.AiConversationListDto
import com.itime.app.data.remote.AiMessageCreateBody
import com.itime.app.data.remote.AiMessageDto
import com.itime.app.data.remote.AiMessageEnvelope
import com.itime.app.data.remote.AiProposalActionBody
import com.itime.app.data.remote.AiToolProposalDto
import com.itime.app.data.remote.ItimeApi
import kotlinx.coroutines.delay
import kotlinx.serialization.json.jsonPrimitive
import java.util.UUID

/**
 * AI conversation is intentionally online-only: the server owns the model
 * endpoint, budget checks and the background worker.
 *
 * Agent writes use the approval flow: the reply carries a tool proposal, the
 * user decides in the chat sheet, and an approved proposal is applied on the
 * server which then resumes the agent run.
 */
class AiRepository(private val api: ItimeApi) {

    suspend fun ensureConversation(): AiConversationDto {
        val list: AiConversationListDto = api.getAiConversations(limit = 1)
        list.items.firstOrNull()?.let { return it }
        return api.createAiConversation(
            AiConversationCreateBody(operation_id = UUID.randomUUID().toString()),
        ).conversation
    }

    suspend fun createConversation(): AiConversationDto = api.createAiConversation(
        AiConversationCreateBody(
            operation_id = UUID.randomUUID().toString(),
            title = "新对话",
        ),
    ).conversation

    suspend fun messages(conversationId: String): List<AiMessageDto> =
        api.aiMessages(conversationId).items.sortedBy { it.sequence }

    /**
     * Sends one user message in agent mode and waits for the reply.
     *
     * ``response_mode=auto`` lets the server run the tool-calling agent; the
     * reply appears once the job finishes, and a pending write shows up as a
     * tool proposal id in the job output.
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
            response_mode = "auto",
        )
        val accepted: AiMessageEnvelope = api.sendAiMessage(conversation.id, body)
        var latest = accepted.messages.ifEmpty {
            api.aiMessages(conversation.id).items
        }.sortedBy { it.sequence }
        val baseline = latest.size
        onProgress(latest)
        repeat(180) { attempt ->
            val last = latest.lastOrNull()
            val settled = last != null && last.role == "assistant" && last.status == "complete"
            if (latest.size > baseline && settled) return latest
            delay(if (attempt < 10) 800 else 1500)
            latest = api.aiMessages(conversation.id).items.sortedBy { it.sequence }
            onProgress(latest)
        }
        return latest
    }

    /** Reads the approval card for one assistant reply, if it queued a write. */
    suspend fun proposalFor(message: AiMessageDto): AiToolProposalDto? {
        val output = message.job?.output ?: return null
        val proposalId = runCatching {
            output["proposal_id"]?.jsonPrimitive?.content
        }.getOrNull()?.takeIf { it.isNotBlank() } ?: return null
        return runCatching { api.aiToolProposal(proposalId) }.getOrNull()
    }

    /**
     * Applies or rejects a proposal and waits for the resumed run to finish.
     * Returns the refreshed message list.
     */
    suspend fun decide(
        conversationId: String,
        proposal: AiToolProposalDto,
        approve: Boolean,
        knownMessages: Int,
        onProgress: suspend (List<AiMessageDto>) -> Unit = {},
    ): List<AiMessageDto> {
        val body = AiProposalActionBody(
            operation_id = UUID.randomUUID().toString(),
            expected_proposal_version = proposal.version,
        )
        if (approve) {
            api.applyAiToolProposal(proposal.id, body)
        } else {
            api.rejectAiToolProposal(proposal.id, body)
        }
        var latest = api.aiMessages(conversationId).items.sortedBy { it.sequence }
        onProgress(latest)
        repeat(60) { attempt ->
            val last = latest.lastOrNull()
            val settled = last != null && last.role == "assistant" && last.status == "complete"
            if (latest.size > knownMessages && settled) return latest
            delay(if (attempt < 10) 600 else 1200)
            latest = api.aiMessages(conversationId).items.sortedBy { it.sequence }
            onProgress(latest)
        }
        return latest
    }
}
