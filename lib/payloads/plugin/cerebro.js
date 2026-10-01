import fs from "node:fs"
import path from "node:path"

export default {
  id: "cerebro",
  async setup(ctx) {
    const permissions = ctx.options.permissions || []
    const guarded = permissions[0]?.effect === "deny"
    let commandRegistered
    const commandReady = new Promise((resolve) => { commandRegistered = resolve })
    if (guarded) {
      const allowed = new Set(permissions.filter((rule) => rule.effect === "allow").map((rule) => rule.action))
      await ctx.tool.transform((editor) => {
        for (const tool of editor.list()) if (!allowed.has(tool.id)) editor.remove(tool.id)
        if (editor.list().some((tool) => tool.id === "cerebro_command")) commandRegistered()
      })
    }
    const dir = process.env.CEREBRO_SESSION_DIR
    await ctx.session.hook("prompt", async (event) => {
      // MCP connects asynchronously; its native registry refresh admits the
      // direct command tool before guarded inference starts.
      if (guarded) {
        let timeout
        try {
          await Promise.race([commandReady, new Promise((_, reject) => {
            timeout = setTimeout(() => reject(new Error("Cerebro command tool did not become ready; check MCP startup")), 30_000)
          })])
        } finally { clearTimeout(timeout) }
      }
      // Native session rules follow agent-local grants. Pin them before model
      // inference so user agent settings cannot restore edit, shell or execute.
      if (permissions.length) {
        await ctx.session.update({ sessionID: event.sessionID, permissions })
      }
      if (!dir) return
      const metadata = path.join(dir, "metadata.json")
      const meta = JSON.parse(fs.readFileSync(metadata, "utf8"))
      meta.foreign_session_id = event.sessionID
      meta.last_touched = new Date().toISOString()
      const tmp = `${metadata}.tmp.${process.pid}`
      fs.writeFileSync(tmp, JSON.stringify(meta, null, 2) + "\n")
      fs.renameSync(tmp, metadata)
      fs.appendFileSync(path.join(dir, "transcript.jsonl"), JSON.stringify({
        kind: "user", ts: meta.last_touched, text: event.prompt.text,
      }) + "\n")
    })
    // RPC calls await native plugin activation, unlike the inventory endpoint.
    await ctx.rpc.register({
      id: "cerebro", methods: { ready: { input: { type: "object" }, output: { type: "boolean" } } }, events: {},
    }, { ready: async () => true })
  },
}
