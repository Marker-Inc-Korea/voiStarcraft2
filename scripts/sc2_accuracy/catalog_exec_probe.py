import sys,collections; sys.path.insert(0,'.')
from sc2.ids.unit_typeid import UnitTypeId as U
from starcraft_commander import sc2_catalog as C
from starcraft_commander.policy_modulation_provider import compile_policy_modulation_provider_output as P
from starcraft_commander.unified_command_router import route_policy_vector
from scripts.sc2_accuracy import harness as h
res=collections.defaultdict(lambda:{"sent":0,"refused":0,"compile":0,"error":0,"n":0}); detail=collections.defaultdict(list)
for c in C.all_commands():
    r=res[c.category]; r["n"]+=1
    try:
        issuer=(c.valid_issuers or c.issuers or [None])[0]
        pay=C.direct_task_payload(c.id, issuer=issuer) if issuer else C.direct_task_payload(c.id)
        out=P(pay)
        if out.vector is None: r["compile"]+=1; detail["compile"].append(c.id); continue
        plan=route_policy_vector(out.vector).direct_plan
        if plan is None: r["compile"]+=1; detail["noplan"].append(c.id); continue
        units=[]
        if issuer:
            try: units=[(getattr(U,str(issuer).replace("TERRAN_","")),2)]
            except Exception: units=[]
        bot=h.make_bot(*units); bot.minerals=5000; bot.vespene=5000
        res_,views,w=h.run_plan(bot,*plan.ordered_actions)
        if views: r["sent"]+=1
        else: r["refused"]+=1; detail["refused:"+c.category].append(c.id)
    except BaseException as e:
        r["error"]+=1; detail["error:"+c.category].append(f"{c.id}:{type(e).__name__}")
tot=sum(v["sent"] for v in res.values()); print("SENT",tot,"of",sum(v["n"] for v in res.values()))
for k,v in res.items(): print(f"{k:9}",v)
for k,v in detail.items(): print(k,len(v),v[:6])
