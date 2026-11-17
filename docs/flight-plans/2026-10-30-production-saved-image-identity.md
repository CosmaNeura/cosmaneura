# Production saved-image identity compatibility

Docker's image `Id` can be a manifest digest under containerd or a config
digest under the classic store. The Core901 producer must establish config
identity from actual exported bytes before preparing an immutable candidate.

The compatibility stack exports only the already inspected immutable image ID.
It selects one Docker-save catalogue, verifies the selected config, Linux/AMD64,
all runtime Config fields and RootFS against inspection, and hashes every layer.
A native OCI index must select a single matching manifest whose config and all
compressed layer digest/size bytes are verified; every expanded layer must also
match its config diff_id. A manifest ID requires a matching verified Descriptor.
Malformed, ambiguous, missing, changed or mismatched evidence refuses before
source probing or registry publication. Config records use the verified config
SHA; source probes and tags retain the verified opaque Docker image ID.

Published immutable manifests must retain that exact config digest/size and
verified layer count. Native compressed layers must retain their exact digest,
size and compression category across OCI/Docker media-type naming. Classic
Docker-save exports contain uncompressed layers; their bytes are checked against
RootFS, and are not represented as an original compressed registry manifest.

The compatibility source adds no workflow, dependency, deployment, activation,
credential or application startup. Existing manual founder controls, production
and staging contexts, disabled deployment switches and retention checks remain.
Production source composition must include this stack after its reviewed parent.
Current local tests exercise both engine formats and corruption, ambiguity,
platform, Descriptor, config, layer and publication-order failures using synthetic
bytes. They do not claim live production or registry acceptance.
