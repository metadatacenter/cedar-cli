// Used only by the local reactor. Standalone/CI gates continue to execute every stage.
import { createHash, randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';

export function stageCache(environment = process.env) {
  if (environment.CEDAR_TASK_CACHE_CONFIG) {
    const path = environment.CEDAR_TASK_CACHE_CONFIG;
    const config = JSON.parse(readFileSync(path, 'utf8'));
    const logs = new Map();
    const misses = new Set();
    const probe = stage => JSON.stringify([stage.name, stage.script, stage.env ?? {}]);
    function call(operation, stage) {
      const result = spawnSync(config.python,
        ['-I', config.engine, operation, path, stage.name,
          JSON.stringify({ script: stage.script, environment: stage.env ?? {} })],
        { encoding: 'utf8', input: operation === 'save' ? logs.get(stage.name) ?? '' : '',
          maxBuffer: 4 * 1024 * 1024 });
      if (result.status !== 0) {
        console.warn(`[gate] cache ${stage.name}: ${result.stderr || result.error || 'unavailable'}`);
        return null;
      }
      try { return JSON.parse(result.stdout); } catch { return null; }
    }
    return {
      observe(stage, data) {
        logs.set(stage.name, (logs.get(stage.name) ?? '') + data.toString());
      },
      reuse(stage) {
        // Dependencies have completed before the gate probes. A miss need not spawn Python
        // again on every worker-pool poll; a concurrent cache fill can safely be missed.
        if (misses.has(probe(stage))) return false;
        const saved = call('reuse', stage);
        if (!saved) { misses.add(probe(stage)); return false; }
        console.log(`[gate] reuse ${stage.name} (${saved.key.slice(0, 12)}, passed ${saved.passedAt})`);
        return true;
      },
      save(stage) { call('save', stage); logs.delete(stage.name); misses.delete(probe(stage)); },
    };
  }
  let config;
  try { config = JSON.parse(environment.CEDAR_STAGE_CACHE); } catch { /* No cache. */ }
  const enabled = config && typeof config.directory === 'string' && isAbsolute(config.directory) &&
    /^[a-f0-9]{64}$/.test(config.fingerprint ?? '') && Array.isArray(config.stages);
  function identity(stage) {
    if (!enabled || !config.stages.includes(stage.name)) return null;
    const inputs = { fingerprint: config.fingerprint, stage: stage.name,
      script: stage.script, environment: stage.env ?? {} };
    const key = createHash('sha256').update(JSON.stringify(inputs)).digest('hex');
    return { key, path: join(config.directory, `${key}.json`), inputs };
  }
  return {
    reuse(stage) {
      const entry = identity(stage);
      if (!entry) return false;
      try {
        const saved = JSON.parse(readFileSync(entry.path, 'utf8'));
        if (saved.schemaVersion !== 1 || saved.key !== entry.key || saved.exitCode !== 0 ||
            JSON.stringify(saved.inputs) !== JSON.stringify(entry.inputs) ||
            !Number.isFinite(Date.parse(saved.passedAt))) return false;
        console.log(`[gate] reuse ${stage.name} (${entry.key.slice(0, 12)}, passed ${saved.passedAt})`);
        return true;
      } catch { return false; }
    },
    save(stage) {
      const entry = identity(stage);
      if (!entry) return;
      const temporary = `${entry.path}.${randomUUID()}.tmp`;
      try {
        mkdirSync(config.directory, { recursive: true });
        writeFileSync(temporary, JSON.stringify({ schemaVersion: 1, key: entry.key,
          inputs: entry.inputs, exitCode: 0, passedAt: new Date().toISOString() }));
        renameSync(temporary, entry.path);
      } catch (error) {
        console.warn(`[gate] could not record ${stage.name}: ${error.message}`);
      } finally {
        try { rmSync(temporary, { force: true }); } catch { /* A cache write is optional. */ }
      }
    },
  };
}
