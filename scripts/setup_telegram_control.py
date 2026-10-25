#!/usr/bin/env python3
"""Stage owner Telegram control without exposing credentials or starting polling."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kodi.notify import TelegramAPI
from kodi.owner_review import owner_gh


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--github-approver',required=True)
    parser.add_argument('--whisper-url',required=True)
    args=parser.parse_args()
    if args.output.resolve()==args.config.resolve():raise ValueError('Stage to a separate output file first')
    config=json.loads(args.config.read_text());api=TelegramAPI(config['telegram_env'])
    if api.call('getWebhookInfo').get('url'):
        raise RuntimeError('This bot already has a webhook; do not replace its existing integration')
    chat=api.call('getChat',{'chat_id':api.chat_id})
    if chat.get('type')!='private' or chat.get('id')!=api.chat_id or api.chat_id<=0:
        raise RuntimeError('Use the existing private owner notification chat')
    identity=json.loads(owner_gh('api','user'))
    if identity.get('login','').lower()!=args.github_approver.lower():raise RuntimeError('Host GitHub identity mismatch')
    for key,repo in config['repos'].items():
        reviewers=repo.get('reviewers',config.get('reviewers',[]))
        if args.github_approver.lower() not in {r.lower() for r in reviewers}:
            raise RuntimeError('Approver is not configured for repository '+key)
    config['telegram']={'enabled':True,'allowed_user_ids':[api.chat_id],
        'approver_user_id':api.chat_id,'github_approver':args.github_approver,
        'whisper_url':args.whisper_url,'whisper_model':'openai/whisper-large-v3-turbo',
        'max_voice_seconds':300,'max_voice_bytes':10*1024*1024}
    args.output.write_text(json.dumps(config,indent=2)+'\n');args.output.chmod(0o600)
    print('Private owner chat and GitHub identity verified; Telegram control configuration staged.')
    print('Activate only after the implementation PR has passed human merge approval.')


if __name__=='__main__':main()
