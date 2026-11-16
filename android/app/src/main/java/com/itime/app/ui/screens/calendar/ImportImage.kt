package com.itime.app.ui.screens.calendar

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import java.io.ByteArrayOutputStream
import java.io.IOException

/**
 * 课表截图在本地先做一次收敛：服务端只接受常见图片格式且不超过 5 MiB，
 * 手机相册里的原图经常是 6–12 MiB 的 HEIC/PNG，直接上传会被 413 拦掉。
 */
object ImportImage {
    const val MAX_SERVER_BYTES = 5 * 1024 * 1024
    private const val MAX_EDGE = 2200
    private const val JPEG_QUALITY = 88
    private val passthroughTypes = setOf("image/jpeg", "image/png", "image/webp")

    data class Payload(val bytes: ByteArray, val fileName: String, val contentType: String)

    fun load(context: Context, uri: Uri): Payload {
        val resolver = context.contentResolver
        val declared = resolver.getType(uri).orEmpty()
        val raw = resolver.openInputStream(uri)?.use { it.readBytes() }
            ?: throw IOException("无法读取所选图片")
        if (declared in passthroughTypes && raw.size <= MAX_SERVER_BYTES) {
            return Payload(raw, "timetable.${extensionOf(declared)}", declared)
        }
        val decoded = BitmapFactory.decodeByteArray(raw, 0, raw.size)
            ?: throw IOException("无法解析所选图片")
        val scaled = scaleDown(decoded)
        val out = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
        if (scaled !== decoded) scaled.recycle()
        decoded.recycle()
        val bytes = out.toByteArray()
        if (bytes.size > MAX_SERVER_BYTES) throw IOException("图片过大，请截取课表部分后重试")
        return Payload(bytes, "timetable.jpg", "image/jpeg")
    }

    private fun scaleDown(source: Bitmap): Bitmap {
        val longest = maxOf(source.width, source.height)
        if (longest <= MAX_EDGE) return source
        val ratio = MAX_EDGE.toFloat() / longest
        return Bitmap.createScaledBitmap(
            source,
            (source.width * ratio).toInt().coerceAtLeast(1),
            (source.height * ratio).toInt().coerceAtLeast(1),
            true,
        )
    }

    private fun extensionOf(contentType: String): String = when (contentType) {
        "image/png" -> "png"
        "image/webp" -> "webp"
        else -> "jpg"
    }
}
