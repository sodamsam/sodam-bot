# -*- coding: utf-8 -*-
"""릴스 배경음악을 직접 만들어 assets/bgm/ 에 저장한다 (한 번만 실행하면 된다).

남의 음원을 쓰지 않고 소리를 계산으로 합성한다(전자피아노풍 화음 + 베이스 + 가벼운 리듬 + 짧은 멜로디).
그래서 저작권 걱정 없이 영상에 넣을 수 있다. 실행: python tools/make_bgm.py  (numpy, ffmpeg 필요)
"""
import os
import subprocess
import wave

import numpy as np

SR = 44100
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "bgm")


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def tone(freq, dur, kind="keys"):
    t = np.arange(int(SR * dur)) / SR
    if kind == "keys":      # 전자피아노풍: 배음 조금 + 부드럽게 사라짐
        w = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) + 0.12 * np.sin(6 * np.pi * freq * t)
        env = np.exp(-2.2 * t)
    elif kind == "bass":
        w = np.sin(2 * np.pi * freq * t) + 0.2 * np.sin(4 * np.pi * freq * t)
        env = np.exp(-1.6 * t)
    else:                   # bell: 멜로디용 맑은 소리
        w = np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * freq * 2.01 * t) * np.exp(-6 * t)
        env = np.exp(-3.5 * t)
    attack = np.minimum(1.0, t / 0.008)
    return w * env * attack


def kick(dur=0.25):
    t = np.arange(int(SR * dur)) / SR
    return np.sin(2 * np.pi * (48 + 70 * np.exp(-28 * t)) * t) * np.exp(-14 * t)


def hat(rng, dur=0.05):
    n = rng.standard_normal(int(SR * dur))
    n = np.diff(n, prepend=0)           # 높은 소리만 남긴다
    return n * np.exp(-90 * np.arange(len(n)) / SR)


def add(buf, start, sound, gain):
    i = int(start * SR)
    j = min(len(buf), i + len(sound))
    if i < len(buf):
        buf[i:j] += sound[: j - i] * gain


def make(name, bpm, root, prog, seed):
    """prog: 마디별 (근음에서의 반음 거리, 장/단) 목록. 8마디를 두 번 돌린다."""
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    bars = len(prog) * 2
    buf = np.zeros(int(SR * (bars * 4 * beat + 2)))
    scale = [0, 2, 4, 7, 9]             # 5음 음계(어느 화음에도 잘 어울린다)
    for b in range(bars):
        off, minor = prog[b % len(prog)]
        base = root + off
        chord = [base, base + (3 if minor else 4), base + 7, base + (10 if minor else 11)]
        t0 = b * 4 * beat
        for k, (pos, g) in enumerate([(0, 1.0), (1.5, 0.6), (2.5, 0.75)]):   # 화음을 리듬 있게
            for m in chord:
                add(buf, t0 + pos * beat + 0.012 * chord.index(m), tone(hz(m), 2.2 * beat), 0.11 * g)
        add(buf, t0, tone(hz(base - 24), 1.8 * beat, "bass"), 0.42)
        add(buf, t0 + 2.5 * beat, tone(hz(base - 24), 1.2 * beat, "bass"), 0.3)
        for q in range(4):
            if q in (0, 2):
                add(buf, t0 + q * beat, kick(), 0.5)
            add(buf, t0 + (q + 0.5) * beat, hat(rng), 0.10)
        if b >= 2:                      # 3번째 마디부터 짧은 멜로디
            for q in (0, 1, 2.5, 3):
                if rng.random() < 0.7:
                    note = root + 12 + scale[int(rng.integers(0, 5))] + (12 if rng.random() < 0.25 else 0)
                    add(buf, t0 + q * beat, tone(hz(note), 1.2 * beat, "bell"), 0.13)
    buf = np.tanh(buf * 1.3)
    buf = buf / np.max(np.abs(buf)) * 0.8
    # 살짝 넓게 들리도록 좌우에 아주 짧은 시차를 준다
    right = np.concatenate([np.zeros(180), buf[:-180]])
    stereo = np.stack([buf, right * 0.96], axis=1)
    os.makedirs(OUT, exist_ok=True)
    wav = os.path.join(OUT, name + ".wav")
    with wave.open(wav, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((stereo * 32767).astype(np.int16).tobytes())
    m4a = os.path.join(OUT, name + ".m4a")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", "aac", "-b:a", "128k", m4a], check=True)
    os.remove(wav)
    print(m4a)


if __name__ == "__main__":
    make("sodam_bgm_1", 88, 60, [(0, False), (9, True), (5, False), (7, False)], 1)     # C  Am F  G
    make("sodam_bgm_2", 80, 65, [(0, False), (7, False), (9, True), (5, False)], 2)     # F  C  Dm Bb
    make("sodam_bgm_3", 96, 62, [(9, True), (5, False), (0, False), (7, False)], 3)     # Bm G  D  A
