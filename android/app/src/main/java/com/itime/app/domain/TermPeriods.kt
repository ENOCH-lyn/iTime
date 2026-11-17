package com.itime.app.domain

import java.time.LocalTime
import java.time.format.DateTimeFormatter

/** 学期节次表的一行：编号、名称、上下课时间。 */
data class PeriodInput(
    val periodNumber: Int,
    val name: String,
    val startsAt: LocalTime,
    val endsAt: LocalTime,
)

/**
 * 节次表的默认值与本地校验。
 *
 * 默认 10 节与 Web 端一致；校验规则对齐服务端（编号/名称唯一、结束晚于开始、
 * 相邻可以首尾衔接但不能重叠），这样大多数错误在本地就提示，不必等服务端 4xx。
 */
object TermPeriods {

    const val MAX_COUNT = 30

    private val clock = DateTimeFormatter.ofPattern("HH:mm")

    private val defaultTimes = listOf(
        LocalTime.of(8, 30) to LocalTime.of(9, 15),
        LocalTime.of(9, 25) to LocalTime.of(10, 10),
        LocalTime.of(10, 30) to LocalTime.of(11, 15),
        LocalTime.of(11, 25) to LocalTime.of(12, 10),
        LocalTime.of(14, 0) to LocalTime.of(14, 45),
        LocalTime.of(14, 55) to LocalTime.of(15, 40),
        LocalTime.of(16, 0) to LocalTime.of(16, 45),
        LocalTime.of(16, 55) to LocalTime.of(17, 40),
        LocalTime.of(19, 0) to LocalTime.of(19, 55),
        LocalTime.of(19, 55) to LocalTime.of(20, 40),
    )

    /** 新建学期时预填的「第 N 节」时间表。 */
    fun defaults(): List<PeriodInput> = defaultTimes.mapIndexed { index, (starts, ends) ->
        PeriodInput(index + 1, "第${index + 1}节", starts, ends)
    }

    fun format(time: LocalTime): String = time.format(clock)

    /** 服务端要的 `HH:mm:ss`（本地不带时区偏移）。 */
    fun toWire(time: LocalTime): String =
        "%02d:%02d:00".format(time.hour, time.minute)

    fun toWire(period: PeriodInput) = PeriodWire(
        periodNumber = period.periodNumber,
        name = period.name.trim(),
        startsAt = toWire(period.startsAt),
        endsAt = toWire(period.endsAt),
    )

    fun parseTime(raw: String): LocalTime? = try {
        LocalTime.parse(raw.trim())
    } catch (_: Exception) {
        runCatching {
            LocalTime.parse(raw.trim(), DateTimeFormatter.ofPattern("H:mm"))
        }.getOrNull()
    }

    /** 从服务端的 `HH:mm:ss` 读回，给编辑面板用。 */
    fun fromWire(periodNumber: Int, name: String, startsAt: String, endsAt: String): PeriodInput? {
        val starts = parseTime(startsAt) ?: return null
        val ends = parseTime(endsAt) ?: return null
        return PeriodInput(periodNumber, name, starts, ends)
    }

    /**
     * 保存前的自检。返回第一条错误文案，null 表示可以保存。
     * 空表是合法的（表示这个学期不按节次排课）。
     */
    fun validate(periods: List<PeriodInput>): String? {
        if (periods.size > MAX_COUNT) return "一个学期最多设置 $MAX_COUNT 个节次。"
        val numbers = mutableSetOf<Int>()
        val names = mutableMapOf<String, Int>()
        periods.forEachIndexed { index, period ->
            if (period.periodNumber !in 1..99) return "节次编号要在 1–99 之间。"
            if (period.name.isBlank()) return "第 ${index + 1} 个节次还没有名称。"
            if (!period.startsAt.isBefore(period.endsAt)) {
                return "「${period.name.trim()}」的结束时间要晚于开始时间。"
            }
            if (!numbers.add(period.periodNumber)) {
                return "节次编号 ${period.periodNumber} 重复了。"
            }
            val key = period.name.trim().lowercase()
            names[key]?.let { previous ->
                return "节次名称「${period.name.trim()}」和编号 $previous 重复了。"
            }
            names[key] = period.periodNumber
        }
        val sorted = periods.sortedWith(compareBy({ it.startsAt }, { it.endsAt }))
        sorted.zipWithNext().forEach { (previous, current) ->
            if (current.startsAt.isBefore(previous.endsAt)) {
                return "「${previous.name.trim()}」和「${current.name.trim()}」的时间重叠了。"
            }
        }
        return null
    }
}

/** 提交给服务端的节次行（时间已是 `HH:mm:ss`）。 */
data class PeriodWire(
    val periodNumber: Int,
    val name: String,
    val startsAt: String,
    val endsAt: String,
)
