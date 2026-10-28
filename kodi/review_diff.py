"""Byte-safe, complete candidate review material from committed Git objects."""
import hashlib
import json
import subprocess

from .policy import SECRET


def review_material(checkout, base, sha, mode="changed", max_chars=1000000, diff_max_chars=100000):
    if type(diff_max_chars) is not int or not 1 <= diff_max_chars <= 1000000:
        raise ValueError("diff_max_chars must be an integer from 1 to 1000000")
    if mode not in ("changed", "compact", "qwen"):
        raise ValueError("Unknown review material mode")

    def git_bytes(*args):
        try:
            return subprocess.check_output(
                ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                 *args], cwd=checkout, stderr=subprocess.PIPE)
        except (OSError, subprocess.CalledProcessError):
            raise RuntimeError('Cannot read committed review material; inspect the preserved checkout and Git objects before retrying') from None

    patches=[]; context=[]; assets=[]
    paths=git_bytes('diff','--no-renames','--name-only','-z',base,sha).split(b'\0')
    for raw_path in filter(None,paths):
        try:
            path=raw_path.decode('utf-8')
        except UnicodeError:
            raise RuntimeError('Review path is not UTF-8; rename the asset before review') from None
        if SECRET.search(path):
            raise RuntimeError("Secret-shaped path cannot enter review context")
        versions=[]
        for revision in (base,sha):
            entry=git_bytes('ls-tree','-z',revision,'--',':(literal)'+path)
            if not entry:
                versions.append(None)
                continue
            metadata=entry.split(b'\t',1)[0].split()
            if metadata[1]!=b'blob':
                raise RuntimeError('Cannot review non-blob change '+json.dumps(path)+'; supply a supported source change')
            oid=metadata[2].decode('ascii')
            versions.append((oid,git_bytes('cat-file','blob',oid),metadata[0].decode('ascii')))
        patch=git_bytes('diff','--no-renames','--no-ext-diff','--no-textconv',base,sha,
                        '--',':(literal)'+path)
        try:
            decoded=patch.decode('utf-8')
            contents=[None if v is None else v[1].decode('utf-8') for v in versions]
            binary=any(v is not None and b'\0' in v[1] for v in versions)
            binary=binary or b'Binary files ' in patch
        except UnicodeError:
            binary=True
        if binary:
            description={'path':path,'before':None,'after':None}
            for key,version in zip(('before','after'),versions):
                if version is not None:
                    oid,data,blob_mode=version
                    description[key]={'git_blob':oid,'mode':blob_mode,'bytes':len(data),
                                      'sha256':hashlib.sha256(data).hexdigest()}
            assets.append(description)
            patches.append('Asset change (binary or non-UTF-8): '+json.dumps(description)+'\n')
        else:
            patches.append(decoded)
            if mode != "compact" and contents[1] is not None:
                context.append('\nFile '+path+':\n'+contents[1])
        if len(''.join(patches))>diff_max_chars:
            raise RuntimeError('Change exceeds review budget; split the task')
    if mode == "compact":
        if git_bytes("rev-parse","HEAD").decode().strip() != sha or git_bytes("status","--porcelain","--untracked-files=all"):
            raise RuntimeError("Review requires a clean exact candidate checkout")
    if mode == "qwen" and not assets:
        # Tool-less review cannot discover unchanged callers/dependencies itself.
        # Include every committed text blob; fail rather than guess dependencies.
        context=[]
        context_chars=0
        for entry in git_bytes('ls-tree','-r','-z',sha).split(b'\0'):
            if not entry:
                continue
            metadata,raw_path=entry.split(b'\t',1)
            try:
                path=raw_path.decode('utf-8')
            except UnicodeError:
                raise RuntimeError('Review path is not UTF-8; rename it before review') from None
            if SECRET.search(path):
                raise RuntimeError("Secret-shaped path cannot enter review context")
            mode_bits,kind,oid=metadata.split()
            if kind != b'blob':
                raise RuntimeError("Tool-less review cannot inspect non-blob dependencies")
            if mode_bits not in (b'100644', b'100755'):
                raise RuntimeError("Tool-less review cannot inspect symbolic-link dependencies")
            data=git_bytes('cat-file','blob',oid.decode('ascii'))
            try:
                content=data.decode('utf-8')
            except UnicodeError:
                raise RuntimeError("Tool-less review cannot inspect binary dependencies") from None
            if b'\0' in data:
                raise RuntimeError("Tool-less review cannot inspect binary dependencies")
            section='\nFile '+path+':\n'+content
            context.append(section)
            context_chars+=len(section)
            if context_chars > max_chars:
                raise RuntimeError("Review context exceeds budget; split the task")
    return ''.join(patches), ''.join(context), assets
