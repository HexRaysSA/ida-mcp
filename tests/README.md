# Tests

These tests drive a real, licensed IDA through ida-nexus -- they spawn idalib
workers, kill them, and check what survives on disk. There are no fakes or
mocks, so the suite needs an IDA installation before it can run.

```bash
uv run --with ida-hcli hcli ida install \
  --download-id ida-pro:latest \
  --license-id "$IDA_LICENSE_ID" \
  --install-dir="$HOME/app/ida" \
  --accept-eula --set-default --yes

uv sync --group dev
uv run pytest              # add -m "not slow" to skip the two-minute timer test
```

`HCLI_API_KEY` and `IDA_LICENSE_ID` come from your Hex-Rays account; CI reads
them from repository secrets of the same name (`.github/workflows/tests.yml`).

Each test gets a private copy of `data/crackme03.elf` (the sample binary from
the ida-nexus test suite) in a temporary directory, and its own Nexus state
directory, so runs never touch a developer's real `~/.idapro`.

Tests marked `xfail(strict=True)` describe behavior ida-nexus does not
implement yet -- see `research/unpacked-databases/` for the measurements behind
them. When the fix lands they become unexpected passes, which is the signal to
delete the marker.
