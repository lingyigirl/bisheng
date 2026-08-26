from typing import Literal

import httpx

from ..base import BaseTTSClient


class MiniMaxTTSClient(BaseTTSClient):
    """MiniMax native T2A protocol client (aiping gateway + MiniMax official API).

    Speaks the MiniMax-native text-to-speech protocol (``text`` +
    ``voice_setting`` + ``audio_setting``), NOT the OpenAI TTS protocol:
    the aiping gateway rejects the OpenAI ``input`` field with a 422, and
    returns the audio as base64 inside ``{"data": {"audio": ...}}``
    (MiniMax official API returns hex in the same envelope).
    """

    def __init__(self, api_key: str, **kwargs):
        """
        :param api_key: provider API key (sent as Bearer token)
        :param kwargs: model / base_url / endpoint / voice / http_client
        """
        self.api_key = api_key
        self.model = kwargs.pop('model', 'MiniMax-Speech-2.8-hd')
        self.voice = kwargs.pop('voice', None)
        self.base_url = (kwargs.pop('base_url', '') or '').rstrip('/')
        self.endpoint = kwargs.pop('endpoint', '/audio/speech')
        # Optional pre-built client (e.g. proxied via openai_proxy, or a
        # test client with a MockTransport); the client is never closed
        # here — ownership stays with whoever created it.
        self._http_client = kwargs.pop('http_client', None)

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        return httpx.AsyncClient(timeout=200)

    async def synthesize(
            self,
            text: str,
            voice: str | None = None,
            language: str | None = None,
            format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = "mp3"
    ) -> bytes:
        """
        Synthesize text to speech via the MiniMax native T2A protocol.

        :param text: text to synthesize
        :param voice: voice_id override; falls back to the client's configured voice
        :param language: unused (MiniMax T2A auto-detects language)
        :param format: audio output format
        :return: audio bytes
        """
        voice_id = voice or self.voice
        if not voice_id:
            raise Exception(
                'MiniMax TTS requires voice_id: configure the model voice field or pass a voice parameter')

        payload = {
            'model': self.model,
            'text': text,
            'voice_setting': {
                'voice_id': voice_id,
                'speed': 1.0,
                'vol': 1.0,
            },
            'audio_setting': {
                'sample_rate': 32000,
                'format': format,
                'channel': 1,
            },
        }
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

        client = self._get_http_client()
        response = await client.post(f'{self.base_url}{self.endpoint}', json=payload, headers=headers)
        if response.status_code != 200:
            raise Exception(
                f'MiniMax TTS API ERROR, Code: {response.status_code}, Message: {response.text}')

        try:
            body = response.json()
        except ValueError:
            raise Exception('MiniMax TTS response is not valid JSON')
        audio = (body.get('data') or {}).get('audio')
        if not audio:
            raise Exception('MiniMax TTS response missing data.audio field')

        # The two upstreams encode differently: aiping returns base64,
        # MiniMax official returns hex. Hex detection goes first because
        # hex strings are also valid base64 alphabet — lenient base64
        # decoding would silently produce garbage for official responses.
        if len(audio) % 2 == 0 and all(c in '0123456789abcdefABCDEF' for c in audio):
            try:
                return bytes.fromhex(audio)
            except ValueError:
                pass
        try:
            return _strict_b64decode(audio)
        except Exception:
            raise Exception('MiniMax TTS audio decode failed: data.audio is neither hex nor base64')


def _strict_b64decode(data: str) -> bytes:
    import base64

    return base64.b64decode(data, validate=True)
