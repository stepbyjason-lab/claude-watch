# claude-watch `--slides` 모드 설계 스펙 (2026-06-04)

> 상태: **설계 v2 — 사용자 승인 완료(2026-06-04).** 다음 단계: 구현 계획(plan) 문서 → 코드.
> 작업 위치: **fork `github.com/stepbyjason-lab/claude-watch`** (upstream: devinilabs/claude-watch), 로컬 `d:\code\_tools\claude-watch`, 브랜치 `feat/slides-mode`. 본 파일이 **canonical 스펙**(초안 `~/.claude/plans/...`는 사본).
> ⚠️ **§8(리뷰 반영 v2)이 authoritative** — §2~§6의 원안과 충돌하면 §8을 따른다.

---

## 1. 무엇을 · 왜

**무엇을**: claude-watch에 `--slides` 모드를 추가해, 강의/세미나 영상의 **모든 고유 슬라이드(p1~마지막)를 빠짐없이, 읽히게** 캡처한다.

**왜 (현재 동작의 한계)**: 현 파이프라인(`scenes.py`)은
1. **전체 프레임** scene 감지 `select='gt(scene,0.30)'` (+ t=0 앵커)
2. 간격 >45s면 45초마다 합성 floor 프레임
3. 80장 cap (점수 낮은 detected부터 드롭, floor는 보존)
4. 512px 추출

→ 일반 튜토리얼(토킹헤드+B롤)용. **슬라이드 덱에서 깨짐**: 전체 프레임 점수가 정적 밝은 슬라이드 + 움직이는 진행자 캠 + 바뀌는 자막에 휘둘려 **밝은색→밝은색 슬라이드 전환을 거의 못 잡음**(실측: 38분 강의 3개 감지). 45초 시간 floor에 의존 → ① <45s 분리 슬라이드 누락 ② 슬라이드 경계 불일치 ③ 512px는 슬라이드 글씨 판독 곤란. (하네스 영상에서 수동 crop-scene + 1080p→720p로 28장 전수 추출한 경험이 근거.)

## 2. 목표 / 성공 기준

- `--slides` 한 번 실행으로 강의의 **고유 슬라이드 전부**를 캡처(누락 0 지향), **중복 최소**(같은 슬라이드 오래 보여줘도 1장).
- 결과 프레임은 **가장 작은 본문 글씨까지 판독 가능**(720p 기준 검증됨).
- 기존(비-slides) 동작은 **무변경**.
- 회귀 fixture: 하네스 영상(정답 28장)에서 `--slides`가 ~28장 고유 슬라이드 ± 소수, 가독 OK.

## 3. 확정된 결정 (대화 합의)

| 결정 | 값 | 근거 |
|---|---|---|
| 구현 위치 | **fork** (devinilabs/claude-watch) | third-party + 우리가 pull → 로컬 패치는 충돌. fork면 upstream PR 여지 |
| 활성화 | **`--slides` 명시 플래그** (기본 off) | 단순·예측가능·오작동 없음. 자동감지는 YAGNI |
| 다운로드 해상도 | **기본 720p** (`--hi-res`면 1080p) | 감지는 해상도 무관(문제 없음). 720p 네이티브 추출=1:1 가독 sweet spot, 1080p보다 작고 빠름. 360p만 불가 |
| 추출 해상도 | **네이티브 720p**(1280폭, scale 0) | 리샘플 손실 0 |

## 4. 설계

### 4.1 파이프라인 (`--slides`일 때만 분기, 기존 경로 무변경)

```
download(720p) → [slides.py] crop-detect → floor(촘촘) → phash dedup → extract(720p full-frame)
```

1. **다운로드**: 포맷 선택을 720p(≤720 best, 예 yt-dlp `bv*[height<=720]`)로. `--hi-res`면 1080p.
2. **감지용 crop**: 프레임에서 진행자 캠 + 자막 밴드를 **제외한 슬라이드 영역**만 잘라 scene 감지.
   - crop은 **상대좌표(프레임 비율)** 로 정의 → 해상도 무관.
   - `--cam-corner {tr,tl,br,bl,none}` 기본 `tr` (≈우상단 20%w×20%h 제외)
   - `--caption {bottom,top,none}` 기본 `bottom` (≈하단 15%h 제외)
