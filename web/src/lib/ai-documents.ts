import { uploadApi, uploadFormApi } from './api'

export interface AIDocumentExtract {
  filename: string
  text: string
  characters: number
  truncated: boolean
}

export async function extractAIDocument(file: File, operationId: string) {
  const form = new FormData()
  form.append('document', file, file.name)
  return uploadFormApi<AIDocumentExtract>(
    '/ai/documents/extract',
    form,
    operationId,
    {
      timeoutMs: 60_000,
      timeoutMessage: '文件解析超时，请换一个较小的文件。',
      networkMessage: '文件上传失败，请检查网络后重试。',
    },
  )
}

async function prepareChatImage(file: File): Promise<File> {
  if (!file.type.startsWith('image/')) return file
  try {
    const bitmap = await createImageBitmap(file)
    let width = bitmap.width
    let height = bitmap.height
    const scale = Math.min(1, 1280 / Math.max(width, height))
    width = Math.max(1, Math.round(width * scale))
    height = Math.max(1, Math.round(height * scale))
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      const context = canvas.getContext('2d')
      if (!context) return file
      context.fillStyle = '#fff'
      context.fillRect(0, 0, width, height)
      context.drawImage(bitmap, 0, 0, width, height)
      const blob = await new Promise<Blob | null>(resolve => {
        canvas.toBlob(resolve, 'image/jpeg', attempt < 2 ? 0.82 : 0.65)
      })
      if (blob && blob.size <= 600 * 1024) {
        return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', {
          type: 'image/jpeg',
        })
      }
      width = Math.max(1, Math.round(width * 0.75))
      height = Math.max(1, Math.round(height * 0.75))
    }
  } catch {
    // Fall back to the original image; the server will reject an oversized file.
  }
  return file
}

export async function uploadAIImage(file: File, operationId: string) {
  const prepared = await prepareChatImage(file)
  const params = new URLSearchParams({ filename: prepared.name })
  return uploadApi<{ media_id: string }>(
    `/ai/images?${params}`,
    prepared,
    operationId,
    {
      timeoutMs: 60_000,
      timeoutMessage: '图片上传超时，请换一张较小的图片。',
      networkMessage: '图片上传失败，请检查网络后重试。',
    },
  )
}
