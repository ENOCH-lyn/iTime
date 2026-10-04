# iTime Task And Difficulty Voice Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为任务名称和困难正文提供可恢复、可校对、按时长计费且遵守 24 小时媒体生命周期的 Web 语音转写。

**Architecture:** 浏览器使用 AudioWorklet 采集并生成 16 kHz 单声道 PCM WAV，服务端严格校验后加密保存，通过独立 `transcription` AI 能力和持久作业调用 OpenAI 兼容 `/audio/transcriptions`。转写结果保存在短期 `VoiceTranscription` 对象中，只有用户明确应用后才进入原表单，业务对象仍走原有保存 API。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy、PostgreSQL 18、httpx、Vue 3、TypeScript、Web Audio API、Playwright、pytest。

---

## 文件结构

新增文件：

- `server/src/itime/migrations/versions/0035_voice_transcriptions.py`：能力、配置计价、媒体、作业、成本和临时结果迁移。
- `server/src/itime/migrations/versions/0036_transcription_quota_usage.py`：允许额度预留与账本记录服务端实测音频用量来源。
- `server/src/itime/ai/audio_validation.py`：严格 PCM WAV 解析、时长计算和内容哈希。
- `server/src/itime/ai/transcription_schemas.py`：purpose、resolve 输入和临时结果输出合同。
- `server/src/itime/ai/transcription_router.py`：上传、列表、详情和解决 API。
- `server/src/itime/ai/transcription_service.py`：worker 输入加载、成功写回、失败清理和到期清理。
- `server/src/itime/ai/transcription_provider.py`：OpenAI 兼容 multipart 转写调用与响应校验。
- `server/tests/test_audio_validation.py`：WAV 边界单元测试。
- `server/tests/test_voice_transcription_models.py`：迁移、约束、外键和降级保护。
- `server/tests/test_voice_transcription_api.py`：真实 HTTP/PostgreSQL 接单、读取、解决和权限测试。
- `server/tests/test_voice_transcription_worker.py`：真实 worker、上游故障、主备、成本和生命周期测试。
- `web/src/lib/audio-recorder.ts`：麦克风与 AudioWorklet 生命周期。
- `web/src/lib/pcm-wav.ts`：PCM 分片组装和 WAV 编码。
- `web/src/lib/voice-transcriptions.ts`：转写 API、轮询和类型。
- `web/public/pcm-recorder-worklet.js`：单声道采样与连续 16 kHz 下采样。
- `web/src/components/VoiceTranscriptionControl.vue`：共享录音、转写草稿和解决控件。
- `web/e2e/voice-transcriptions.spec.ts`：任务/困难真实浏览器流程。

修改文件：

- `server/src/itime/ai/models.py`、`schemas.py`、`provider.py`、`worker.py`、`job_service.py`、
  `cost_service.py`、`pricing.py`、`routing_service.py`：增加转写能力及音频计价分支。
- `server/src/itime/ai/admin_router.py`、`admin_cost_router.py`、`governance_models.py`：配置、成本和治理查询。
- `server/src/itime/quota/service.py`、`admin_router.py`：按时长预留、结算及策略币种校验。
- `server/src/itime/main.py`：注册转写路由。
- `server/src/itime/data_export/snapshot.py`：导出未过期转写文字和安全元数据。
- `server/src/itime/account_deletion/quiesce.py`：验证取消作业与媒体清理摘要。
- `server/tests/web_environment.py`：受控 `/audio/transcriptions` 上游和已发布能力。
- `web/src/lib/admin-ai.ts`、`ai-jobs.ts`、`admin-costs.ts`：能力与音频计价类型。
- `web/src/components/AdminAISettings.vue`、`AdminProviderCosts.vue`、`AdminBodyLogs.vue`、
  `AdminQualitySamples.vue`：转写配置和治理筛选。
- `web/src/components/TaskEditor.vue`、`DifficultyPanel.vue`：接入共享控件。
- `web/src/style.css`：稳定录音控件、草稿及响应式样式。
- `doc/README.md`、`README.md`、`designer.md`、`cloud.md`、需求追踪、详细设计、测试、
  产品验收和 Web 实施记录：同步真实实现与证据。

## Task 1: 建立数据库与领域类型

**Files:**
- Create: `server/src/itime/migrations/versions/0035_voice_transcriptions.py`
- Modify: `server/src/itime/ai/models.py`
- Modify: `server/src/itime/ai/governance_models.py`
- Modify: `server/src/itime/ai/schemas.py`
- Create: `server/src/itime/ai/transcription_schemas.py`
- Test: `server/tests/test_voice_transcription_models.py`

- [x] **Step 1: 写迁移失败测试**

覆盖 `transcription` 能力种子、音频/图片媒体互斥、配置计价互斥、作业输入引用、
`VoiceTranscription` 三方所有者外键、resolution 约束、成本计价分支和用户级联。

