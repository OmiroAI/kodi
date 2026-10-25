"""Transcribe on the operator's Whisper host; no external API fallback."""
import json
import uuid
from urllib.request import Request,urlopen
from urllib.parse import urlsplit


def transcribe(config,audio,mime='audio/ogg'):
    url=config.get('whisper_url','')
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError('Whisper transcription endpoint is not configured')
    if not audio or len(audio)>config.get('max_voice_bytes',10*1024*1024):
        raise ValueError('Voice note is empty or too large.')
    model=config.get('whisper_model','openai/whisper-large-v3-turbo')
    boundary=uuid.uuid4().hex
    mime=mime if mime in ('audio/ogg','audio/wav','audio/mpeg','audio/mp4') else 'audio/ogg'
    extension={'audio/ogg':'ogg','audio/wav':'wav','audio/mpeg':'mp3','audio/mp4':'m4a'}[mime]
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
          f'--{boundary}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\njson\r\n'
          f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.{extension}"\r\n'
          f'Content-Type: {mime}\r\n\r\n').encode()+audio+f'\r\n--{boundary}--\r\n'.encode()
    request=Request(url,data=body,headers={'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
    try:
        with urlopen(request,timeout=120) as response:result=json.load(response)
    except Exception:
        raise RuntimeError('Voice transcription is unavailable. Please send text or retry the voice note.') from None
    text=result.get('text','') if isinstance(result,dict) else ''
    if not isinstance(text,str) or not text.strip():raise ValueError('No speech was recognized. Please try again.')
    if len(text)>20000:raise ValueError('The transcript is too long. Please split it into shorter tickets.')
    return text.strip()
