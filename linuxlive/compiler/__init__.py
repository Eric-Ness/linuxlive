"""Compile a validated spec into a container build context.

A build context is a directory podman can build directly:

  Containerfile    how to build the image
  rootfs/          files the Containerfile copies into the image
  iso/             GRUB configuration the ISO assembler puts on the ISO
  iso.env          settings for scripts/assemble-iso.sh, as shell assignments
  linuxlive.json   the resolved spec, for people and tools
  MANIFEST         every file with its mode and SHA-256

Compilation is deterministic: the same spec and assets give byte-identical
contexts, so golden tests can pin the output and image layer caches stay warm.
"""

from __future__ import annotations

import json
from pathlib import Path

from linuxlive.catalog import Catalog, load_catalog
from linuxlive.compiler import extras, fedora, options, ubuntu
from linuxlive.compiler.common import add_branding, add_iso_env
from linuxlive.compiler.context import MARKER, BuildContext, CompileError
from linuxlive.compiler.plan import Plan, plan_build
from linuxlive.spec import Spec

__all__ = ["MARKER", "BuildContext", "CompileError", "Plan", "build_context", "compile_spec", "plan_build"]

_BASES = {"ubuntu": ubuntu.add_base, "fedora": fedora.add_base}


def build_context(spec: Spec, catalog: Catalog | None = None) -> tuple[BuildContext, Plan]:
    """Build the context in memory without touching the disk."""
    plan = plan_build(spec, catalog or load_catalog())
    add_base = _BASES.get(plan.base.id)
    if add_base is None:
        raise CompileError(f"no build recipe for base {plan.base.id}")
    ctx = BuildContext()
    add_branding(ctx, plan)
    add_iso_env(ctx, plan)
    options.add_options(ctx, plan)
    add_base(ctx, plan)
    extras.add_extras(ctx, plan)
    resolved = plan.as_dict()
    if options.uses_options(plan):
        resolved["settings"] = options.as_dict(plan)
    if extras.has_extras(plan):
        resolved["extras"] = extras.as_dict(plan)
    ctx.add_text("linuxlive.json", json.dumps(resolved, indent=2, sort_keys=True))
    return ctx, plan


def compile_spec(spec: Spec, outdir: Path, catalog: Catalog | None = None) -> Plan:
    ctx, plan = build_context(spec, catalog)
    ctx.write(Path(outdir))
    return plan