```python
def test_voice_transcription_constraints_and_owner_links(database_url):
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "SELECT capability FROM ai_config_capabilities "
            "WHERE capability='transcription'"
        ).fetchone() == ("transcription",)
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO temporary_media (
                    id, owner_id, purpose, original_filename, content_type,
                    content_sha256, byte_size, width, height, duration_ms,
                    sample_rate_hz, channel_count, sample_width_bits,
                    encrypted_content, expires_at
                ) VALUES (
                    %s, %s, 'voice_transcription', 'voice.wav', 'audio/wav',
                    %s, 32044, 10, NULL, 1000, 16000, 1, 16, %s, now() + interval '1 day'
                )
                """,
                (uuid4(), owner_id, "a" * 64, b"ciphertext"),
            )
```

- [x] **Step 2: 运行迁移测试并确认失败**

Run: `cd server && uv run pytest -q tests/test_voice_transcription_models.py`

Expected: FAIL，缺少迁移、模型或 `transcription` 枚举。

- [x] **Step 3: 实现迁移和模型**

迁移使用 revision `0035_voice_transcriptions`、down revision `0034_calendar_lifecycle`。模型合同：

```python
class VoiceTranscription(Base):
    __tablename__ = "voice_transcriptions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    job_id: Mapped[UUID] = mapped_column(unique=True)
    media_id: Mapped[UUID] = mapped_column(unique=True)
    purpose: Mapped[str] = mapped_column(String(32))
    transcript: Mapped[str | None] = mapped_column(Text)
    transcript_sha256: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(32))
    resolution: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
```

`Capability` 增加 `"transcription"`；`AIConfigVersion.max_output_tokens` 改为可空并新增
`pricing_basis`、`audio_price_nanos_per_minute`；`TemporaryMedia` 增加四个音频元数据字段；
`AIProviderCost` 增加音频计价快照。迁移 downgrade 在存在转写数据时明确拒绝，避免静默丢数据。

- [x] **Step 4: 验证迁移和模型**

Run: `cd server && uv run pytest -q tests/test_voice_transcription_models.py`

Expected: PASS，包含 upgrade、约束、级联与有数据 downgrade 保护。

- [x] **Step 5: 提交**

```bash
git add server/src/itime/migrations/versions/0035_voice_transcriptions.py \
  server/src/itime/ai/models.py server/src/itime/ai/governance_models.py \
  server/src/itime/ai/schemas.py server/src/itime/ai/transcription_schemas.py \
  server/tests/test_voice_transcription_models.py
git commit -m "feat: 建立语音转写数据合同"
```

## Task 2: 严格校验浏览器 WAV

**Files:**
- Create: `server/src/itime/ai/audio_validation.py`
- Create: `server/tests/test_audio_validation.py`

- [x] **Step 1: 写格式与边界失败测试**

```python
@pytest.mark.parametrize(
    ("channels", "sample_rate", "sample_width", "code"),
    [
        (2, 16000, 2, "audio_channels_unsupported"),
        (1, 48000, 2, "audio_sample_rate_unsupported"),
        (1, 16000, 1, "audio_sample_width_unsupported"),
    ],
)
def test_validate_voice_wav_rejects_noncanonical_pcm(
    channels, sample_rate, sample_width, code
):
    audio = wav_bytes(
        channels=channels,
        sample_rate=sample_rate,
        sample_width=sample_width,
        frames=sample_rate,
    )
    with pytest.raises(AppError) as raised:
        validate_voice_wav(audio, "voice.wav", "audio/wav")
    assert raised.value.code == code
```

另测 249/250 ms、60,000/60,001 ms、空文件、截断 data chunk、重复 data chunk、压缩格式、
伪 MIME、伪扩展和 2 MiB 边界。

- [x] **Step 2: 运行并确认失败**

Run: `cd server && uv run pytest -q tests/test_audio_validation.py`

Expected: FAIL，`itime.ai.audio_validation` 尚不存在。

- [x] **Step 3: 实现严格 RIFF/WAVE 校验**

```python
@dataclass(frozen=True)
class ValidatedVoiceAudio:
    content_type: Literal["audio/wav"]
    byte_size: int
    duration_ms: int
    sample_rate_hz: int
    channel_count: int
    sample_width_bits: int
    sha256: str


def validate_voice_wav(
    contents: bytes,
    filename: str,
    content_type: str,
) -> ValidatedVoiceAudio:
    if len(contents) > MAX_VOICE_BYTES:
        raise audio_error("audio_too_large", "录音不能超过 2 MiB。", status=413)
    header = parse_canonical_pcm_wav(contents)
    if header.sample_rate_hz != 16_000:
        raise audio_error("audio_sample_rate_unsupported", "录音采样率必须为 16 kHz。")
    duration_ms = ceil(header.frame_count * 1000 / header.sample_rate_hz)
    if not MIN_VOICE_DURATION_MS <= duration_ms <= MAX_VOICE_DURATION_MS:
        raise audio_error("audio_duration_invalid", "录音时长必须在 0.25—60 秒之间。")
    return ValidatedVoiceAudio(
        content_type="audio/wav",
        byte_size=len(contents),
        duration_ms=duration_ms,
        sample_rate_hz=header.sample_rate_hz,
        channel_count=header.channels,
        sample_width_bits=header.sample_width_bits,
        sha256=hashlib.sha256(contents).hexdigest(),
    )
```

