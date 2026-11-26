# trove-cam (private, feasibility only)

Phase 1 test: can a free GitHub Actions runner (ubuntu-latest, 4 vCPU, no GPU) run a real-looking
Bedrock client, film a PvP scene and hand back an MP4?

- `.github/workflows/cam-test.yml` (manual, `workflow_dispatch`): installs Xvfb + Mesa (lavapipe Vulkan /
  llvmpipe GL) + ffmpeg, downloads the Cinnabar release AppImage (sha256-checked, extracted, cached),
  starts a throwaway PocketMine-MP (NetherGames fork phar from this repo's `pm-ng-eaa2e581` release,
  Axolotl PHP 8.4) on 127.0.0.1:19132 with xbox-auth off and a vanilla flat world, joins it with Cinnabar
  as `TroveCam`, adds a bedrock-protocol bot, records the virtual display and uploads `clip` + `logs`.
- `server/plugins/CamTest.php`: one-file script plugin that places the camera and two sparring NPCs.
- `scripts/run.sh`: the run itself; writes `out/summary.json` (distinct fps, CPU per process, timings).

Nothing here connects to the live Trove Prisons server. No Trove plugins, worlds or secrets are in this repo.

## Licences
- Cinnabar (https://github.com/cinnabar-mc/cinnabar) is **PolyForm Noncommercial 1.0.0**. This private
  feasibility test is noncommercial; using it for a monetised server needs the maintainers' permission
  (they offer commercial licences). On first run Cinnabar downloads Mojang's public sample resource pack
  after `CINNABAR_ACCEPT_MOJANG_EULA=1` (the Minecraft EULA applies).
- PocketMine-MP (NetherGames fork) is LGPL-3.0.

Run: `gh workflow run cam-test.yml -R BruceIsBxe/trove-cam` then `gh run watch`.
