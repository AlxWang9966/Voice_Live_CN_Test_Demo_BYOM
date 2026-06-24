from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
HTML_FILE = ROOT / "VoiceLive_BYOM_Test_Console.html"
LOG_DIR = ROOT / "logs"


def load_local_env(*, override: bool = False) -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_key = key.strip()
        env_value = value.strip().strip('"').strip("'")
        if override or env_key not in os.environ:
            os.environ[env_key] = env_value


load_local_env()

MODEL_PRESETS: dict[str, dict[str, str]] = {
    "baseline": {
        "label": "Baseline",
        "provider": "foundry",
        "state": "Ready",
        "endpoint": "https://<your-foundry-resource>.cognitiveservices.azure.com/openai/v1",
        "modelType": "gpt-5.4",
        "authMode": "api-key",
        "note": "Baseline Voice Live path. Point this at your own Foundry deployment.",
    },
    "doubao": {
        "label": "Doubao",
        "provider": "doubao",
        "state": "Passed",
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3",
        "modelType": "doubao-seed-2-0-lite-260428",
        "authMode": "bearer",
        "note": "Use versioned Ark model ID.",
    },
    "deepseek": {
        "label": "DeepSeek",
        "provider": "deepseek",
        "state": "Ready",
        "endpoint": "https://<your-foundry-resource>.services.ai.azure.com/openai/v1/",
        "modelType": "DeepSeek-V4-Flash",
        "authMode": "bearer",
        "note": "Paste token or leave blank to use az token.",
    },
    "kimi": {
        "label": "Kimi",
        "provider": "kimi",
        "state": "Ready",
        "endpoint": "https://<your-foundry-resource>.services.ai.azure.com/openai/v1/",
        "modelType": "Kimi-K2.6",
        "authMode": "bearer",
        "note": "Paste token or leave blank to use az token.",
    },
    "minimax": {
        "label": "MiniMax",
        "provider": "minimax",
        "state": "Ready",
        "endpoint": "https://api.minimaxi.com/v1",
        "modelType": "MiniMax-M2.7",
        "authMode": "bearer",
        "note": "Fill BYOM_MINIMAX_API_KEY to test.",
    },
}


class RuntimeState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.output: deque[str] = deque(maxlen=300)
        self.started_at: float | None = None
        self.log_path: Path | None = None
        self.provider: str | None = None
        self.model_label: str | None = None
        self.model_type: str | None = None
        self.run_id: str | None = None
        self.test_config: dict[str, Any] = {}
        self.last_error: str | None = None


STATE = RuntimeState()


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def provider_env(provider_key: str, suffix: str) -> str:
    normalized = provider_key.upper().replace("-", "_")
    return os.environ.get(f"BYOM_{normalized}_{suffix}", "")


def server_config() -> dict[str, Any]:
    load_local_env(override=True)
    return {
        "speechEndpoint": os.environ.get("AZURE_VOICELIVE_ENDPOINT", ""),
        "speechKeyConfigured": bool(os.environ.get("AZURE_VOICELIVE_API_KEY")),
        "genericByomKeyConfigured": bool(os.environ.get("BYOM_API_KEY")),
        "providerKeysConfigured": {
            key: bool(provider_env(key, "API_KEY"))
            for key in MODEL_PRESETS
        },
    }


def presets_for_client() -> dict[str, dict[str, str]]:
    load_local_env(override=True)
    resolved: dict[str, dict[str, str]] = {}
    for key, preset in MODEL_PRESETS.items():
        item = dict(preset)
        env_endpoint = provider_env(key, "ENDPOINT")
        env_model = provider_env(key, "MODEL_TYPE")
        if env_endpoint:
            item["endpoint"] = env_endpoint
        if env_model:
            item["modelType"] = env_model
        resolved[key] = item
    return resolved