解析器只接受一个 `fmt ` 和一个 `data` chunk，检查 RIFF 声明长度、块对齐、PCM code 1、
byte rate、block align 和实际帧长度。

- [x] **Step 4: 运行单元测试与静态检查**

Run: `cd server && uv run pytest -q tests/test_audio_validation.py && uv run ruff check src/itime/ai/audio_validation.py tests/test_audio_validation.py`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add server/src/itime/ai/audio_validation.py server/tests/test_audio_validation.py
git commit -m "feat: 校验临时语音录音"
```

## Task 3: 扩展转写配置与按分钟价格

**Files:**
- Modify: `server/src/itime/ai/pricing.py`
- Modify: `server/src/itime/ai/schemas.py`
- Modify: `server/src/itime/ai/admin_router.py`
- Modify: `server/src/itime/ai/provider.py`
- Modify: `server/src/itime/ai/routing_service.py`
- Modify: `server/src/itime/quota/admin_router.py`
- Test: `server/tests/test_ai_config.py`
- Test: `server/tests/test_admin_quota_policy.py`
- Test: `server/tests/test_ai_fallback_routing.py`

- [x] **Step 1: 写配置、测试、发布和路由失败测试**

```python
def test_admin_can_test_and_publish_audio_priced_transcription(admin_client):
    created = admin_client.post(
        "/api/v1/admin/ai-configs",
        json={
            "capability": "transcription",
            "endpoint_url": upstream_url,
            "model": "transcribe-test-model",
            "api_key": "test-key",
            "timeout_seconds": 30,
            "max_output_tokens": None,
            "pricing": {
                "basis": "audio_minute",
                "currency": "CNY",
                "audio_per_minute": "0.006",
            },
        },
    )
    assert created.status_code == 201
    assert created.json()["pricing"]["basis"] == "audio_minute"
```

同时断言转写不能提交 Token 价格、文本能力不能提交音频价格、合成 WAV 发往
`/audio/transcriptions`、备用币种/计价类型不一致被拒绝、已发布转写配置参与新额度策略校验。

- [x] **Step 2: 运行并确认失败**

Run: `cd server && uv run pytest -q tests/test_ai_config.py tests/test_admin_quota_policy.py tests/test_ai_fallback_routing.py`

Expected: FAIL，当前 schema 和配置测试只支持 Token 计价与聊天端点。

- [x] **Step 3: 实现价格与配置分支**

```python
def audio_duration_cost(duration_ms: int, price_nanos_per_minute: int) -> int:
    if not 1 <= duration_ms <= 60_000:
        raise ValueError("音频时长必须位于支持范围内")
    numerator = duration_ms * price_nanos_per_minute
    cost = (numerator + 60_000 - 1) // 60_000
    if cost > MAX_STORED_NANOS:
        raise ValueError("成本超过可存储范围")
    return cost
```

配置 schema 使用 `basis` 判别字段；`test_openai_compatible()` 对 `transcription` 调用独立
`test_transcription_endpoint()`，发送仓库内固定合成语音，不复用聊天 JSON。路由完整性函数按
capability 校验对应价格列。

- [x] **Step 4: 运行相关测试**

Run: `cd server && uv run pytest -q tests/test_ai_config.py tests/test_admin_quota_policy.py tests/test_ai_fallback_routing.py tests/test_ai_usage.py`

Expected: PASS，现有四类能力行为不变。

- [x] **Step 5: 提交**

```bash
git add server/src/itime/ai/pricing.py server/src/itime/ai/schemas.py \
  server/src/itime/ai/admin_router.py server/src/itime/ai/provider.py \
  server/src/itime/ai/routing_service.py server/src/itime/quota/admin_router.py \
  server/tests/test_ai_config.py server/tests/test_admin_quota_policy.py \
  server/tests/test_ai_fallback_routing.py
git commit -m "feat: 配置语音转写与按分钟价格"
```

## Task 4: 实现转写接单与本人查询

**Files:**
- Create: `server/src/itime/ai/transcription_router.py`
- Modify: `server/src/itime/main.py`
- Modify: `server/src/itime/quota/service.py`
- Modify: `server/src/itime/config.py`
- Test: `server/tests/test_voice_transcription_api.py`

- [x] **Step 1: 写接单 API 失败测试**

```python
def test_transcription_upload_is_atomic_replayable_and_owner_scoped(
    client, database_url, published_transcription_config
):
    operation_id = str(uuid4())
    response = client.post(
        "/api/v1/voice-transcriptions"
        "?purpose=task_title&filename=voice.wav",
        content=wav_bytes(duration_ms=1000),
        headers={
            "Content-Type": "audio/wav",
            "X-Operation-ID": operation_id,
        },
    )
    assert response.status_code == 202
    replay = client.post(
        "/api/v1/voice-transcriptions"
        "?purpose=task_title&filename=voice.wav",
        content=wav_bytes(duration_ms=1000),
        headers={
            "Content-Type": "audio/wav",
            "X-Operation-ID": operation_id,
        },
    )
    assert replay.json() == response.json()
