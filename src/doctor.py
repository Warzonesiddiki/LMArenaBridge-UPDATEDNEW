"""Offline diagnostics for a local LMArenaBridge checkout.

The doctor intentionally does **not** contact Arena, launch a browser, or
print secret values. It checks the Python environment, installed package
versions, the fetched Camoufox browser, and the local JSON state files.
Optionally, it can query the health endpoint of an already-running local
bridge.

Run from the repository root:

    python -m src.doctor
    python -m src.doctor --check-server
    python -m src.doctor --json
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from . import constants
from .config import validate_config

PASS = "pass"
WARN = "warn"
FAIL = "fail"

# Distribution name -> import module. "python-multipart" is the distribution;
# its import package is "multipart".
_REQUIRED_PACKAGES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "camoufox": "camoufox",
    "playwright": "playwright",
    "httpx": "httpx",
    "cloudscraper": "cloudscraper",
    "python-multipart": "multipart",
    "requests": "requests",
}


@dataclass(frozen=True)
class CheckResult:
    """One redacted diagnostic result."""

    name: str
    status: str
    detail: str
    remedy: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _version_tuple(version: str) -> tuple[int, ...]:
    """Return the leading numeric components of a version string."""
    parts: list[int] = []
    for raw in version.replace("-", ".").split("."):
        digits = ""
        for char in raw:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _distribution_version(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _import_error(module_name: str) -> Optional[str]:
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # Import errors are exactly what doctor reports.
        return f"{type(exc).__name__}: {exc}"
    return None


def check_python(version_info: tuple[int, ...] = sys.version_info[:3]) -> CheckResult:
    version = ".".join(str(part) for part in version_info)
    if version_info < (3, 10, 0):
        return CheckResult(
            "python",
            FAIL,
            f"Python {version} ({platform.python_implementation()})",
            "Install Python 3.10 or newer and recreate the virtual environment.",
        )
    return CheckResult(
        "python",
        PASS,
        f"Python {version} ({platform.python_implementation()}, {platform.system()})",
    )


def check_dependencies(
    version_getter: Callable[[str], Optional[str]] = _distribution_version,
    import_error_getter: Callable[[str], Optional[str]] = _import_error,
) -> list[CheckResult]:
    """Check installed distributions without importing the bridge app."""
    results: list[CheckResult] = []
    for distribution, module in _REQUIRED_PACKAGES.items():
        version = version_getter(distribution)
        if not version:
            results.append(
                CheckResult(
                    f"package:{distribution}",
                    FAIL,
                    "not installed",
                    "Install the pinned runtime set: pip install -r requirements.txt",
                )
            )
            continue

        import_error = import_error_getter(module)
        if import_error:
            results.append(
                CheckResult(
                    f"package:{distribution}",
                    FAIL,
                    f"installed {version}, but import failed ({import_error})",
                    "Recreate the virtual environment and reinstall requirements.txt.",
                )
            )
            continue

        status = PASS
        remedy = ""
        parsed = _version_tuple(version)
        if distribution == "playwright" and parsed >= (1, 63):
            status = FAIL
            remedy = (
                "Camoufox 0.5.8 requires playwright<1.63. "
                "Reinstall the pinned requirements.txt set."
            )
        elif distribution == "camoufox" and parsed[:2] != (0, 5):
            status = WARN
            remedy = "The verified set uses Camoufox 0.5.8; other series are untested here."
        results.append(
            CheckResult(f"package:{distribution}", status, f"installed {version}", remedy)
        )
    return results


def _camoufox_install_info() -> tuple[Path, str]:
    """Read Camoufox's install location without triggering a download."""
    import camoufox.pkgman as pkgman

    return Path(pkgman.INSTALL_DIR), str(pkgman.LAUNCH_FILE[pkgman.OS_NAME])


def check_camoufox_browser(
    install_dir: Optional[Path] = None,
    launch_file: Optional[str] = None,
) -> CheckResult:
    """Check for a fetched Camoufox executable using a read-only scan.

    Camoufox's ``camoufox_path()`` helper can download a browser or perform
    compatibility cleanup, so the doctor deliberately only inspects its
    documented install directory and launch filename.
    """
    if install_dir is None or launch_file is None:
        try:
            detected_dir, detected_launch = _camoufox_install_info()
        except Exception as exc:
            return CheckResult(
                "camoufox-browser",
                FAIL,
                f"could not determine the Camoufox install path ({type(exc).__name__})",
                "Reinstall requirements.txt, then run: python -m camoufox fetch",
            )
        install_dir = install_dir or detected_dir
        launch_file = launch_file or detected_launch

    launch_name = Path(launch_file).name
    candidates: list[Path] = []
    if install_dir.exists():
        direct = install_dir / launch_file
        candidates.append(direct)
        try:
            candidates.extend(
                path for path in install_dir.rglob(launch_name) if path.is_file()
            )
        except OSError:
            pass

    for candidate in candidates:
        if candidate.is_file():
            if os.name != "nt" and not os.access(candidate, os.X_OK):
                return CheckResult(
                    "camoufox-browser",
                    WARN,
                    f"executable found but is not executable: {candidate}",
                    "Fix its permissions or rerun: python -m camoufox fetch",
                )
            return CheckResult("camoufox-browser", PASS, f"found {candidate}")

    if install_dir.exists():
        detail = f"Camoufox data directory exists, but {launch_name} was not found under {install_dir}"
    else:
        detail = f"Camoufox browser directory does not exist: {install_dir}"
    return CheckResult(
        "camoufox-browser",
        FAIL,
        detail,
        "Fetch the browser once: python -m camoufox fetch",
    )


