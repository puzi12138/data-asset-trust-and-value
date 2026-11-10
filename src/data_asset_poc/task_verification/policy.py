"""Same-evidence deterministic budget policies. No truth or future outcomes."""
from collections import defaultdict
import time

MODES=('same_information_gate','fixed_order','static_greedy','group_dynamic','shared_dynamic','full')
BUDGET_FRACTIONS=(0.,.1,.25,.5,.75,1.)

def run(records,actions,costs,budget,mode,fetch,evaluate):
    if mode not in MODES or budget<0:raise ValueError('mode or budget')
    ids=[r['sample_id'] for r in records]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate member')
    graph={a['action_id']:a for a in actions}
    if len(graph)!=len(actions):raise ValueError('duplicate action')
    for a in actions:
        if not set(a['affected_records']).issubset(ids) or not set(a.get('requires',[])).issubset(graph):raise ValueError('unbound dependency')
        if costs[a['action_id']]<=0:raise ValueError('positive measured cost required')
    groups=defaultdict(set)
    for r in records:groups[r['group_id']].add(r['sample_id'])
    observed={};spent=0;trajectory=[];pending=set(graph);rows=evaluate(observed)
    def gain(a,rr):return sum(rr[s]['strict_decision']=='ABSTAIN' for s in graph[a]['affected_records'])
    initial={a:gain(a,rows)/costs[a] for a in graph}
    static=sorted(graph,key=lambda a:(-initial[a],a))
    while pending:
        ready=[a for a in pending if all(p in observed for p in graph[a].get('requires',[])) and spent+costs[a]<=budget+1e-12]
        if mode!='full':ready=[a for a in ready if gain(a,rows)>0]
        if not ready:break
        t=time.perf_counter_ns()
        scores={a:gain(a,rows)/costs[a] for a in ready}
        if mode in ('static_greedy','same_information_gate'):chosen=next(a for a in static if a in ready)
        elif mode in ('fixed_order','full'):chosen=min(ready)
        elif mode=='shared_dynamic':chosen=min(ready,key=lambda a:(-scores[a],a))
        else:
            eligible_groups={g for g,ss in groups.items() if any(ss.intersection(graph[a]['affected_records']) for a in ready)}
            g=min(eligible_groups,key=lambda g:(-sum(rows[s]['strict_decision']=='ABSTAIN' for s in groups[g]),g))
            local=[a for a in ready if groups[g].intersection(graph[a]['affected_records'])]
            chosen=min(local,key=lambda a:(-scores[a],a))
        planning=time.perf_counter_ns()-t
        before=rows;obs=fetch(chosen);observed[chosen]=obs;spent+=costs[chosen];pending.remove(chosen);rows=evaluate(observed)
        trajectory.append({'action_id':chosen,'scope':graph[chosen]['action_scope'],'affected_records':graph[chosen]['affected_records'],
             'initial_priority':initial[chosen],'current_priority':scores[chosen],'initial_top_remaining':next(a for a in static if a in ready),
             'priority_changed':scores[chosen]!=initial[chosen],'cost_local_ms':costs[chosen],'cumulative_cost_local_ms':spent,
             'planning_ns':planning,'observation':obs,'decision_changes':sum(rows[s]['strict_decision']!=before[s]['strict_decision'] for s in ids)})
    ordered=[rows[s] for s in ids]
    return {'mode':mode,'budget_local_ms':budget,'charged_local_ms':spent,'trajectory':trajectory,'rows':ordered,
            'planning_ns':sum(t['planning_ns'] for t in trajectory),'action_count':len(trajectory),
            'scope':'offline replay of measured local evidence verification costs; common prepaid AI pool'}
