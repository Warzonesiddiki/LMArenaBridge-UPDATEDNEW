import os
import shutil
import sys
import tempfile
from pathlib import Path


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path or os.getcwd()))


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT_NORM = _norm_path(str(REPO_ROOT))

if not any(_norm_path(p) == REPO_ROOT_NORM for p in sys.path):
    sys.path.insert(0, str(REPO_ROOT))


# --- Per-test config/models isolation -------------------------------------
# Several production code paths write config as a side effect (e.g. browser
# session capture) using main.CONFIG_FILE. Tests that don't use
# BaseBridgeTest (which patches CONFIG_FILE itself) previously wrote to the
# real ./config.json in the repo root. Point every test at a throwaway
# temp dir; BaseBridgeTest may re-point it to its own temp dir on top.

_isolation: dict[str, tuple[str, str, str]] = {}


def pytest_runtest_setup(item):  # noqa: ANN001
    try:
        from src import main
    except Exception:
        return
    tmp = tempfile.mkdtemp(prefix="lmab-test-")
    _isolation[item.nodeid] = (main.CONFIG_FILE, main.MODELS_FILE, tmp)
    main.CONFIG_FILE = str(Path(tmp) / "config.json")
    main.MODELS_FILE = str(Path(tmp) / "models.json")


def pytest_runtest_teardown(item, nextitem):  # noqa: ANN001
    saved = _isolation.pop(item.nodeid, None)
    if not saved:
        return
    orig_config, orig_models, tmp = saved
    try:
        from src import main

        main.CONFIG_FILE = orig_config
        main.MODELS_FILE = orig_models
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)