def _load_json(path: Path) -> tuple[object, Optional[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _nonempty_string_count(value: object) -> int:
    if isinstance(value, list):
        return sum(1 for item in value if str(item or "").strip())
    return 1 if str(value or "").strip() else 0


def check_local_state(
    config_path: Path = Path(constants.CONFIG_FILE),
    models_path: Path = Path(constants.MODELS_FILE),
) -> list[CheckResult]:
    """Inspect config/models JSON without revealing any stored secrets."""
    results: list[CheckResult] = []
    config, config_error = _load_json(config_path)

    if config_error == "missing":
        results.append(
            CheckResult(
                "config-file",
                WARN,
                f"{config_path} does not exist yet",
                "Start the server once from this directory or create the file via the dashboard.",
            )
        )
        config = {}
    elif config_error:
        results.append(
            CheckResult(
                "config-file",
                FAIL,
                f"{config_path} could not be read as JSON ({config_error})",
                "Fix the JSON syntax or move the file aside and let the bridge recreate it.",
            )
        )
        config = {}
    elif not isinstance(config, dict):
        results.append(
            CheckResult(
                "config-file",
                FAIL,
                f"{config_path} must contain a JSON object",
                "Replace its top-level value with a JSON object.",
            )
        )
        config = {}
    else:
        results.append(CheckResult("config-file", PASS, f"read {config_path}"))
        schema_warnings = validate_config(config)
        if schema_warnings:
            results.append(
                CheckResult(
                    "config-schema",
                    WARN,
                    f"{len(schema_warnings)} known-field issue(s) will be repaired on load: "
                    + "; ".join(schema_warnings[:5]),
                    "Let the bridge rewrite config.json or fix the named fields manually; unknown keys are preserved.",
                )
            )
        else:
            results.append(
                CheckResult(
                    "config-schema",
                    PASS,
                    "known config fields have valid types/ranges; unknown keys are preserved",
                )
            )

    token_count = _nonempty_string_count(config.get("auth_tokens"))
    has_legacy_token = bool(str(config.get("auth_token") or "").strip())
    cookies = config.get("browser_cookies")
    has_arena_cookie = isinstance(cookies, dict) and bool(
        str(cookies.get("arena-auth-prod-v1") or "").strip()
    )
    auth_sources = []
    if token_count:
        auth_sources.append(f"{token_count} configured token(s)")
    if has_legacy_token:
        auth_sources.append("legacy auth_token")
    if has_arena_cookie:
        auth_sources.append("persisted arena-auth-prod-v1 cookie")
    if auth_sources:
        results.append(
            CheckResult("upstream-auth", PASS, "configured via " + ", ".join(auth_sources))
        )
    else:
        results.append(
            CheckResult(
                "upstream-auth",
                WARN,
                "no durable Arena authentication is configured",
                "Add your own arena-auth-prod-v1 token in the dashboard; browser/userscript flows may still supply one at runtime.",
            )
        )

    api_keys = config.get("api_keys")
    api_key_count = len(api_keys) if isinstance(api_keys, list) else 0
    if api_key_count:
        detail = f"{api_key_count} bridge API key(s) configured"
        if config.get("allow_missing_api_key") is True:
            results.append(
                CheckResult(
                    "bridge-api-keys",
                    WARN,
                    detail + "; allow_missing_api_key=true restores legacy missing-key access",
                    "Set allow_missing_api_key to false unless an empty-key client requires it.",
                )
            )
        else:
            results.append(CheckResult("bridge-api-keys", PASS, detail))
    else:
        results.append(
            CheckResult(
                "bridge-api-keys",
                WARN,
                "no bridge API keys configured; anonymous bootstrap access remains enabled",
                "Create an API key in the dashboard before exposing the bridge beyond a trusted local network.",
            )
        )

    if str(config.get("password") or "admin") == "admin":
        results.append(
            CheckResult(
                "dashboard-password",
                WARN,
                "the retained default dashboard password is still in use",
                "Change config.json's password before exposing the dashboard beyond localhost/trusted LAN use.",
            )
        )
    else:
        results.append(CheckResult("dashboard-password", PASS, "non-default password configured"))

    if str(config.get("cf_clearance") or "").strip():
        results.append(
            CheckResult(
                "cf-clearance",
                PASS,
                "a cf_clearance value is stored (presence only; expiry cannot be verified offline)",
            )
        )
    else:
        results.append(
            CheckResult(
                "cf-clearance",
                WARN,
                "no cf_clearance value is stored",
                "Start the bridge and use Refresh Tokens & Models; upstream availability still requires live verification.",
            )
        )

    models, models_error = _load_json(models_path)
    if models_error == "missing":
        results.append(
            CheckResult(
                "models-file",
                WARN,
                f"{models_path} does not exist yet",
                "Start the bridge or use Refresh Tokens & Models to populate the model list.",
            )
        )
    elif models_error:
        results.append(
            CheckResult(
                "models-file",
                FAIL,
                f"{models_path} could not be read as JSON ({models_error})",
                "Fix the JSON syntax or regenerate the model list from the dashboard.",
            )
        )
    elif not isinstance(models, list):
        results.append(
            CheckResult(
                "models-file",
                FAIL,
                f"{models_path} must contain a JSON list",
                "Regenerate the model list from the dashboard.",
            )
        )
    else:
        status = PASS if models else WARN
        remedy = "" if models else "Start the bridge or refresh models from the dashboard."
        results.append(
            CheckResult("models-file", status, f"{len(models)} model record(s) in {models_path}", remedy)
        )

    return results


def check_server(base_url: str, timeout: float = 5.0) -> CheckResult:
    """Query an already-running bridge's unauthenticated readiness endpoint."""
    url = base_url.rstrip("/") + "/api/v1/health"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = response.status
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            payload = {}
    except Exception as exc:
        return CheckResult(
            "server-health",
            FAIL,
            f"could not reach {url} ({type(exc).__name__}: {exc})",
            "Start the bridge with python -m src.main, then rerun with --check-server.",
        )

    checks = payload.get("checks") if isinstance(payload, dict) else None
    check_detail = ""
    if isinstance(checks, dict):
        check_detail = "; " + ", ".join(
            f"{name}={bool(value)}" if not isinstance(value, int) else f"{name}={value}"
            for name, value in checks.items()
        )
    if status_code == 200:
        return CheckResult(
            "server-health",
            PASS,
            f"{url} returned HTTP 200 (status={payload.get('status', 'unknown')}{check_detail})",
        )
    return CheckResult(
        "server-health",
        WARN,
        f"{url} returned HTTP {status_code} (status={payload.get('status', 'unknown')}{check_detail})",
        "The server is reachable but reports it is not ready to serve chat yet.",
    )


def collect_results(
    *,
    config_path: Path = Path(constants.CONFIG_FILE),
    models_path: Path = Path(constants.MODELS_FILE),
    server_url: Optional[str] = None,
) -> list[CheckResult]:
    results = [check_python()]
    results.extend(check_dependencies())
    results.append(check_camoufox_browser())
    results.extend(check_local_state(config_path, models_path))
    if server_url:
        results.append(check_server(server_url))
    return results


def format_results(results: list[CheckResult]) -> str:
    labels = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL"}
    lines = []
    for result in results:
        lines.append(f"[{labels[result.status]}] {result.name}: {result.detail}")
        if result.remedy:
            lines.append(f"       -> {result.remedy}")
    failed = sum(result.status == FAIL for result in results)
    warned = sum(result.status == WARN for result in results)
    lines.append(f"\nSummary: {failed} failure(s), {warned} warning(s), {len(results)} check(s).")
    if failed:
        lines.append("The bridge is not ready yet; address the FAIL items above.")
    elif warned:
        lines.append("No hard failures. Warnings may still prevent live chat from being ready.")
    else:
        lines.append("Offline checks passed. Live Arena chat still requires scripts/live_smoke.py.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose a local LMArenaBridge installation.")
    parser.add_argument("--config", default=constants.CONFIG_FILE, help="Path to config.json")
    parser.add_argument("--models", default=constants.MODELS_FILE, help="Path to models.json")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Bridge base URL used with --check-server",
    )
    parser.add_argument(
        "--check-server",
        action="store_true",
        help="Also query /api/v1/health on an already-running bridge",
    )
    parser.add_argument("--json", action="store_true", help="Emit redacted JSON instead of text")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    results = collect_results(
        config_path=Path(args.config),
        models_path=Path(args.models),
        server_url=args.base_url if args.check_server else None,
    )
    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        print(format_results(results))
    return 1 if any(result.failed for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
