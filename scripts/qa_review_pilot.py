"""Summarize privately adjudicated paired reviewer runs; never invokes workers."""
import argparse
import json

METRICS = ('prompt_chars', 'broad_reruns', 'focused_checks', 'valid_findings',
           'missed_defects', 'spurious_findings')


def compare(runs):
    if not isinstance(runs, list) or not runs:
        raise ValueError('Supply a nonempty list of paired runs')
    pairs = {}
    for run in runs:
        key = (run['candidate_sha'], run['repeat'])
        arm = run['arm']
        if arm not in ('before', 'after') or arm in pairs.setdefault(key, {}):
            raise ValueError('Each candidate/repeat needs one before and one after')
        values = {metric: run[metric] for metric in METRICS}
        if any(type(v) is not int or v < 0 for v in values.values()):
            raise ValueError('Metrics must be measured nonnegative integers')
        pairs[key][arm] = values
    if any(set(pair) != {'before', 'after'} for pair in pairs.values()):
        raise ValueError('Unpaired run')
    totals = {arm: {m: sum(p[arm][m] for p in pairs.values()) for m in METRICS}
              for arm in ('before', 'after')}
    return dict(pairs=len(pairs), totals=totals,
                after_minus_before={m: totals['after'][m]-totals['before'][m] for m in METRICS})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('records', help='Private JSON file of adjudicated paired runs')
    args = parser.parse_args()
    with open(args.records) as source:
        result = compare(json.load(source))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
