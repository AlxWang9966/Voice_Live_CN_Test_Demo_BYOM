# Voice Live BYOM Web Test Console

This package provides a local browser-based test console for Voice Live + BYOM model validation. The customer only needs to prepare the Speech/Voice Live key and the model provider key, start the local server, and run tests from the web page.

## What Is Included

| File | Purpose |
| --- | --- |
| `start_web_console.cmd` | Windows one-click launcher. |
| `web_test_server.py` | Local HTTP server for the browser console. |
| `VoiceLive_BYOM_Test_Console.html` | Customer-facing test UI. |
| `byom_demo.py` | Voice Live runtime used by the server. |
| `.env.example` | Configuration template. |
| `Customer_BYOM_Testing_Quickstart.md` | Customer setup and test guide. |
| `requirements.txt` | Python dependencies. |

Generated files such as `.env`, `logs/`, and `__pycache__/` are intentionally ignored.

## Quick Start

```powershell
# 1. Install dependencies (Python 3.10+)
py -m pip install -r requirements.txt

# 2. Copy the template and fill in your keys
copy .env.example .env

# 3. Start the console (or double-click start_web_console.cmd)
py web_test_server.py --open
```

The browser opens at `http://127.0.0.1:8765/`. Pick a provider, confirm the endpoint/model, then click `Start test` and speak into the microphone.

Only fill the provider keys you plan to test. You can also paste a key/token in the web page for a single run instead of storing it in `.env`.

## Scripted Runs

For repeated command-line testing, use the provider wrapper. It reads `.env`, resolves the provider endpoint/model, disables the proactive greeting, and runs the same Voice Live runtime:

```powershell
.\run_demo.cmd deepseek
.\run_demo.cmd kimi
.\run_demo.cmd doubao
```

PowerShell users can call the script directly:

```powershell
.\run_demo.ps1 -Provider deepseek
.\run_demo.ps1 -Provider kimi -PrintConfig
.\run_demo.ps1 -Provider kimi -WithGreeting
```

For the full walkthrough — `.env` fields, test steps, metric definitions, and troubleshooting — see [`Customer_BYOM_Testing_Quickstart.md`](Customer_BYOM_Testing_Quickstart.md).

## Verified Providers

| Provider | Default endpoint | Default model | Auth mode | Status |
| --- | --- | --- | --- | --- |
| Baseline | `https://<your-foundry-resource>.cognitiveservices.azure.com/openai/v1` | `gpt-5.4` | `api-key` | Configure your own |
| Doubao | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-2-0-lite-260428` | `bearer` | Passed |
| DeepSeek | Foundry OpenAI-compatible endpoint | `DeepSeek-V4-Flash` | `bearer` | Passed |
| Kimi | Foundry OpenAI-compatible endpoint | `Kimi-K2.6` | `bearer` | Passed |
| MiniMax | `https://api.minimaxi.com/v1` | `MiniMax-M2.7` | `bearer` | Ready for key validation |

If your deployment uses a different endpoint or model name, update it in `.env` or directly in the web page before starting the test.

## Notes

- Voice speed is fixed at `10%` for customer testing.
- Provider-specific keys take precedence over the generic `BYOM_API_KEY` (e.g. Doubao uses `BYOM_DOUBAO_API_KEY`).
- Keep `.env` local and never commit or share real keys.
- The web console disables the automatic proactive greeting for latency runs. The primary latency metric is now turn-based: from `input_audio_buffer.speech_stopped` to the following first `response.audio.delta`.

For metric definitions, troubleshooting, and a test-record template, see [`Customer_BYOM_Testing_Quickstart.md`](Customer_BYOM_Testing_Quickstart.md).
