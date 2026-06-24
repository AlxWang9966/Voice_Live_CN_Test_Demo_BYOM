# Voice Live BYOM 客户测试上手指南

本文档面向客户本地测试。目标是让客户拿到文件夹后，只需要准备 Speech / Voice Live 配置和模型 API key，就能在网页里完成本地语音测试、保存指标和对比结果。

## 1. 测试目标

本 demo 验证以下链路：

```text
Speech / Voice Live + BYOM 模型 + 本机麦克风/扬声器 = 实时语音对话
```

| 能力 | 说明 |
| --- | --- |
| 语音输入 | 本机麦克风音频进入 Voice Live。 |
| ASR / VAD | Voice Live 识别说话开始、结束和转写。 |
| BYOM | Voice Live 把请求转发给客户自带模型。 |
| TTS | 模型返回文本后，Voice Live 生成语音。 |
| 指标对比 | 网页保存每次测试结果，支持详情、对比和 CSV 导出。 |

## 2. 文件说明

| 文件 | 用途 |
| --- | --- |
| `start_web_console.cmd` | Windows 双击启动入口。 |
| `web_test_server.py` | 本地测试服务器。 |
| `VoiceLive_BYOM_Test_Console.html` | 浏览器测试页面。 |
| `byom_demo.py` | Voice Live 实时语音客户端。 |
| `run_demo.cmd` / `run_demo.ps1` / `run_demo.sh` | 可复用的命令行测试脚本，适合批量或重复验证。 |
| `.env.example` | 配置模板，复制为 `.env` 后填写。 |
| `requirements.txt` | Python 依赖。 |

客户日常测试建议优先使用网页；如果需要固定参数重复跑同一个 provider，可以复用脚本。

## 3. 准备清单

| 类别 | 需要准备 |
| --- | --- |
| Python | 建议 Python 3.10+；当前验证使用 Python 3.12。 |
| Speech / Voice Live | endpoint 和 API key。 |
| BYOM 模型 | endpoint、model/deployment name、API key 或 bearer token。 |
| 本机设备 | 可用麦克风和扬声器。 |

Speech / Voice Live endpoint 通常形如：

```text
https://<speech-resource>.cognitiveservices.azure.cn/
```

## 4. 安装依赖

在 demo 文件夹中运行：

```powershell
py -m pip install -r requirements.txt
```

## 5. 配置 `.env`

复制模板：

```powershell
copy .env.example .env
```

打开 `.env`，先填写 Speech / Voice Live：

```env
AZURE_VOICELIVE_ENDPOINT=https://<speech-resource>.cognitiveservices.azure.cn/
AZURE_VOICELIVE_API_KEY=<speech-or-voice-live-key>
```

然后按需要填写模型 key：

```env
BYOM_API_KEY=<baseline-foundry-key>
BYOM_DOUBAO_API_KEY=<ark-api-key>
BYOM_DEEPSEEK_API_KEY=<deepseek-or-foundry-token>
BYOM_KIMI_API_KEY=<kimi-or-foundry-token>
BYOM_MINIMAX_API_KEY=<minimax-key>
```

说明：

- `BYOM_API_KEY` 是 baseline / Foundry 的通用 key。
- Provider 专用 key 优先级更高，例如 Doubao 会优先使用 `BYOM_DOUBAO_API_KEY`。
- 如果网页里临时粘贴 `Model key / token`，该值只用于本次运行，不会保存进历史结果。
- 语速已固定为 `10%`，客户测试页面不提供语速调节。

## 6. 启动网页控制台

双击：

```text
start_web_console.cmd
```

或在 PowerShell 中运行：

```powershell
py web_test_server.py --open
```

浏览器会打开：

```text
http://127.0.0.1:8765/
```

如果浏览器没有自动打开，可以手动访问这个地址。

## 7. 运行测试

| 步骤 | 操作 |
| --- | --- |
| 1 | 在左侧选择 `Baseline`、`Doubao`、`DeepSeek`、`Kimi` 或 `MiniMax`。 |
| 2 | 检查 endpoint、model/deployment name 和 auth mode。 |
| 3 | 如果 `.env` 已配置对应 key，`Model key / token` 可以留空。 |
| 4 | 点击 `Start test`。 |
| 5 | 对本机麦克风说话。 |
| 6 | 点击 `Stop test`。 |
| 7 | 在结果表中查看详情、选择多条结果对比，或导出 CSV。 |

网页启动测试时默认关闭 proactive greeting，避免把“服务启动后主动问候”的音频误算进用户说话后的实时响应延迟。

建议第一轮测试说：

```text
你好，请用一句话介绍你自己。
```

如果需要复用脚本做重复测试，可以在 demo 文件夹运行：

```powershell
.\run_demo.cmd deepseek
.\run_demo.cmd kimi
.\run_demo.cmd doubao
.\run_demo.ps1 -Provider kimi -PrintConfig
```

脚本会读取 `.env` 中的 provider 配置，并使用和网页一致的 latency-friendly 默认参数。

## 8. 已验证模型

