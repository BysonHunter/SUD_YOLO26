'use strict';
// Only a single 1 MiB plaintext/ciphertext record is held in browser memory.
// HTTPS authenticates both this code and the server's public transport key.
window.sudEncryptedUpload = async function (file, {api, progress, signal}) {
  if (!window.isSecureContext || !window.crypto?.subtle) {
    throw Error('Для защищённой загрузки откройте приложение через HTTPS. HTTP доступен только на localhost / 127.0.0.1.');
  }
  const subtle = window.crypto.subtle;
  const decode = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));
  const encode = bytes => btoa(String.fromCharCode(...bytes));
  const transport = await api('/upload-key', {signal});
  if (transport.protocol !== 'sud-aesgcm-v1' || transport.chunk_size !== 1048576) {
    throw Error('Сервер использует неизвестный протокол загрузки.');
  }
  const publicKey = await subtle.importKey('spki', decode(transport.spki),
    {name: 'RSA-OAEP', hash: 'SHA-256'}, false, ['encrypt']);
  const key = await subtle.generateKey({name: 'AES-GCM', length: 256}, true, ['encrypt']);
  const rawKey = new Uint8Array(await subtle.exportKey('raw', key));
  const wrapped = await subtle.encrypt({name: 'RSA-OAEP'}, publicKey, rawKey);
  rawKey.fill(0);
  const prefix = crypto.getRandomValues(new Uint8Array(8));
  const job = await api('/jobs', {method: 'POST', signal,
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({filename: file.name, size: file.size,
      wrapped_key: encode(new Uint8Array(wrapped)), nonce: encode(prefix)})});
  try {
    for (let offset = 0, index = 0; offset < file.size; offset += transport.chunk_size, index++) {
      signal?.throwIfAborted();
      const iv = new Uint8Array(12);
      iv.set(prefix);
      new DataView(iv.buffer).setUint32(8, index, false);
      const end = Math.min(file.size, offset + transport.chunk_size);
      const plain = await file.slice(offset, end).arrayBuffer();
      const additionalData = new TextEncoder().encode(`sud-aesgcm-v1:${job.id}:${file.size}:${index}`);
      const ciphertext = await subtle.encrypt({name: 'AES-GCM', iv, additionalData, tagLength: 128}, key, plain);
      await api(`/jobs/${job.id}/upload/${index}`, {method: 'PUT', signal,
        headers: {'Content-Type': 'application/octet-stream'}, body: ciphertext});
      progress(end / file.size * 100);
    }
    return await api(`/jobs/${job.id}/complete`, {method: 'POST', signal});
  } catch (error) {
    if (!signal?.aborted) {
      try { await api(`/jobs/${job.id}/cancel`, {method: 'POST', signal}); } catch {}
    }
    throw error;
  }
};
