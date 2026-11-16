# Scribe API child startup under uvloop

Risk: Tier 2. The process boundary changes transport while preserving its
unprivileged identity and secret isolation. No identity grant or cohort expands.

Production API revision 20 failed before the Scribe child started: the enabled
Core gateway passed `user=10001` and `group=10001` to uvloop's subprocess
transport, which raised `ValueError`. The disabled API path had not entered it.

Use the existing worker's stdlib `subprocess.Popen` mechanism for the API child.
Keep UID/GID 10001, fixed executable/arguments/directory, closed environment and
null input/output. Poll the owned process and wait outside the async event loop;
after the existing twenty-second termination budget, kill and reap it. HTTP
authorization, proxy headers, limits and service contracts remain unchanged.

Validate the focused offline bridge tests, including exact privilege/environment
arguments, rejecting use of the event-loop subprocess transport, termination
escalation and fail-closed responses after child exit. The tests launch no real
Core/Scribe application, child, Redis connection or SDK request.

Receiver-first release: qualify a paired image containing this merged Core fix
and the merged Scribe receiver, bind its exact receiver manifest in the API
configuration, then verify the normal API ready revision and operational client
recording sequence. Local unit checks do not prove production readiness.

Recovery: retain the previous ready revision. Disable only the Scribe API bundle
flag if the new ready revision cannot serve the original Core routes; retain
completed database/IAM work and do not replay migrations. Production changes
and actual canary execution remain with the founder's delegated operator.