```

另测未登录、AI 冻结、缺时区、无配置、额度不足、三活动作业、10 分钟 20 次、部署队列上限、
异内容重放、另一账号读取和输入失败无数据库残留。

- [x] **Step 2: 运行并确认失败**

Run: `cd server && uv run pytest -q tests/test_voice_transcription_api.py`

Expected: FAIL，路由不存在。

- [x] **Step 3: 实现接单、列表和详情**

```python
@router.post("", status_code=202)
async def create_voice_transcription(
    request: Request,
    purpose: Annotated[TranscriptionPurpose, Query()],
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    operation_id: Annotated[UUID, Header(alias="X-Operation-ID")],
) -> dict:
    contents = await request.body()
    audio = await asyncio.to_thread(
        validate_voice_wav, contents, filename, request.headers.get("content-type", "")
    )
    encrypted = await asyncio.to_thread(
        request.app.state.security.ai_cipher.encrypt, contents
    )
    async with authorized(request, write=True) as identity:
        return await admit_voice_transcription(
            identity, request, purpose, filename, audio, encrypted, operation_id
        )
```

`estimate_audio_reservation_budget()` 按每条冻结路由的音频分钟价计算并取最大值；新增配置
`ai_transcription_queue_limit=500`。列表默认只返回最近 20 条，支持 purpose、稳定游标和本人范围。

- [x] **Step 4: 验证 API**

Run: `cd server && uv run pytest -q tests/test_voice_transcription_api.py tests/test_quota_service.py`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add server/src/itime/ai/transcription_router.py server/src/itime/main.py \
  server/src/itime/quota/service.py server/src/itime/config.py \
  server/tests/test_voice_transcription_api.py
git commit -m "feat: 接收并恢复语音转写作业"
```

## Task 5: 执行转写作业并记录实际成本

**Files:**
- Create: `server/src/itime/ai/transcription_provider.py`
- Create: `server/src/itime/ai/transcription_service.py`
- Modify: `server/src/itime/ai/worker.py`
- Modify: `server/src/itime/ai/cost_service.py`
- Modify: `server/src/itime/ai/usage.py`
- Modify: `server/src/itime/ai/governance_service.py`
- Create: `server/src/itime/migrations/versions/0036_transcription_quota_usage.py`
- Modify: `server/src/itime/quota/models.py`
- Modify: `server/src/itime/quota/service.py`
- Test: `server/tests/test_voice_transcription_worker.py`
- Test: `server/tests/test_ai_provider_cost_models.py`
- Test: `server/tests/test_admin_provider_costs.py`

- [x] **Step 1: 写真实上游和成本失败测试**

```python
def test_worker_posts_multipart_and_settles_only_successful_route(
    transcription_case,
):
    job_id = transcription_case.enqueue(duration_ms=1500)
    transcription_case.primary.respond(429)
    transcription_case.fallback.respond_json(
        {"text": "周五前完成论文提纲", "language": "zh"}
    )
    transcription_case.run_worker_twice()
    result = transcription_case.get(job_id)
    assert result["job"]["status"] == "succeeded"
    assert result["transcript"] == "周五前完成论文提纲"
    assert transcription_case.user_charge_count(job_id) == 1
    assert transcription_case.provider_cost_count(job_id) == 2
```

测试同时断言 multipart 文件字节、固定路径、请求头不入日志、空/超长/控制字符结果、断连、
慢响应、401、429、5xx、租约失效、取消晚到、质量开关和主备实际配置价格。

- [x] **Step 2: 运行并确认失败**

Run: `cd server && uv run pytest -q tests/test_voice_transcription_worker.py tests/test_ai_provider_cost_models.py tests/test_admin_provider_costs.py`

Expected: FAIL，worker 不识别 `transcription`。

- [x] **Step 3: 实现 provider 和 worker 分支**

```python
async def transcribe_audio(
    config: TranscriptionProviderConfig,
    settings: Settings,
    cipher: Fernet,
    *,
    audio: bytes,
    filename: str,
    timeout_seconds: float,
    on_request_started: Callable[[], Awaitable[bool]],
) -> TranscriptionProviderResult:
    endpoint = config.endpoint_url + "/audio/transcriptions"
    files = {"file": (filename, audio, "audio/wav")}
    data = {"model": config.model, "response_format": "json"}
    return await bounded_transcription_request(
        endpoint, config, settings, cipher, files, data,
        timeout_seconds=timeout_seconds,
        on_request_started=on_request_started,
    )
```

