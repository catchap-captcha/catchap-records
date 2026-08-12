import sys, json, uuid, urllib.request, urllib.error; sys.path.insert(0,".")
from app.config import settings
from app.db import Database
from scripts.redteam import human
B="http://127.0.0.1:8000"; SK=settings.site_key
def post(p,b,h):
    r=urllib.request.Request(B+p,data=json.dumps(b).encode(),method="POST",headers={"Content-Type":"application/json",**h})
    try: return json.load(urllib.request.urlopen(r,timeout=15))
    except urllib.error.HTTPError as e: return json.load(e)
db=Database(settings)
def solve(signals,label):
    sess=f"auto-{uuid.uuid4().hex[:10]}"
    ch=post("/api/captcha/challenges",{"purpose":"lecture","session_id":sess},{"X-Captcha-Site-Key":SK}); cid=ch["challenge_id"]
    with db.connection(True) as c, c.cursor() as cur:
        cur.execute("SELECT m.temporary_object_id t FROM captcha_challenge_objects m JOIN captcha_objects o ON o.id=m.object_id WHERE m.challenge_id=%s AND o.role='target'",(cid,))
        tids=[r["t"] for r in cur.fetchall()]
    ev,dur=human(1)  # 사람같은 마우스(개별 통과 수준)
    res=post(f"/api/captcha/challenges/{cid}/verify",{"selected_object_ids":tids,"session_id":sess,"duration_ms":dur,"events":[e.model_dump() for e in ev],"client_signals":signals},{"X-Captcha-Site-Key":SK})
    out = "✅토큰발급" if res.get("success") else (f"🛡차단({res.get('risk_level')})" if res.get("blocked") else f"추가검증" if res.get("step_up") else str(res))
    print(f"  {label}: {out}")
solve({"webdriver":False,"headlessUA":False,"languages":2,"cores":8}, "정상 브라우저 신호")
solve({"webdriver":True,"headlessUA":False,"languages":2,"cores":8}, "webdriver=true(자동화)")
solve({"webdriver":False,"headlessUA":True,"languages":0,"cores":0}, "헤드리스 신호")
solve(None, "신호 없음(구버전/미전송)")
