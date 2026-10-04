const TARGET_SAMPLE_RATE = 16_000
const CHUNK_SAMPLE_COUNT = 800

class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this.targetSampleRate =
      options.processorOptions?.targetSampleRate ?? TARGET_SAMPLE_RATE
    this.inputSamplesPerOutput = sampleRate / this.targetSampleRate
    this.nextOutputPosition = 0
    this.totalInputSamples = 0
    this.previousSample = 0
    this.hasPreviousSample = false
    this.pending = []
    this.active = true
    this.port.onmessage = event => {
      if (event.data?.type !== 'stop') return
      this.flush()
      this.active = false
      this.port.postMessage({ type: 'stopped' })
    }
  }

  push(value) {
    const bounded = Math.max(-1, Math.min(1, value))
    this.pending.push(
      bounded < 0
        ? Math.round(bounded * 32_768)
        : Math.round(bounded * 32_767),
    )
    if (this.pending.length >= CHUNK_SAMPLE_COUNT) this.flush()
  }

  flush() {
    if (!this.pending.length) return
    const samples = Int16Array.from(this.pending)
    this.pending = []
    this.port.postMessage(
      { type: 'chunk', samples: samples.buffer },
      [samples.buffer],
    )
  }

  process(inputs, outputs) {
    for (const output of outputs) {
      for (const channel of output) channel.fill(0)
    }
    if (!this.active) return false

    const channels = inputs[0]
    const frameCount = channels?.[0]?.length ?? 0
    for (let frame = 0; frame < frameCount; frame += 1) {
      let currentSample = 0
      for (const channel of channels) currentSample += channel[frame] ?? 0
      currentSample /= channels.length

      const currentPosition = this.totalInputSamples
      if (!this.hasPreviousSample) {
        this.previousSample = currentSample
        this.hasPreviousSample = true
        if (this.nextOutputPosition === 0) {
          this.push(currentSample)
          this.nextOutputPosition += this.inputSamplesPerOutput
        }
      } else {
        while (this.nextOutputPosition <= currentPosition) {
          const fraction = this.nextOutputPosition - (currentPosition - 1)
          this.push(
            this.previousSample
              + (currentSample - this.previousSample) * fraction,
          )
          this.nextOutputPosition += this.inputSamplesPerOutput
        }
        this.previousSample = currentSample
      }
      this.totalInputSamples += 1
    }
    return true
  }
}

registerProcessor('itime-pcm-recorder', PcmRecorderProcessor)
