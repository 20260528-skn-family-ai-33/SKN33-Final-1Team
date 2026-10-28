const KEY = 'creative-checker-modeltest-ssh';

function fields(value) {
  return {
    command: typeof value?.command === 'string' && value.command.length <= 2048 && !/[\r\n\0]/.test(value.command) ? value.command : '',
    endpoint: typeof value?.endpoint === 'string' && /^\/api\/modeltest\/ssh\/connections\/[\w-]{1,128}$/.test(value.endpoint) ? value.endpoint : '',
  };
}

export function readSSH(storage) {
  try { return fields(JSON.parse((storage || globalThis.localStorage).getItem(KEY))); } catch { return fields(null); }
}

export function writeSSH(value, storage = globalThis.localStorage) {
  storage.setItem(KEY, JSON.stringify(fields(value)));
}
