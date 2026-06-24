# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# -------------------------------------------------------------------------
from __future__ import annotations
from html import parser
import os
import sys
import argparse
import asyncio
import base64
import json
from datetime import datetime
import logging
import queue
import signal
from typing import Union, Optional, TYPE_CHECKING, cast, Literal, Any

from azure.core.credentials import AzureKeyCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import AzureCliCredential, DefaultAzureCredential

from azure.ai.voicelive.aio import connect
from azure.ai.voicelive.models import (
    AudioEchoCancellation,
    AudioNoiseReduction,
    AzureStandardVoice,
    InputAudioFormat,
    Modality,
    OutputAudioFormat,
    RequestSession,
    ServerEventType,
    ServerVad,
    MessageItem,
    InputTextContentPart
)
from dotenv import load_dotenv
import pyaudio

if TYPE_CHECKING:
    # Only needed for type checking; avoids runtime import issues
    from azure.ai.voicelive.aio import VoiceLiveConnection

## Change to the directory where this script is located
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Environment variable loading
load_dotenv('./.env', override=True)

# Set up logging
## Add folder for logging
if not os.path.exists('logs'):
    os.makedirs('logs')

## Add timestamp for logfiles
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

## Set up logging
logging.basicConfig(
    filename=f'logs/{timestamp}_voicelive.log',
    filemode="w",
    encoding="utf-8",
    format='%(asctime)s:%(name)s:%(levelname)s:%(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BYOM_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "foundry": {
        "model_type": "gpt-5.4",
        "endpoint": "https://<your-resource>.cognitiveservices.azure.com/openai/v1",
        "auth_scheme": "api-key",
        "extra_headers": {},
        "extra_body": {},
    },
    "kimi": {
        "model_type": "kimi-k2.6",
        "endpoint": "https://api.moonshot.cn/v1",
        "auth_scheme": "bearer",
        "extra_headers": {},
        "extra_body": {},
    },
    "minimax": {
        "model_type": "MiniMax-M2.7",
        "endpoint": "https://api.minimaxi.com/v1",
        "auth_scheme": "bearer",
        "extra_headers": {},
        "extra_body": {"reasoning_split": True},
    },
    "deepseek": {
        "model_type": "deepseek-v4-flash",
        "endpoint": "https://api.deepseek.com",
        "auth_scheme": "bearer",
        "extra_headers": {},
        "extra_body": {},
    },
    "doubao": {
        "model_type": "doubao-seed-2-0-lite-260428",
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3",
        "auth_scheme": "bearer",
        "extra_headers": {},
        "extra_body": {},
    },
}


