#!/usr/bin/env python3
"""Operator-only: pin required status sources for an explicitly selected repo."""
import argparse
import json
import re
import subprocess


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repository',required=True)
    parser.add_argument('--app-id',required=True,type=int)
    parser.add_argument('--check',default='system-tests')
    parser.add_argument('--branch',default='main')
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',args.repository) or args.app_id<=0:
        raise ValueError('Explicit repository and positive App ID are required')
    if not re.fullmatch('[A-Za-z0-9_-]+',args.branch):raise ValueError('Invalid branch')
    payload={'strict':True,'checks':[{'context':args.check,'app_id':15368},
        {'context':'kodi/independent-acceptance','app_id':args.app_id}]}
    subprocess.run(['gh','api','--method','PATCH',f'repos/{args.repository}/branches/{args.branch}/protection/required_status_checks',
        '--input','-'],input=json.dumps(payload),text=True,check=True)


if __name__=='__main__':main()
