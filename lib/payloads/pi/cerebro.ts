import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
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

  pi.on('input', (event, ctx) => {
    const directory = pi.getFlag('cerebro-bind');
    if (!directory || process.env.CEREBRO_INPUT_OWNER === 'external') return;
    role();
    const nativeFile = ctx.sessionManager.getSessionFile();
    if (!nativeFile || !ctx.sessionManager.getSessionId()) {
      ctx.ui.notify('Pi input has no native session identity.', 'error');
      return { action: 'handled' };
    }
    const helper = fileURLToPath(new URL('../../python/native_input.py', import.meta.url));
    const result = spawnSync('python3', [helper, 'pi', directory], {
      input: JSON.stringify({ session_id: nativeFile,
        content: [{ type: 'text', text: event.text }, ...(event.images || [])] }),
      encoding: 'utf8',
    });
    if (result.error || result.status !== 0) {
      ctx.ui.notify(result.error?.message || result.stderr || 'Pi user input capture failed.', 'error');
      return { action: 'handled' };
    }
  });
}
