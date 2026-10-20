import { uploadFormApi } from './api'

export async function transcribeVoice(blob: Blob) {
  const form = new FormData()
  form.set('audio', blob, 'voice.webm')
  return uploadFormApi<{ text: string }>(
    '/ai/voice/transcribe',
    form,
    crypto.randomUUID(),
    { timeoutMs: 90_000 },
  )
}