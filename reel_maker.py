# -*- coding: utf-8 -*-
"""카드 이미지 여러 장을 인스타 릴스용 세로 영상(1080x1920, mp4)으로 만든다.

  · 카드(1080x1350)를 세로 화면 가운데에 놓고, 위아래는 카드 바탕색으로 채운다
  · 한 장씩 옆으로 밀려 넘어가는 전환
  · 배경음악은 assets/bgm/ 안의 파일 중 하나를 골라 넣는다(날짜마다 돌아가며).
    이 음악은 tools/make_bgm.py로 직접 만든 것이라 저작권 문제가 없다.
    (인스타 앱 안의 음원은 API로 붙일 수 없어서 영상 파일에 미리 넣는다)

ffmpeg은 imageio-ffmpeg 패키지에 들어 있는 것을 쓰고, 없으면 시스템의 ffmpeg을 쓴다.
"""
import glob
import hashlib
import os
import subprocess

from PIL import Image

BGM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "bgm")
SECONDS_PER_CARD = 4.0   # 카드 한 장을 보여주는 시간(초)
FADE = 0.4               # 넘어가는 전환 시간(초)
BGM_VOLUME = 0.6


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def pick_bgm(seed=""):
    """배경음악 파일 하나를 고른다. seed(보통 날짜)가 같으면 같은 곡이 나온다. 없으면 None."""
    tracks = sorted(glob.glob(os.path.join(BGM_DIR, "*.m4a")))
    if not tracks:
        return None
    n = int(hashlib.md5(str(seed).encode("utf-8")).hexdigest(), 16)
    return tracks[n % len(tracks)]


def reel_length(count):
    return count * (SECONDS_PER_CARD - FADE) + FADE


def make_reel(image_paths, out_path, seed="", bgm_path=None):
    """image_paths(2장 이상)를 이어 붙인 세로 영상을 out_path에 만들고 경로를 돌려준다."""
    if len(image_paths) < 2:
        raise ValueError("릴스는 카드 2장 이상이 필요합니다")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    r, g, b = Image.open(image_paths[0]).convert("RGB").getpixel((4, 4))
    bg = f"0x{r:02x}{g:02x}{b:02x}"
    total = reel_length(len(image_paths))
    bgm = bgm_path or pick_bgm(seed)

    cmd = [_ffmpeg(), "-y", "-loglevel", "error"]
    for p in image_paths:
        cmd += ["-loop", "1", "-t", str(SECONDS_PER_CARD), "-i", p]
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", bgm]
    else:   # 음악 파일이 없으면 무음 트랙(인스타는 소리 트랙이 있는 영상을 더 안정적으로 받는다)
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]

    n = len(image_paths)
    parts = [f"[{i}:v]scale=1080:1350,pad=1080:1920:0:285:color={bg},setsar=1,fps=30,format=yuv420p[v{i}]"
             for i in range(n)]
    last = "v0"
    for i in range(1, n):
        offset = i * (SECONDS_PER_CARD - FADE)
        parts.append(f"[{last}][v{i}]xfade=transition=slideleft:duration={FADE}:offset={offset:.2f}[x{i}]")
        last = f"x{i}"
    parts.append(f"[{n}:a]volume={BGM_VOLUME},afade=t=in:d=0.5,afade=t=out:st={total - 1.2:.2f}:d=1.2[a]")
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{last}]", "-map", "[a]", "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", out_path]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"릴스 영상 생성 실패: {done.stderr[-400:]}")
    return out_path
