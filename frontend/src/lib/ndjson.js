// Reads a newline-delimited JSON body one network chunk at a time. Each read
// yields that chunk's complete, non-empty lines (possibly none), so callers can
// handle a chunk's lines together. A line or a multi-byte character split
// across chunks waits for the rest; the last line needs no trailing newline.
// Parsing, cancel and releaseLock stay with the caller.
export async function* ndjsonBatches(reader) {
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = done ? "" : lines.pop();
    yield lines.filter(line => line.trim());
    if (done) return;
  }
}