`load_transcription_work()` 在事务内读取配置/媒体/结果后释放事务，再解密与验哈希；
`complete_transcription_job()` 在一个事务中校验正文、完成 attempt/body log/provider cost、
结算额度、写临时文字并完成作业。失败路径在最终终态清媒体。

- [x] **Step 4: 验证 worker 与现有能力回归**

Run: `cd server && uv run pytest -q tests/test_voice_transcription_worker.py tests/test_ai_provider_cost_models.py tests/test_admin_provider_costs.py tests/test_ai_conversation_worker.py tests/test_planning_worker.py tests/test_review_worker.py`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add server/src/itime/ai/transcription_provider.py \
  server/src/itime/ai/transcription_service.py server/src/itime/ai/worker.py \
  server/src/itime/ai/cost_service.py server/src/itime/ai/usage.py \
  server/src/itime/ai/governance_service.py \
  server/tests/test_voice_transcription_worker.py \
  server/tests/test_ai_provider_cost_models.py \
  server/tests/test_admin_provider_costs.py
git commit -m "feat: 执行并结算语音转写"
```

## Task 6: 完成解决、清理、导出和注销生命周期

**Files:**
- Modify: `server/src/itime/ai/transcription_router.py`
- Modify: `server/src/itime/ai/transcription_service.py`
- Modify: `server/src/itime/ai/job_service.py`
- Modify: `server/src/itime/ai/worker.py`
- Modify: `server/src/itime/data_export/snapshot.py`
- Modify: `server/src/itime/account_deletion/quiesce.py`
- Test: `server/tests/test_voice_transcription_api.py`
- Test: `server/tests/test_voice_transcription_worker.py`
- Test: `server/tests/test_data_export_snapshot.py`
- Test: `server/tests/test_account_deletion_api.py`

- [x] **Step 1: 写生命周期失败测试**

```python
def test_accept_deletes_audio_but_keeps_text_until_fixed_expiry(case):
    item = case.ready_transcription("写完实验报告")
    accepted = case.resolve(
        item,
        action="accept",
        transcript_sha256=item["transcript_sha256"],
    )
    assert accepted["resolution"] == "accepted"
    assert accepted["transcript"] == "写完实验报告"
    assert case.media_ciphertext(item["media_id"]) is None
    case.run_cleanup(at=item["expires_at"])
    assert case.get(item["id"])["transcript"] is None
```

另测 discard 取消活动作业、成功/取消竞态、重复解决、不同解决冲突、到期 worker 双实例、
晚到结果、导出不含音频密文、注销申请立即清媒体。

- [x] **Step 2: 运行并确认失败**

Run: `cd server && uv run pytest -q tests/test_voice_transcription_api.py tests/test_voice_transcription_worker.py tests/test_data_export_snapshot.py tests/test_account_deletion_api.py`

Expected: FAIL，resolve 和结果清理尚未实现。

- [x] **Step 3: 实现统一解决和清理**

```python
async def resolve_transcription(session, owner_id, item_id, data):
    job, item, media = await lock_transcription_graph(
        session, owner_id, item_id
    )
    if data.action == "accept":
        require_ready_hash(item, data.transcript_sha256)
        item.resolution = "accepted"
        delete_media(media, reason="accepted")
    else:
        await cancel_job(session, job)
        await release_reservation(session, job=job, reason="cancelled")
        item.resolution = "discarded"
        item.transcript = None
        item.text_purged_at = now()
        delete_media(media, reason="discarded")
    item.resolved_at = now()
    item.version += 1
    return item
```

清理 worker 每批最多 100 条，以 `AIJob → VoiceTranscription → TemporaryMedia` 顺序处理。导出只
增加临时结果白名单，不导出 `encrypted_content`。注销摘要继续统计统一临时媒体数量。

- [x] **Step 4: 验证生命周期**

Run: `cd server && uv run pytest -q tests/test_voice_transcription_api.py tests/test_voice_transcription_worker.py tests/test_data_export_snapshot.py tests/test_account_deletion_api.py`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add server/src/itime/ai/transcription_router.py \
  server/src/itime/ai/transcription_service.py server/src/itime/ai/job_service.py \
  server/src/itime/ai/worker.py server/src/itime/data_export/snapshot.py \
  server/src/itime/account_deletion/quiesce.py \
  server/tests/test_voice_transcription_api.py \
  server/tests/test_voice_transcription_worker.py \
  server/tests/test_data_export_snapshot.py \
  server/tests/test_account_deletion_api.py
git commit -m "feat: 管理语音转写临时生命周期"
```

## Task 7: 在后台管理转写能力与音频成本

**Files:**
- Modify: `web/src/lib/admin-ai.ts`
- Modify: `web/src/lib/admin-costs.ts`
- Modify: `web/src/lib/admin-governance.ts`
- Modify: `web/src/components/AdminAISettings.vue`
- Modify: `web/src/components/AdminProviderCosts.vue`
- Modify: `web/src/components/AdminBodyLogs.vue`
- Modify: `web/src/components/AdminQualitySamples.vue`
- Modify: `web/src/style.css`
- Test: `web/e2e/admin-management.spec.ts`
- Test: `web/e2e/privacy-governance.spec.ts`

