import os
import uuid
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = Path(os.getenv("WORK_DIR", BASE_DIR / "work"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "outputs"))
WORK_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 部署时建议在 Render/Railway 环境变量里设置 RENDER_API_KEY。
# 扣子里的 render_api_key 必须和这里一致。
API_KEY = os.getenv("RENDER_API_KEY", "123456")
FONT_PATH_ENV = os.getenv("FONT_PATH", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

app = FastAPI(title="大字报视频 FFmpeg 云端渲染接口", version="1.1.0")


class Line(BaseModel):
    text: str
    color: str = "white"
    start: Optional[float] = None
    end: Optional[float] = None


class FontConfig(BaseModel):
    size: int = 86
    stroke_color: str = "black"
    stroke_width: int = 7
    position: str = "center"


class RenderPayload(BaseModel):
    video_url: str
    title: str = "大字报视频"
    duration: float = 7
    ratio: str = "9:16"
    background_blur: bool = True
    # all = 全部大字常驻画面；timed = 按 start/end 逐行显示
    display_mode: str = Field("all", description="all/timed")
    font: FontConfig = Field(default_factory=FontConfig)
    lines: List[Line]


def require_key(authorization: Optional[str], x_api_key: Optional[str]):
    if API_KEY == "":
        return
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token and x_api_key:
        token = x_api_key.strip()
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="render_api_key 错误或缺失")


def find_font() -> str:
    candidates = []
    if FONT_PATH_ENV:
        candidates.append(FONT_PATH_ENV)
    candidates += [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return p
    raise RuntimeError("没有找到可用中文字体。请在环境变量 FONT_PATH 指定字体路径。")


def ffmpeg_escape(path: str) -> str:
    p = str(path).replace("\\", "/")
    p = p.replace(":", r"\:")
    p = p.replace("'", r"\'")
    return p


def color_value(color: str) -> str:
    c = (color or "white").lower().strip()
    if c == "yellow":
        return "#FFD900"
    if c == "red":
        return "#FF2D2D"
    return "#FFFFFF"


def download_file(url: str, out_path: Path):
    headers = {"User-Agent": "Mozilla/5.0 dazibao-render/1.1"}
    with requests.get(url, stream=True, timeout=180, headers=headers) as r:
        r.raise_for_status()
        total = 0
        max_bytes = int(os.getenv("MAX_INPUT_MB", "300")) * 1024 * 1024
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError(f"输入视频超过限制：{max_bytes // 1024 // 1024}MB")
                f.write(chunk)


def run_cmd(cmd: List[str], timeout: int = 900):
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-5000:])
    return p


def build_filter(payload: RenderPayload, font_path: str, text_dir: Path) -> str:
    width, height = 1080, 1920
    lines = [x for x in payload.lines if x.text.strip()]
    if not lines:
        raise RuntimeError("lines 为空")

    # 竖屏 9:16：裁切填满 + 强模糊，符合大字报背景效果。
    blur_part = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=12:1[base0]"
    )

    # 字号自动适配：避免字太长超出屏幕。
    max_len = max(len(x.text.strip()) for x in lines)
    requested = int(payload.font.size or 86)
    auto_size = int(980 / max(max_len, 1))
    font_size = max(48, min(requested, auto_size, 96))
    if len(lines) >= 10:
        font_size = min(font_size, 64)
    elif len(lines) >= 8:
        font_size = min(font_size, 72)

    line_gap = int(font_size * 1.35)
    total_h = line_gap * len(lines)
    y0 = int((height - total_h) / 2)
    if y0 < 110:
        y0 = 110

    filters = [blur_part]
    prev = "base0"
    fontfile = ffmpeg_escape(font_path)

    display_mode = (payload.display_mode or "all").lower().strip()
    for i, line in enumerate(lines):
        text_file = text_dir / f"line_{i}.txt"
        text_file.write_text(line.text.strip(), encoding="utf-8")
        textfile = ffmpeg_escape(str(text_file))
        y = y0 + i * line_gap
        fontcolor = color_value(line.color)
        out = f"v{i}"

        enable = ""
        if display_mode == "timed" and line.start is not None and line.end is not None:
            start = max(0.0, float(line.start))
            end = max(start + 0.1, float(line.end))
            enable = f":enable='between(t,{start},{end})'"

        draw = (
            f"[{prev}]drawtext=fontfile='{fontfile}':textfile='{textfile}':"
            f"fontcolor={fontcolor}:fontsize={font_size}:"
            f"borderw={int(payload.font.stroke_width or 7)}:bordercolor={payload.font.stroke_color or 'black'}:"
            f"x=(w-text_w)/2:y={y}{enable}[{out}]"
        )
        filters.append(draw)
        prev = out

    filters.append(f"[{prev}]format=yuv420p[v]")
    return ";".join(filters)


@app.get("/health")
def health():
    return {
        "ok": True,
        "message": "大字报视频渲染接口正常",
        "font": find_font(),
    }


@app.post("/render")
def render_video(
    payload: RenderPayload,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
):
    require_key(authorization, x_api_key)

    if not payload.video_url:
        raise HTTPException(status_code=400, detail="缺少 video_url")
    if not payload.lines:
        raise HTTPException(status_code=400, detail="缺少 lines")

    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_video = job_dir / "input.mp4"
    output_video = OUTPUT_DIR / f"dazibao_{job_id}.mp4"

    try:
        font_path = find_font()
        download_file(payload.video_url, input_video)
        duration = max(3.0, min(float(payload.duration or 7), 30.0))
        filter_complex = build_filter(payload, font_path, job_dir)

        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(input_video),
            "-t", str(duration),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", os.getenv("FFMPEG_PRESET", "veryfast"),
            "-crf", os.getenv("FFMPEG_CRF", "23"),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(output_video),
        ]
        run_cmd(cmd, timeout=int(os.getenv("FFMPEG_TIMEOUT", "900")))

        base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
        return {
            "success": True,
            "video_url": f"{base}/outputs/{output_video.name}",
            "message": "成品视频已生成",
            "job_id": job_id,
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": str(e),
                "job_id": job_id,
            },
        )
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@app.get("/outputs/{filename}")
def get_output(filename: str):
    # 防止路径穿越。
    safe_name = Path(filename).name
    path = OUTPUT_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在或服务重启后已丢失")
    return FileResponse(path, media_type="video/mp4", filename=safe_name)
