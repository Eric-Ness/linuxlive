# Security policy

linuxlive builds operating system images that people boot on real computers.
A bug that lets a spec, a catalog entry or another build input run code it
should not, change what ends up in an ISO without it showing, or get past the
boot verifier, is a security problem.

## Reporting a problem

Please don't open a public issue. Use GitHub's private reporting instead: on
this repository's **Security** tab, choose **Report a vulnerability**. Only
the maintainers can see the report.

Say what you found, how to reproduce it (a spec file that triggers it is
ideal) and what it lets an attacker do. We will reply as soon as we can and
keep you posted until it is fixed.

## Supported versions

While linuxlive is before version 1.0, only the latest commit on `main` gets
security fixes.

## Out of scope

Problems in Ubuntu, Fedora, Flathub or the apps they ship belong with those
projects. If you are not sure where something belongs, report it here and we
will help send it to the right place.