def _json_arg(value: Optional[str], name: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


def _provider_env(provider: Optional[str], suffix: str) -> Optional[str]:
    if not provider:
        return None
    return os.environ.get(f"BYOM_{provider.upper()}_{suffix}")

class AudioProcessor:
    """
    Handles real-time audio capture and playback for the voice assistant.

    Threading Architecture:
    - Main thread: Event loop and UI
    - Capture thread: PyAudio input stream reading
    - Send thread: Async audio data transmission to VoiceLive
    - Playback thread: PyAudio output stream writing
    """
    
    loop: asyncio.AbstractEventLoop
    
    class AudioPlaybackPacket:
        """Represents a packet that can be sent to the audio playback queue."""
        def __init__(self, seq_num: int, data: Optional[bytes]):
            self.seq_num = seq_num
            self.data = data

    def __init__(self, connection):
        self.connection = connection
        self.audio = pyaudio.PyAudio()

        # Audio configuration - PCM16, 24kHz, mono as specified
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 24000
        self.chunk_size = 1200 # 50ms

        # Capture and playback state
        self.input_stream = None

        self.playback_queue: queue.Queue[AudioProcessor.AudioPlaybackPacket] = queue.Queue()
        self.playback_base = 0
        self.next_seq_num = 0
        self.output_stream: Optional[pyaudio.Stream] = None

        logger.info("AudioProcessor initialized with 24kHz PCM16 mono audio")

    def start_capture(self):
        """Start capturing audio from microphone."""
        def _capture_callback(
            in_data,      # data
            _frame_count,  # number of frames
            _time_info,    # dictionary
            _status_flags):
            """Audio capture thread - runs in background."""
            audio_base64 = base64.b64encode(in_data).decode("utf-8")
            asyncio.run_coroutine_threadsafe(
                self.connection.input_audio_buffer.append(audio=audio_base64), self.loop
            )
            return (None, pyaudio.paContinue)

        if self.input_stream:
            return

        # Store the current event loop for use in threads
        self.loop = asyncio.get_event_loop()

        try:
            self.input_stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk_size,
                stream_callback=_capture_callback,
            )
            logger.info("Started audio capture")

        except Exception:
            logger.exception("Failed to start audio capture")
            raise

    def start_playback(self):
        """Initialize audio playback system."""
        if self.output_stream:
            return

        remaining = bytes()
        def _playback_callback(
            _in_data,
            frame_count,  # number of frames
            _time_info,
            _status_flags):

            nonlocal remaining
            frame_count *= pyaudio.get_sample_size(pyaudio.paInt16)

            out = remaining[:frame_count]
            remaining = remaining[frame_count:]

            while len(out) < frame_count:
                try:
                    packet = self.playback_queue.get_nowait()
                except queue.Empty:
                    out = out + bytes(frame_count - len(out))
                    continue
                except Exception:
                    logger.exception("Error in audio playback")
                    raise

                if not packet or not packet.data:
                    # None packet indicates end of stream
                    logger.info("End of playback queue.")
                    break

                if packet.seq_num < self.playback_base:
                    # skip requested
                    # ignore skipped packet and clear remaining
                    if len(remaining) > 0:
                        remaining = bytes()
                    continue

                num_to_take = frame_count - len(out)
                out = out + packet.data[:num_to_take]
                remaining = packet.data[num_to_take:]

            if len(out) >= frame_count:
                return (out, pyaudio.paContinue)
            else:
                return (out, pyaudio.paComplete)

        try:
            self.output_stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                output=True,
                frames_per_buffer=self.chunk_size,
                stream_callback=_playback_callback
            )
            logger.info("Audio playback system ready")
        except Exception:
            logger.exception("Failed to initialize audio playback")
            raise

    def _get_and_increase_seq_num(self):
        seq = self.next_seq_num
        self.next_seq_num += 1
        return seq

    def queue_audio(self, audio_data: Optional[bytes]) -> None:
        """Queue audio data for playback."""
        self.playback_queue.put(
            AudioProcessor.AudioPlaybackPacket(
                seq_num=self._get_and_increase_seq_num(),
                data=audio_data))

    def skip_pending_audio(self):
        """Skip current audio in playback queue."""
        self.playback_base = self._get_and_increase_seq_num()

    def shutdown(self):
        """Clean up audio resources."""
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.input_stream = None

        logger.info("Stopped audio capture")

        # Inform thread to complete
        if self.output_stream:
            self.skip_pending_audio()
            self.queue_audio(None)
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.output_stream = None

        logger.info("Stopped audio playback")

        if self.audio:
            self.audio.terminate()

        logger.info("Audio processor cleaned up")

