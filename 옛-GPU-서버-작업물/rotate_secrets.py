#!/usr/bin/env python3
"""시크릿 로테이션. 새 값을 서버에서 생성해 .env를 갱신한다.
값은 화면에 출력하지 않는다(관리자키만 배포용 파일에 기록). 실행 후:
  1) NEW_ADMIN_KEYS.txt 를 읽어 각 팀원에게 안전하게 전달(채팅/메신저 평문 지양)
  2) 인강 연동 담당(하지영)에게 새 CAPTCHA_SITE_SECRET 전달(.env에서 읽기)
  3) 서비스 재기동으로 활성화 → 기존 키 즉시 무효
"""
import secrets, re, os, pathlib, sys

ENV = pathlib.Path("/srv/codex-workspaces/ms/drag-captcha/.env")
if not ENV.exists():
    print("ERR: .env 없음", ENV); sys.exit(1)

names = ["민서", "태형", "지영", "성원", "민용"]
new_admin = {n: "cak_" + secrets.token_urlsafe(24) for n in names}
updates = {
    "CAPTCHA_ADMIN_KEYS": ",".join(f"{n}:{k}" for n, k in new_admin.items()),
    "CAPTCHA_ADMIN_KEY": "cak_" + secrets.token_urlsafe(24),   # 단일 폴백도 새로
    "CAPTCHA_SITE_SECRET": "css_" + secrets.token_urlsafe(32),
    "APP_SECRET": secrets.token_urlsafe(32),
}

# .env 백업 후 upsert
bak = ENV.with_suffix(".env.pre-rotate")
bak.write_text(ENV.read_text()); os.chmod(bak, 0o600)
lines = ENV.read_text().splitlines()
for key, val in updates.items():
    pat = re.compile(rf"^{re.escape(key)}=")
    replaced = False
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = f"{key}={val}"; replaced = True; break
    if not replaced:
        lines.append(f"{key}={val}")
ENV.write_text("\n".join(lines) + "\n")

dist = ENV.parent / "NEW_ADMIN_KEYS.txt"
dist.write_text("CatChap 새 관리자 키 (배포 후 이 파일 삭제)\n\n" +
                "\n".join(f"{n}: {k}" for n, k in new_admin.items()) + "\n")
os.chmod(dist, 0o600)

print("✅ 로테이션 완료 — .env 갱신됨 (백업: .env.pre-rotate)")
print("• 관리자키 5개 → ", dist, "(읽어서 팀 배포 후 삭제)")
print("• 새 CAPTCHA_SITE_SECRET·APP_SECRET → .env 안에 있음(값 미출력)")
print("• 활성화: systemctl --user restart drag-captcha.service  (재기동해야 새 키 적용)")
print("⚠️ 재기동하면 기존(노출됐던) 키는 즉시 무효. 배포 완료 후 재기동 권장.")
