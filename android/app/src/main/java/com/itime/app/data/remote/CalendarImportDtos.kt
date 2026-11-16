package com.itime.app.data.remote

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * 课表截图导入的线上数据结构。
 *
 * 上传图片后服务端返回一个导入批次和一条 AI 作业；作业结束（status =
 * succeeded）后批次进入 needs_review，候选草稿由 `/calendar/imports/{id}`
 * 返回，确认后服务端直接写入 calendar_series，客户端再同步拉取。
 */
@Serializable
data class ImportErrorDto(
    val code: String = "",
    val message: String = "",
    val details: JsonElement? = null,
)

@Serializable
data class ImportBatchDto(
    val id: String,
    val state: String = "",
    val source_type: String = "",
    val source_display: String? = null,
    val filename: String = "",
    val candidate_count: Int = 0,
    val version: Int = 1,
    val error: ImportErrorDto? = null,
)

@Serializable
data class AiJobStatusDto(
    val id: String,
    val capability: String = "",
    val status: String = "",
    val error: ImportErrorDto? = null,
    val output: JsonElement? = null,
)

@Serializable
data class AiImportBatchResult(
    val operation_id: String = "",
    val batch: ImportBatchDto,
    val job: AiJobStatusDto,
)

@Serializable
data class ImportDraftDto(
    val kind: String = "fixed",
    val name: String = "",
    val teacher: String = "",
    val location: String = "",
    val start_date: String? = null,
    val starts_at: String? = null,
    val ends_at: String? = null,
    val end_day_offset: Int = 0,
    val recurrence: RecurrenceBody? = null,
)

@Serializable
data class ImportCandidateDto(
    val id: String,
    val position: Int = 0,
    val state: String = "valid",
    val draft: ImportDraftDto? = null,
    val error: ImportErrorDto? = null,
    val version: Int = 1,
)

@Serializable
data class ImportDetailDto(
    val batch: ImportBatchDto,
    val candidates: List<ImportCandidateDto> = emptyList(),
)

@Serializable
data class ImportDecisionBody(
    val candidate_id: String,
    val expected_version: Int,
    val action: String,
)

@Serializable
data class ImportConfirmBody(
    val operation_id: String,
    val expected_batch_version: Int,
    val decisions: List<ImportDecisionBody>,
)

@Serializable
data class ImportConfirmResult(
    val operation_id: String = "",
    val batch: ImportBatchDto? = null,
    val outcomes: List<JsonElement> = emptyList(),
)