- [x] **Step 1: 扩展后台 E2E 失败断言**

```typescript
await page.getByLabel('能力').getByText('语音转写').click()
await page.getByLabel('音频价格（每分钟）').fill('0.006')
await page.getByRole('button', { name: '保存新版本' }).click()
await expect(page.getByText('音频 0.006 / 分钟')).toBeVisible()
```

测试创建、合成测试、发布、回滚、备用顺序、字段互斥，以及成本/正文日志/质检筛选中的转写标签和
音频时长。

- [x] **Step 2: 运行并确认失败**

Run: `cd web && pnpm exec playwright test e2e/admin-management.spec.ts e2e/privacy-governance.spec.ts --project=desktop`

Expected: FAIL，后台尚无语音转写能力。

- [x] **Step 3: 实现动态配置与成本展示**

```typescript
export type AIConfigCapability =
  | 'calendar_screenshot'
  | 'conversation'
  | 'planning'
  | 'review'
  | 'transcription'

export type AIConfigPricing =
  | {
      basis: 'token'
      currency: string
      input_per_million_tokens: string
      cached_input_per_million_tokens: string
      output_per_million_tokens: string
    }
  | {
      basis: 'audio_minute'
      currency: string
      audio_per_minute: string
    }
```

`AdminAISettings` 在 capability 为 transcription 时隐藏 Token 和最大输出字段，显示每分钟音频
价格；历史列表按 `pricing.basis` 展示。治理和成本组件补齐能力筛选与 `billing_basis` 分支。

- [x] **Step 4: 运行后台 UI 测试与构建**

Run: `cd web && pnpm exec playwright test e2e/admin-management.spec.ts e2e/privacy-governance.spec.ts --project=desktop && pnpm build`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add web/src/lib/admin-ai.ts web/src/lib/admin-costs.ts \
  web/src/lib/admin-governance.ts web/src/components/AdminAISettings.vue \
  web/src/components/AdminProviderCosts.vue web/src/components/AdminBodyLogs.vue \
  web/src/components/AdminQualitySamples.vue web/src/style.css \
  web/e2e/admin-management.spec.ts web/e2e/privacy-governance.spec.ts
git commit -m "feat: 管理语音转写配置与成本"
```

## Task 8: 实现浏览器录音与共享校对控件

**Files:**
- Create: `web/public/pcm-recorder-worklet.js`
- Create: `web/src/lib/audio-recorder.ts`
- Create: `web/src/lib/pcm-wav.ts`
- Create: `web/src/lib/voice-transcriptions.ts`
- Create: `web/src/components/VoiceTranscriptionControl.vue`
- Modify: `web/playwright.config.ts`
- Modify: `web/src/lib/ai-jobs.ts`
- Modify: `web/src/style.css`
- Test: `web/e2e/voice-transcriptions.spec.ts`

- [x] **Step 1: 写控件状态失败测试**

```typescript
await page.getByRole('button', { name: '开始语音输入' }).click()
await expect(page.getByRole('status')).toContainText('正在录音')
await page.getByRole('button', { name: '停止录音' }).click()
await expect(page.getByLabel('转写草稿')).toHaveValue('周五前完成论文提纲')
await expect(page.getByRole('button', { name: '替换当前内容' })).toBeVisible()
```

测试注入真实 MediaStream 假设备；同时覆盖权限拒绝、自动停止、组件卸载关闭 track、上传未知重放、
取消、恢复、编辑草稿和超限阻断。

- [x] **Step 2: 运行并确认失败**

Run: `cd web && pnpm exec playwright test e2e/voice-transcriptions.spec.ts --project=desktop`

Expected: FAIL，共享控件尚不存在。

- [x] **Step 3: 实现 PCM/WAV 与录音状态机**

```typescript
export interface VoiceRecorder {
  start(): Promise<void>
  stop(): Promise<Blob>
  cancel(): Promise<void>
  readonly state: 'idle' | 'requesting' | 'recording' | 'preparing'
}

export function encodePcm16Wav(samples: Int16Array, sampleRate = 16_000): Blob {
  const bytes = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(bytes)
  writeCanonicalWavHeader(view, samples.length, sampleRate)
  samples.forEach((sample, index) => view.setInt16(44 + index * 2, sample, true))
  return new Blob([bytes], { type: 'audio/wav' })
}
```

AudioWorklet 只输出 16 kHz mono PCM16 分片。组件拥有唯一活动 recorder、轮询定时器和
AbortController；卸载时终止全部资源。组件通过 props 接收 purpose、fieldLimit、currentText，
通过 emits 输出 replace/append。

- [x] **Step 4: 验证共享控件和构建**

Run: `cd web && pnpm exec playwright test e2e/voice-transcriptions.spec.ts --project=desktop && pnpm build`

Expected: 录音控件专项通过，TypeScript 无错误。

- [x] **Step 5: 提交**

```bash
git add web/public/pcm-recorder-worklet.js web/src/lib/audio-recorder.ts \
  web/src/lib/pcm-wav.ts web/src/lib/voice-transcriptions.ts \
  web/src/lib/ai-jobs.ts web/src/components/VoiceTranscriptionControl.vue \
  web/src/style.css web/playwright.config.ts web/e2e/voice-transcriptions.spec.ts
