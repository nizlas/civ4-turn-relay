# Licensing notes

This document describes the project's licensing policy in practical terms. It is not legal advice.

## Project license

Original code in this repository is released under the MIT License. See [`LICENSE`](../LICENSE) at the repository root.

## Third-party dependencies

Third-party libraries keep their own licenses. Using a dependency does not relicense that dependency, and dependency licenses do not automatically change the MIT license of this project's original code.

## Planned PySide6 / Qt use

The planned GUI stack includes PySide6 (Qt for Python). Qt/PySide6 distribution involves separate LGPL, GPL, or commercial terms depending on how Qt is obtained and redistributed.

- Using PySide6 does **not** change the MIT license of our original source code.
- Distributed Windows builds must include and comply with the relevant third-party notices and obligations for Qt/PySide6 and any other bundled libraries.

## Avoiding accidental license narrowing

Do not introduce GPL-only dependencies casually. A GPL-only dependency can reduce future licensing options for combined distributions even when our own code remains MIT.

Prefer dependencies with licenses compatible with MIT redistribution (for example MIT, Apache-2.0, BSD, or LGPL where appropriate and understood).

## Release checklist

For every release that ships binaries or a locked dependency set:

1. Review the locked dependency set and their licenses.
2. Generate or update third-party notices included with the release.
3. Confirm that redistribution requirements (attribution, license texts, LGPL compliance where applicable) are met.

## Future licensing changes

Public MIT releases remain available under MIT even if later development changes repository visibility or adopts different licensing for new work. Recipients of an MIT-licensed release keep the rights granted by that release's license.
