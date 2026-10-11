# Contributing to linuxlive

Thanks for helping. linuxlive turns a short spec file into a live ISO that
people boot on their own computers, so keep changes focused and tested.

## Set up

You need Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate            # on Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
```

## Before you open a pull request

- Run the tests with `python -m pytest`. They need no network and no root.
- If you changed what the compiler writes, regenerate the golden copies with
  `LINUXLIVE_UPDATE_GOLDEN=1 python -m pytest tests/test_compiler.py`, read
  the diff, and commit it with your change.
- If you added a package, image or app to `linuxlive/data/catalog.yaml`, run
  `python scripts/check_catalog.py` (it needs network) to confirm it exists
  upstream for every base.
- Building and boot-testing an ISO needs Linux, root, podman and QEMU; see
  "Building locally" in the README. CI builds and boots every example on each
  push to `main`, so small changes don't need a local build.

## Sign off your commits

Add a `Signed-off-by` line to each commit with `git commit -s`. It certifies
that you wrote the change, or otherwise have the right to submit it under
this project's licence, as set out in the
[Developer Certificate of Origin](https://developercertificate.org/).

## Licence

By contributing you agree that your contribution is licensed under
GPL-3.0-or-later, the licence of this project.
