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
- `.github/workflows/film.yml` (production): films one replay on the real server for a staff report. Inputs are a
  public job id and an age-encrypted payload (to `.github/cam-runner-recipient.txt`; the runner's identity is the
  `CAM_RUNNER_AGE_IDENTITY` secret). The server address is the `SERVER_ADDR` secret. Output: the MP4 encrypted to
  `.github/bot-recipient.txt` (the Discord bot) and `.github/clip-recipient.txt`, kept 1 day; the bot deletes the run.
- `.github/workflows/ping.yml`: a RakNet unconnected ping (server-list ping, no join) to an encrypted address.
- `.github/workflows/map.yml` and `bundle.yml` (2026-11-27): the Trove web admin's live map tiles and 3D clip viewer bundles.
  The Discord bot dispatches them with a public job id and an age-encrypted payload (its address + a one-time token); the
  runner fetches every input from the bot with that token (each body is age-encrypted to this runner): a KIT of the game's
  own tools and pack files (never in this repo; cached here only as ciphertext), a read-only world copy, and for a bundle
  the clip + the players' skins. Mojang's Bedrock textures (Mojang/bedrock-samples) and Java sounds / font (Mojang's
  launcher servers, `scripts/run_export.py`) are fetched at run time and are never cached or uploaded in clear. Results go
  back encrypted to the bot (`result-<job>` artifact, 1 day); the bot deletes the run. `scripts/runner_job.py` opens the
  payload and fetches the inputs.
- `.github/workflows/render.yml` (2026-11-28): #reports clip videos from the game's own clip renderer (cliprender
  `render_clip.py`, in the clip kit) instead of a camera client. Same payload, token and inputs as a bundle (the clip
  input also carries `render.json`: the view choice). OpenGL is Mesa llvmpipe through EGL (Xvfb + GLX when EGL has no
  device); `scripts/run_render.py` renders SERVER | the reported player's view side by side (one view when the clip has
  one player) at 1280x720 30 fps and fits it under 9 MiB with a two-pass encode. The MP4 + sizes go back encrypted to the
  bot (`result-<job>`, 1 day); the bot adds it to the #reports post and deletes the run.

## Privacy
Clips and detailed logs are encrypted on the runner with [age](https://age-encryption.org) to the public recipient in
`.github/clip-recipient.txt`; the plaintext never leaves the runner. Only the holder of the matching identity (kept
off GitHub) can decrypt:

    age -d -i <identity file> -o clip.mp4 clip.mp4.age

The public Actions log shows stage names and numbers only. A real server address and camera name, if ever used,
come from repository secrets (`SERVER_ADDR`, `CAMERA_NAME`) and are masked.

## Credits and third-party software
- **[Cinnabar](https://github.com/cinnabar-mc/cinnabar)** by the Cinnabar maintainers renders every clip: an
  independent Minecraft: Bedrock Edition client. It is licensed PolyForm Noncommercial 1.0.0 and is **used here with
  the maintainers' permission** (on-demand staff report clips, unmodified v0.2.1 release). Thank you! On first run Cinnabar downloads Mojang's public sample resource pack after
  `CINNABAR_ACCEPT_MOJANG_EULA=1` (the Minecraft EULA applies). Not an official Minecraft product.
- PocketMine-MP (NetherGames fork, LGPL-3.0): the `pm-ng-eaa2e581` release asset is an unmodified build of
  https://github.com/NetherGamesMC/PocketMine-MP at commit `eaa2e58156e71e7a10093cffc98b02e1023f05ee`
  (`composer install --no-dev --classmap-authoritative`, then `build/server-phar.php`); source at that commit.
- PHP binaries: https://github.com/axolotl-pm/PHP-Binaries (`pm5-php-8.4-latest`).
