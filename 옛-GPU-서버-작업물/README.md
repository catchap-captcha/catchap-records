# 옛 GPU 서버에만 있던 소스 (2026-08-12 회수)

## 무엇인가

옛 GPU 서버 `Team1-GPU-01`(10.0.1.52)의
`/srv/codex-workspaces/ms/drag-captcha` 에 있었지만
**`catchap-captcha` 저장소에는 없던 소스 파일**들입니다.

★그 서버를 지우면 **이 파일들은 어디에도 남지 않습니다.** 그래서 여기로 옮겼습니다.

## 어떻게 골랐나

추측하지 않고 **저장소 파일 목록과 서버 파일 목록을 한 줄씩 대조**했습니다.

```
catchap-captcha 저장소   81개
옛 GPU 서버              124개
                        ─────
서버에만 있던 것          43개
  − __pycache__          제외 (파이썬이 자동 생성)
  − static/dist·deploy/dist-new  제외 (빌드 산출물 — 소스에서 다시 만듦)
                        ─────
✅여기에 넣은 것          26개
```

## 들어 있는 것

| 무엇 | 개수 | 설명 |
|---|---|---|
| `app/admin_auth.py` · `labeling.py` · `publisher.py` | 3 | ★라벨링 도구의 앱 모듈 — 저장소에 없었음 |
| `scripts/*.py` | 16 | 분류·번역·라벨링큐·레드팀·스모크 시험 |
| `deploy/incoming/*` | 5 | 팀원이 넘겨 준 파일 |
| `TEAM_LABELING_OPERATIONS.md` | 1 | 라벨링 운영 안내 |
| `rotate_secrets.py` | 1 | 최상위에 있던 것 (저장소에는 `scripts/` 아래) |

## ⚠️안 넣은 것과 그 이유

```
.env · .env.bak-*        ★자격증명. 절대 안 넣음
개인 폴더(/home/*)        ★.ssh/authorized_keys · .bash_history 가 들어 있어 통째로 안 담음
__pycache__               파이썬이 다시 만듦
static/dist·deploy/dist-new  빌드 산출물
```

★**`--exclude="*.env"` 로는 `.env.bak-catchap_dev_db` 를 못 거릅니다.**
처음 묶었을 때 실제로 걸려 나와서, 개인 폴더를 통째로 담는 방식을 버렸습니다.

## 검사

넣기 전에 **자격증명 모양이 있는지** 전 파일을 훑었습니다.

```
검사한 것   SECRET_KEY= · PASSWORD= · AKIA…  형태
결과        ★0건
```

## 왜 `catchap-captcha` 가 아니라 여기인가

`catchap-captcha` 는 **살아 있는 서비스 저장소**입니다. main 에 넣으면 CI 가 돌고
이미지가 다시 구워집니다. 검토 없이 옛 파일을 넣을 곳이 아닙니다.

여기는 **지난 결과물 보관소**입니다. 나중에 필요하면 여기서 꺼내
정식으로 검토해 서비스 저장소에 올리면 됩니다.
