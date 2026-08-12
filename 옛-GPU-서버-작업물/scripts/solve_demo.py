import json, math, uuid, urllib.request, urllib.error, sys
sys.path.insert(0,".")
B="http://127.0.0.1:8000"
from app.config import settings
from app.db import Database
SK=settings.site_key; SEC=settings.site_secret
def post(path, body, headers):
    req=urllib.request.Request(B+path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type":"application/json",**headers})
    try:
        r=urllib.request.urlopen(req,timeout=15); return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)

sess="lecdemo-"+uuid.uuid4().hex[:12]
# ① 챌린지 생성 (강의 바인딩)
st,ch=post("/api/captcha/challenges",{"purpose":"lecture","session_id":sess,"lecture_id":"LEC-DEMO-01","playback_position":312.5},{"X-Captcha-Site-Key":SK})
cid=ch["challenge_id"]
print("① 챌린지 발급:",cid[:8],"| 지시문:",ch["instruction"])
# 정답(target temp id) 조회
db=Database(settings)
with db.connection(True) as c, c.cursor() as cur:
    cur.execute("SELECT m.temporary_object_id t,o.bbox_x x,o.bbox_y y,o.bbox_width w,o.bbox_height h FROM captcha_challenge_objects m JOIN captcha_objects o ON o.id=m.object_id WHERE m.challenge_id=%s AND o.role='target'",(cid,))
    targets=cur.fetchall()
tids=[t["t"] for t in targets]
print("② 정답 객체:",len(tids),"개")
# ③ 사람같은 행동 이벤트 생성 (드래그 곡선+속도변화)
import time
evs=[{"type":"challenge_loaded","object_id":None,"x":None,"y":None,"timestamp_ms":0}]
tobj=targets[0]; sx=tobj["x"]+tobj["w"]/2; sy=tobj["y"]+tobj["h"]/2
dzx,dzy=0.845,0.805  # 정답존 중앙
t=350
evs.append({"type":"object_enter","object_id":tids[0],"x":sx,"y":sy,"timestamp_ms":t})
for i in range(4):  # 탐색 이동
    t+=70+i*15; evs.append({"type":"pointer_move","object_id":tids[0],"x":min(1,sx+0.02*math.sin(i)),"y":min(1,sy+0.015*i),"timestamp_ms":t})
t=1150
evs.append({"type":"pointer_down","object_id":tids[0],"x":sx,"y":sy,"timestamp_ms":t})
evs.append({"type":"drag_start","object_id":tids[0],"x":sx,"y":sy,"timestamp_ms":t})
n=14
for i in range(1,n+1):
    f=i/n; cx=sx+(dzx-sx)*f+0.05*math.sin(f*math.pi); cy=sy+(dzy-sy)*f-0.04*math.sin(f*math.pi)
    t+=40+int(35*abs(math.sin(f*3.3)))
    evs.append({"type":"pointer_move","object_id":tids[0],"x":min(1,max(0,cx)),"y":min(1,max(0,cy)),"timestamp_ms":t})
t+=60; evs.append({"type":"drop","object_id":tids[0],"x":dzx,"y":dzy,"timestamp_ms":t})
evs.append({"type":"selection_add","object_id":tids[0],"x":dzx,"y":dzy,"timestamp_ms":t})
evs.append({"type":"submit","object_id":None,"x":None,"y":None,"timestamp_ms":t+120})
dur=t+120
# ④ verify
st,res=post(f"/api/captcha/challenges/{cid}/verify",{"selected_object_ids":tids,"session_id":sess,"duration_ms":dur,"events":evs},{"X-Captcha-Site-Key":SK})
print("③ 검증 결과:",res if not res.get("success") else {"success":True,"token":res["captcha_token"][:20]+"..."})
if not res.get("success"): print("   (행동점수/정답 문제로 토큰 미발급)"); sys.exit()
tok=res["captcha_token"]
# ⑤ 인강 서버가 토큰 검증
st,vr=post("/api/verify-token",{"token":tok,"session_id":sess,"lecture_id":"LEC-DEMO-01"},{"X-Captcha-Site-Secret":SEC})
print("④ 인강서버 /api/verify-token:",vr)
st,vr2=post("/api/verify-token",{"token":tok,"session_id":sess,"lecture_id":"LEC-DEMO-01"},{"X-Captcha-Site-Secret":SEC})
print("⑤ 같은 토큰 재검증(1회용):",vr2)
