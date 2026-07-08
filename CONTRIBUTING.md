# Contributing to animoflow-blender

Thanks for wanting to help! Bug reports, small fixes, and feature ideas are
all welcome.

## Before your first pull request

External contributions require a one-time Contributor License Agreement
(CLA) — it keeps AnimoFlow able to maintain the addon across the whole
platform (see the [per-repo license map](https://github.com/AnimoFlow/legal)).
A bot will ask you to sign on your first PR; nothing to do in advance.

The addon itself is licensed [GPL-3.0](LICENSE), as all Blender addons must
be. Your contributions are accepted under the CLA and distributed under
GPL-3.0.

## Development setup

No dependencies beyond Python and pytest — the addon uses only Blender's
bundled `bpy` and the standard library:

```bash
pip install pytest
./run_tests.sh          # unit tests run against a mocked bpy (no Blender needed)
```

For changes that touch Blender behavior, please also run the headless
smoke test against a real Blender binary:

```bash
./scripts/test_headless.sh /path/to/blender
```

## Building the addon zips

```bash
./scripts/build.sh      # produces the 4.2+ extension zip and the 3.6–4.1 legacy zip
```

## Guidelines

- Keep the addon dependency-free (bpy + standard library only).
- User-facing error messages come from the backend verbatim — don't invent
  new error text in the addon; surface what the API returns.
- One change per PR; include a test where the change is testable without
  Blender.

Questions first? Open an issue or write to guy@animoflow.ai.
