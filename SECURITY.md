# Security Policy

## Reporting a vulnerability

Please report security issues privately to **guy@animoflow.ai** — do not
open a public issue. You'll get an acknowledgment within a few days.

## Scope notes

- The addon stores your Hugging Face token in Blender's preferences on your
  machine and sends it only to the configured AnimoFlow backend (the hosted
  service, or a server you run yourself).
- On Blender 4.2+ the addon respects the **Allow Online Access** preference
  and makes no network requests when it is disabled.
- The addon executes no code from the network; generated animation arrives
  as data files (glTF) only.

There is no bug bounty program at this time.
