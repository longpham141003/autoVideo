# autoVideo Update — Huong dan ap dung

Ban cap nhat nay port cac pattern tot tu `review-phim` sang `autoVideo`.

## File moi

| File | Muc dich |
|---|---|
| `config.yaml` | Cau hinh tap trung (veo3, llm, tts, video, shorts, api_keys) |
| `core/config.py` | Loader YAML + override bang bien moi truong + truy cap dot-notation |
| `core/manifest.py` | Quan ly trang thai theo stage, fail-resume (s0 → s6) |
| `core/log.py` | Log ra console + file + callback cho UI |
| `core/keypool.py` | Xoay vong API key, cooldown luy thua khi dinh 429 |
| `providers/image_gen.py` | **Quan trong nhat** — tao anh VEO3 goi API truc tiep, fallback qua browser khi 403 |
| `fix_veo3_token.py` | Cong cu quet selector de tim UI VEO3 moi |

## Cai dat

```bash
pip install pyyaml
```

## Cach dung

### 1. Doc config

```python
from core import get_config

cfg = get_config()
print(cfg.veo3.create_image_model)   # "Nano Banana pro"
print(cfg.llm.model)                 # "gemini-2.5-flash"
```

### 2. Theo doi tien trinh job (fail-resume)

```python
from pathlib import Path
from core import create_manifest, stage_start, stage_done, get_next_pending_stage

job = Path("Projects/my_story")
create_manifest(job, "my_story")

stage_start(job, "s4_images")
# ... tao anh ...
stage_done(job, "s4_images", outputs={"count": 42})

print(get_next_pending_stage(job))   # "s5_video"
```

Chay lai bi loi giua chung? `get_next_pending_stage()` cho biet phai bat dau lai tu dau.

### 3. Tao anh VEO3 (thay the flow cu)

```python
import asyncio
from providers import ImageGenProvider, BatchImageRunner

provider = ImageGenProvider(
    session_id=SESSION_ID,
    project_id=PROJECT_ID,
    access_token=ACCESS_TOKEN,
    cookie=COOKIE,
    model="Nano Banana pro",
    output_dir="Projects/my_story/images",
)

# Tuy chon: gan Playwright page de fallback khi bi 403
provider.set_browser_page(page)

runner = BatchImageRunner(
    provider,
    prompts=[{"id": 1, "prompt": "a rainy alley at night"}],
    wait_between=15,
    max_concurrent=3,
)
result = asyncio.run(runner.run())
print(result["success"], result["failed"])
```

### 4. Xoay vong API key

```python
from core.keypool import create_gemini_pool, retry_with_pool

pool = create_gemini_pool(["key1", "key2", "key3"])
result = retry_with_pool(pool, lambda key: call_gemini(key, prompt))
```

## Sua loi tao anh VEO3

Luong cu (`veo3-local/A_workflow_get_token.py`) phu thuoc vao selector Chrome/Playwright, va cac selector nay da hong khi VEO doi giao dien.

Cach xu ly:

1. Chay `python fix_veo3_token.py` — mo Chrome, dang nhap thu cong, roi Enter de quet.
2. Doi chieu selector no in ra voi selector dang hardcode trong `A_workflow_get_token.py`.
3. Cap nhat selector.
4. Sau khi lay duoc token, chuyen sang goi `providers.image_gen.ImageGenProvider` thay vi dieu khien UI — provider goi thang API nen khong con phu thuoc giao dien nua.

## Vi sao provider moi on dinh hon

- **API truc tiep truoc, browser sau** — chi dung trinh duyet khi API tra 403.
- **`_extract_media` duyet de quy** — tim URL anh o bat ky vi tri nao trong response, khong vo khi Google doi schema.
- **Retry co backoff** — 15s, 30s, 45s.
- **Gioi han song song bang semaphore** — tranh bi rate limit.
