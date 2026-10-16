"""Command line: python -m linuxlive <command> ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from linuxlive import __version__
from linuxlive.compiler import CompileError, compile_spec
from linuxlive.flathub import FlathubError, check_apps
from linuxlive.isoinspect import IsoError, inspect_iso
from linuxlive.spec import SpecError, load_spec
from linuxlive.verify import CHECKS, verify_results


def _validate(args) -> int:
    spec = load_spec(args.spec)
    if args.online and spec.flathub:
        problems, found = check_apps(spec.flathub)
        for app in found:
            print(f"  flathub: {app.id} ({app.name}){' verified' if app.verified else ''}, {', '.join(app.arches)}")
        if problems:
            raise SpecError(args.spec, problems)
    extra = f" and {len(spec.flathub)} from Flathub" if spec.flathub else ""
    print(f"{args.spec}: valid ({spec.name} {spec.version}, {spec.base}, {spec.desktop}, {len(spec.apps)} apps{extra})")
    return 0


def _compile(args) -> int:
    spec = load_spec(args.spec)
    plan = compile_spec(spec, Path(args.output))
    print(f"wrote build context for {spec.name} {spec.version} to {args.output}")
    print(f"  base image: {plan.base_image}")
    print(f"  ISO: {plan.iso_name} (volume {plan.volid})")
    return 0


def _inspect(args) -> int:
    report = inspect_iso(args.iso, checksum=not args.no_checksum)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _verify(args) -> int:
    spec = load_spec(args.spec)
    checks = [c.strip() for c in args.check.split(",") if c.strip()]
    outcomes = verify_results(spec, Path(args.results), checks, expect_commit=args.expect_commit)
    for o in outcomes:
        print(f"{'PASS' if o.ok else 'FAIL'} {o.check}: {o.detail}")
    failed = [o for o in outcomes if not o.ok]
    if failed:
        print(f"VERIFY FAILED: {len(failed)} of {len(outcomes)} checks failed")
        return 1
    print(f"VERIFY OK {','.join(checks)} ({len(outcomes)} checks)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="linuxlive", description="Build custom live distros from a spec file.")
    parser.add_argument("--version", action="version", version=f"linuxlive {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="check a spec file")
    p.add_argument("spec")
    p.add_argument("--online", action="store_true", help="also ask Flathub's API about the spec's flathub apps")
    p.set_defaults(func=_validate)

    p = sub.add_parser("compile", help="write a container build context for a spec")
    p.add_argument("spec")
    p.add_argument("-o", "--output", required=True, help="build context directory (replaced if it exists)")
    p.set_defaults(func=_compile)

    p = sub.add_parser("inspect-iso", help="report an ISO's boot structures and payload")
    p.add_argument("iso")
    p.add_argument("--json", help="also write the report to this file")
    p.add_argument("--no-checksum", action="store_true", help="skip the SHA-256 of the whole image")
    p.set_defaults(func=_inspect)

    p = sub.add_parser("verify-results", help="check CI build and boot results against a spec")
    p.add_argument("--spec", required=True)
    p.add_argument("--results", required=True, help="results directory written by CI")
    p.add_argument("--check", default=",".join(CHECKS), help=f"comma-separated subset of {','.join(CHECKS)}")
    p.add_argument("--expect-commit", help="fail unless the results come from this commit")
    p.set_defaults(func=_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SpecError as exc:
        print(f"{exc.path}: invalid spec", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    except (CompileError, IsoError, FlathubError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
