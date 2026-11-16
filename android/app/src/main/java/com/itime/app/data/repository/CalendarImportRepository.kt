package com.itime.app.data.repository

import com.itime.app.data.remote.AiImportBatchResult
import com.itime.app.data.remote.ApiException
import com.itime.app.data.remote.ImportConfirmBody
import com.itime.app.data.remote.ImportDecisionBody
import com.itime.app.data.remote.ImportDetailDto
import com.itime.app.data.remote.ItimeApi
import com.itime.app.data.sync.SyncEngine
import kotlinx.coroutines.delay
import java.time.LocalDate
import java.util.UUID

/** 课表截图导入的两个可见阶段，用于驱动界面文案。 */
enum class ImportStage { Uploading, Recognizing }

class CalendarImportRepository(
    private val api: ItimeApi,
    private val calendar: CalendarRepository,
    private val sync: SyncEngine,
) {
    /**
     * 课表识别需要学期上下文：服务端按学期的第一周周一和结束日期补出周次。
     * 这里先建学期待同步落地，再允许上传，避免识别出全是缺日期的无效候选。
     */
    suspend fun prepareTerm(
        name: String,
        firstWeekMonday: LocalDate,
        weeks: Int,
        timeZone: String,
    ) {
        val end = firstWeekMonday.plusWeeks(weeks.toLong()).minusDays(1)
        calendar.createTerm(
            name = name,
            startDate = firstWeekMonday,
            endDate = end,
            firstWeekMonday = firstWeekMonday,
            timeZone = timeZone,
        )
        sync.syncNow("calendar-import-term", force = true)
    }

    /** 把本地图片上传给服务端识别，并阻塞到 AI 作业结束（约 10–60 秒）。 */
    suspend fun recognize(
        bytes: ByteArray,
        filename: String,
        contentType: String,
        onStage: (ImportStage) -> Unit,
    ): ImportDetailDto {
        onStage(ImportStage.Uploading)
        val created: AiImportBatchResult = api.createAiCalendarImport(bytes, filename, contentType)
        onStage(ImportStage.Recognizing)
        waitForJob(created)
        return api.calendarImport(created.batch.id)
    }

    private suspend fun waitForJob(created: AiImportBatchResult) {
        val deadline = System.currentTimeMillis() + JOB_TIMEOUT_MS
        while (true) {
            val job = api.aiJob(created.job.id)
            when (job.status) {
                "succeeded" -> return
                "failed", "timed_out", "cancelled" -> throw ApiException(
                    code = job.error?.code ?: "calendar_import_failed",
                    message = job.error?.message?.takeIf { it.isNotBlank() }
                        ?: "课表识别失败，请稍后重试。",
                    status = 200,
                    retryable = false,
                )
            }
            if (System.currentTimeMillis() > deadline) {
                throw ApiException(
                    code = "calendar_import_timeout",
                    message = "课表识别超时，稍后可在日历中查看识别结果。",
                    status = 0,
                    retryable = true,
                )
            }
            delay(POLL_INTERVAL_MS)
        }
    }

    /** 确认整批候选：可导入的写入日程，重复与无效项直接跳过。返回实际导入数量。 */
    suspend fun confirm(detail: ImportDetailDto): Int {
        val decisions = detail.candidates.map { candidate ->
            ImportDecisionBody(
                candidate_id = candidate.id,
                expected_version = candidate.version,
                action = if (candidate.state == "valid") "confirm" else "skip",
            )
        }
        if (decisions.isEmpty()) return 0
        api.confirmCalendarImport(
            batchId = detail.batch.id,
            body = ImportConfirmBody(
                operation_id = UUID.randomUUID().toString(),
                expected_batch_version = detail.batch.version,
                decisions = decisions,
            ),
        )
        sync.syncNow("calendar-import", force = true)
        return decisions.count { it.action == "confirm" }
    }

    private companion object {
        const val POLL_INTERVAL_MS = 1_500L
        const val JOB_TIMEOUT_MS = 120_000L
    }
}
