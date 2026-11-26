#!/usr/bin/env node
// Second player for the trove-cam test: joins the throwaway runner server offline (bedrock-protocol),
// stays connected for --seconds, and logs join/spawn. The CamTest plugin places it and swings its arm
// server-side, so the camera films a real network player as well as the two NPC fighters.
//
//   node sparring.mjs [--host 127.0.0.1] [--port 19132] [--name Sparring] [--version 1.26.60]
//                     [--seconds 60] [--raknet raknet-native|jsp-raknet]
import bedrock from 'bedrock-protocol'

const o = { host: '127.0.0.1', port: 19132, name: 'Sparring', version: '1.26.60', seconds: 60, raknet: 'raknet-native' }
const argv = process.argv.slice(2)
for (let i = 0; i < argv.length; i++) {
  const k = argv[i].replace(/^--/, '')
  if (k in o) o[k] = typeof o[k] === 'number' ? Number(argv[++i]) : argv[++i]
}

const t0 = Date.now()
const log = (...a) => console.log(`[bot +${((Date.now() - t0) / 1000).toFixed(1)}s]`, ...a)
log('connecting', JSON.stringify(o))

const client = bedrock.createClient({
  host: o.host,
  port: o.port,
  username: o.name,
  offline: true,
  version: o.version,
  skipPing: false,
  connectTimeout: 20000,
  raknetBackend: o.raknet,
  conLog: null
})

client.on('play_status', (p) => log('play_status', p.status))
client.on('start_game', (p) => log('start_game', 'entity', String(p.runtime_entity_id), 'pos', JSON.stringify(p.player_position)))
client.on('spawn', () => log('SPAWNED'))
client.on('text', (p) => log('text', JSON.stringify(p.message)))
client.on('kick', (p) => log('kick', JSON.stringify(p)))
client.on('disconnect', (p) => { log('disconnect', JSON.stringify(p)); process.exit(2) })
client.on('error', (e) => { log('error', e?.stack || String(e)); process.exit(3) })
client.on('close', () => log('close'))

setTimeout(() => { log('done, leaving'); try { client.close() } catch {} ; setTimeout(() => process.exit(0), 500) }, o.seconds * 1000)
