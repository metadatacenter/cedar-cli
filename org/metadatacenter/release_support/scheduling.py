"""Release build dependencies; publication and ledger writes stay on the coordinator."""

def build_edges(tasks):
    edges = {i: set() for i in range(len(tasks))}
    for i, task in enumerate(tasks):
        for j, previous in enumerate(tasks[:i]):
            if previous['variant'] != task['variant']:
                continue
            # A repository's npm commands can share generated files/caches even
            # when commands use different subdirectories. Keep them ordered.
            if (task['kind'] == 'maven'
                    or previous['repository'] == task['repository']):
                edges[i].add(j)
    return edges
