import { encodePcm16Wav, mergePcm16Chunks } from './pcm-wav'

export type VoiceRecorderState =
  | 'idle'
  | 'requesting'
  | 'recording'
  | 'preparing'

export type VoiceRecorderFailureCode =
  | 'audio_not_supported'
  | 'audio_interrupted'
  | 'audio_stop_failed'
  | 'audio_cancelled'

export class VoiceRecorderError extends Error {
  constructor(public code: VoiceRecorderFailureCode, message: string) {
    super(message)
  }
}

export interface VoiceRecorder {
  start(): Promise<void>
  stop(): Promise<Blob>
  cancel(): Promise<void>
  readonly state: VoiceRecorderState
}

type InterruptedHandler = (error: VoiceRecorderError) => void

export class BrowserVoiceRecorder implements VoiceRecorder {
  private currentState: VoiceRecorderState = 'idle'
  private stream: MediaStream | null = null
  private context: AudioContext | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private worklet: AudioWorkletNode | null = null
  private silence: GainNode | null = null
  private chunks: Int16Array[] = []
  private generation = 0
  private stopResolver: (() => void) | null = null
  private stopRejecter: ((reason: unknown) => void) | null = null

  constructor(private readonly onInterrupted?: InterruptedHandler) {}

  get state() {
    return this.currentState
  }

  async start() {
    if (this.currentState !== 'idle') {
      throw new VoiceRecorderError(
        'audio_not_supported',
        '已有录音正在进行。',
      )
    }
    if (
      !window.isSecureContext
      || !navigator.mediaDevices?.getUserMedia
      || !window.AudioContext
      || !window.AudioWorkletNode
    ) {
      throw new VoiceRecorderError(
        'audio_not_supported',
        '当前浏览器无法安全录音，请使用最新版 Chrome 或 Edge。',
      )
    }

    const generation = ++this.generation
    this.currentState = 'requesting'
    this.chunks = []
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      if (generation !== this.generation) {
        for (const track of stream.getTracks()) track.stop()
        throw new VoiceRecorderError('audio_cancelled', '录音已取消。')
      }
      this.stream = stream
      for (const track of stream.getAudioTracks()) {
        track.addEventListener('ended', this.trackEnded)
      }

      const context = new AudioContext({ latencyHint: 'interactive' })
      this.context = context
      if (!context.audioWorklet) {
        throw new VoiceRecorderError(
          'audio_not_supported',
          '当前浏览器缺少录音工作线程支持。',
        )
      }
      await context.audioWorklet.addModule('/pcm-recorder-worklet.js')
      if (generation !== this.generation) {
        throw new VoiceRecorderError('audio_cancelled', '录音已取消。')
      }

      const source = context.createMediaStreamSource(stream)
      const worklet = new AudioWorkletNode(context, 'itime-pcm-recorder', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: { targetSampleRate: 16_000 },
      })
      const silence = context.createGain()
      silence.gain.value = 0
      worklet.port.onmessage = this.workletMessage
      worklet.onprocessorerror = this.processorFailed
      source.connect(worklet)
      worklet.connect(silence)
      silence.connect(context.destination)
      this.source = source
      this.worklet = worklet
      this.silence = silence
      await context.resume()
      if (generation !== this.generation) {
        throw new VoiceRecorderError('audio_cancelled', '录音已取消。')
      }
      this.currentState = 'recording'
    } catch (error) {
      if (generation === this.generation) {
        this.currentState = 'idle'
        await this.teardown()
      }
      throw error
    }
  }

  async stop() {
    if (this.currentState !== 'recording' || !this.worklet) {
      throw new VoiceRecorderError(
        'audio_stop_failed',
        '当前没有可停止的录音。',
      )
    }
    const generation = this.generation
    this.currentState = 'preparing'
    try {
      await new Promise<void>((resolve, reject) => {
        this.stopResolver = resolve
        this.stopRejecter = reject
        this.worklet?.port.postMessage({ type: 'stop' })
        window.setTimeout(() => {
          if (!this.stopRejecter) return
          const rejectPending = this.stopRejecter
          this.stopResolver = null
          this.stopRejecter = null
          rejectPending(new VoiceRecorderError(
            'audio_stop_failed',
            '录音整理超时，请重新录制。',
          ))
        }, 1_500)
      })
      if (generation !== this.generation) {
        throw new VoiceRecorderError('audio_cancelled', '录音已取消。')
      }
      return encodePcm16Wav(mergePcm16Chunks(this.chunks))
    } finally {
      if (generation === this.generation) this.currentState = 'idle'
      await this.teardown()
    }
  }

  async cancel() {
    this.generation += 1
    this.currentState = 'idle'
    this.chunks = []
    if (this.stopRejecter) {
      this.stopRejecter(
        new VoiceRecorderError('audio_cancelled', '录音已取消。'),
      )
    }
    this.stopResolver = null
    this.stopRejecter = null
    this.worklet?.port.postMessage({ type: 'stop' })
    await this.teardown()
  }

  private workletMessage = (event: MessageEvent) => {
    if (event.data?.type === 'chunk' && event.data.samples instanceof ArrayBuffer) {
      this.chunks.push(new Int16Array(event.data.samples))
      return
    }
    if (event.data?.type === 'stopped' && this.stopResolver) {
      const resolve = this.stopResolver
      this.stopResolver = null
      this.stopRejecter = null
      resolve()
    }
  }

  private trackEnded = () => {
    if (!['requesting', 'recording'].includes(this.currentState)) return
    void this.interrupt(new VoiceRecorderError(
      'audio_interrupted',
      '麦克风连接已中断，请检查设备后重新录制。',
    ))
  }

  private processorFailed = () => {
    if (!['requesting', 'recording', 'preparing'].includes(this.currentState)) return
    void this.interrupt(new VoiceRecorderError(
      'audio_interrupted',
      '录音处理已中断，请重新录制。',
    ))
  }

  private async interrupt(error: VoiceRecorderError) {
    this.generation += 1
    this.currentState = 'idle'
    this.chunks = []
    if (this.stopRejecter) this.stopRejecter(error)
    this.stopResolver = null
    this.stopRejecter = null
    await this.teardown()
    this.onInterrupted?.(error)
  }

  private async teardown() {
    const stream = this.stream
    const context = this.context
    const source = this.source
    const worklet = this.worklet
    const silence = this.silence
    this.stream = null
    this.context = null
    this.source = null
    this.worklet = null
    this.silence = null

    for (const track of stream?.getAudioTracks() ?? []) {
      track.removeEventListener('ended', this.trackEnded)
    }
    source?.disconnect()
    worklet?.disconnect()
    worklet?.port.close()
    silence?.disconnect()
    for (const track of stream?.getTracks() ?? []) track.stop()
    if (context && context.state !== 'closed') {
      await context.close().catch(() => undefined)
    }
  }
}

export function createVoiceRecorder(onInterrupted?: InterruptedHandler) {
  return new BrowserVoiceRecorder(onInterrupted)
}
