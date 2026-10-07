# Third-party dependencies

This source distribution contains CodeIntel project code and synthetic fixtures. It does not bundle dependency source, dependency wheels, model weights or generated SCIP code. Dependencies installed separately from PyPI retain their own licenses. The CodeIntel MIT license does not replace those terms.

| Component | Pin | Official license / handling |
|---|---|---|
| APSW | 3.53.4.0 | [APSW license](https://rogerbinns.github.io/apsw/copyright.html): zlib-style conditions or an OSI-approved license option. Preserve its shipped copyright/origin notice when redistributing it. |
| py-tree-sitter | 0.26.0 | [MIT](https://github.com/tree-sitter/py-tree-sitter/blob/master/LICENSE). Preserve copyright and permission notice when redistributed. |
| tree-sitter-python | 0.25.0 | [MIT](https://github.com/tree-sitter/tree-sitter-python/blob/master/LICENSE). |
| tree-sitter-javascript | 0.25.0 | [MIT](https://github.com/tree-sitter/tree-sitter-javascript/blob/master/LICENSE). |
| tree-sitter-typescript | 0.23.2 | [MIT](https://github.com/tree-sitter/tree-sitter-typescript/blob/master/LICENSE). |
| setuptools, build tool | 80.10.2 | [MIT](https://github.com/pypa/setuptools/blob/main/LICENSE), with vendored-component notices. Not bundled in the source archive. |

NumPy and all model/vector provider packages were removed from the active offline runtime and its pins during the scope audit. They are not dependencies of this source distribution.

Historical model, SCIP, watcher and benchmark executables are removed from the active repository tree. Their original private history is retained separately; they are not installable extras.
