package com.itime.app.data.remote

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Decodes fixtures captured from the deployed FastAPI server (2026-11-16).
 * This is the regression net for client/server schema drift: if a server
 * response shape changes, these tests fail before an APK ships.
 */
class ServerContractTest {

    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        isLenient = true
    }

    private fun fixture(name: String): String {
        val stream = checkNotNull(javaClass.classLoader?.getResourceAsStream("fixtures/$name.json")) {
            "missing fixture $name"
        }
        return stream.bufferedReader().use { it.readText() }
    }

    private inline fun <reified T> decode(name: String): T =
        json.decodeFromString(fixture(name))

    @Test
    fun `policy decodes`() {
        val policy = decode<PolicyDto>("policy")
        assertTrue(policy.password_min_length in 1..128)
    }

    @Test
    fun `login session decodes`() {
        val session = decode<SessionDto>("login")
        assertEquals("fixture@example.com", session.user.email)
        assertTrue(session.csrf_token.isNotBlank())
    }

    @Test
    fun `me decodes`() {
        val me = decode<MeDto>("me")
        assertEquals("fixture@example.com", me.user.email)
    }

    @Test
    fun `task list and envelope decode`() {
        val list = decode<TaskListDto>("tasks_one")
        assertEquals(1, list.items.size)
        assertEquals("契约夹具任务", list.items.first().title)

        val envelope = decode<TaskEnvelope>("task_get")
        assertEquals("契约夹具任务", envelope.task.title)
        assertTrue(envelope.task.version >= 1)
    }

    @Test
    fun `project list decodes`() {
        val projects = decode<ProjectListDto>("projects_one")
        assertEquals(1, projects.items.size)
        assertEquals("夹具项目", projects.items.first().name)
    }

    @Test
    fun `terms and time settings decode`() {
        val terms = decode<TermListDto>("terms")
        assertTrue(terms.items.isEmpty() || terms.items.isNotEmpty())
        val settings = decode<TimeSettingsEnvelope>("time_settings")
        assertTrue(settings.settings.week_starts_on in 1..7)
    }

    @Test
    fun `calendar series list decodes`() {
        val series = decode<SeriesListDto>("series_empty")
        assertTrue(series.items.isEmpty())
    }

    @Test
    fun `sync changes and snapshot decode`() {
        val page = decode<SyncPageDto>("sync_changes")
        assertTrue(page.next_cursor.isNotBlank())
        val snapshot = decode<SyncSnapshotDto>("sync_snapshot")
        assertEquals(1, snapshot.tasks.size)
        assertEquals(1, snapshot.projects.size)
    }

    @Test
    fun `sync entity decodes`() {
        val entity = decode<SyncEntityDto>("sync_entity")
        assertTrue(entity.data != null)
    }

    @Test
    fun `ai conversations and messages decode`() {
        val list = decode<AiConversationListDto>("ai_conversations_one")
        assertEquals("夹具会话", list.items.first().title)

        val created = decode<AiConversationEnvelope>("ai_conversation_created")
        assertTrue(created.conversation.id.isNotBlank())

        val messages = decode<AiMessageListDto>("ai_messages_empty")
        assertTrue(messages.items.isEmpty())
    }

    @Test
    fun `calendar screenshot import decodes`() {
        val created = decode<AiImportBatchResult>("calendar_import_created")
        assertEquals("calendar_screenshot", created.job.capability)
        assertEquals("image", created.batch.source_type)
        assertTrue(created.batch.id.isNotBlank())
        assertTrue(created.job.id.isNotBlank())

        val detail = decode<ImportDetailDto>("calendar_import_detail")
        assertEquals(7, detail.candidates.size)
        assertTrue(detail.candidates.all { it.state == "valid" })
        val draft = detail.candidates.first().draft
        assertTrue(draft != null)
        assertEquals("08:00:00", draft?.starts_at)
        assertEquals(listOf(1), draft?.recurrence?.weekdays)
        assertTrue(draft?.recurrence?.week_numbers?.isNotEmpty() == true)
    }
}
