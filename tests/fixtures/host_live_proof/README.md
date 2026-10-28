# Host live-proof fixture

A deliberately buggy `slugify` with three tests; two fail on purpose. Used
for the M1 host live proof (`awos submit --repo` → `awos serve --worker
--once` → a local `awos/<id8>` branch holding the fix). pytest ignores
`tests/fixtures/`, so the failing tests never run in the normal suite.
