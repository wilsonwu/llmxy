"""Compile the minimal als.proto subset at import time.

We define a tiny proto subset (see protos/als.proto) and compile it once
using grpcio_tools.protoc, then re-export the resulting modules. This
avoids vendoring the full envoy proto tree.

The wire format is compatible with envoy because protobuf is identified
by field number — fields we omit are silently dropped as unknown.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PROTO_FILE = os.path.join(_PKG_DIR, "als.proto")


def _ensure_compiled() -> None:
    """Run protoc into a sibling _generated/ dir; idempotent."""
    out_dir = os.path.join(_PKG_DIR, "_generated")
    os.makedirs(out_dir, exist_ok=True)
    init_py = os.path.join(out_dir, "__init__.py")
    if not os.path.exists(init_py):
        open(init_py, "w").close()

    target = os.path.join(out_dir, "als_pb2.py")
    grpc_target = os.path.join(out_dir, "als_pb2_grpc.py")
    if (
        os.path.exists(target)
        and os.path.exists(grpc_target)
        and os.path.getmtime(target) >= os.path.getmtime(_PROTO_FILE)
    ):
        return

    from grpc_tools import protoc  # type: ignore

    # Locate google.protobuf well-known types shipped with grpcio_tools.
    import grpc_tools
    wkt_inc = os.path.join(os.path.dirname(grpc_tools.__file__), "_proto")

    rc = protoc.main([
        "protoc",
        f"-I{_PKG_DIR}",
        f"-I{wkt_inc}",
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
        _PROTO_FILE,
    ])
    if rc != 0:
        raise RuntimeError(f"protoc failed with rc={rc} compiling als.proto")


_ensure_compiled()

# Make `from app.services.envoy.protos._generated import als_pb2` work.
_gen_dir = os.path.join(_PKG_DIR, "_generated")
if _gen_dir not in sys.path:
    sys.path.insert(0, _gen_dir)

als_pb2 = importlib.import_module("als_pb2")
als_pb2_grpc = importlib.import_module("als_pb2_grpc")