def latest_log_after(started_at: float | None) -> Path | None:
    if not LOG_DIR.exists():
        return None
    candidates = sorted(LOG_DIR.glob("*_voicelive.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    if started_at is None:
        return candidates[0]
    for candidate in candidates:
        if candidate.stat().st_mtime >= started_at - 1:
            return candidate
    return candidates[0]


def newer_log_after(current: Path | None, started_at: float | None) -> Path | None:
    if started_at is None:
        return current
    latest = latest_log_after(started_at)
    if not latest:
        return current
    if not current or not current.exists():
        return latest
    if latest.stat().st_mtime > current.stat().st_mtime:
        return latest
    return current


def read_log(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def metric_from_log_name(log_name: str) -> dict[str, Any]:
    name = Path(log_name).name
    if not name.endswith("_voicelive.log"):
        raise RuntimeError("invalid log name")
    path = (LOG_DIR / name).resolve()
    if path.parent != LOG_DIR.resolve() or not path.exists():
        raise RuntimeError("log file not found")
    run_id = path.stem
    return parse_log(read_log(path), run_id=run_id)


LOG_LINE_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}):(?P<body>.*)$")
SESSION_ID_RE = re.compile(r"\bsess_[A-Za-z0-9]+\b")


def parse_log_time(value: str) -> float | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f").timestamp()
    except ValueError:
        return None


def elapsed_ms(start: float | None, value: float | None) -> int | None:
    if start is None or value is None:
        return None
    return max(0, int((value - start) * 1000))


def parse_json_event(body: str) -> dict[str, Any] | None:
    marker = "Received websocket text message: "
    if marker not in body:
        return None
    raw = body.split(marker, 1)[1].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def parse_log(text: str, model_label: str | None = None, run_id: str | None = None, test_config: dict[str, Any] | None = None) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    errors: list[str] = []
    transcripts: list[str] = []
    token_totals = {"input": 0, "output": 0, "total": 0, "reasoning": 0}
    turns: list[dict[str, Any]] = []
    response_turns: dict[str, dict[str, Any]] = {}
    current_speech_start_ts: float | None = None
    first_ts: float | None = None
    last_ts: float | None = None
    ready_ts: float | None = None
    audio_ready_ts: float | None = None
    first_speech_ts: float | None = None
    last_speech_stop_ts: float | None = None
    first_transcript_ts: float | None = None
    first_response_created_ts: float | None = None
    first_response_done_ts: float | None = None
    first_output_text_ts: float | None = None
    first_audio_ts: float | None = None
    session_id: str = ""

    for line in text.splitlines():
        match = LOG_LINE_RE.match(line)
        if not match:
            continue
        ts = parse_log_time(match.group("ts"))
        body = match.group("body")
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        if "Audio playback system ready" in body and audio_ready_ts is None:
            audio_ready_ts = ts
        if ("Voice assistant ready" in body or "Session ready" in body) and ready_ts is None:
            ready_ts = ts
        event = parse_json_event(body)
        if not event:
            continue
        if not session_id:
            session_candidate = str((event.get("session") or {}).get("id") or event.get("session_id") or "").strip()
            if session_candidate.startswith("sess_"):
                session_id = session_candidate
            else:
                fallback_match = SESSION_ID_RE.search(body)
                if fallback_match:
                    session_id = fallback_match.group(0)
        event_type = str(event.get("type") or "")
        if event_type:
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type == "input_audio_buffer.speech_started" and first_speech_ts is None:
            first_speech_ts = ts
            current_speech_start_ts = ts
        elif event_type == "input_audio_buffer.speech_started":
            current_speech_start_ts = ts
        elif event_type == "input_audio_buffer.speech_stopped":
            last_speech_stop_ts = ts
            turns.append({
                "index": len(turns) + 1,
                "speechStartedTs": current_speech_start_ts,
                "speechStoppedTs": ts,
                "transcriptTs": None,
                "transcript": "",
                "responseId": "",
                "responseCreatedTs": None,
                "firstOutputTextTs": None,
                "firstAudioTs": None,
                "responseDoneTs": None,
            })
        elif event_type == "conversation.item.input_audio_transcription.completed":
            first_transcript_ts = first_transcript_ts or ts
            transcript = str(event.get("transcript") or "").strip()
            if transcript:
                transcripts.append(transcript)
            for turn in reversed(turns):
                if turn["transcriptTs"] is None:
                    turn["transcriptTs"] = ts
                    turn["transcript"] = transcript
                    break
        elif event_type == "response.created" and first_response_created_ts is None:
            first_response_created_ts = ts
            response_id = str((event.get("response") or {}).get("id") or "")
            for turn in reversed(turns):
                if turn["responseCreatedTs"] is None and turn["speechStoppedTs"] is not None and ts is not None and ts >= turn["speechStoppedTs"]:
                    turn["responseCreatedTs"] = ts
                    turn["responseId"] = response_id
                    if response_id:
                        response_turns[response_id] = turn
                    break
        elif event_type == "response.created":
            response_id = str((event.get("response") or {}).get("id") or "")
            for turn in reversed(turns):
                if turn["responseCreatedTs"] is None and turn["speechStoppedTs"] is not None and ts is not None and ts >= turn["speechStoppedTs"]:
                    turn["responseCreatedTs"] = ts
                    turn["responseId"] = response_id
                    if response_id:
                        response_turns[response_id] = turn
                    break
        elif event_type == "response.audio_transcript.delta":
            first_output_text_ts = first_output_text_ts or ts
            response_id = str(event.get("response_id") or "")
            turn = response_turns.get(response_id)
            if turn and turn["firstOutputTextTs"] is None:
                turn["firstOutputTextTs"] = ts
        elif event_type == "response.audio.delta" and first_audio_ts is None:
            first_audio_ts = ts
            response_id = str(event.get("response_id") or "")
            turn = response_turns.get(response_id)
            if turn and turn["firstAudioTs"] is None:
                turn["firstAudioTs"] = ts
        elif event_type == "response.audio.delta":
            response_id = str(event.get("response_id") or "")
            turn = response_turns.get(response_id)
            if turn and turn["firstAudioTs"] is None:
                turn["firstAudioTs"] = ts
        elif event_type == "response.done":
            first_response_done_ts = first_response_done_ts or ts
            response = event.get("response") or {}
            response_id = str(response.get("id") or "")
            turn = response_turns.get(response_id)
            if turn and turn["responseDoneTs"] is None:
                turn["responseDoneTs"] = ts
            status_details = response.get("status_details") or {}
            status_error = status_details.get("error") or {}
            if status_error:
                message = str(status_error.get("message") or status_error.get("code") or "Response failed")
                if message and message not in errors:
                    errors.append(message)
            usage = response.get("usage") or {}
            token_totals["input"] += int(usage.get("input_tokens") or 0)
            token_totals["output"] += int(usage.get("output_tokens") or 0)
            token_totals["total"] += int(usage.get("total_tokens") or 0)
            output_details = usage.get("output_token_details") or {}
            token_totals["reasoning"] += int(output_details.get("reasoning_tokens") or 0)
        elif event_type == "error":
            error = event.get("error") or {}
            message = str(error.get("message") or error.get("code") or "Unknown error")
            if "Cancellation failed: no active response" not in message:
                errors.append(message)

    turn_metrics: list[dict[str, Any]] = []
    for turn in turns:
        speech_stop = turn["speechStoppedTs"]
        transcript_done = turn["transcriptTs"]
        response_created = turn["responseCreatedTs"]
        first_output_text = turn["firstOutputTextTs"]
        first_audio = turn["firstAudioTs"]
        response_done = turn["responseDoneTs"]
        turn_metrics.append({
            "index": turn["index"],
            "responseId": turn["responseId"],
            "transcript": turn["transcript"],
            "speechDurationMs": elapsed_ms(turn["speechStartedTs"], speech_stop),
            "asrFinalizationMs": elapsed_ms(speech_stop, transcript_done),
            "turnEndToResponseCreatedMs": elapsed_ms(speech_stop, response_created),
            "llmFirstTextMs": elapsed_ms(transcript_done, first_output_text),
            "responseCreatedToFirstTextMs": elapsed_ms(response_created, first_output_text),
            "ttsFirstAudioMs": elapsed_ms(first_output_text, first_audio),
            "turnEndToFirstAudioMs": elapsed_ms(speech_stop, first_audio),
            "responseCreatedToFirstAudioMs": elapsed_ms(response_created, first_audio),
            "turnEndToResponseDoneMs": elapsed_ms(speech_stop, response_done),
        })

    first_user_turn = next((turn for turn in turn_metrics if turn["turnEndToFirstAudioMs"] is not None), None)
    first_response_turn = next((turn for turn in turn_metrics if turn["turnEndToResponseCreatedMs"] is not None), None)
    first_text_turn = next((turn for turn in turn_metrics if turn["llmFirstTextMs"] is not None), None)
    if not session_id:
        fallback_match = SESSION_ID_RE.search(text)
        if fallback_match:
            session_id = fallback_match.group(0)
    config = test_config or {}
    metrics: dict[str, Any] = {
        "timestamp": time.time(),
        "runId": run_id or "",
        "sessionId": session_id,
        "model": model_label or "",
        "provider": config.get("provider", ""),
        "modelType": config.get("modelType", ""),
        "status": "Running",
        "ready": "Voice assistant ready" in text or "Session ready" in text,
        "connected": "Connecting to VoiceLive API" in text,
        "speechStarted": event_counts.get("input_audio_buffer.speech_started", 0),
        "speechStopped": event_counts.get("input_audio_buffer.speech_stopped", 0),
        "transcripts": event_counts.get("conversation.item.input_audio_transcription.completed", 0),
        "responseCreated": event_counts.get("response.created", 0),
        "audioDelta": event_counts.get("response.audio.delta", 0),
        "audioDone": event_counts.get("response.audio.done", 0),
        "responseDone": event_counts.get("response.done", 0),
        "completed": text.count('"status":"completed"') + text.count("Response complete"),
        "cancelled": text.count('"status":"cancelled"'),
        "failed": text.count('"status":"failed"'),
        "errors": len(errors) + sum(text.count(token) for token in ["server_error", "AuthenticationTypeDisabled", "InvalidEndpointOrModel", "ModelNotOpen", "Fatal Error"]),
        "errorMessages": errors[:5],
        "durationMs": elapsed_ms(first_ts, last_ts),
        "readyMs": elapsed_ms(first_ts, ready_ts),
        "audioReadyMs": elapsed_ms(first_ts, audio_ready_ts),
        "firstSpeechMs": elapsed_ms(first_ts, first_speech_ts),
        "firstTranscriptMs": elapsed_ms(first_ts, first_transcript_ts),
        "firstResponseCreatedMs": first_response_turn["turnEndToResponseCreatedMs"] if first_response_turn else None,
        "firstResponseCreatedFromStartMs": elapsed_ms(first_ts, first_response_created_ts),
        "firstResponseDoneMs": elapsed_ms(first_ts, first_response_done_ts),
        "firstAudioMs": first_user_turn["turnEndToFirstAudioMs"] if first_user_turn else None,
        "firstAudioFromStartMs": elapsed_ms(first_ts, first_audio_ts),
        "asrFinalizationMs": first_text_turn["asrFinalizationMs"] if first_text_turn else None,
        "llmFirstTextMs": first_text_turn["llmFirstTextMs"] if first_text_turn else None,
        "responseCreatedToFirstTextMs": first_text_turn["responseCreatedToFirstTextMs"] if first_text_turn else None,
        "ttsFirstAudioMs": first_user_turn["ttsFirstAudioMs"] if first_user_turn else None,
        "modelLatencyMs": first_response_turn["turnEndToResponseCreatedMs"] if first_response_turn else None,
        "firstAudioAfterSpeechMs": first_user_turn["turnEndToFirstAudioMs"] if first_user_turn else None,
        "responseCreatedToFirstAudioMs": first_user_turn["responseCreatedToFirstAudioMs"] if first_user_turn else None,
        "firstOutputTextFromStartMs": elapsed_ms(first_ts, first_output_text_ts),
        "turnMetrics": turn_metrics,
        "inputTokens": token_totals["input"],
        "outputTokens": token_totals["output"],
        "totalTokens": token_totals["total"],
        "reasoningTokens": token_totals["reasoning"],
        "transcriptSamples": transcripts[-3:],
        "eventCounts": event_counts,
    }
    metrics["pendingResponses"] = max(0, metrics["responseCreated"] - metrics["responseDone"])
    if metrics["failed"] or metrics["errors"]:
        metrics["status"] = "Failed"
    elif metrics["ready"] and metrics["audioDelta"] and not metrics["speechStarted"]:
        metrics["status"] = "No speech turn"
    elif metrics["speechStopped"] and metrics["completed"] and metrics["audioDelta"] and metrics["firstAudioAfterSpeechMs"] is not None:
        metrics["status"] = "Passed"
    elif metrics["speechStopped"] and metrics["cancelled"] and metrics["audioDelta"]:
        metrics["status"] = "Partial"
    elif metrics["ready"] and metrics["pendingResponses"]:
        metrics["status"] = "Waiting for response"
    elif metrics["ready"] and metrics["speechStarted"] and metrics["responseDone"]:
        metrics["status"] = "No audio response"
    elif metrics["ready"]:
        metrics["status"] = "Connected"
    return metrics


def get_az_token() -> str:
    az_path = shutil.which("az") or shutil.which("az.cmd")
    if not az_path:
        raise RuntimeError("Azure CLI (az) was not found on PATH. Install it and run 'az login', or paste a token in the page.")
    result = subprocess.run(
        [az_path, "account", "get-access-token", "--resource", "https://ai.azure.com", "--query", "accessToken", "-o", "tsv"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=45,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "az token command failed").strip())
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("az returned an empty token")
    return token


def key_auth_disabled(endpoint: str, key: str, auth_mode: str) -> bool:
    """Return True if the Foundry resource rejected key auth (AuthenticationTypeDisabled)."""
    url = endpoint.rstrip("/") + "/models"
    header_name = "Authorization" if auth_mode == "bearer" else "api-key"
    header_value = f"Bearer {key}" if auth_mode == "bearer" else key
    request = Request(url, headers={header_name: header_value}, method="GET")
    try:
        with urlopen(request, timeout=20):
            return False
    except HTTPError as error:
        if error.code in (401, 403):
            try:
                body = error.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            return "AuthenticationTypeDisabled" in body
        return False
    except Exception:
        return False


def drain_output(process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        with STATE.lock:
            STATE.output.append(line.rstrip())


def start_test(payload: dict[str, Any]) -> dict[str, Any]:
    load_local_env(override=True)
    with STATE.lock:
        if STATE.process and STATE.process.poll() is None:
            raise RuntimeError("A test is already running. Stop it before starting another one.")

    provider_key = str(payload.get("provider") or "doubao")
    preset = MODEL_PRESETS.get(provider_key, MODEL_PRESETS["doubao"])
    provider = preset["provider"]
    speech_endpoint = str(payload.get("speechEndpoint") or os.environ.get("AZURE_VOICELIVE_ENDPOINT") or "").strip()
    speech_key = str(payload.get("speechKey") or os.environ.get("AZURE_VOICELIVE_API_KEY") or "").strip()
    byom_endpoint = str(payload.get("providerEndpoint") or provider_env(provider_key, "ENDPOINT") or preset["endpoint"]).strip()
    model_type = str(payload.get("modelType") or provider_env(provider_key, "MODEL_TYPE") or preset["modelType"]).strip()
    auth_mode = str(payload.get("authMode") or preset["authMode"]).strip()
    byom_key = str(payload.get("providerKey") or provider_env(provider_key, "API_KEY") or "").strip()
    voice = str(payload.get("voice") or os.environ.get("AZURE_VOICELIVE_VOICE") or "zh-CN-XiaoxiaoMultilingualNeural").strip()
    voice_rate = str(os.environ.get("AZURE_VOICELIVE_VOICE_RATE") or "10%").strip()

    if not speech_endpoint:
        raise RuntimeError("Speech endpoint is required.")
    if not speech_key:
        raise RuntimeError("Speech key is required unless AZURE_VOICELIVE_API_KEY is already set for the server.")
    if not byom_endpoint:
        raise RuntimeError("Model endpoint is required.")
    if not model_type:
        raise RuntimeError("Model/deployment name is required.")
    if not byom_key and provider_key in {"deepseek", "kimi"} and auth_mode == "bearer":
        byom_key = get_az_token()
    if not byom_key and provider_key in {"baseline", "foundry"}:
        byom_key = str(os.environ.get("BYOM_API_KEY") or "").strip()
    if not byom_key:
        raise RuntimeError(f"Model key/token is required for {preset['label']}. Paste it in the page or set BYOM_{provider_key.upper()}_API_KEY in .env.")

    if provider_key in {"deepseek", "kimi"} and key_auth_disabled(byom_endpoint, byom_key, auth_mode):
        STATE.output.append(f"[auth] {preset['label']}: key auth disabled on resource, switching to Azure AD token.")
        byom_key = get_az_token()
        auth_mode = "bearer"

    command = [
        sys.executable,
        str(ROOT / "byom_demo.py"),
        "--provider", provider,
        "--endpoint", speech_endpoint,
        "--model", model_type,
        "--byom", "byom-chat-completion",
        "--voice", voice,
        "--byom-endpoint", byom_endpoint,
        "--byom-model-type", model_type,
        "--byom-api-key", byom_key,
        "--byom-auth-scheme", auth_mode,
        "--voice-rate", voice_rate,
        "--no-proactive-greeting",
        "--verbose",
    ]

    child_env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "AZURE_VOICELIVE_API_KEY": speech_key,
        "BYOM_API_KEY": byom_key,
    }

    started_at = time.time()
    run_id = datetime.fromtimestamp(started_at).strftime("%Y%m%d-%H%M%S")
    test_config = {
        "provider": provider_key,
        "providerLabel": preset["label"],
        "modelType": model_type,
        "speechEndpoint": speech_endpoint,
        "byomEndpoint": byom_endpoint,
        "authMode": auth_mode,
        "voice": voice,
        "voiceRate": voice_rate,
    }
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
    )

    with STATE.lock:
        STATE.process = process
        STATE.output.clear()
        STATE.started_at = started_at
        STATE.log_path = None
        STATE.provider = provider_key
        STATE.model_label = preset["label"]
        STATE.model_type = model_type
        STATE.run_id = run_id
        STATE.test_config = test_config
        STATE.last_error = None
    threading.Thread(target=drain_output, args=(process,), daemon=True).start()
    time.sleep(0.4)
    with STATE.lock:
        STATE.log_path = latest_log_after(started_at)
    return current_status()