3. **감지**: crop 스트림에 `select='gt(scene,T)'`, **T 기본 0.10**(`--scene-threshold`로 조정). → 슬라이드 전환 후보 타임스탬프.
4. **촘촘 floor**: `--max-gap` 기본 **20s**(slides 모드)로 낮춰, scene이 놓친 밝은색→밝은색 슬라이드 보강.
5. **phash dedup**: 후보 프레임들의 **슬라이드 영역(crop) perceptual hash**(예: aHash/dHash 8×8) 계산. 직전 보존 프레임과 해밍거리 ≤ 임계(기본 ~5)면 **같은 슬라이드로 보고 드롭**. → 같은 슬라이드 중복 제거.
6. **추출**: 살아남은 타임스탬프에서 **전체 프레임**(crop 아님)을 네이티브 720p(scale=1280:-2), JPEG q2~3로 저장. (저장본은 슬라이드 전체 — 작은 캠은 허용.)
7. **예산 cap 해제**: slides 모드는 80장 cap 미적용(또는 매우 높게). 슬라이드는 보통 20~60장.

### 4.2 컴포넌트 (격리)

- **신규 `scripts/slides.py`**: `detect_slides(video, *, cam_corner, caption, threshold, max_gap, phash_dist) -> list[Scene(kind="slide")]`.
  - 내부: (a) crop vf 문자열 빌더(상대→픽셀, ffprobe로 W/H), (b) crop scene 감지, (c) floor 보강(기존 `apply_coverage_floor` 재사용), (d) 후보 프레임 임시 추출 → phash → dedup.
  - 입출력 계약: 입력=video Path + 옵션, 출력=시간순 `Scene` 리스트. `scenes.py`의 `Scene` dataclass 재사용(`kind="slide"` 추가).
- **`watch.py`**: `--slides/--cam-corner/--caption/--hi-res` 플래그 추가. `if args.slides:` → `slides.detect_slides(...)` 경로, else 기존 `detect_scenes/apply_coverage_floor/apply_budget_cap`. 다운로드 포맷도 분기.
- **`download.py`**: 포맷 인자 받게(현재 하드코딩 추정) → 720p/1080p 선택.
- **`frames.py`**: 변경 없음(width_px만 watch가 720으로 전달). 저장은 전체 프레임.
- **manifest**: 프레임 `kind="slide"`, 시간순. stdout에 `slides_extracted: N`.

### 4.3 데이터 흐름

`source(url) → meta → video(720p) → slides.detect_slides → Scene[] → frames.extract_frames(full,720p) → manifest + frames/*.jpg`

### 4.4 에러/엣지

- **캠 없음/자막 없음**: `--cam-corner none` / `--caption none`로 crop 축소. 기본값이 안 맞으면 사용자 override.
- **빌드/애니메이션 슬라이드**(불릿 점진 노출): phash 임계로 흡수(같은 슬라이드의 미세변화는 dedup). 임계 너무 빡세면 분리됨 → `--phash-dist` 조정.
- **페이지번호 점프**(예 p24→p26): 정보용일 뿐, 누락 아님. (OCR 검증은 out of scope.)
- **ffmpeg 실패/저화질만 존재**: 720p 없으면 best 폴백 + 경고.
- **cp949/인코딩**: 기존 우회(PYTHONIOENCODING) 영향권 — 슬라이드 모드도 동일.

### 4.5 테스트 / 회귀

- fixture: 하네스 영상(5buNm0pA1mg, 정답 28장 — title/질문/p04~p28/closing/outro, p25 없음).
- 단위: crop vf 빌더(비율→픽셀 정확), phash 해밍거리, floor 보강.
- 통합: `--slides` 1회 → 고유 슬라이드 수 ≈ 28(±소수), 중복 슬라이드 없음, 720p 가독.
- 비-slides 회귀: 기존 동작 바이트 동일성(스냅샷) — 무변경 보장.

