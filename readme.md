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

1. Install Python 3.10+ and make sure `py` works from PowerShell.

2. Install dependencies:

```powershell
py -m pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in:

```env
AZURE_VOICELIVE_ENDPOINT=https://<your-speech-resource>.cognitiveservices.azure.cn/
AZURE_VOICELIVE_API_KEY=<your-speech-key>

BYOM_API_KEY=<baseline-or-foundry-key>
BYOM_DOUBAO_API_KEY=<ark-key>
BYOM_DEEPSEEK_API_KEY=<deepseek-or-foundry-token>
BYOM_KIMI_API_KEY=<kimi-or-foundry-token>
BYOM_MINIMAX_API_KEY=<minimax-key>
```

Only fill the provider keys you plan to test. The web page also lets you paste a key/token for a single run.

4. Start the console:

```powershell
py web_test_server.py --open
```

Or double-click `start_web_console.cmd`.

5. Open the local page if it does not open automatically:

```text
http://127.0.0.1:8765/
```

## Test Flow

1. Select `Baseline`, `Doubao`, `DeepSeek`, `Kimi`, or `MiniMax`.
2. Confirm the endpoint, model name, and auth mode.
3. Keep `Model key / token` blank if the key is already in `.env`, or paste a temporary key for this run.
4. Click `Start test` and speak into the machine microphone.
5. Click `Stop test` when finished.
6. Review the saved metrics, open details, compare runs, or export CSV.

The local server stores logs under `logs/`. The browser stores comparison history in localStorage. Secrets are not written into the result history or CSV export.

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
- Doubao must use a real Ark API key and a versioned model ID such as `doubao-seed-2-0-lite-260428`.
- DeepSeek and Kimi can use a pasted Bearer token. If no key/token is configured, the server can attempt an Azure CLI token for Foundry-based deployments.
- Keep `.env` local and do not commit or share real keys.

See `Customer_BYOM_Testing_Quickstart.md` for the customer-facing step-by-step guide.
