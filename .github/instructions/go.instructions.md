---
applyTo: "**/*.go,**/go.mod,**/go.sum"
---

# Go engineering instructions

- Preserve module boundaries, supported Go version, package conventions, and existing error-handling patterns.
- Pass `context.Context` through request and I/O boundaries; respect cancellation and deadlines.
- Handle errors explicitly and wrap them with useful context without leaking secrets.
- Avoid shared mutable state and unbounded goroutines; protect concurrency with appropriate synchronization.
- Validate external input and use parameterized database operations.
- Keep modules tidy and run gofmt, go vet, unit tests, and race checks where practical.
