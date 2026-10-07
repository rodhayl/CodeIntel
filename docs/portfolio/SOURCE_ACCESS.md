# Source identity and artifact boundaries

This is the separately prepared, history-free source tree for
[`rodhayl/CodeIntel`](https://github.com/rodhayl/CodeIntel). The original private
development repository `rodhayl/CodeIntelPriv`, its old branches and tags,
pull-request refs, logs and historical Git objects are not part of this delivery
and must remain private.
Publication of this clean tree requires separate owner approval of its destination.
These instructions do not themselves change repository visibility or upload a
package to a registry.

The upstream source reviewed for this candidate was commit
`9b7c95a4fb08c23b1857c3c608eb46570b171cf8`, Git tree
`1a9d7ba5af511f0ae3a9d4dc8d7a24e7d83c2b7f`, in the original `rodhayl/CodeIntelPriv` development
repository. These identifiers record provenance; they are not a requirement for
readers to access the private original. The preparation delivery records the
local reconstructed identity, candidate-only documentation changes and exact
runtime equality separately. A clean publication repository will have its own
commit identity and must not claim to contain the original history.

## Get the identified source

Obtain the clean destination with your authorized GitHub access while it is
private, or anonymously if its owner has subsequently made it public:

```bash
git clone --single-branch --no-tags https://github.com/rodhayl/CodeIntel.git codeintel
cd codeintel
```

The repository must contain the approved source before this command is useful.
Do not substitute `CodeIntelPriv` or import its refs. If you already have this
clean repository checkout, start in its root directory.
Use the exact commit identified by its accompanying review or release record;
do not assume the default branch still equals an older tested version. Check
`git rev-parse HEAD` and `git status --porcelain` before reproducing a Git-based
acceptance result. If no matching passing receipt was delivered, generate a new
one using [validation](VALIDATION.md); do not reuse a receipt from another commit.

If you obtained the reviewed history-free source archive, extract it to a new
directory and inspect `SNAPSHOT_MANIFEST.json`. It records the source identity
and SHA-256 of every selected file. Keep the matching source receipt if you want
to run the installed acceptance gate. The archive itself has no Git history and
cannot claim a clean-Git source acceptance result. It can still be installed and
run with the [README recipe](../../README.md).

No access to the private development repository is needed to install or inspect
these delivered files. Do not fetch its branches or tags into a clean publication
checkout. Original private-source receipts and their commit identities remain
historical evidence; a later documentation-only candidate must identify its own
acceptance rather than relabeling an earlier receipt.

Hashes and commit IDs bind bytes; they do not authenticate the producer. A locally
reconstructed commit is not the upstream commit. Preserve the verified mapping
and any declared differences instead of presenting those identities as equal.

## Choose the appropriate artifact

- A clean Git checkout supports the full source gate. A new Git destination
  should retain the reviewed `.gitignore` and `.gitattributes` so generated files
  do not enter source control or dirty validation.
- The curated review archive includes selected technical docs, tests and tools.
  Its `SNAPSHOT_MANIFEST.json` binds delivered bytes and source identity; it has
  no Git history and intentionally omits Git-maintenance files.
- The standard sdist contains build source with [its own recipe](../../README.package.md),
  without the review suite or tools.
- The wheel contains installed runtime and four synthetic fixtures.

Follow [installation](../../README.md) and [validation](VALIDATION.md) using
absolute executable, repository and external-state paths. Packet output contains
literal source without automatic secret redaction; source access does not by
itself authorize redistributing unrelated inspected repositories. Historical
reports, editorial assets, personal material and private Git history are outside
this product tree. [Versioned evidence](EVIDENCE_INDEX.md) and [historical
arithmetic](HISTORY.md) explain the retained references.

MIT applies to the current offline source and its selected distribution. It does
not retroactively license excluded private history or third-party material.
Creating or validating this tree changes no website, repository visibility,
permissions or deployment.