git commit -m "feat: 提供可恢复语音转写控件"
```

## Task 9: 接入任务名称

**Files:**
- Modify: `web/src/components/TaskEditor.vue`
- Test: `web/e2e/voice-transcriptions.spec.ts`

- [x] **Step 1: 写任务流程失败测试**

```typescript
await page.getByRole('button', { name: '新建任务' }).click()
await page.getByRole('button', { name: '开始语音输入' }).click()
await page.getByRole('button', { name: '停止录音' }).click()
await page.getByLabel('转写草稿').fill('周五前完成论文提纲')
await page.getByRole('button', { name: '应用到任务名称' }).click()
await expect(page.getByLabel('任务名称')).toHaveValue('周五前完成论文提纲')
await expect(page.getByText('尚未保存')).toBeVisible()
```

另测已有标题替换/追加、转写期间继续编辑不被覆盖、200 字限制、创建失败保留文字、已归档和回收站
不显示入口。

- [x] **Step 2: 运行并确认失败**

Run: `cd web && pnpm exec playwright test e2e/voice-transcriptions.spec.ts --project=desktop -g "任务"`

Expected: FAIL，任务编辑器尚未接入。

- [x] **Step 3: 接入任务表单**

```vue
<VoiceTranscriptionControl
  v-if="editable"
  purpose="task_title"
  target-label="任务名称"
  :field-limit="200"
  :current-text="values.title"
  @replace="values.title = $event"
  @append="values.title = `${values.title}\\n${$event}`.trim()"
/>
```

应用后保持现有 dirty、operation ID、创建/更新和版本冲突行为；转写控件不得直接调用
`createTask()` 或 `updateTask()`。

- [x] **Step 4: 验证任务语音流程**

Run: `cd web && pnpm exec playwright test e2e/voice-transcriptions.spec.ts --project=desktop -g "任务"`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add web/src/components/TaskEditor.vue web/e2e/voice-transcriptions.spec.ts
git commit -m "feat: 通过语音填写任务名称"
```

## Task 10: 接入困难正文

**Files:**
- Modify: `web/src/components/DifficultyPanel.vue`
- Test: `web/e2e/voice-transcriptions.spec.ts`

- [x] **Step 1: 写困难流程失败测试**

```typescript
await page.getByRole('tab', { name: '困难' }).click()
await page.getByRole('button', { name: '开始语音输入' }).click()
await page.getByRole('button', { name: '停止录音' }).click()
await page.getByLabel('转写草稿').fill('不知道如何拆分这一章')
await page.getByRole('button', { name: '应用到困难正文' }).click()
await page.getByRole('button', { name: '记录困难' }).click()
await expect(page.getByText('不知道如何拆分这一章')).toBeVisible()
```

另测关联当前专注、编辑已有困难、2000 字限制、可选分类/情绪不被推断、丢弃不创建困难、不自动
进入 AI 对话。

- [x] **Step 2: 运行并确认失败**

Run: `cd web && pnpm exec playwright test e2e/voice-transcriptions.spec.ts --project=desktop -g "困难"`

Expected: FAIL，困难表单尚未接入。

- [x] **Step 3: 接入困难表单**

```vue
<VoiceTranscriptionControl
  purpose="difficulty_content"
  target-label="困难正文"
  :field-limit="2000"
  :current-text="content"
  @replace="content = $event"
  @append="content = `${content}\\n${$event}`.trim()"
/>
```

切换回收站或重置表单时主动丢弃本地未解决控件状态，但不能静默取消已经提交到服务端的作业；用户
重新进入记录范围时可恢复最近未解决结果。

- [x] **Step 4: 验证困难语音流程**

Run: `cd web && pnpm exec playwright test e2e/voice-transcriptions.spec.ts --project=desktop -g "困难"`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add web/src/components/DifficultyPanel.vue web/e2e/voice-transcriptions.spec.ts
git commit -m "feat: 通过语音记录困难"
```

## Task 11: 补齐专项服务与浏览器故障矩阵

**Files:**
- Modify: `server/tests/test_voice_transcription_api.py`
- Modify: `server/tests/test_voice_transcription_worker.py`
- Modify: `server/tests/test_account_deletion_worker.py`
- Modify: `server/tests/web_environment.py`
- Modify: `web/playwright.config.ts`
- Modify: `web/e2e/voice-transcriptions.spec.ts`

- [x] **Step 1: 扩展真实故障测试**

```python
@pytest.mark.parametrize(
    ("status", "expected_attempts", "expected_job_status"),
    [(429, 2, "succeeded"), (401, 1, "failed"), (500, 2, "succeeded")],
)
def test_transcription_route_failure_policy(
    transcription_case, status, expected_attempts, expected_job_status
):
    job_id = transcription_case.enqueue(duration_ms=1000)
    transcription_case.primary.respond(status)
    transcription_case.fallback.respond_json({"text": "恢复成功"})
    transcription_case.run_until_terminal(job_id)
    assert transcription_case.job_status(job_id) == expected_job_status
    assert transcription_case.attempt_count(job_id) == expected_attempts
