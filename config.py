import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _path(name, default):
    return os.environ.get(name, default)


STUDY_ROOT = _path("LIVE_STUDY_ROOT", os.path.join(BASE_DIR, "直播研究数据"))
FFMPEG = _path(
    "LIVE_FFMPEG",
    os.path.join(
        BASE_DIR,
        "DouyinLiveRecorder_v4.0.7",
        "ffmpeg",
        "ffmpeg.exe",
    ),
)
PYTHON = _path("LIVE_PYTHON", "python")
