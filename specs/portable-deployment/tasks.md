# Delivery tasks and evidence

- [x] Implement typed deployment profile, preflight, doctor, release-canary and production-ops modules. Evidence: `graph_os/deployment/` and `tests/deployment/`. **Source implemented; independent install and first-boot acceptance pending.**
- [ ] Add and verify the fresh-checkout development path, including a disposable engine and clear optional component selection.
- [ ] Complete migration of deployment modules and scripts from agent-runtime internals onto supported public ports.
- [ ] Render and validate both Compose and Kubernetes profiles from one closed schema; reject plaintext secrets and floating production images.
- [ ] Add positive and negative first-boot/rollback tests using provisioned CI resources and reference-only credentials.
- [ ] Audit hook installation and required CI gates; make all ordinary PR gates reproducible in a cloud checkout.
- [ ] Publish usage to the GraphOS GitHub Pages site and link the contribution guide to this spec and a verified setup command.
- [ ] Record exact commit, package hashes, CI runs, profile digests and redacted functional receipts; then promote individual deliverables to Verified/Released/Accepted.

**Current evidence gap:** The existing source and test names establish implementation progress. They do not establish a public clean-install, profile matrix or first-boot acceptance receipt for this complete contract.