## 5. Out of scope (YAGNI)

- 슬라이드 영역 **자동 감지**(캠 위치 자동 추정) — 실제 다른 레이아웃 영상 나오면 그때.
- 페이지번호 **OCR 검증**.
- 슬라이드 **내용 자동 dedup across decks**.
- 저장본에서 캠 자동 crop(슬라이드 잘림 위험).

## 6. 리스크 / 미해결

- (R1) 기본 crop 비율(캠 20%/자막 15%)이 실제 웹세미나마다 다를 수 있음 → 기본이 빗나가면 캠/자막이 scene 노이즈로 들어와 과검출. 완화: override 플래그 + 회귀로 기본값 튜닝.
- (R2) phash 임계 단일값으로 모든 덱 커버 가능한가 — 텍스트 위주 덱 vs 다이어그램 덱 민감도 차이.
- (R3) 720p에 없는 초소형 글씨 덱 — `--hi-res` 탈출구로 충분한가.
- (R4) fork 유지보수: upstream 업데이트 시 rebase 부담.

## 7. 롤백

- fork 브랜치 작업 → 로컬은 fork 연결만. 문제 시 로컬 remote를 upstream으로 되돌리면 끝(원본 무변경).

---

## 8. 멀티렌즈 리뷰 반영 — v2 개정 (authoritative)

4개 렌즈(architect·devils-advocate·security·performance) 병렬 리뷰 결과를 반영한 **확정 설계**. 원안(§2~§6)과 충돌 시 본 절 우선.

### 8.0 가장 중요한 재정의 — 완전성 약속 (devils-advocate)
- ❌ 원안 "모든 슬라이드 누락 0" → **보장 불가**. phash dedup이 *구별되는* 두 슬라이드(예: 한 단어 하이라이트 차이, 점진 빌드)를 같은 것으로 합치면 그게 **진짜 누락**. dedup(중복 제거) ↔ completeness(구별 보존)는 본질적 충돌.
- ✅ **새 약속: "고완전성(high recall) + 검증 가능"**. 원칙:
  1. **과소수집보다 과대수집** — 애매하면 **버리지 말고 남긴다**. dedup은 **보수적**으로(아주 높은 유사도 = 거의 동일 프레임만 합침).
  2. **silent drop 금지 → flag** — phash 거리가 "경계 구간"(합칠지 말지 애매)인 쌍은 둘 다 남기고 manifest/stdout에 `review: near-dup @ t=A,t=B` 표시. 사람이 본다.
  3. 결과 성공 기준: **"슬라이드 누락 0, 중복은 소수 허용(검증가능)"** — 중복 몇 장은 OK, 누락은 안 됨.
- 임계 2단(`--phash-dist` 단일값의 텍스트덱 vs 다이어그램덱 민감도 문제 완화): `drop_dist`(이하면 확실 중복→drop) < `flag_dist`(사이면 플래그). 기본 보수값.

### 8.1 캐시 정체성 (architect C1/C2 — CRITICAL)
- 원안 치명결함: `library.slug_for`(source+focus만)·`scenes.json` 재사용이 **모드/해상도/플래그 무지** → `--slides` 실행이 이전 일반 실행의 360p `video.*`나 whole-frame `scenes.json`을 **조용히 재사용**해 모드 무력화.
- ✅ 수정: **slug에 `mode` + 다운로드 해상도 + 전체 detection 프로파일**(`slides_profile` = cam_corner·caption·scene_threshold·max_gap·drop_dist·flag_dist)을 포함 → slides·일반 실행이 **다른 library 디렉토리**를 갖고 `video.*`를 절대 공유 안 함. **어떤 slides 플래그를 바꿔도 새 slug → 자동 cache-bust**(L3 해결).
- **비-default(일반) 모드는 upstream 해시(`sha1(source|focus)`)를 그대로 보존** → 기존 캐시 라이브러리 무효화 0.
- slides 모드는 **별도 scene-cache 파일을 쓰지 않음**(옛 설계의 `slides.json` 폐기). slug 격리만으로 정확성 보장 — 플래그가 바뀌면 디렉토리 자체가 달라져 stale 재사용 불가. `detect_slides`는 매 실행 재감지(다운로드 대비 저렴).

