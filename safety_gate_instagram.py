# -*- coding: utf-8 -*-
"""露出セーフティゲート（Instagram向け・中間ティア）。

方針: 軽め水着(=COVERED系)はOK、乳首/性器/尻の露出(=EXPOSED系)だけ弾く。
- 画像は直接、動画はフレーム抽出して NudeNet で判定。
- 判定不能・エラーは全て False（fail-closed = 疑わしきは投稿しない）。
無人投稿でアカウントを飛ばさないための最後の砦。
"""
import os
import tempfile
from pathlib import Path

# 弾く = 露出系のみ（COVERED=水着 は通す）
NUDE_STRICT_CLASSES = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
}
NUDE_THRESHOLD = 0.40   # この確信度以上の露出検出で弾く（低めほど厳しい）
GATE_FRAMES = 6         # 動画から判定するフレーム数
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}

_detector = None


def _get_detector():
    global _detector
    if _detector is None:
        from nudenet import NudeDetector
        _detector = NudeDetector()
    return _detector


def _hits(detections):
    """露出系の検出があれば "クラス:スコア" を返す。無ければ None。"""
    for d in detections or []:
        cls = d.get("class") or d.get("label")
        try:
            score = float(d.get("score", d.get("confidence", 0)))
        except (TypeError, ValueError):
            score = 0.0
        if cls in NUDE_STRICT_CLASSES and score >= NUDE_THRESHOLD:
            return f"{cls}:{score:.2f}"
    return None


def _check_image(path):
    det = _get_detector()
    hit = _hits(det.detect(str(path)))
    return (False, f"exposed({hit})") if hit else (True, "ok")


def _check_video(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return False, "video_unreadable"
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if total > 0:
        step = max(1, total // (GATE_FRAMES + 1))
        idxs = [step * (i + 1) for i in range(GATE_FRAMES)]
    else:
        idxs = list(range(GATE_FRAMES))  # フレーム数不明でも先頭数枚
    det = _get_detector()
    tmpdir = tempfile.mkdtemp(prefix="iggate_")
    checked = 0
    try:
        for i, fi in enumerate(idxs):
            if total > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            fp = os.path.join(tmpdir, f"f{i}.jpg")
            cv2.imwrite(fp, frame)
            checked += 1
            hit = _hits(det.detect(fp))
            if hit:
                return False, f"exposed_frame({hit})"
    finally:
        cap.release()
    if checked == 0:
        return False, "no_frame_decoded"
    return True, "ok"


def check(path):
    """(ok: bool, reason: str)。判定不能・エラーは全て False（fail-closed）。"""
    try:
        p = Path(path)
        if not p.exists():
            return False, "not_found"
        ext = p.suffix.lower()
        if ext in IMAGE_EXT:
            return _check_image(p)
        if ext in VIDEO_EXT:
            return _check_video(p)
        return False, f"unsupported_ext({ext})"
    except Exception as e:
        return False, f"gate_error({type(e).__name__}:{e})"


if __name__ == "__main__":
    import sys
    for a in sys.argv[1:]:
        print(a, "->", check(a))