| 模型 | 状态 | 默认配置 |
| --- | --- | --- |
| Baseline | 已通过 | `gpt-5.4`，Foundry-style endpoint。 |
| Doubao | 已通过 | `https://ark.cn-beijing.volces.com/api/v3`，`doubao-seed-2-0-lite-260428`，bearer。 |
| DeepSeek | 已通过 | 可使用 provider key 或 Foundry bearer token。 |
| Kimi | 已通过 | 可使用 provider key 或 Foundry bearer token。 |
| MiniMax | 预置待测 | 填入 `BYOM_MINIMAX_API_KEY` 后可测试。 |

客户可以在网页里直接修改 endpoint 和 model/deployment name，以匹配自己的模型部署。

## 9. 指标怎么看

| 指标 | 说明 |
| --- | --- |
| `Status` | `Passed` 表示本轮有完整响应和音频输出。 |
| `Ready latency` | Voice Live session ready 的耗时。 |
| `Speech turns` | 检测到的用户说话轮次。 |
| `Responses created/done` | 模型响应创建和完成次数。 |
| `Audio chunks` | 返回的流式音频片段数。 |
| `Turn end to first audio` | **最核心端到端体验指标**：从 Voice Live 检测到用户说话结束，到第一段 TTS 音频返回。 |
| `ASR finalization` | ASR/VAD 收尾耗时：从用户说话结束到最终转写完成。 |
| `LLM first text` | **最核心 LLM 指标**：从最终转写完成到模型首个流式文本返回。 |
| `TTS first audio` | TTS 首音频耗时：从模型首个流式文本返回到第一段音频返回。 |
| `First audio from start` | 诊断指标：从本次测试进程启动到第一段音频返回，包含 session 启动、用户等待和说话时间，不建议作为实时对话延迟主指标。 |
| `Input/Output/Total tokens` | 模型 token 用量。 |
| `Errors` | 服务或 BYOM 模型错误数量。 |

点击结果详情可以看到更完整的字段、错误信息和每一轮对话的拆分指标。历史记录每页显示 10 条，点击 `Details` 后页面会自动跳到详情区域。选择多条结果后可以进行对比。

三段式 latency 对应关系：

```text
用户说话结束
  -> ASR finalization -> 最终转写完成
  -> LLM first text   -> 模型首个流式文本
  -> TTS first audio  -> 第一段语音音频
```

因此客户侧做模型对比时，建议重点看 `LLM first text`；做端到端语音体验对比时，建议重点看 `Turn end to first audio`。

## 10. 常见问题

| 问题 | 可能原因 | 处理方式 |
| --- | --- | --- |
| 页面显示 server unavailable | 本地 server 没启动。 | 运行 `py web_test_server.py --open`。 |
| Speech key 缺失 | `.env` 没填 Speech key。 | 填写 `AZURE_VOICELIVE_API_KEY`，或在网页里临时粘贴。 |
| BYOM authentication error | 模型 key/token 错误，或用了错误 provider 的 key。 | 使用 provider 专用 key，例如 `BYOM_DOUBAO_API_KEY`。修改 `.env` 后重启 server。 |
| Doubao key format error | 复制了错误内容。 | 使用 Ark 控制台完整 `ark-...` API key，不要带 `Bearer `。 |
| Doubao model not found / not open | 模型 ID 错误或账号未开通。 | 使用 Ark 返回的版本化模型 ID，并确认模型已开通。 |
| Foundry 返回 AuthenticationTypeDisabled | Foundry 禁用了 key auth。 | 使用 AAD bearer token。 |
| 没有声音 | 输出设备或音量问题。 | 检查系统默认扬声器和音量。 |

## 11. 日志

每次测试会生成本地日志：

```text
logs/<timestamp>_voicelive.log
```

网页会自动解析日志并保存指标。排查问题时，只需要关注详情里的 `errorMessages`，不要分享任何 API key。

`Clear history` 只会清除浏览器本地保存的指标历史，不会删除 `logs/*.log` 文件。需要回溯历史测试时，可以保留日志并通过页面或 `/api/metric-from-log` 重新解析。

## 12. 客户测试记录建议

| 字段 | 示例 |
| --- | --- |
| 日期 | 2026-06-05 |
| Provider | Doubao / DeepSeek / Kimi / Baseline |
| Model type | `doubao-seed-2-0-lite-260428` |
| Status | Passed / Failed |
| Session id | 详情中的 `sessionId` |
| Turn end to first audio | 页面指标中的 `Turn end to first audio` |
| ASR finalization | 页面指标中的 `ASR finalization` |
| LLM first text | 页面指标中的 `LLM first text` |
| TTS first audio | 页面指标中的 `TTS first audio` |
| Total tokens | 页面指标中的 `Total tokens` |
| 主观体验 | 流畅 / 有卡顿 / 延迟高 |
| 错误信息 | 只记录错误文本，不记录 key |

当前代码包的目标是保持部署和测试路径简单：客户填好 `.env`，启动本地 server，然后在网页里完成测试。
