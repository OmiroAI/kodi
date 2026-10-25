"""Telegram credentials stay in this host-side adapter, never in workers."""
import json
import re
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
from .env import read_env


class TelegramAPI:
    METHODS={'sendMessage','getUpdates','getWebhookInfo','getMe','getChat','getFile',
             'answerCallbackQuery','editMessageReplyMarkup','setMyCommands'}

    def __init__(self,path):
        env=read_env(path)
        self._token=env.get('TELEGRAM_BOT_TOKEN')
        try:self.chat_id=int(env['TELEGRAM_CHAT_ID'])
        except (KeyError,ValueError):raise RuntimeError('Telegram chat ID is missing or invalid') from None
        if not self._token:raise RuntimeError('Telegram token is missing')

    def call(self,method,data=None,timeout=20):
        if method not in self.METHODS:raise ValueError('Unsupported Telegram method')
        request=Request('https://api.telegram.org/bot'+self._token+'/'+method,
            data=json.dumps(data or {}).encode(),headers={'Content-Type':'application/json'},method='POST')
        try:
            with urlopen(request,timeout=timeout) as response:result=json.load(response)
        except (HTTPError,URLError,TimeoutError,OSError,ValueError):
            # urllib exceptions may contain the token-bearing URL.
            raise RuntimeError('Telegram operation failed; credentials suppressed') from None
        if not isinstance(result,dict) or not result.get('ok'):
            raise RuntimeError('Telegram rejected the operation')
        return result['result']

    def send(self,text,markup=None):
        data={'chat_id':self.chat_id,'text':text[:4000],'disable_web_page_preview':True}
        if markup:data['reply_markup']=markup
        return self.call('sendMessage',data)['message_id']

    def download_voice(self,file_id,limit):
        result=self.call('getFile',{'file_id':file_id})
        path=result.get('file_path','')
        if not re.fullmatch('[A-Za-z0-9_./-]+',path) or path.startswith('/') or '..' in path.split('/'):
            raise RuntimeError('Invalid Telegram voice file path')
        if result.get('file_size',0)>limit:raise ValueError('Voice note is too large. Please send a shorter note.')
        try:
            with urlopen('https://api.telegram.org/file/bot'+self._token+'/'+path,timeout=30) as response:
                audio=response.read(limit+1)
        except (HTTPError,URLError,TimeoutError,OSError):
            raise RuntimeError('Telegram voice download failed; credentials suppressed') from None
        if not audio or len(audio)>limit:raise ValueError('Voice note is empty or too large.')
        return audio


def send(path,text,reply_markup=None):
    return TelegramAPI(path).send(text,reply_markup)
