import { appendFileSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createMcpExtension } from '@earendil-works/pi-coding-agent';

export default function (pi) {
  pi.registerFlag('cerebro-role', { type: 'string' });
  pi.registerFlag('cerebro-mcp-config', { type: 'string' });
  pi.registerFlag('cerebro-bind', { type: 'string' });
  const role = () => {
    const value = pi.getFlag('cerebro-role');
    if (value !== 'supervisor') throw new Error('Unsupported Cerebro role');
    return value;
  };

  createMcpExtension({
    loadConfig: () => {
      role();
      const source = pi.getFlag('cerebro-mcp-config');
      const config = JSON.parse(readFileSync(source, 'utf8')).mcpServers.cerebro;
      return {
        servers: [{ name: 'cerebro', config: { ...config, exposure: 'direct', timeout: 0 }, source, scope: 'extension' }],
        errors: [], autoEnableCodemode: false,
      };
    },
  })(pi);

  const inputs = [];
  pi.on('message_end', (event) => {
    if (!pi.getFlag('cerebro-bind') || event.message.role !== 'user') return;
    const content = event.message.content;
    inputs.push(typeof content === 'string' ? content : content.filter((part) => part.type === 'text').map((part) => part.text).join('\n'));
  });
  pi.on('agent_settled', (_event, ctx) => {
    const directory = pi.getFlag('cerebro-bind');
    if (!directory) return;
    if (role() !== 'supervisor') throw new Error('Only a supervisor can bind a parent session.');
    const nativeFile = ctx.sessionManager.getSessionFile();
    const header = JSON.parse(readFileSync(nativeFile, 'utf8').split('\n', 1)[0]);
    if (header.type !== 'session' || header.id !== ctx.sessionManager.getSessionId()) {
      throw new Error('Invalid native Pi session identity.');
    }
    const metadata = join(directory, 'metadata.json');
    const meta = JSON.parse(readFileSync(metadata, 'utf8'));
    if (('role' in meta && meta.role !== 'supervisor') || (meta.foreign_session_id && meta.foreign_session_id !== nativeFile)) {
      throw new Error('Pi notification belongs to another parent session.');
    }
    meta.foreign_session_id = nativeFile;
    meta.last_touched = new Date().toISOString();
    const temporary = metadata + '.tmp.' + process.pid;
    writeFileSync(temporary, JSON.stringify(meta, null, 2) + '\n', { mode: 0o600 });
    renameSync(temporary, metadata);
    for (const text of inputs.splice(0)) {
      appendFileSync(join(directory, 'transcript.jsonl'), JSON.stringify({ kind: 'user', ts: meta.last_touched, text }) + '\n');
    }
  });
}
