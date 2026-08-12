import sys, math, random; sys.path.insert(0,".")
from app.main import summarize, BehaviorEvent
from app.config import settings
def ev(t,x=None,y=None,ts=0):
    oid = None if t in ("challenge_loaded","submit") else "o"
    return BehaviorEvent(type=t,object_id=oid,x=x,y=y,timestamp_ms=ts)
SX,SY,DX,DY=0.3,0.4,0.845,0.805
def human(seed=0):
    r=random.Random(seed); e=[ev("challenge_loaded",ts=0)]
    sx,sy=0.25+r.random()*0.2,0.35+r.random()*0.2; t=300+int(r.random()*200)
    e.append(ev("object_enter",sx,sy,t))
    for i in range(3+r.randint(0,3)): t+=55+int(r.random()*60); e.append(ev("pointer_move",sx+0.02*math.sin(i+r.random()),sy+0.015*i,t))
    t=900+int(r.random()*500); e.append(ev("pointer_down",sx,sy,t)); e.append(ev("drag_start",sx,sy,t))
    n=10+r.randint(0,8)
    for i in range(1,n+1):
        f=i/n; cx=sx+(DX-sx)*f+0.05*math.sin(f*math.pi)+0.01*(r.random()-0.5); cy=sy+(DY-sy)*f-0.04*math.sin(f*math.pi)
        t+=35+int(40*abs(math.sin(f*3.3+r.random())))
        e.append(ev("pointer_move",min(1,max(0,cx)),min(1,max(0,cy)),t))
    t+=50; e.append(ev("drop",DX,DY,t)); e.append(ev("selection_add",DX,DY,t)); return e,t
def bot_teleport(seed=0):
    e=[ev("challenge_loaded",ts=0)]; e.append(ev("pointer_down",SX,SY,60)); e.append(ev("drag_start",SX,SY,60))
    e.append(ev("pointer_move",DX,DY,90)); e.append(ev("drop",DX,DY,100)); e.append(ev("selection_add",DX,DY,100)); return e,100
def bot_line(seed=0):
    e=[ev("challenge_loaded",ts=0)]; t=150; e.append(ev("pointer_down",SX,SY,t)); e.append(ev("drag_start",SX,SY,t))
    N=8
    for i in range(1,N):
        f=i/N; t+=20; e.append(ev("pointer_move",SX+(DX-SX)*f,SY+(DY-SY)*f,t))
    t+=20; e.append(ev("drop",DX,DY,t)); e.append(ev("selection_add",DX,DY,t)); return e,t
def bot_smart(seed=0):
    r=random.Random(seed); e=[ev("challenge_loaded",ts=0)]; t=200+int(r.random()*300)
    e.append(ev("pointer_down",SX,SY,t)); e.append(ev("drag_start",SX,SY,t)); N=10
    for i in range(1,N):
        f=i/N; t+=25+int(r.random()*20)
        e.append(ev("pointer_move",SX+(DX-SX)*f+0.03*math.sin(f*3.14),SY+(DY-SY)*f+0.01*(r.random()-0.5),t))
    t+=25; e.append(ev("drop",DX,DY,t)); e.append(ev("selection_add",DX,DY,t)); return e,t
def outcome(rk):
    return "차단" if rk>=settings.behavior_block_score else ("추가검증" if rk>=settings.behavior_step_up_score else "통과")
def measure(name,gen,correct=True,n=40):
    import statistics as st
    rs=[summarize(gen(s)[0],({"o"} if correct else set()),{"o"},gen(s)[1],correct,{"ip_challenges_1m":1,"session_challenges_10m":1,"session_failures_10m":0},False)["risk_score"] for s in range(n)]
    outs={"통과":0,"추가검증":0,"차단":0}
    for rk in rs: outs[outcome(rk)]+=1
    tag = "✅통과됨(사람)" if name.startswith("사람") else ("⚠️통과(구멍)" if outs["통과"]>0 else "🛡차단됨")
    print(f"  {name}: risk 평균 {round(st.mean(rs),1)} (max {max(rs)}) | 통과{outs['통과']}·추가검증{outs['추가검증']}·차단{outs['차단']}  {tag}")
print(f"=== 임계값: 추가검증 ≥{settings.behavior_step_up_score}, 차단 ≥{settings.behavior_block_score} ===")
measure("사람흉내(정답)",human)
measure("봇-순간이동",bot_teleport)
measure("봇-직선등속",bot_line)
measure("봇-스마트(곡선지터)",bot_smart)