```

浏览器测试补齐桌面、手机、平板、320px 最大字号深色、第二上下文恢复、网络失败后原 operation ID
重放、权限拒绝和假麦克风轨道终止检查。

- [x] **Step 2: 运行专项并处理失败**

Run: `cd server && uv run pytest -q tests/test_audio_validation.py tests/test_voice_transcription_models.py tests/test_voice_transcription_api.py tests/test_voice_transcription_worker.py tests/test_account_deletion_worker.py`

Run: `cd web && pnpm exec playwright test e2e/voice-transcriptions.spec.ts`

Expected: 两组全部 PASS；每个视口检查 axe、控制台、框架错误层和根级横向溢出。

- [x] **Step 3: 固化受控测试上游与浏览器音频夹具**

`server/tests/web_environment.py` 对 `/audio/transcriptions` 解析 multipart，仅验证固定测试模型、
规范 WAV 和 Bearer 头，返回：

```json
{
  "text": "周五前完成论文提纲",
  "language": "zh",
  "duration": 1.0
}
```

Playwright Chromium 增加：

```typescript
launchOptions: {
  args: [
    '--use-fake-device-for-media-stream',
    '--use-fake-ui-for-media-stream',
  ],
}
```

- [x] **Step 4: 重跑专项**

Run: `cd server && uv run pytest -q tests/test_audio_validation.py tests/test_voice_transcription_models.py tests/test_voice_transcription_api.py tests/test_voice_transcription_worker.py tests/test_account_deletion_worker.py`

Run: `cd web && pnpm exec playwright test e2e/voice-transcriptions.spec.ts`

Expected: 全部 PASS，测试数据库和浏览器进程正常清理。

- [x] **Step 5: 提交**

```bash
git add server/tests/test_voice_transcription_api.py \
  server/tests/test_voice_transcription_worker.py \
  server/tests/test_account_deletion_worker.py server/tests/web_environment.py \
  web/playwright.config.ts web/e2e/voice-transcriptions.spec.ts
git commit -m "test: 验证语音转写完整流程"
```

## Task 12: 全量验证、文档和运行收口

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `designer.md`
- Modify: `cloud.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/设计/iTime-AI作业与临时媒体设计.md`
- Modify: `doc/设计/iTime-AI额度与成本账本设计.md`
- Modify: `doc/设计/iTime-执行与困难设计.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`

- [x] **Step 1: 执行服务端完整质量门**

Run: `cd server && uv run ruff check .`

Expected: PASS。

Run: `cd server && uv run pytest -q`

Expected: 全量 PASS；记录实际通过数和耗时。

Run: `cd server && uv build --no-sources`

Expected: wheel 与 sdist 构建成功。

- [x] **Step 2: 验证迁移与独立 worker**

Run: `cd server && uv run alembic upgrade head`

Expected: 开发库位于 `0036_transcription_quota_usage (head)`。

Run: `cd server && uv run python -m itime.ai.worker --once`

Expected: 无待处理作业时正常退出，媒体/临时文字清理路径可执行。

- [x] **Step 3: 执行前端完整质量门**

Run: `cd web && pnpm build`

Expected: TypeScript 检查与生产构建 PASS。

Run: `cd web && pnpm test:e2e`

Expected: Chromium 全量 PASS；记录通过、跳过、耗时和证据路径。

- [x] **Step 4: 核验数据库与进程清理**

Run: `psql \"$ITIME_ADMIN_DATABASE_URL\" -Atc \"SELECT count(*) FROM pg_database WHERE datname LIKE 'itime_test_%'\"`

Expected: `0`。

确认 Playwright 启动的 API、worker、预览服务和受控上游均已退出；开发 API 的 `/api/ready` 返回
新 migration head。

- [x] **Step 5: 同步真实证据**

文档只写实际命令结果与当前提交 SHA。把任务和困难语音从“未实现”改为“已实现并动态验证”，
同时保留正式第三方模型、Edge、实体设备、性能、备份恢复、生产部署和其余 Web 首版缺口。

- [x] **Step 6: 提交文档**

```bash
git add README.md doc/README.md designer.md cloud.md \
  doc/需求/iTime-需求追踪矩阵.md doc/设计/iTime-详细设计文档.md \
  doc/设计/iTime-AI作业与临时媒体设计.md \
  doc/设计/iTime-AI额度与成本账本设计.md \
  doc/设计/iTime-执行与困难设计.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md \
  doc/验收/iTime-Web实施与验证.md
git commit -m "docs: 记录语音转写实施证据"
```
