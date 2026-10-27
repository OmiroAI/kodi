"""Offline synthetic size pilot: python3 scripts/review_context_pilot.py."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kodi.review_diff import review_material


def main():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        def git(*args):
            return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',
                '-c','user.name=Example','-c','user.email=test@example.invalid',*args],
                cwd=root,stderr=subprocess.DEVNULL,text=True).strip()
        git('init','-b','main')
        source='# surrounding source\n'*2000+'value = 0\n'
        (root/'source.py').write_text(source)
        (root/'caller.py').write_text('from source import value\n')
        git('add','.'); git('commit','-m','baseline')
        base=git('rev-parse','HEAD')
        for value in (1,2):
            (root/'source.py').write_text(source.replace('value = 0',f'value = {value}'))
            git('add','.'); git('commit','-m','repair')
        sha=git('rev-parse','HEAD')
        sizes={}
        for label,mode in [('before','changed'),('compact','compact'),('qwen','qwen')]:
            diff,context,_=review_material(root,base,sha,mode=mode)
            sizes[label]=len(diff)+len(context)
        print(json.dumps({'material_chars':sizes,'provider_tokens':None,
                          'note':'Material only; no measured token or credit savings'},sort_keys=True))


if __name__=='__main__':
    main()