### 8.2 upstream diff 최소화 (architect H2/H3 — fork 유지보수 R4)
- ❌ `kind="slide"` 추가 **안 함** — `apply_budget_cap`의 `{detected,floor}` 파티션 깨짐. `Scene` dataclass **byte-identical 유지**(가장 많이 재사용되는 타입, 0 diff).
- ✅ `scenes.detect_scenes(video, threshold, *, prefilter="")` **kwarg 1개 추가**(기본 "" → 기존 동작 동일). slides는 `prefilter="crop=..."`만 넘겨 **stderr 파서·t=0 앵커·fallback 전부 재사용**(40줄 복붙 회피).
- 추출 프로파일(720p full-frame)은 `Scene` 정체성이 아니라 **`extract_frames` 인자**로(이미 watch가 width 제어).

### 8.3 패스 최소화 (performance)
- 원안 3패스(감지 full-decode → phash용 임시추출 N seek → 최종추출 K seek) 중 **pass2/pass3는 같은 작업 분할** — 후보를 한 번만 추출하고 그 JPEG로 phash하면 최종추출(K seek)이 사라짐.
- ✅ **v1 구현 채택 = 1 full decode + N keyframe seek**: 감지 패스(`detect_scenes` + crop `prefilter`, null 출력) → 후보를 `extract_frames`로 **한 번만** 720p 추출(N seek) → 디스크 JPEG에서 phash dedup → loser만 `unlink`. **비디오 재접근 0**(survivor는 이미 디스크). N≈20~60 seek 허용.
- ⏸ **deferred 최적화** (v1 미채택): 감지 패스에서 후보를 동시 덤프(`ffmpeg -vf "crop=...,select=...,showinfo" -vsync 0 cand_%04d.jpg`)해 N seek까지 제거(1 decode + 0 seek). split-filter stateful 복잡성 + 저장본 full-frame 요구 때문에 v1 보류. 실측 후 필요 시 도입.
- 📝 **알려진 v1 편차**: phash가 후보 JPEG마다 ffmpeg를 1회 spawn(`ahash`)하므로 실제 비용은 **1 full decode + N seek + N hash-spawn**. 정상 규모(N≈수십~백)에선 +5~10s, `candidate_cap=800` 한계에선 더 큼. 위 단일덤프 최적화와 함께 **batch-hash**(한 번의 ffmpeg로 다수 8×8 추출)로 묶는 것을 후속 과제로 둠.

### 8.4 zero-dependency phash (architect M2)
- ❌ Pillow/imagehash 신규 의존성 추가 **안 함**(dev-workflow "의존성 추가" 게이트 + repo는 ffmpeg/yt-dlp shell-out만).
- ✅ ffmpeg로 후보의 슬라이드 영역을 **8×8 그레이스케일 raw**로 축소(`-vf "crop=...,scale=8:8,format=gray" -f rawvideo`) → 64바이트 읽어 평균 기준 aHash(64bit) 계산, 해밍거리. 표준 라이브러리만.

