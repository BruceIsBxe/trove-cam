# cam-test: a Bedrock client as a clip camera on GitHub Actions

Feasibility test: can a GitHub-hosted runner (no GPU) run a real-looking Minecraft: Bedrock Edition client in
software, join a server, film a short PvP scene and hand back an MP4?

- `.github/workflows/cam-test.yml` (manual, `workflow_dispatch`): installs Xvfb + Mesa (lavapipe Vulkan /
  llvmpipe GL) + ffmpeg + age, downloads the [Cinnabar](https://github.com/cinnabar-mc/cinnabar) release AppImage
  (sha256-checked, extracted, cached), starts a throwaway PocketMine-MP on 127.0.0.1:19132 (xbox-auth off, vanilla
  flat world, no plugins except the test scene), joins it with Cinnabar, records the virtual display and uploads the
  clip and logs **encrypted**.
- `server/plugins/CamTest.php`: one-file script plugin that places the camera and two sparring NPCs.
- `bot/sparring.mjs`: an offline second player (bedrock-protocol) so a real network player is in shot.
- `scripts/run.sh`: the run itself; writes `summary.json` (fps, CPU per process, timings).

## Privacy
Clips and detailed logs are encrypted on the runner with [age](https://age-encryption.org) to the public recipient in
`.github/clip-recipient.txt`; the plaintext never leaves the runner. Only the holder of the matching identity (kept
off GitHub) can decrypt:

    age -d -i <identity file> -o clip.mp4 clip.mp4.age

The public Actions log shows stage names and numbers only. A real server address and camera name, if ever used,
come from repository secrets (`SERVER_ADDR`, `CAMERA_NAME`) and are masked.

## Third-party software
- Cinnabar is licensed **PolyForm Noncommercial 1.0.0**; this repository only downloads its official release for a
  noncommercial test. On first run Cinnabar downloads Mojang's public sample resource pack after
  `CINNABAR_ACCEPT_MOJANG_EULA=1` (the Minecraft EULA applies). Not an official Minecraft product.
- PocketMine-MP (NetherGames fork, LGPL-3.0): the `pm-ng-eaa2e581` release asset is an unmodified build of
  https://github.com/NetherGamesMC/PocketMine-MP at commit `eaa2e58156e71e7a10093cffc98b02e1023f05ee`
  (`composer install --no-dev --classmap-authoritative`, then `build/server-phar.php`); source at that commit.
- PHP binaries: https://github.com/axolotl-pm/PHP-Binaries (`pm5-php-8.4-latest`).