class BasicVoiceAssistant:
    """Basic voice assistant implementing the VoiceLive SDK patterns."""

    def __init__(
        self,
        endpoint: str,
        credential: Union[AzureKeyCredential, AsyncTokenCredential],
        model: str,
        voice: str,
        voice_rate: str,
        instructions: str,
        byom: Literal["byom-azure-openai-realtime", "byom-azure-openai-chat-completion", "byom-chat-completion"],
        byom_endpoint: str,
        byom_auth_header_name: str,
        byom_auth_headers: dict[str, Any],
        byom_extra_headers: dict[str, Any],
        byom_extra_body: dict[str, Any],
        proactive_greeting: bool,
    ):

        self.endpoint = endpoint
        self.credential = credential
        self.model = model
        self.voice = voice
        self.voice_rate = voice_rate
        self.instructions = instructions
        self.connection: Optional["VoiceLiveConnection"] = None
        self.audio_processor: Optional[AudioProcessor] = None
        self.session_ready = False
        self._active_response = False
        self._response_api_done = False
        self.conversation_started = False
        self.byom = byom
        self.byom_endpoint = byom_endpoint
        self.byom_auth_header_name = byom_auth_header_name
        self.byom_auth_headers = byom_auth_headers
        self.byom_extra_headers = byom_extra_headers
        self.byom_extra_body = byom_extra_body
        self.proactive_greeting = proactive_greeting

    async def start(self):
        """Start the voice assistant session."""
        try:
            logger.info("Connecting to VoiceLive API with model %s", self.model)

            # Connect to VoiceLive WebSocket API
            async with connect(
                endpoint=self.endpoint,
                credential=self.credential,
                model=self.model,
                headers={
                    "byom-endpoint": self.byom_endpoint,
                    self.byom_auth_header_name: json.dumps(self.byom_auth_headers),
                    "x-ms-byom-extra-headers": json.dumps(self.byom_extra_headers),
                },
                query={
                    "profile": self.byom
                } if self.byom else None
            ) as connection:
                conn = connection
                self.connection = conn

                # Initialize audio processor
                ap = AudioProcessor(conn)
                self.audio_processor = ap

                # Configure session for voice conversation
                await self._setup_session()

                # Start audio systems
                ap.start_playback()

                logger.info("Voice assistant ready! Start speaking...")
                print("\n" + "=" * 60)
                print("🎤 VOICE ASSISTANT READY")
                print("Start speaking to begin conversation")
                print("Press Ctrl+C to exit")
                print("=" * 60 + "\n")

                # Process events
                await self._process_events()
        finally:
            if self.audio_processor:
                self.audio_processor.shutdown()

    async def _setup_session(self):
        """Configure the VoiceLive session for audio conversation."""
        logger.info("Setting up voice conversation session...")

        # Create voice configuration
        voice_config: Union[AzureStandardVoice, str]
        if self.voice.startswith("en-US-") or self.voice.startswith("en-CA-") or "-" in self.voice:
            # Azure voice
            voice_kwargs = {"name": self.voice}
            if self.voice_rate:
                voice_kwargs["rate"] = self.voice_rate
            voice_config = cast(Any, AzureStandardVoice)(**voice_kwargs)
        else:
            # OpenAI voice (alloy, echo, fable, onyx, nova, shimmer)
            voice_config = self.voice

        # Create turn detection configuration
        turn_detection_config = ServerVad(
            threshold=0.5,
            prefix_padding_ms=300,
            silence_duration_ms=500)

        # Create session configuration
        session_config = RequestSession(
            modalities=[Modality.TEXT, Modality.AUDIO],
            instructions=self.instructions,
            voice=voice_config,
            input_audio_format=InputAudioFormat.PCM16,
            output_audio_format=OutputAudioFormat.PCM16,
            turn_detection=turn_detection_config,
            input_audio_echo_cancellation=AudioEchoCancellation(),
            input_audio_noise_reduction=AudioNoiseReduction(type="azure_deep_noise_suppression"),
            max_response_output_tokens=3000,
        )

        conn = self.connection
        assert conn is not None, "Connection must be established before setting up session"
        if self.byom_extra_body:
            session_dict = session_config.as_dict()
            session_dict["extra_body"] = self.byom_extra_body
            await conn.session.update(session=session_dict)
        else:
            await conn.session.update(session=session_config)

        logger.info("Session configuration sent")

    async def _process_events(self):
        """Process events from the VoiceLive connection."""
        try:
            conn = self.connection
            assert conn is not None, "Connection must be established before processing events"
            async for event in conn:
                await self._handle_event(event)
        except Exception:
            logger.exception("Error processing events")
            raise

    async def _handle_event(self, event):
        """Handle different types of events from VoiceLive."""
        logger.debug("Received event: %s", event.type)
        ap = self.audio_processor
        conn = self.connection
        assert ap is not None, "AudioProcessor must be initialized"
        assert conn is not None, "Connection must be established"

        if event.type == ServerEventType.SESSION_UPDATED:
            logger.info("Session ready: %s", event.session.id)
            self.session_ready = True
        
            # Proactive greeting
            if self.proactive_greeting and not self.conversation_started:
                self.conversation_started = True
                logger.info("Sending proactive greeting request")
                try:
                    await conn.conversation.item.create(
                        item=MessageItem(
                            role="user",
                            content=[InputTextContentPart(text="Hello.")],
                        )
                    )
                    await conn.response.create()
        
                except Exception:
                    logger.exception("Failed to send proactive greeting request")
        
            # Start audio capture once session is ready
            ap.start_capture()

        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            logger.info("User started speaking - stopping playback")
            print("🎤 Listening...")

            ap.skip_pending_audio()

            # Only cancel if response is active and not already done
            if self._active_response and not self._response_api_done:
                try:
                    await conn.response.cancel()
                    logger.debug("Cancelled in-progress response due to barge-in")
                except Exception as e:
                    if "no active response" in str(e).lower():
                        logger.debug("Cancel ignored - response already completed")
                    else:
                        logger.warning("Cancel failed: %s", e)

        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            logger.info("🎤 User stopped speaking")
            print("🤔 Processing...")

        elif event.type == ServerEventType.RESPONSE_CREATED:
            logger.info("🤖 Assistant response created")
            self._active_response = True
            self._response_api_done = False

        elif event.type == ServerEventType.RESPONSE_AUDIO_DELTA:
            logger.debug("Received audio delta")
            ap.queue_audio(event.delta)

        elif event.type == ServerEventType.RESPONSE_AUDIO_DONE:
            logger.info("🤖 Assistant finished speaking")
            print("🎤 Ready for next input...")

        elif event.type == ServerEventType.RESPONSE_DONE:
            logger.info("✅ Response complete")
            self._active_response = False
            self._response_api_done = True

        elif event.type == ServerEventType.ERROR:
            msg = event.error.message
            if "Cancellation failed: no active response" in msg:
                logger.debug("Benign cancellation error: %s", msg)
            else:
                logger.error("❌ VoiceLive error: %s", msg)
                print(f"Error: {msg}")

        elif event.type == ServerEventType.CONVERSATION_ITEM_CREATED:
            logger.debug("Conversation item created: %s", event.item.id)

        else:
            logger.debug("Unhandled event type: %s", event.type)


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Basic Voice Assistant using Azure VoiceLive SDK",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--api-key",
        help="Azure VoiceLive API key. If not provided, will use AZURE_VOICELIVE_API_KEY environment variable.",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_API_KEY"),
    )

    parser.add_argument(
        "--endpoint",
        help="Azure VoiceLive endpoint",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_ENDPOINT", "https://your-resource-name.services.ai.azure.com/"),
    )

    parser.add_argument(
        "--model",
        help="VoiceLive model to use",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_MODEL", "gpt-realtime"),
    )

    parser.add_argument(
        "--byom",
        help="BYOM (Bring Your Own Model) profile type",
        type=str,
        choices=["byom-azure-openai-realtime", "byom-azure-openai-chat-completion", "byom-chat-completion"],
        default=os.environ.get("AZURE_VOICELIVE_BYOM_MODE", "byom-chat-completion"),

    )

    parser.add_argument(
        "--provider",
        help="BYOM provider preset. Explicit endpoint/model/header arguments override preset values.",
        type=str,
        choices=sorted(BYOM_PROVIDER_PRESETS.keys()),
        default=os.environ.get("BYOM_PROVIDER"),
    )
    
    parser.add_argument(
        "--byom-model-type",
        help="BYOM model type",
        type=str,
        default=os.environ.get("BYOM_MODEL_TYPE"),
    )
    
    parser.add_argument(
        "--byom-endpoint",
        help="BYOM model endpoint",
        type=str,
        default=os.environ.get("BYOM_ENDPOINT"),
    )
    
    parser.add_argument(
        "--byom-api-key",
        help="BYOM API Key",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--byom-auth-scheme",
        help="How to convert --byom-api-key into auth headers when --byom-auth-headers is not supplied.",
        type=str,
        choices=["api-key", "bearer"],
        default=os.environ.get("BYOM_AUTH_SCHEME"),
    )

    parser.add_argument(
        "--byom-auth-header-name",
        help="Voice Live header name carrying BYOM authorization headers.",
        type=str,
        choices=["x-ms-byom-authentication-headers", "x-ms-byom-authorization-headers"],
        default=os.environ.get("BYOM_AUTH_HEADER_NAME", "x-ms-byom-authentication-headers"),
    )

    parser.add_argument(
        "--byom-auth-headers",
        help='Raw JSON auth headers to forward to BYOM endpoint, for example {"Authorization":"Bearer ..."}.',
        type=str,
        default=os.environ.get("BYOM_AUTH_HEADERS"),
    )

    parser.add_argument(
        "--byom-extra-headers",
        help='Raw JSON extra headers to forward to BYOM endpoint, for example {"X-ModelType":"model"}.',
        type=str,
        default=os.environ.get("BYOM_EXTRA_HEADERS"),
    )

    parser.add_argument(
        "--byom-extra-body",
        help='Raw JSON extra body for session.update. MiniMax can use {"reasoning_split":true}.',
        type=str,
        default=os.environ.get("BYOM_EXTRA_BODY"),
    )

    parser.add_argument(
        "--voice",
        help="Voice to use for the assistant. E.g. alloy, echo, fable, en-US-AvaNeural, en-US-GuyNeural",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_VOICE", "en-US-Ava:DragonHDLatestNeural"),
    )

    parser.add_argument(
        "--voice-rate",
        help='Azure TTS speaking rate, for example "0%", "10%", or "-10%". Only applies to Azure neural voices.',
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_VOICE_RATE", "10%"),
    )

    parser.add_argument(
        "--instructions",
        help="System instructions for the AI assistant",
        type=str,
        default=os.environ.get(
            "AZURE_VOICELIVE_INSTRUCTIONS",
            "You are a helpful AI assistant. Respond naturally and conversationally. Always start the conversation in English."
            "Keep your responses concise but engaging.",
        ),
    )

    parser.add_argument(
        "--use-token-credential", help="Use Azure token credential instead of API key", action="store_true", default=False
    )

    parser.add_argument("--verbose", help="Enable verbose logging", action="store_true")

    parser.add_argument(
        "--print-config",
        help="Print the resolved BYOM config without starting the audio session.",
        action="store_true",
    )

    parser.add_argument(
        "--no-proactive-greeting",
        help="Disable the automatic initial Hello response. Recommended for latency measurement.",
        action="store_true",
        default=os.environ.get("AZURE_VOICELIVE_NO_PROACTIVE_GREETING", "").lower() in {"1", "true", "yes"},
    )

    return parser.parse_args()


