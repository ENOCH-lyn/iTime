package com.itime.app.ui.screens.ai

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.net.Uri
import java.io.ByteArrayOutputStream
import java.io.IOException
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * 对话图片按服务端限额（600 KiB）在本地收敛。
 *
 * 相册原图常见 3–12 MiB，直接上传会被 413 拒掉；这里先缩到最长边 1280，再按
 * 质量台阶压 JPEG，直到落进限额。带透明通道的图先铺白底，避免 JPEG 变黑块。
 */
object AiImageFiles {
    const val MAX_BYTES = 600 * 1024
    private const val MAX_EDGE = 1280
    private val qualities = intArrayOf(82, 72, 62, 52)

    data class Prepared(val bytes: ByteArray, val fileName: String, val contentType: String)

    fun prepare(context: Context, uri: Uri): Prepared {
        val resolver = context.contentResolver
        val raw = resolver.openInputStream(uri)?.use { it.readBytes() }
            ?: throw IOException("无法读取所选图片")
        val decoded = BitmapFactory.decodeByteArray(raw, 0, raw.size)
            ?: throw IOException("无法解析所选图片")
        val scaled = scaleTo(decoded, MAX_EDGE)
        val opaque = if (scaled.hasAlpha()) flatten(scaled) else scaled
        try {
            for (quality in qualities) {
                val out = ByteArrayOutputStream()
                opaque.compress(Bitmap.CompressFormat.JPEG, quality, out)
                val bytes = out.toByteArray()
                if (bytes.size <= MAX_BYTES) {
                    return Prepared(bytes, "chat-${System.currentTimeMillis()}.jpg", "image/jpeg")
                }
            }
            // 极端长图：再缩一半试一次，仍超限就明确失败，不偷偷发原图。
            val half = scaleTo(opaque, MAX_EDGE / 2)
            val out = ByteArrayOutputStream()
            half.compress(Bitmap.CompressFormat.JPEG, 55, out)
            val bytes = out.toByteArray()
            if (bytes.size > MAX_BYTES) throw IOException("图片过大，请裁剪后重试")
            return Prepared(bytes, "chat-${System.currentTimeMillis()}.jpg", "image/jpeg")
        } finally {
            if (opaque !== scaled) opaque.recycle()
            if (scaled !== decoded) scaled.recycle()
            decoded.recycle()
        }
    }

    private fun scaleTo(source: Bitmap, maxEdge: Int): Bitmap {
        val longest = max(source.width, source.height)
        if (longest <= maxEdge) return source
        val ratio = maxEdge.toFloat() / longest
        return Bitmap.createScaledBitmap(
            source,
            (source.width * ratio).roundToInt().coerceAtLeast(1),
            (source.height * ratio).roundToInt().coerceAtLeast(1),
            true,
        )
    }

    private fun flatten(source: Bitmap): Bitmap {
        val output = Bitmap.createBitmap(source.width, source.height, Bitmap.Config.ARGB_8888)
        val canvas = android.graphics.Canvas(output)
        canvas.drawColor(Color.WHITE)
        canvas.drawBitmap(source, 0f, 0f, null)
        return output
    }
}
