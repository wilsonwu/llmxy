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
from app.services.envoy import bootstrap as envoy_bootstrap, xds_server

log = logging.getLogger(__name__)

# Track Popen handles in-process so we can SIGTERM later. Lost on api
# restart; in that case we fall back to inst.pid.
_procs: dict[int, subprocess.Popen] = {}


def _bootstrap_path(inst: EnvoyInstance) -> str:
    return os.path.join(inst.config_dir, "bootstrap.yaml")


def _pid_path(inst: EnvoyInstance) -> str:
    return os.path.join(inst.config_dir, "envoy.pid")


def _write_pid_file(inst: EnvoyInstance, pid: int) -> None:
    try:
        with open(_pid_path(inst), "w", encoding="utf-8") as f:
            f.write(str(pid))
    except Exception as e:
        log.warning("envoy[%s] write pid file failed: %s", inst.name, e)


def _read_pid_file(inst: EnvoyInstance) -> int | None:
    try:
        with open(_pid_path(inst), encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _remove_pid_file(inst: EnvoyInstance) -> None:
    try:
        os.unlink(_pid_path(inst))
    except FileNotFoundError:
        pass
    except Exception as e:
        log.debug("envoy[%s] unlink pid file: %s", inst.name, e)


async def _port_in_use(port: int) -> bool:
    """Returns True if a TCP listener is already bound on port (any iface)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


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

    # Refuse if something else already holds listen_port — otherwise envoy
    # would crash with EADDRINUSE and we'd leave the DB in a weird half-state.
    if await _port_in_use(inst.listen_port):
        inst.status = EnvoyStatus.error
        inst.last_error = f"listen_port {inst.listen_port} already in use"
        await db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"port {inst.listen_port} is already in use by another process",
        )

    # 1. (re)write bootstrap.yaml. CDS/LDS/RDS arrive via ADS — nothing else
    # is written to disk.
    os.makedirs(inst.config_dir, exist_ok=True)
    with open(_bootstrap_path(inst), "w", encoding="utf-8") as bf:
        bf.write(envoy_bootstrap.render_bootstrap_yaml(inst))

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
    _write_pid_file(inst, proc.pid)
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
    # Pid resolution order: in-process Popen → DB column → pid file on disk.
    # The last fallback catches orphans that survived an api restart with
    # the DB column wiped out (e.g. previous botched stop).
    pid = (
        (proc.pid if proc else None)
        or inst.pid
        or (_read_pid_file(inst) if inst.config_dir else None)
    )

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

    # Refuse to clear pid until we've actually observed the process die — that
    # way a transient kill failure leaves the DB consistent for the next stop.
    if pid and _pid_alive(pid):
        inst.last_error = f"failed to terminate envoy pid={pid}"
        await db.commit()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"envoy pid {pid} still alive after SIGKILL — manual intervention required",
        )

    inst.status = EnvoyStatus.stopped
    inst.pid = None
    _remove_pid_file(inst)
    await db.commit()
    return {"status": "stopped"}


async def restart(db: AsyncSession, inst: EnvoyInstance) -> dict[str, Any]:
    await stop(db, inst)
    return await start(db, inst)


async def reload(db: AsyncSession, inst: EnvoyInstance) -> dict[str, Any]:
    """Push config to this envoy via ADS. Mode-agnostic: both local and remote
    consume CDS/LDS/RDS over the same ADS stream. We bump config_version so
    operators see something moved, then nudge the ADS server. If the envoy
    isn't currently connected, the bump still applies and the next stream
    open delivers the change."""
    inst.config_version = (inst.config_version or 0) + 1
    await db.commit()
    xds_server.notify_node(inst.node_id)
    return {"status": "pushed", "config_version": inst.config_version}


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


async def adopt_orphans() -> None:
    """On API startup, reconcile DB state with reality. For each local instance:
    - status=running + pid alive + admin ready → adopt (restore pid).
    - status=running but pid dead → reset to stopped.
    - status=stopped but pid file shows a live process → kill it (orphan from
      a previous botched stop). Without this, the prior envoy keeps serving
      :listen_port while the UI says stopped.
    Local-mode only; remote is owned by the operator.
    """
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(EnvoyInstance).where(EnvoyInstance.mode == EnvoyMode.local)
            )
        ).scalars().all()
        for inst in rows:
            file_pid = _read_pid_file(inst) if inst.config_dir else None
            pid = file_pid or inst.pid
            alive = _pid_alive(pid)
            ready = await health(inst) if alive and inst.admin_port else False

            if inst.status == EnvoyStatus.stopped and alive:
                log.warning(
                    "envoy[%s] orphan detected (status=stopped, pid=%s alive) — killing",
                    inst.name, pid,
                )
                try:
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
                    log.warning("envoy[%s] orphan kill failed: %s", inst.name, e)
                inst.pid = None
                _remove_pid_file(inst)
                continue

            if alive and ready:
                inst.pid = pid
                inst.status = EnvoyStatus.running
                inst.last_error = None
                log.info("envoy[%s] adopted orphan pid=%d", inst.name, pid)
            else:
                if inst.status == EnvoyStatus.running:
                    log.info(
                        "envoy[%s] was marked running but pid=%s alive=%s ready=%s — resetting",
                        inst.name, pid, alive, ready,
                    )
                inst.pid = None
                inst.status = EnvoyStatus.stopped
                if inst.config_dir:
                    _remove_pid_file(inst)
        await db.commit()


_health_task: asyncio.Task | None = None
_health_fail_count: dict[int, int] = {}

# Lock key for multi-replica leader election (only one replica probes health).
_HEALTH_LEADER_KEY = "llmxy:envoy:health-leader"
_LEADER_ID = f"{os.getpid()}-{id(object())}"


async def _acquire_leader_lock(ttl_seconds: int) -> bool:
    """Best-effort leader election via Redis SET NX EX. Returns True if this
    process is the leader for the next ttl_seconds. If Redis is unreachable,
    fall back to always-leader (single-replica deployments work either way)."""
    try:
        from app.core.redis import get_redis
        r = get_redis()
        ok = await r.set(_HEALTH_LEADER_KEY, _LEADER_ID, nx=True, ex=ttl_seconds)
        if ok:
            return True
        # Already held by us? Refresh.
        cur = await r.get(_HEALTH_LEADER_KEY)
        if cur == _LEADER_ID:
            await r.expire(_HEALTH_LEADER_KEY, ttl_seconds)
            return True
        return False
    except Exception:
        return True


async def _probe_one(inst: EnvoyInstance) -> bool:
    """Probe an instance's admin /ready. Works for both local (loopback admin
    port) and remote (operator-supplied admin_url)."""
    if inst.mode == EnvoyMode.local:
        if not inst.admin_port:
            return False
        url = f"http://127.0.0.1:{inst.admin_port}/ready"
    else:
        if not inst.admin_url:
            return False
        url = inst.admin_url.rstrip("/") + "/ready"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(url)
            return r.status_code == 200
    except Exception:
        return False


async def _health_loop() -> None:
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal

    interval = max(5, settings.ENVOY_HEALTH_INTERVAL_SECONDS)
    threshold = max(1, settings.ENVOY_HEALTH_FAIL_THRESHOLD)
    log.info("envoy health monitor: interval=%ss threshold=%d", interval, threshold)
    while True:
        # Multi-replica guard: only one api process should probe at a time.
        # Use a short-TTL redis lock; if we can't acquire it, sleep and try
        # again on the next tick (some other replica is the leader).
        acquired = await _acquire_leader_lock(interval * 2)
        if not acquired:
            await asyncio.sleep(interval)
            continue
        try:
            async with AsyncSessionLocal() as db:
                rows = (
                    await db.execute(
                        select(EnvoyInstance).where(
                            EnvoyInstance.status.in_(
                                (EnvoyStatus.running, EnvoyStatus.error)
                            )
                        )
                    )
                ).scalars().all()
                changed = False
                for inst in rows:
                    ok = await _probe_one(inst)
                    if ok:
                        if _health_fail_count.pop(inst.id, 0):
                            log.info("envoy[%s] recovered", inst.name)
                        inst.last_health_at = datetime.now(timezone.utc)
                        if inst.status == EnvoyStatus.error:
                            inst.status = EnvoyStatus.running
                            inst.last_error = None
                            changed = True
                    else:
                        n = _health_fail_count.get(inst.id, 0) + 1
                        _health_fail_count[inst.id] = n
                        if n >= threshold and inst.status != EnvoyStatus.error:
                            inst.status = EnvoyStatus.error
                            inst.last_error = f"{n} consecutive health probe failures"
                            log.warning("envoy[%s] marked error after %d failed probes", inst.name, n)
                            changed = True
                if changed:
                    await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("envoy health loop tick failed: %s", e)
        await asyncio.sleep(interval)


async def start_health_monitor() -> None:
    global _health_task
    if _health_task is not None or settings.ENVOY_HEALTH_INTERVAL_SECONDS <= 0:
        return
    _health_task = asyncio.create_task(_health_loop())


async def stop_health_monitor() -> None:
    global _health_task
    if _health_task is None:
        return
    _health_task.cancel()
    try:
        await _health_task
    except (asyncio.CancelledError, Exception):
        pass
    _health_task = None