def resolve_byom_config(args: argparse.Namespace) -> dict[str, Any]:
    preset = BYOM_PROVIDER_PRESETS.get(args.provider or "", {})
    provider_key = _provider_env(args.provider, "API_KEY")
    provider_endpoint = _provider_env(args.provider, "ENDPOINT")
    provider_model_type = _provider_env(args.provider, "MODEL_TYPE")

    model_type = args.byom_model_type or provider_model_type or preset.get("model_type")
    endpoint = args.byom_endpoint or provider_endpoint or preset.get("endpoint")
    api_key = args.byom_api_key or provider_key or os.environ.get("BYOM_API_KEY")
    auth_scheme = args.byom_auth_scheme or preset.get("auth_scheme", "api-key")

    if not model_type:
        raise ValueError("BYOM model type is required. Use --byom-model-type or --provider.")
    if not endpoint:
        raise ValueError("BYOM endpoint is required. Use --byom-endpoint or --provider.")
    if "<" in endpoint or ">" in endpoint:
        raise ValueError("BYOM endpoint still contains placeholder text. Provide a real endpoint.")

    auth_header_name = args.byom_auth_header_name
    auth_headers = _json_arg(args.byom_auth_headers, "--byom-auth-headers")
    if not auth_headers:
        if not api_key:
            raise ValueError("BYOM API key is required. Use --byom-api-key, BYOM_API_KEY, or provider-specific BYOM_<PROVIDER>_API_KEY.")
        if auth_scheme == "api-key":
            auth_headers = {"api-key": api_key}
        else:
            auth_headers = {"Authorization": f"Bearer {api_key}"}
            if auth_header_name == "x-ms-byom-authentication-headers":
                auth_header_name = "x-ms-byom-authorization-headers"

    extra_headers = dict(preset.get("extra_headers", {}))
    extra_headers.update(_json_arg(args.byom_extra_headers, "--byom-extra-headers"))
    extra_headers.setdefault("X-ModelType", model_type)

    if args.byom_extra_body is not None:
        extra_body = _json_arg(args.byom_extra_body, "--byom-extra-body")
    else:
        extra_body = dict(preset.get("extra_body", {}))

    return {
        "provider": args.provider or "custom",
        "endpoint": endpoint,
        "model_type": model_type,
        "auth_header_name": auth_header_name,
        "auth_headers": auth_headers,
        "extra_headers": extra_headers,
        "extra_body": extra_body,
    }


