package com.itime.app.data.remote

import com.itime.app.data.session.SessionStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/**
 * Thin HTTP client for the iTime server.
 *
 * Authentication matches the Web client: the 43 character session token is sent
 * as the ``itime_session`` cookie and every write carries the CSRF token that
 * the server derives for that session. The token is captured from the login
 * response's ``Set-Cookie`` header because the JSON body intentionally does not
 * repeat it.
 */
class ItimeApi(
    private val client: OkHttpClient,
    private val json: Json,
    private val sessionStore: SessionStore,
) {
    companion object {
        val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
        const val COOKIE_NAME = "itime_session"
        private const val MAX_PAGES = 50
    }

    // ---------------------------------------------------------------- auth

    suspend fun policy(): PolicyDto = get("/api/v1/auth/policy")

    suspend fun login(email: String, password: String, deviceName: String): LoginResult {
        val (body, headers) = requestRaw(
            method = "POST",
            path = "/api/v1/auth/login",
            body = json.encodeToString(
                LoginRequest.serializer(),
                LoginRequest(email = email, password = password, device_name = deviceName),
            ),
            authenticated = false,
        )
        val session = json.decodeFromString(SessionDto.serializer(), body)
        val token = headers.filter { it.first.equals("Set-Cookie", ignoreCase = true) }
            .mapNotNull { (_, value) -> extractCookie(value) }
            .firstOrNull()
            ?: throw ApiException(
                code = "session_unavailable",
                message = "登录成功但未收到会话凭据，请稍后重试。",
                status = 500,
            )
        return LoginResult(token = token, session = session)
    }

    suspend fun register(email: String, password: String, deviceName: String): LoginResult? {
        val (body, headers) = requestRaw(
            method = "POST",
            path = "/api/v1/auth/register",
            body = json.encodeToString(
                RegisterRequest.serializer(),
                RegisterRequest(email = email, password = password, device_name = deviceName),
            ),
            authenticated = false,
        )
        val token = headers.filter { it.first.equals("Set-Cookie", ignoreCase = true) }
            .mapNotNull { (_, value) -> extractCookie(value) }
            .firstOrNull()
        if (token == null) return null
        val me = json.decodeFromString(MeDto.serializer(), body)
        return LoginResult(
            token = token,
            session = SessionDto(
                user = me.user,
                session_id = me.session_id,
                expires_at = me.expires_at,
                csrf_token = me.csrf_token,
            ),
        )
    }

    suspend fun me(): MeDto = get("/api/v1/auth/me")

    suspend fun logout() {
        runCatching {
            requestRaw("POST", "/api/v1/auth/logout", body = "")
        }
    }

    // --------------------------------------------------------------- tasks

    suspend fun listTasks(
        status: String = "all",
        lifecycle: String = "active",
        beforeTime: String? = null,
        beforeId: String? = null,
        limit: Int = 100,
    ): TaskListDto = get(
        "/api/v1/tasks",
        query = buildMap {
            put("status", status)
            put("lifecycle", lifecycle)
            put("limit", limit.toString())
            beforeTime?.let { put("before_time", it) }
            beforeId?.let { put("before_id", it) }
        },
    )

    suspend fun getTask(id: String): TaskDto =
        json.decodeFromString(TaskEnvelope.serializer(), getRaw("/api/v1/tasks/$id")).task

    suspend fun createTask(body: TaskCreateBody): TaskDto =
        post("/api/v1/tasks", body, TaskEnvelope.serializer()).task

    suspend fun updateTask(id: String, body: TaskUpdateBody): TaskDto =
        put("/api/v1/tasks/$id", body, TaskEnvelope.serializer()).task

    suspend fun completeTask(id: String, body: TaskCompletionBody): TaskDto =
        put("/api/v1/tasks/$id/completion", body, TaskEnvelope.serializer()).task

    suspend fun deleteTask(id: String, body: TaskLifecycleBody): TaskDto =
        deleteWithBody("/api/v1/tasks/$id", body, TaskEnvelope.serializer()).task

    suspend fun restoreTask(id: String, body: TaskLifecycleBody): TaskDto =
        post("/api/v1/tasks/$id/restore", body, TaskEnvelope.serializer()).task

    suspend fun archiveTask(id: String, body: TaskArchiveBody): TaskDto =
        put("/api/v1/tasks/$id/archive", body, TaskEnvelope.serializer()).task

    suspend fun putSchedule(id: String, body: ScheduleUpdateBody): TaskDto =
        put("/api/v1/tasks/$id/schedule", body, TaskEnvelope.serializer()).task

    suspend fun setOccurrenceStatus(id: String, body: OccurrenceStatusBody): TaskDto =
        put("/api/v1/tasks/$id/occurrences/status", body, TaskEnvelope.serializer()).task

    suspend fun createStep(taskId: String, body: StepCreateBody): TaskDto =
        post("/api/v1/tasks/$taskId/steps", body, TaskEnvelope.serializer()).task

    suspend fun updateStep(taskId: String, stepId: String, body: StepUpdateBody): TaskDto =
        put("/api/v1/tasks/$taskId/steps/$stepId", body, TaskEnvelope.serializer()).task

    suspend fun deleteStep(taskId: String, stepId: String, body: StepDeleteBody): TaskDto =
        deleteWithBody("/api/v1/tasks/$taskId/steps/$stepId", body, TaskEnvelope.serializer()).task

    suspend fun reorderSteps(taskId: String, body: StepReorderBody): TaskDto =
        put("/api/v1/tasks/$taskId/steps", body, TaskEnvelope.serializer()).task

    // ------------------------------------------------------------ projects

    suspend fun listProjects(): List<ProjectDto> = get<ProjectListDto>("/api/v1/projects").items

    suspend fun createProject(body: ProjectCreateBody): ProjectDto =
        post("/api/v1/projects", body, ProjectEnvelope.serializer()).project

    // ------------------------------------------------------------ calendar

    suspend fun listSeries(): List<SeriesDto> =
        get<SeriesListDto>("/api/v1/calendar/series").items

    suspend fun createSeries(body: SeriesCreateBody): SeriesDto =
        post("/api/v1/calendar/series", body, SeriesEnvelope.serializer()).series

    suspend fun updateSeries(id: String, body: SeriesUpdateBody): SeriesDto =
        put("/api/v1/calendar/series/$id", body, SeriesEnvelope.serializer()).series

    suspend fun deleteSeries(id: String, body: SeriesDeletePayloadBody): OperationAckDto =
        deleteWithBody(
            "/api/v1/calendar/series/$id",
            body,
            OperationAckDto.serializer(),
        )

    suspend fun listTerms(): List<TermDto> = get<TermListDto>("/api/v1/terms").items

    suspend fun createTerm(body: TermCreateBody): TermDto =
        post("/api/v1/terms", body, TermEnvelope.serializer()).term

    suspend fun timeSettings(): TimeSettingsDto =
        get<TimeSettingsEnvelope>("/api/v1/time-settings").settings

    suspend fun updateTimeSettings(body: TimeSettingsUpdateBody): TimeSettingsDto =
        put("/api/v1/time-settings", body, TimeSettingsEnvelope.serializer()).settings

    // ---------------------------------------------------------------- sync

    suspend fun syncSnapshot(): SyncSnapshotDto = get("/api/v1/sync/snapshot")

    suspend fun syncChanges(cursor: String?, limit: Int = 100): SyncPageDto = get(
        "/api/v1/sync/changes",
        query = buildMap {
            put("limit", limit.toString())
            cursor?.let { put("cursor", it) }
        },
    )

    suspend fun syncEntity(entityType: String, entityId: String): SyncEntityDto =
        get("/api/v1/sync/entities/$entityType/$entityId")

    // -------------------------------------------------------------- AI 对话

    suspend fun getAiConversations(limit: Int = 20): AiConversationListDto = get(
        "/api/v1/ai/conversations",
        query = mapOf("limit" to limit.toString()),
    )

    suspend fun createAiConversation(body: AiConversationCreateBody): AiConversationEnvelope =
        post("/api/v1/ai/conversations", body, AiConversationEnvelope.serializer())

    suspend fun aiMessages(
        conversationId: String,
        limit: Int = 100,
    ): AiMessageListDto = get(
        "/api/v1/ai/conversations/$conversationId/messages",
        query = mapOf("limit" to limit.toString()),
    )

    suspend fun sendAiMessage(
        conversationId: String,
        body: AiMessageCreateBody,
    ): AiMessageEnvelope =
        post("/api/v1/ai/conversations/$conversationId/messages", body, AiMessageEnvelope.serializer())

    suspend fun aiToolProposal(proposalId: String): AiToolProposalDto =
        getDecoded(
            "/api/v1/ai/tool-proposals/$proposalId",
            AiToolProposalEnvelope.serializer(),
        ).proposal

    suspend fun applyAiToolProposal(
        proposalId: String,
        body: AiProposalActionBody,
    ): AiToolProposalEnvelope = post(
        "/api/v1/ai/tool-proposals/$proposalId/apply",
        body,
        AiToolProposalEnvelope.serializer(),
    )

    suspend fun rejectAiToolProposal(
        proposalId: String,
        body: AiProposalActionBody,
    ): AiToolProposalEnvelope = post(
        "/api/v1/ai/tool-proposals/$proposalId/reject",
        body,
        AiToolProposalEnvelope.serializer(),
    )

    // ------------------------------------------------------------ plumbing

    private suspend fun extractCookie(setCookie: String): String? {
        val first = setCookie.substringBefore(';').trim()
        if (!first.startsWith("$COOKIE_NAME=")) return null
        val value = first.removePrefix("$COOKIE_NAME=")
        return value.takeIf { it.isNotBlank() }
    }

    private fun baseUrl(): String {
        val configured = sessionStore.baseUrl()
        return if (configured.endsWith("/")) configured else "$configured/"
    }

    private fun urlBuilder(path: String, query: Map<String, String?>): okhttp3.HttpUrl.Builder {
        val builder = (baseUrl() + path.removePrefix("/")).toHttpUrl().newBuilder()
        query.forEach { (key, value) -> if (value != null) builder.addQueryParameter(key, value) }
        return builder
    }

    private fun buildRequest(
        method: String,
        path: String,
        query: Map<String, String?>,
        body: String?,
        authenticated: Boolean,
    ): Request {
        val builder = Request.Builder()
            .url(urlBuilder(path, query).build())
            .header("Accept", "application/json")
            .header("X-Client", "itime-android/0.1.0")
        if (authenticated) {
            val session = sessionStore.current()
            if (session != null) {
                builder.header("Cookie", "$COOKIE_NAME=${session.token}")
                if (method != "GET") builder.header("X-CSRF-Token", session.csrfToken)
            }
        }
        val requestBody = if (body == null) {
            if (method == "GET" || method == "HEAD") null
            else ByteArray(0).toRequestBody(JSON_MEDIA)
        } else {
            body.toRequestBody(JSON_MEDIA)
        }
        builder.method(method, requestBody)
        return builder.build()
    }

    private suspend fun requestRaw(
        method: String,
        path: String,
        query: Map<String, String?> = emptyMap(),
        body: String? = null,
        authenticated: Boolean = true,
    ): Pair<String, List<Pair<String, String>>> = withContext(Dispatchers.IO) {
        val request = buildRequest(method, path, query, body, authenticated)
        val response = try {
            client.newCall(request).execute()
        } catch (error: IOException) {
            throw ApiException(
                code = "network_unavailable",
                message = "网络不可用，已保留本地数据。",
                status = 0,
                retryable = true,
            )
        }
        response.use { result ->
            val text = result.body?.string().orEmpty()
            val headers = result.headers.map { it.first to it.second }
            if (!result.isSuccessful) {
                throw parseError(result.code, text)
            }
            text to headers
        }
    }

    private fun parseError(status: Int, body: String): ApiException {
        val envelope = runCatching {
            json.decodeFromString(ErrorEnvelope.serializer(), body)
        }.getOrNull()
        return if (envelope != null) {
            ApiException(
                code = envelope.error.code,
                message = envelope.error.message,
                status = status,
                retryable = envelope.error.retryable,
                details = envelope.error.details,
                requestId = envelope.error.request_id,
            )
        } else {
            ApiException(
                code = "http_$status",
                message = "服务暂时不可用，请稍后重试。",
                status = status,
                retryable = status >= 500,
            )
        }
    }

    private suspend inline fun <reified T> get(
        path: String,
        query: Map<String, String?> = emptyMap(),
    ): T = json.decodeFromString<T>(getRaw(path, query))

    suspend fun getRaw(path: String, query: Map<String, String?> = emptyMap()): String =
        requestRaw("GET", path, query).first

    private suspend fun <T> getDecoded(
        path: String,
        serializer: kotlinx.serialization.DeserializationStrategy<T>,
    ): T = json.decodeFromString(serializer, getRaw(path))

    private suspend inline fun <reified Req, Res> send(
        method: String,
        path: String,
        body: Req,
        responseSerializer: kotlinx.serialization.DeserializationStrategy<Res>,
    ): Res {
        val encoded = json.encodeToString(body)
        val text = requestRaw(method, path, body = encoded).first
        return json.decodeFromString(responseSerializer, text)
    }

    private suspend inline fun <reified Req, Res> post(
        path: String,
        body: Req,
        responseSerializer: kotlinx.serialization.DeserializationStrategy<Res>,
    ): Res = send("POST", path, body, responseSerializer)

    private suspend inline fun <reified Req, Res> put(
        path: String,
        body: Req,
        responseSerializer: kotlinx.serialization.DeserializationStrategy<Res>,
    ): Res = send("PUT", path, body, responseSerializer)

    private suspend inline fun <reified Req, Res> deleteWithBody(
        path: String,
        body: Req,
        responseSerializer: kotlinx.serialization.DeserializationStrategy<Res>,
    ): Res = send("DELETE", path, body, responseSerializer)
}

data class LoginResult(val token: String, val session: SessionDto)
