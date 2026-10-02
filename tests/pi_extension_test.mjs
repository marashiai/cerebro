// Exercise the production Pi extension's public hooks with a controlled MCP
// factory boundary. Installed-runtime MCP startup is verified separately.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { registerHooks } from 'node:module'
import os from 'node:os'
import path from 'node:path'

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'cerebro-pi-extension-tests-'))
const factories = []
globalThis.cerebroFixtureMcpFactory = (options) => (pi) => { factories.push({ options, pi }) }
const factoryModule = 'data:text/javascript,' + encodeURIComponent(
  'export const createMcpExtension = (options) => globalThis.cerebroFixtureMcpFactory(options);')
const loader = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '@earendil-works/pi-coding-agent') return { url: factoryModule, shortCircuit: true }
    return nextResolve(specifier, context)
  },
})
const extension = (await import('../lib/payloads/pi/cerebro.ts')).default

function setup(name, role, metadata = { role: 'supervisor', backend: 'pi' }, bind = true) {
  const directory = path.join(temporary, name)
  fs.mkdirSync(directory)
  const config = path.join(directory, 'tools-' + role + '.json')
  const server = { command: '/fixture/cerebro', args: ['tools', role], env: {
    CEREBRO_SESSION_ID: name, CEREBRO_SESSION_DIR: directory,
    CEREBRO_ROLE: role, CEREBRO_PI_CMD: '/fixture/pi', CEREBRO_MODEL: 'fixture/model',
  } }
  fs.writeFileSync(config, JSON.stringify({ mcpServers: { cerebro: server,
    foreign: { command: '/unapproved/mutation-server' } } }))
  fs.writeFileSync(path.join(directory, 'metadata.json'), JSON.stringify(metadata))
  fs.writeFileSync(path.join(directory, 'transcript.jsonl'), '')
  const native = path.join(directory, 'native-session.jsonl')
  const nativeId = '00000000-0000-4000-8000-000000000210'
  fs.writeFileSync(native, JSON.stringify({ type: 'session', version: 3, id: nativeId,
    timestamp: '2026-10-01T00:00:00Z', cwd: directory }) + '\n' + JSON.stringify({
    type: 'message', message: { role: 'user', content: 'existing native conversation' } }) + '\n')
  const flags = { 'cerebro-role': role, 'cerebro-mcp-config': config,
    'cerebro-bind': bind ? directory : undefined }
  const hooks = new Map()
  const registered = []
  const pi = {
    registerFlag: (key, definition) => { assert.equal(definition.type, 'string'); registered.push(key) },
    getFlag: (key) => flags[key],
    on: (name, callback) => hooks.set(name, callback),
  }
  extension(pi)
  assert.deepEqual(registered, ['cerebro-role', 'cerebro-mcp-config', 'cerebro-bind'])
  const factory = factories.at(-1)
  assert.equal(factory.pi, pi, 'the native MCP factory was not installed on the same Pi API')
  const context = { sessionManager: { getSessionFile: () => native, getSessionId: () => nativeId } }
  return { directory, native, nativeId, config, server, flags, hooks, factory, context }
}

