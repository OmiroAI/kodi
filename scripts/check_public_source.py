#!/usr/bin/env python3
"""Detect common private deployment disclosures without printing matched values."""
import argparse
import ipaddress
import json
import re
import subprocess
from pathlib import Path

IPV4=re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
INTERNAL_DOMAIN=re.compile(r'\b(?:[a-z0-9-]+\.)+(?:ts\.net|local|lan|internal)\b',re.I)
HOME=re.compile(r'/(?:home|Users)/([^/\s\"\'`]+)')
SECRET=re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9_]{20,}|\bsk-[A-Za-z0-9_-]{24,}|\b\d{6,12}:[A-Za-z0-9_-]{25,}')
DOC_NETWORKS=tuple(ipaddress.ip_network(n) for n in ('192.0.2.0/24','198.51.100.0/24','203.0.113.0/24'))


def tracked(root):
    raw=subprocess.check_output(['git','ls-files','-z'],cwd=root)
    return [p for p in raw.decode().split('\0') if p]


def private_patterns(path):
    if path is None:return []
    patterns=json.loads(Path(path).read_text())
    if not isinstance(patterns,list) or any(not isinstance(p,str) or not p for p in patterns):
        raise ValueError('Private patterns must be a JSON list of nonempty strings')
    return patterns


def scan(root,paths=None,patterns=()):
    root=Path(root);findings=[]
    for relative in tracked(root) if paths is None else paths:
        p=root/relative
        if not p.exists() and not p.is_symlink():continue  # Pending deletion.
        if any(pattern.casefold() in relative.casefold() for pattern in patterns):
            findings.append(('<private path>',0,'operator-private path identifier'));continue
        suffix=p.suffix.lower();name=p.name.lower()
        if p.is_symlink() or not p.is_file():
            findings.append((relative,0,'symlink or unsupported tracked object'));continue
        if (name in ('auth.json','credentials.json') or name=='.env' or
                name.startswith('.env.') and name!='.env.example' or
                suffix in ('.pem','.key','.sqlite','.sqlite3','.bundle','.tar','.gz','.zip')):
            # Never open credential-shaped or private-state files.
            findings.append((relative,0,'credential, private state or archive file'));continue
        try:text=p.read_bytes().decode('utf-8')
        except UnicodeDecodeError:
            findings.append((relative,0,'non-text data requires an explicit release review'));continue
        for number,line in enumerate(text.splitlines(),1):
            reasons=set()
            if INTERNAL_DOMAIN.search(line):reasons.add('internal or mesh DNS name')
            if any(m.group(1) not in ('worker','runner','example') for m in HOME.finditer(line)):
                reasons.add('personal home path')
            if SECRET.search(line):reasons.add('credential-shaped value')
            if any(pattern.casefold() in line.casefold() for pattern in patterns):
                reasons.add('operator-private identifier')
            for match in IPV4.finditer(line):
                try:address=ipaddress.ip_address(match.group())
                except ValueError:continue
                if address.is_loopback or address.is_unspecified or any(address in net for net in DOC_NETWORKS):continue
                if not address.is_global:reasons.add('private, shared or reserved network address')
            for reason in sorted(reasons):findings.append((relative,number,reason))
    return findings


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--private-patterns',type=Path)
    args=parser.parse_args()
    findings=scan(args.root,patterns=private_patterns(args.private_patterns))
    for path,line,reason in findings:print(f'{path}:{line}: {reason}')
    if findings:raise SystemExit(1)
    print('Public-source check passed. Manual content and history review are still required.')


if __name__=='__main__':main()
