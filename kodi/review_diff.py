"""Byte-safe, complete candidate review material from committed Git objects."""
import hashlib
import json
import subprocess


def review_material(checkout, base, sha):
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
                    oid,data,mode=version
                    description[key]={'git_blob':oid,'mode':mode,'bytes':len(data),
                                      'sha256':hashlib.sha256(data).hexdigest()}
            assets.append(description)
            patches.append('Asset change (binary or non-UTF-8): '+json.dumps(description)+'\n')
        else:
            patches.append(decoded)
            if contents[1] is not None:
                context.append('\nFile '+path+':\n'+contents[1])
        if len(''.join(patches))>100000:
            raise RuntimeError('Change exceeds review budget; split the task')
    return ''.join(patches), ''.join(context), assets