try {
  for (const role of ['supervisor', 'reviewer']) {
    const state = setup(role, role, undefined, role === 'supervisor')
    const loaded = state.factory.options.loadConfig()
    assert.equal(loaded.servers.length, 1, 'restricted Pi inherited an unrelated native MCP server')
    assert.deepEqual(loaded.errors, [])
    assert.equal(loaded.autoEnableCodemode, false)
    assert.deepEqual(loaded.servers[0], { name: 'cerebro', config: {
      ...state.server, exposure: 'direct', timeout: 0,
    }, source: state.config, scope: 'extension' })
    const call = state.hooks.get('tool_call')
    assert.equal(call({ toolName: 'mcp__cerebro__command', input: { argv: ['status'] } }), undefined)
    for (const toolName of ['bash', 'edit', 'write', 'read', 'mcp__foreign__mutate']) {
      assert.equal(call({ toolName, input: {} }).block, true, 'restricted Pi admitted ' + toolName)
    }
    assert.throws(() => state.hooks.get('user_bash')({ command: 'touch escaped' }), /Shell execution is unavailable/)
    const input = 'literal `input` $(never execute); keep "quoted" text'
    state.hooks.get('message_end')({ message: { role: 'user', content: input } })
    state.hooks.get('message_end')({ message: { role: 'assistant', content: [{ type: 'text', text: 'ignored reply' }] } })
    state.hooks.get('message_end')({ message: { role: 'user', content: [
      { type: 'text', text: 'first line' }, { type: 'image', data: 'ignored' }, { type: 'text', text: 'second line' },
    ] } })
    const metadata = path.join(state.directory, 'metadata.json')
    assert.equal(JSON.parse(fs.readFileSync(metadata, 'utf8')).foreign_session_id, undefined)
    state.hooks.get('agent_settled')({}, state.context)
    if (role === 'supervisor') {
      const bound = JSON.parse(fs.readFileSync(metadata, 'utf8'))
      assert.equal(bound.foreign_session_id, state.native)
      assert.equal(fs.statSync(metadata).mode & 0o777, 0o600)
      const transcript = fs.readFileSync(path.join(state.directory, 'transcript.jsonl'), 'utf8')
        .trim().split('\n').map(JSON.parse)
      assert.deepEqual(transcript.map((entry) => [entry.kind, entry.text]),
        [['user', input], ['user', 'first line\nsecond line']])
      state.hooks.get('agent_settled')({}, state.context)
      assert.equal(fs.readFileSync(path.join(state.directory, 'transcript.jsonl'), 'utf8').trim().split('\n').length, 2)
    } else {
      assert.equal(JSON.parse(fs.readFileSync(metadata, 'utf8')).foreign_session_id, undefined)
      assert.equal(fs.readFileSync(path.join(state.directory, 'transcript.jsonl'), 'utf8'), '')
    }
  }

  const invalidRole = setup('invalid-role', 'observer', undefined, false)
  assert.throws(() => invalidRole.factory.options.loadConfig(), /Unsupported Cerebro role/)
  assert.throws(() => invalidRole.hooks.get('tool_call')({ toolName: 'mcp__cerebro__command' }), /Unsupported Cerebro role/)
  const reviewerBind = setup('reviewer-bind', 'reviewer')
  assert.throws(() => reviewerBind.hooks.get('agent_settled')({}, reviewerBind.context), /Only a supervisor/)
  for (const [name, metadata] of [
    ['retired-parent', { role: 'observer', backend: 'pi' }],
    ['foreign-binding', { role: 'supervisor', backend: 'pi', foreign_session_id: '/another/native-session.jsonl' }],
  ]) {
    const state = setup(name, 'supervisor', metadata)
    const before = fs.readFileSync(path.join(state.directory, 'metadata.json'), 'utf8')
    assert.throws(() => state.hooks.get('agent_settled')({}, state.context), /another parent session/)
    assert.equal(fs.readFileSync(path.join(state.directory, 'metadata.json'), 'utf8'), before)
  }
  const mismatch = setup('header-mismatch', 'supervisor')
  const before = fs.readFileSync(path.join(mismatch.directory, 'metadata.json'), 'utf8')
  mismatch.context.sessionManager.getSessionId = () => 'different-native-id'
  assert.throws(() => mismatch.hooks.get('agent_settled')({}, mismatch.context), /Invalid native Pi session identity/)
  assert.equal(fs.readFileSync(path.join(mismatch.directory, 'metadata.json'), 'utf8'), before)
} finally {
  loader.deregister()
  delete globalThis.cerebroFixtureMcpFactory
  fs.rmSync(temporary, { recursive: true, force: true })
}

console.log('all checks passed')
