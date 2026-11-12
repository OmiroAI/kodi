"""Observable advisory delivery ownership; metadata is valid only while locked."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time


def status(state_dir):
    path = Path(state_dir)/'delivery.lock'
    if not path.exists():
        return None
    with path.open('r') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                owner = json.loads(handle.read())
            except (ValueError, OSError):
                owner = {'operation': 'unknown retained delivery operation'}
            actual = kernel_owner(handle)
            if actual:
                if actual['mode'] == 'READ':
                    owner = {'pid': actual['pid'], 'operation': 'read-only merge-intent inspection'}
                elif owner.get('pid') != actual['pid']:
                    owner = {'pid': actual['pid'], 'operation': 'unreported delivery operation'}
            return {**owner, 'wait_reason': 'delivery mutation serialization'}
        return None


def kernel_owner(handle):
    """Do not mistake a previous holder's metadata for current ownership."""
    stat = os.fstat(handle.fileno())
    try:
        for line in Path('/proc/locks').read_text().splitlines():
            fields = line.split()
            if len(fields) < 6 or fields[1] != 'FLOCK':
                continue
            major, minor, inode = fields[5].split(':')
            if (int(major, 16), int(minor, 16), int(inode)) == (os.major(stat.st_dev), os.minor(stat.st_dev), stat.st_ino):
                return {'pid': int(fields[4]), 'mode': fields[3]}
    except (OSError, ValueError):
        pass
    return None


def describe(handle, operation, job=None):
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({'pid': os.getpid(), 'operation': operation,
                             'job': job, 'started': time.time()}))
    handle.flush()


@contextmanager
def acquire(state_dir, operation, job=None):
    with (Path(state_dir)/'delivery.lock').open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if operation == 'publish':
                raise RuntimeError('Delivery mutation is busy') from None
            raise
        describe(handle, operation, job)
        try:
            yield handle
        finally:
            handle.seek(0)
            handle.truncate()
