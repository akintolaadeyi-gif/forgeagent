# Security policy

ForgeAgent executes commands on behalf of a language model. Read the threat model in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#security-model) before using it on a machine or data you care about.

* **It is not a sandbox.** Path validation protects file tools; shell commands are only classified heuristically.
  Run it in a VM, WSL2 distribution, Windows Sandbox or a throw-away user account for untrusted tasks.
* Keep the API and the model server bound to `127.0.0.1`. Set `FORGE_API_TOKEN` if other local users share the machine.
* Prefer *Supervised* mode when working on repositories whose contents you do not trust (prompt injection).

## Reporting a vulnerability
Please open a private security advisory on GitHub (Security tab -> *Report a vulnerability*) rather than a public issue.
Include steps to reproduce and the affected version/commit.
