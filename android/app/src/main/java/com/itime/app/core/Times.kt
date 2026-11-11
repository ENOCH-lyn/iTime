package com.itime.app.core

import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Parsing and formatting helpers for the wire formats used by the iTime server.
 *
 * The server emits ISO-8601 instants (``2026-11-12T01:00:00Z``), plain dates
 * (``2026-11-12``), wall times (``09:00:00``) and occasional offset timestamps.
 * Everything here is null tolerant because older server builds may omit fields.
 */
object Times {
    private val dateFormatter = DateTimeFormatter.ISO_LOCAL_DATE

    fun parseDate(value: String?): LocalDate? = try {
        value?.takeIf { it.isNotBlank() }?.let(LocalDate::parse)
    } catch (_: Exception) {
        null
    }

    fun parseTime(value: String?): LocalTime? = try {
        value?.takeIf { it.isNotBlank() }?.let(LocalTime::parse)
    } catch (_: Exception) {
        null
    }

    fun parseInstant(value: String?): Instant? {
        val raw = value?.takeIf { it.isNotBlank() } ?: return null
        return try {
            Instant.parse(raw)
        } catch (_: Exception) {
            try {
                OffsetDateTime.parse(raw).toInstant()
            } catch (_: Exception) {
                try {
                    LocalDateTime.parse(raw).atZone(ZoneId.systemDefault()).toInstant()
                } catch (_: Exception) {
                    null
                }
            }
        }
    }

    fun parseOffset(value: String?): OffsetDateTime? = try {
        value?.takeIf { it.isNotBlank() }?.let(OffsetDateTime::parse)
    } catch (_: Exception) {
        null
    }

    fun formatDate(date: LocalDate): String = date.format(dateFormatter)

    fun formatTime(time: LocalTime): String =
        time.format(DateTimeFormatter.ofPattern("HH:mm:ss", Locale.ROOT))

    fun formatInstant(instant: Instant): String =
        instant.atOffset(java.time.ZoneOffset.UTC)
            .format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.ROOT))

    /** Local date of an instant inside the given IANA zone (falls back to system zone). */
    fun localDate(instant: Instant, zone: ZoneId): LocalDate = instant.atZone(zone).toLocalDate()

    fun localTime(instant: Instant, zone: ZoneId): LocalTime = instant.atZone(zone).toLocalTime()

    fun zoneOf(id: String?): ZoneId = try {
        if (id.isNullOrBlank()) ZoneId.systemDefault() else ZoneId.of(id)
    } catch (_: Exception) {
        ZoneId.systemDefault()
    }

    fun startOfDay(date: LocalDate, zone: ZoneId): Instant =
        date.atStartOfDay(zone).toInstant()

    fun combine(date: LocalDate, time: LocalTime, zone: ZoneId): Instant =
        LocalDateTime.of(date, time).atZone(zone).toInstant()
}
