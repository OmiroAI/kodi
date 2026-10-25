#!/usr/bin/env python3
"""Prepare source-only files and an owner-neutral archive; never publish anything."""
import argparse
import gzip
import shutil
import subprocess
import tarfile
from pathlib import Path

try:
    from .check_public_source import private_patterns,scan,tracked
except ImportError:
    from check_public_source import private_patterns,scan,tracked


def export(root,output,patterns=()):
    root=Path(root).resolve();output=Path(output).resolve()
    if output==root or root in output.parents:raise ValueError('Export outside the source checkout')
    archive=output.with_name(output.name+'.tar.gz')
    if output.exists() or archive.exists():raise ValueError('Export destination or archive already exists')
    if subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip():
        raise ValueError('Commit and review the clean source tree before exporting')
    findings=scan(root,patterns=patterns)
    if findings:raise ValueError('Public-source checks failed; inspect the checker output first')
    files=tracked(root);output.mkdir(parents=True,mode=0o755)
    for relative in files:
        src=root/relative;dst=output/relative
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst)
        dst.chmod(0o755 if src.stat().st_mode&0o111 else 0o644)
    # No history, remote URLs, commit identities, operator files, logs or host UID metadata.
    with archive.open('wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as zipped:
        with tarfile.open(fileobj=zipped,mode='w') as tar:
            for relative in files:
                p=output/relative;info=tarfile.TarInfo('kodi/'+relative)
                info.size=p.stat().st_size;info.mode=0o755 if p.stat().st_mode&0o111 else 0o644
                info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
                with p.open('rb') as content:tar.addfile(info,content)
    return archive


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--private-patterns',type=Path)
    args=parser.parse_args()
    archive=export(args.root,args.output,private_patterns(args.private_patterns))
    print('History-free source export:',args.output)
    print('Owner-neutral source archive:',archive)
    print('Nothing was uploaded, published or made public.')


if __name__=='__main__':main()