### 8.5 입력 검증 / 보안 (security HIGH·MEDIUM)
- `--cam-corner` `choices=["tr","tl","br","bl","none"]`, `--caption` `choices=["bottom","top","none"]` (argparse) **+ slides.py 진입 assert**(필터그래프 주입 차단).
- `--scene-threshold` **(0.0,1.0) 범위 검증**(nan/음수 → 전프레임 매치 방지), `--phash-dist` [0,64].
- **cap 제거 보완**: 후보 프레임 **하드 상한**(예 800장) 초과 시 경고+중단(저threshold×장시간 DoS 방지). 임시 추출은 `tempfile.TemporaryDirectory`.
- `source` **비-http(s) 스킴 거부**(`file://`/`ftp://` → yt-dlp 로컬파일 유출 차단; 로컬 경로 입력은 기존 Path 분기 유지). yt-dlp **`--no-playlist` 유지**(refactor 시 누락 금지).
- `--out-dir` wipe 전 **`frames_dir.resolve().relative_to(LIBRARY_ROOT.resolve())` 컨테인먼트 체크**.
- 다운로드 포맷은 **enum/bool**(720p/1080p/best)로 내부 매핑, **raw yt-dlp `-f` 문자열 노출 금지**.
- ffmpeg `-i`에 **`-protocol_whitelist file`**(로컬 파일만), **list-form subprocess·`-nostdin` 유지**.
- 다운로드 포맷 문자열(확정): 720p=`bv*[height<=720]+ba/b[height<=720]/best`, 1080p=`bv*[height<=1080]+ba/b[height<=1080]/best`. **기존 `download_video` 기본 인자값은 현재 문자열과 byte-identical 유지**(비-slides·upstream 무영향).

### 8.6 네이티브 추출 정정 (architect M3)
- 원안 "frames.py 변경 없음 + scale=720" **오류** — `scale=720:-2`는 720p 소스를 폭 720으로 **다운스케일**(네이티브 아님). ✅ slides는 **width_px=1280**(720p 소스와 1:1) 또는 `extract_frames`에 `native: bool`(스케일 필터 생략) 추가. frames.py 델타 명시(하위호환 유지).

### 8.7 통합 지점 / 소비자 (architect H4·M4)
- watch.py `main()`에 `if args.slides` **인라인 금지**(focus×slides×cached 조합 폭발) → **`select_scenes(video, meta, args, focus)` 전략 시seam**로 분기, main은 선형 유지.
- **SKILL.md 변경집합 포함** — slides 모드용 노트 템플릿 분기("one slide = one section", floor/detected 구분 없음, `slides_extracted`·`review: near-dup` 소비). 안 그러면 Claude가 슬라이드덱에 토킹헤드 템플릿 적용.

### 8.8 detect_slides 분해 (architect H1)
- 단일 거대 함수 금지 → 순수 단위로: `build_crop_vf(w,h,cam_corner,caption)->str`(순수, §4.5 단위테스트), `detect_slide_cuts(video,crop_vf,threshold)->Scene[]`(detect_scenes 재사용), `phash_dedup(frame_paths,drop_dist,flag_dist)->(keep_idx,flagged_pairs)`(순수, 디스크 JPEG만). `detect_slides`는 ~20줄 오케스트레이터.

### 8.9 잔여 검토 대상 (구현 전 미해결 — writing-plans에서 확정)
- 기본 crop 비율(캠 20%·자막 15%) 실측 튜닝값 — 회귀 fixture(하네스 28장)로 보정.
- crop 빌더: 캠/자막 영역 겹침·`none none`(=full frame degenerate)·잔여영역<50% 시 경고/에러 정책(L2).
- floor 프레임도 phash dedup 대상임을 명시(L1, 순서: floor→phash).

### 8.10 검토했으나 채택 안 한 대안 (devils-advocate)
- **companion 스크립트**: 사용자가 fork 선택(upstream PR 여지) → 유지. 단 fork rebase 부담(R4)은 §8.2로 최소화.
- **슬라이드 영역 자동 감지**: v1 YAGNI 유지(다른 레이아웃 영상 등장 시). 기본 crop+override로 시작.
- **crop 없이 threshold만 낮추기**: 캠/자막 노이즈가 그대로 → 부적합(실측 근거).
- **OCR 페이지번호 완전성 검증**: out of scope. 단 §8.0 flag 메커니즘이 "검증 가능"의 경량 대체.
