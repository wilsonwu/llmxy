"""Envoy process lifecycle: subprocess.Popen + admin polling.

One envoy process per `EnvoyInstance` row. We persist `pid` on start; on
api restart we don't try to adopt orphans — admin must `start` again.
Each instance uses its own `--base-id` (taken from `inst.id`) so multiple
envoys can share a host without IPC clashes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import EnvoyInstance, EnvoyMode, EnvoyStatus
from app.services.envoy import config as envoy_config

log = logging.getLogger(__name__)

# Track Popen handles in-process so we can SIGTERM later. Lost on api
# restart; in that case we fall back to inst.pid.
_procs: dict[int, subprocess.Popen] = {}


def _bootstrap_path(inst: EnvoyInstance) -> str:
    return os.path.join(inst.config_dir, "bootstrap.yaml")


def _log_path(inst: EnvoyInstance) -> str:
    os.makedirs(inst.log_dir, exist_ok=True)
    return os.path.join(inst.log_dir, "envoy.log")


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            # Probe via tasklist-equivalent: send signal 0 not supported.
            # subprocess Popen exists in _procs handles already; if not,
            # we can't reliably check on win — assume dead.
            return False
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def _wait_ready(admin_port: int, timeout: float = 10.0) -> bool:
    """Poll envoy admin /ready until 200 or timeout."""
    url = f"http://127.0.0.1:{admin_port}/ready"
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=1.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                r = await client.get(url)
                if r.status_code == 200 and b"LIVE" in r.content.upper():
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
    return False


async def start(db: AsyncSession, inst: EnvoyInstance) -> dict[str, Any]:
    if inst.status == EnvoyStatus.running and _pid_alive(inst.pid):
        return {"status": "running", "pid": inst.pid}

    # 1. (re)write config so we boot off fresh files.
    await envoy_config.write_all(db, inst)

    inst.status = EnvoyStatus.starting
    inst.last_error = None
    await db.commit()

    bootstrap = _bootstrap_path(inst)
    log_file = _log_path(inst)
    # On Windows envoy may not be on PATH; user sets ENVOY_BIN.
    cmd = [
        settings.ENVOY_BIN,
        "-c", bootstrap,
        "--base-id", str(inst.id),
        "--log-level", "info",
    ]
    try:
        # Redirect both stdout/stderr to a single file; envoy's own logs
        # go through stderr by default.
        f = open(log_file, "ab", buffering=0)
        proc = subprocess.Popen(
            cmd,
            stdout=f,
            stderr=f,
            stdin=subprocess.DEVNULL,
            cwd=inst.config_dir,
            close_fds=(sys.platform != "win32"),
        )
    except FileNotFoundError as e:
        inst.status = EnvoyStatus.error
        inst.last_error = f"envoy binary not found: {settings.ENVOY_BIN}"
        await db.commit()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, inst.last_error) from e
    except Exception as e:
        inst.status = EnvoyStatus.error
        inst.last_error = f"spawn failed: {e}"
        await db.commit()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, inst.last_error) from e

    _procs[inst.id] = proc
    inst.pid = proc.pid
    await db.commit()

    ok = await _wait_ready(inst.admin_port, timeout=10.0)
    if not ok:
        # Best effort — kill the half-started process.
        try:
            proc.terminate()
        except Exception:
            pass
        inst.status = EnvoyStatus.error
        inst.last_error = "envoy failed to become ready within 10s"
        await db.commit()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, inst.last_error)

    inst.status = EnvoyStatus.running
    inst.last_health_at = datetime.now(timezone.utc)
    await db.commit()
    log.info("envoy[%s] started pid=%d listen=%d admin=%d",
             inst.name, proc.pid, inst.listen_port, inst.admin_port)
    return {"status": "running", "pid": proc.pid}


async def stop(db: AsyncSession, inst: EnvoyInstance) -> dict[str, Any]:
    proc = _procs.get(inst.id)
    pid = (proc.pid if proc else None) or inst.pid

    if proc is not None:
        try:
            proc.terminate()
            for _ in range(20):
                if proc.poll() is not None:
                    break
                await asyncio.sleep(0.25)
            if proc.poll() is None:
                proc.kill()
        except Exception as e:
            log.warning("envoy[%s] terminate error: %s", inst.name, e)
        _procs.pop(inst.id, None)
    elif pid:
        # No handle (api restarted) — try OS kill.
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
            else:
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):
                    if not _pid_alive(pid):
                        break
                    await asyncio.sleep(0.25)
                if _pid_alive(pid):
                    os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            log.warning("envoy[%s] os-kill error: %s", inst.name, e)

    inst.status = EnvoyStatus.stopped
    inst.pid = None
    await db.commit()
    return {"status": "stopped"}


async def restart(db: AsyncSession, inst: EnvoyInstance) -> dict[str, Any]:
    await stop(db, inst)
    return await start(db, inst)


async def reload(db: AsyncSession, inst: EnvoyInstance) -> dict[str, Any]:
    """Reload config.

    - local: rewrite YAML files; Envoy's watched_directory picks them up.
    - remote: bump config_version, then nudge the ADS server to re-push to
      the live stream for this node. If the remote envoy isn't connected
      we still bump the version so the next stream open delivers the change.
    """
    if inst.mode == EnvoyMode.remote:
        from app.services.envoy import xds_server
        inst.config_version = (inst.config_version or 0) + 1
        await db.commit()
        xds_server.notify_node(inst.node_id)
        return {"status": "pushed", "config_version": inst.config_version}
    if inst.status != EnvoyStatus.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "instance not running")
    version = await envoy_config.write_all(db, inst)
    await db.commit()
    return {"status": "reloaded", "config_version": version}


async def health(inst: EnvoyInstance) -> bool:
    url = f"http://127.0.0.1:{inst.admin_port}/ready"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(url)
            return r.status_code == 200
    except Exception:
        return False


async def stats(inst: EnvoyInstance) -> dict[str, Any]:
    """Return a small curated subset of Prometheus stats for the admin UI."""
    if inst.status != EnvoyStatus.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "instance not running")
    url = f"http://127.0.0.1:{inst.admin_port}/stats?format=json&filter=" \
          "(cluster\\.|http\\.ingress_http\\.downstream)"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"stats fetch failed: {e}") from e
    # Pull a few headline counters.
    wanted_suffixes = (
        "upstream_rq_total", "upstream_rq_2xx", "upstream_rq_4xx",
        "upstream_rq_5xx", "upstream_cx_active",
        "downstream_rq_total", "downstream_rq_2xx", "downstream_rq_4xx",
        "downstream_rq_5xx",
    )
    out: dict[str, int] = {}
    for s in data.get("stats", []) or []:
        name = s.get("name", "")
        if any(name.endswith(suf) for suf in wanted_suffixes):
            v = s.get("value")
            if isinstance(v, (int, float)):
                out[name] = int(v)
    return {"counters": out}


async def tail_logs(inst: EnvoyInstance, n: int) -> list[str]:
    path = os.path.join(inst.log_dir, "envoy.log")
    if not os.path.exists(path):
        return []
    # Read tail without slurping the whole file.
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                read = min(block, size)
                size -= read
                f.seek(size)
                data = f.read(read) + data
        lines = data.decode("utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception as e:
        log.warning("tail_logs failed: %s", e)
        return []