def stop_test() -> dict[str, Any]:
    with STATE.lock:
        process = STATE.process
        started_at = STATE.started_at
    if process and process.poll() is None:
        deadline = time.time() + 10
        while time.time() < deadline:
            with STATE.lock:
                STATE.log_path = newer_log_after(STATE.log_path, started_at)
                log_path = STATE.log_path
                model_label = STATE.model_label
                run_id = STATE.run_id
                test_config = dict(STATE.test_config)
            metrics = parse_log(read_log(log_path), model_label, run_id, test_config)
            if metrics["audioDelta"] or not metrics["pendingResponses"]:
                break
            time.sleep(0.5)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return current_status()


def current_status() -> dict[str, Any]:
    with STATE.lock:
        process = STATE.process
        output = list(STATE.output)
        started_at = STATE.started_at
        log_path = newer_log_after(STATE.log_path, started_at)
        if log_path:
            STATE.log_path = log_path
        model_label = STATE.model_label
        model_type = STATE.model_type
        provider = STATE.provider
        run_id = STATE.run_id
        test_config = dict(STATE.test_config)
        last_error = STATE.last_error
    running = bool(process and process.poll() is None)
    exit_code = None if not process else process.poll()
    log_text = read_log(log_path)
    metrics = parse_log(log_text, model_label, run_id, test_config)
    if process is None and log_path is None:
        metrics["status"] = "Idle"
    if running and metrics["status"] in {"Passed", "Partial"}:
        metrics["status"] = "Running"
    if running and metrics["status"] == "Waiting for response":
        metrics["status"] = "Waiting for response"
    if not running and metrics["status"] == "Waiting for response":
        metrics["status"] = "Stopped before response"
    if not running and process is not None and metrics["status"] == "Running":
        metrics["status"] = "Stopped"
    return {
        "running": running,
        "exitCode": exit_code,
        "provider": provider,
        "model": model_label,
        "modelType": model_type,
        "runId": run_id,
        "testConfig": test_config,
        "logPath": str(log_path.relative_to(ROOT)) if log_path else None,
        "metrics": metrics,
        "outputTail": output[-80:],
        "lastError": last_error,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path in {"/", "/index.html", "/VoiceLive_BYOM_Test_Console.html"}:
            body = HTML_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/presets":
            json_response(self, 200, {"models": presets_for_client()})
        elif path == "/api/metric-from-log":
            log_name = str((query.get("log") or [""])[0] or "").strip()
            if not log_name:
                raise RuntimeError("Missing log query parameter")
            json_response(self, 200, metric_from_log_name(log_name))
        elif path == "/api/status":
            json_response(self, 200, current_status())
        elif path == "/api/config":
            json_response(self, 200, server_config())
        else:
            json_response(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/start":
                json_response(self, 200, start_test(read_json(self)))
            elif path == "/api/stop":
                json_response(self, 200, stop_test())
            else:
                json_response(self, 404, {"error": "Not found"})
        except Exception as exc:
            with STATE.lock:
                STATE.last_error = str(exc)
            json_response(self, 400, {"error": str(exc), "status": current_status()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Voice Live BYOM web test console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Voice Live BYOM web console: {url}")
    print("Press Ctrl+C to stop the server.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop_test()
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
