// The native MCP registry may populate after plugin activation. Guarded
// inference must wait for the actual command tool, then pin session policy.
import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import plugin from "../lib/payloads/plugin/cerebro.js"

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "cerebro-opencode-plugin-tests-"))
const originalSessionDir = process.env.CEREBRO_SESSION_DIR
const originalSetTimeout = globalThis.setTimeout
const originalClearTimeout = globalThis.clearTimeout

async function setup(role, permissions, tools) {
  const directory = path.join(temporary, role)
  fs.mkdirSync(directory)
  fs.writeFileSync(path.join(directory, "metadata.json"), JSON.stringify({ id: role, backend: "opencode" }))
  fs.writeFileSync(path.join(directory, "transcript.jsonl"), "")
  process.env.CEREBRO_SESSION_DIR = directory
  const state = { directory, tools: [...tools], updates: [] }
  const editor = {
    list: () => state.tools.map((id) => ({ id })),
    remove: (id) => { state.tools = state.tools.filter((tool) => tool !== id) },
  }
  const context = {
    options: { role, permissions },
    tool: { transform: async (callback) => { state.transform = callback; callback(editor) } },
    session: {
      hook: async (name, callback) => { assert.equal(name, "prompt"); state.prompt = callback },
      update: async (update) => { state.updates.push(update) },
    },
    rpc: { register: async (definition, handlers) => {
      assert.equal(definition.id, "cerebro")
      assert.equal(await handlers.ready({}), true)
    } },
  }
  await plugin.setup(context)
  state.refresh = (tools) => { state.tools = [...tools]; state.transform(editor) }
  return state
}

try {
  for (const role of ["supervisor", "observer", "reviewer"]) {
    const permissions = [
      { action: "*", resource: "*", effect: "deny" },
      ...["read", "grep", "skill", "cerebro_command"].map((action) => ({ action, resource: "*", effect: "allow" })),
    ]
    const state = await setup(role, permissions, ["read", "shell", "edit", "subagent"])
    assert.deepEqual(state.tools, ["read"], "guarded registry admitted a native mutation tool")
    const event = { sessionID: "native-" + role, prompt: { text: "literal `input` $(never execute)" } }
    let complete = false
    const pending = state.prompt(event).then(() => { complete = true })
    await new Promise(setImmediate)
    assert.equal(complete, false, "guarded inference began before native MCP registration")
    assert.deepEqual(state.updates, [])
    assert.equal(fs.readFileSync(path.join(state.directory, "transcript.jsonl"), "utf8"), "")
    state.refresh(["read", "shell", "edit", "subagent", "cerebro_command"])
    await pending
    assert.deepEqual(state.tools, ["read", "cerebro_command"])
    assert.deepEqual(state.updates, [{ sessionID: event.sessionID, permissions }])
    const metadata = JSON.parse(fs.readFileSync(path.join(state.directory, "metadata.json"), "utf8"))
    assert.equal(metadata.foreign_session_id, event.sessionID)
    const transcript = JSON.parse(fs.readFileSync(path.join(state.directory, "transcript.jsonl"), "utf8"))
    assert.equal(transcript.kind, "user")
    assert.equal(transcript.text, event.prompt.text)
  }

  const denied = await setup("missing-command", [{ action: "*", resource: "*", effect: "deny" }], [])
  let timeoutCallback
  let timeoutCleared = false
  globalThis.setTimeout = (callback, milliseconds) => {
    assert(milliseconds > 0 && milliseconds <= 30_000, "native MCP startup failure must be bounded")
    timeoutCallback = callback
    return "fixture-clock"
  }
  globalThis.clearTimeout = (timer) => { assert.equal(timer, "fixture-clock"); timeoutCleared = true }
  const rejected = assert.rejects(denied.prompt({ sessionID: "unready", prompt: { text: "refuse inference" } }),
                                 /Cerebro command tool did not become ready/)
  assert.equal(typeof timeoutCallback, "function")
  timeoutCallback()
  await rejected
  assert.equal(timeoutCleared, true)
  assert.deepEqual(denied.updates, [], "unready MCP startup pinned a session for inference")
  assert.equal(fs.readFileSync(path.join(denied.directory, "transcript.jsonl"), "utf8"), "")

  globalThis.setTimeout = () => { throw new Error("writer waited for a privileged command channel") }
  const writerPermissions = [
    { action: "*", resource: "*", effect: "allow" },
    { action: "subagent", resource: "*", effect: "deny" },
  ]
  const writer = await setup("execute", writerPermissions, ["read", "edit", "shell"])
  assert.equal(writer.transform, undefined)
  await writer.prompt({ sessionID: "native-writer", prompt: { text: "delegated task" } })
  assert.deepEqual(writer.updates, [{ sessionID: "native-writer", permissions: writerPermissions }])
} finally {
  globalThis.setTimeout = originalSetTimeout
  globalThis.clearTimeout = originalClearTimeout
  if (originalSessionDir === undefined) delete process.env.CEREBRO_SESSION_DIR
  else process.env.CEREBRO_SESSION_DIR = originalSessionDir
  fs.rmSync(temporary, { recursive: true, force: true })
}

console.log("all checks passed")
