import { uploadFormApi } from './api'

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
