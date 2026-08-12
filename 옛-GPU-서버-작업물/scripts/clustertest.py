import sys, json, uuid, urllib.request, urllib.error; sys.path.insert(0,".")
from app.config import settings
from app.db import Database
from scripts.redteam import human  # 결정적(seed 고정) 프로필 = 같은 지문
B="http://127.0.0.1:8000"; SK=settings.site_key
def post(path, body, headers):
    req=urllib.request.Request(B+path,data=json.dumps(body).encode(),method="POST",headers={"Content-Type":"application/json",**headers})
    try: r=urllib.request.urlopen(req,timeout=15); return json.load(r)
    except urllib.error.HTTPError as e: return json.load(e)
db=Database(settings)
print(f"클러스터 차단 임계값: {settings.cluster_block_size} (세션)")
ev,dur=human(0)  # 고정 프로필(모든 세션 동일 지문)
events=[e.model_dump() for e in ev]
ok=blocked=0
for i in range(1,13):
    sess=f"tool-{uuid.uuid4().hex[:10]}"
    ch=post("/api/captcha/challenges",{"purpose":"lecture","session_id":sess},{"X-Captcha-Site-Key":SK})
    cid=ch["challenge_id"]
    with db.connection(True) as c, c.cursor() as cur:
        cur.execute("SELECT m.temporary_object_id t FROM captcha_challenge_objects m JOIN captcha_objects o ON o.id=m.object_id WHERE m.challenge_id=%s AND o.role='target'",(cid,))
        tids=[r["t"] for r in cur.fetchall()]
    res=post(f"/api/captcha/challenges/{cid}/verify",{"selected_object_ids":tids,"session_id":sess,"duration_ms":dur,"events":events},{"X-Captcha-Site-Key":SK})
    if res.get("success"): ok+=1; tag="✅토큰발급"
    elif res.get("reason")=="tool_cluster": blocked+=1; tag="🛡클러스터차단"
    else: tag=f"기타({res})"
    print(f"  세션 {i:2d}: {tag}")
print(f"\n결과: 통과 {ok} · 클러스터차단 {blocked}")
