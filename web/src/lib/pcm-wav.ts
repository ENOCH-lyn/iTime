export const VOICE_SAMPLE_RATE = 16_000
export const VOICE_SAMPLE_WIDTH_BYTES = 2
export const VOICE_WAV_HEADER_BYTES = 44

function writeAscii(view: DataView, offset: number, value: string) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index))
  }
}

export function mergePcm16Chunks(chunks: readonly Int16Array[]) {
  const sampleCount = chunks.reduce((total, chunk) => total + chunk.length, 0)
  const samples = new Int16Array(sampleCount)
  let offset = 0
  for (const chunk of chunks) {
    samples.set(chunk, offset)
    offset += chunk.length
  }
  return samples
}

export function encodePcm16Wav(
  samples: Int16Array,
  sampleRate = VOICE_SAMPLE_RATE,
) {
  const dataBytes = samples.length * VOICE_SAMPLE_WIDTH_BYTES
  const bytes = new ArrayBuffer(VOICE_WAV_HEADER_BYTES + dataBytes)
  const view = new DataView(bytes)
  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, 36 + dataBytes, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * VOICE_SAMPLE_WIDTH_BYTES, true)
  view.setUint16(32, VOICE_SAMPLE_WIDTH_BYTES, true)
  view.setUint16(34, VOICE_SAMPLE_WIDTH_BYTES * 8, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, dataBytes, true)
  for (let index = 0; index < samples.length; index += 1) {
    view.setInt16(
      VOICE_WAV_HEADER_BYTES + index * VOICE_SAMPLE_WIDTH_BYTES,
      samples[index],
      true,
    )
  }
  return new Blob([bytes], { type: 'audio/wav' })
}

export function voiceWavDurationMs(blob: Blob) {
  const sampleBytes = Math.max(0, blob.size - VOICE_WAV_HEADER_BYTES)
  return Math.ceil(
    sampleBytes * 1000 / (VOICE_SAMPLE_RATE * VOICE_SAMPLE_WIDTH_BYTES),
  )
}