def main():
    """Main function."""
    args = parse_arguments()

    try:
        byom_config = resolve_byom_config(args)
    except ValueError as exc:
        print(f"❌ Error: {exc}")
        sys.exit(1)

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.print_config:
        redacted = dict(byom_config)
        redacted["auth_headers"] = {key: "***" for key in byom_config["auth_headers"]}
        print(json.dumps(redacted, indent=2, ensure_ascii=False))
        return

    # Validate credentials
    if not args.api_key and not args.use_token_credential:
        print("❌ Error: No authentication provided")
        print("Please provide an API key using --api-key or set AZURE_VOICELIVE_API_KEY environment variable,")
        print("or use --use-token-credential for Azure authentication.")
        sys.exit(1)

    # Create client with appropriate credential
    credential: Union[AzureKeyCredential, AsyncTokenCredential]
    if args.use_token_credential:
        credential = AzureCliCredential()  # or DefaultAzureCredential() if needed
        logger.info("Using Azure token credential")
    else:
        credential = AzureKeyCredential(args.api_key)
        logger.info("Using API key credential")

    # Create and start voice assistant
    assistant = BasicVoiceAssistant(
        endpoint=args.endpoint,
        credential=credential,
        model=args.model,
        byom=args.byom,
        byom_endpoint=byom_config["endpoint"],
        byom_auth_header_name=byom_config["auth_header_name"],
        byom_auth_headers=byom_config["auth_headers"],
        byom_extra_headers=byom_config["extra_headers"],
        byom_extra_body=byom_config["extra_body"],
        voice=args.voice,
        voice_rate=args.voice_rate,
        instructions=args.instructions,
        proactive_greeting=not args.no_proactive_greeting,
    )

    # Setup signal handlers for graceful shutdown
    def signal_handler(_sig, _frame):
        logger.info("Received shutdown signal")
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start the assistant
    try:
        asyncio.run(assistant.start())
    except KeyboardInterrupt:
        print("\n👋 Voice assistant shut down. Goodbye!")
    except Exception as e:
        print("Fatal Error: ", e)

if __name__ == "__main__":
    # Check audio system
    try:
        p = pyaudio.PyAudio()
        # Check for input devices
        input_devices = [
            i
            for i in range(p.get_device_count())
            if cast(Union[int, float], p.get_device_info_by_index(i).get("maxInputChannels", 0) or 0) > 0
        ]
        # Check for output devices
        output_devices = [
            i
            for i in range(p.get_device_count())
            if cast(Union[int, float], p.get_device_info_by_index(i).get("maxOutputChannels", 0) or 0) > 0
        ]
        p.terminate()

        if not input_devices:
            print("❌ No audio input devices found. Please check your microphone.")
            sys.exit(1)
        if not output_devices:
            print("❌ No audio output devices found. Please check your speakers.")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Audio system check failed: {e}")
        sys.exit(1)

    print("🎙️  Basic Voice Assistant with Azure VoiceLive SDK")
    print("=" * 50)

    # Run the assistant
    main()
